"""Agent 循环：把「聊天 + 工具调用」串起来。

一轮 turn：
1. LLM 理解用户消息，决定是普通回复还是调用工具
2. 调用工具：
   - 免费/读工具 → 直接执行，返回结果
   - 付费工具 → 先报价（cost + quote_token），等用户确认后 execute_tool 才真正扣费执行
"""
import json
import re
import llm
import tools
import hq

# 需要结构化展示（不走自然语言总结）的工具——结果含图片/列表，直接给前端渲染
def _resolve_voice(voice):
    """把 LLM/用户提供的 voice（序号/名字/voice_key）解析成 voice_key。
    LLM 经常把 voice 填成名字或「音色N」，黄雀后端只认 voice_key。"""
    if not voice:
        return ""
    voice = str(voice).strip()
    # 已经是 voice_key（S_ 或 vip_ 开头）
    if voice.startswith(("S_", "vip_")):
        return voice
    try:
        vs = hq.run("voices", {}, confirm=False)
        v_items = (vs.get("result") or {}).get("items") or []
    except Exception:
        return ""
    if not v_items:
        return ""
    # 序号："音色5" / "音色五" / "第5个"
    CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    m = re.search(r"音色\s*([一二三四五六七八九十]|\d+)", voice)
    if m:
        raw = m.group(1)
        idx = CN.get(raw) or (int(raw) if raw.isdigit() else None)
        if idx and 1 <= idx <= len(v_items):
            return v_items[idx - 1].get("voice_key") or ""
    # 名字匹配
    for v in v_items:
        if voice == v.get("display_name") or voice == v.get("voice_name"):
            return v.get("voice_key") or ""
    return ""


def _default_voice():
    """默认音色：第一个可用音色的 voice_key。"""
    try:
        vs = hq.run("voices", {}, confirm=False)
        v_items = (vs.get("result") or {}).get("items") or []
        if v_items:
            return v_items[0].get("voice_key") or ""
    except Exception:
        pass
    return ""


def _voice_global_index(slot_id, voice_key):
    """返回音色在完整音色列表(list_voices)里的全局序号(1-based)。
    用于让「音色槽位」的序号与「查看音色」一致，避免用户说「音色2」时被错解。"""
    try:
        vs = hq.run("voices", {}, confirm=False)
        v_items = (vs.get("result") or {}).get("items") or []
        for i, v in enumerate(v_items, 1):
            if v.get("voice_key") == voice_key or (slot_id and v.get("slot_id") == slot_id):
                return i
    except Exception:
        pass
    return None


def _extract_voice_number(text):
    """从用户消息提取音色序号（支持：音色5、5号音色、第5个、第5号）。返回字符串序号如'5'，无则 None。"""
    if not text:
        return None
    for m in re.finditer(r"音色\s*([一二三四五六七八九十]|\d+)|(\d+)\s*号\s*音色|第\s*([一二三四五六七八九十]|\d+)\s*个|第\s*([一二三四五六七八九十]|\d+)\s*号", text):
        for g in m.groups():
            if g:
                return g
    return None


def _find_prev_voice_text(history):
    """从历史里找上一轮口播的文案/主题（用于换音色时沿用）。"""
    for h in reversed(history or []):
        if h.get("role") != "user":
            continue
        content = (h.get("content") or "").strip()
        if not content or len(content) < 4:
            continue
        # 只跳过明确的音色控制/确认短句；不能误伤“换季肌肤……”这类真实文案。
        if (_extract_voice_number(content) is not None or
                re.search(r"^(?:换(?:个|一个|成)?(?:音色|声音)|确认(?:执行)?|开始|ok|yes)",
                          content, re.I)):
            continue
        return content
    return ""


DISPLAY_TOOLS = {"list_avatars", "list_voices", "list_assets", "list_voice_slots"}


# 黄雀主站域名，用于把相对路径的图片/音色 URL 补全
HQ_SITE = "https://huangquechuanmei.com"


def _fix_urls(obj):
    """把结果里相对路径的 url（/api/gen/file/...）补全为完整域名。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("url") and isinstance(v, str) and v.startswith("/"):
                obj[k] = HQ_SITE + v
            elif isinstance(v, (dict, list)):
                _fix_urls(v)
    elif isinstance(obj, list):
        for it in obj:
            _fix_urls(it)
    return obj


def clean_desc(desc):
    """把工具描述转成自然的动作短语（去掉括号补充和句号后内容）。"""
    d = (desc or "").split("（")[0].split("。")[0].strip()
    return d or "处理这个需求"


def _is_approval(text):
    """判断用户消息是否表达「确认执行」。排除「换/改/重新」等修改意图，避免误判。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    # 修改/否定意图：明确不是确认（如「可以换个音色吗」里的“可以”不能当确认）
    if any(kw in t for kw in ("换", "改", "重新", "不要", "别的", "另一个", "取消", "先别")):
        return False
    for kw in ("确认", "开始", "生成吧", "做吧", "执行", "ok", "yes"):
        if kw in t:
            return True
    return False


def _approve_and_run(pending_quote, history=None):
    """确认执行：用 pending_quote 委派子 Agent（confirmed=True），把结果转成自然语言。
    quote_token 传空，让子 Agent 内部重新报价+提交（旧 quote_token 一次性，且重试时需新任务）。
    渠道自动路由：返回 next_provider，供失败重试时切换渠道。
    """
    tool = tools.get_tool(pending_quote.get("tool", ""))
    if not tool:
        return {"type": "error", "message": "未知委派工具", "assistant_content": "出错"}
    # 渠道自动路由：仅图片委派工具(delegate_image)注入 provider
    params = dict(pending_quote.get("params", {}))
    next_p = None
    if tool.get("capability") == "__delegate_image__":
        next_p = _next_provider(pending_quote)
        inner = dict((params.get("params") or {}))
        inner["provider"] = next_p
        params["params"] = inner
    # 普通付费工具用 pending_quote 里的 quote_token；图片委派工具传空（内部重新报价）
    quote_token = "" if tool.get("capability") == "__delegate_image__" else pending_quote.get("quote_token", "")
    res = tools.call_tool(tool, params,
                          confirm=True, quote_token=quote_token)
    result = {}
    if res.get("_specialist"):
        sr = res["result"]
        status = sr.get("status")
        if status == "running":
            jid = sr.get("job_id", "")
            result = {"type": "running", "job_id": jid, "summary": sr.get("summary", ""),
                      "assistant_content": f"已提交任务 {jid}，正在生成。"}
        elif status == "completed":
            out = sr.get("result") or {}
            if isinstance(out, dict) and "description" in out:
                result = {"type": "text", "text": out["description"], "result": out,
                          "assistant_content": out["description"]}
            else:
                result = {"type": "text", "text": sr.get("summary", "完成"), "result": out,
                          "assistant_content": sr.get("summary", "")}
        elif status == "failed":
            result = {"type": "error", "message": sr.get("summary", "失败"),
                      "assistant_content": sr.get("summary", "失败")}
    else:
        # 普通付费工具（如 generate_audio）：confirm=True 后返回 job_id（异步）或 url（同步）
        rr = res.get("result") or {}
        if rr.get("job_id"):
            result = {"type": "running", "job_id": str(rr["job_id"]), "summary": "已提交",
                      "assistant_content": f"已提交任务 {rr['job_id']}，正在生成。"}
        elif rr.get("url") or rr.get("urls"):
            result = {"type": "result", "tool": tool["name"], "result": rr,
                      "assistant_content": "已完成"}
    if not result:
        msg = res.get("message") or res.get("error") or "执行失败"
        result = {"type": "error", "message": msg, "assistant_content": msg}
    # 记录本次使用的渠道 + 下一个渠道（仅图片委派工具）
    if next_p:
        result["used_provider"] = next_p
        result["next_provider"] = _provider_after(next_p)
    return result


def _provider_after(provider):
    """返回某渠道之后的下一个渠道（轮转）。"""
    import subagents
    order = subagents.IMAGE_PROVIDER_ORDER
    if provider in order:
        return order[(order.index(provider) + 1) % len(order)]
    return order[0]


def _next_provider(pending_quote):
    """返回本次应使用的渠道：pending_quote 当前 provider 的下一个（首次为 seedream）。"""
    import subagents
    inner = dict((pending_quote or {}).get("params", {}).get("params", {}) or {})
    return _provider_after(inner.get("provider", ""))


def run_turn(user_message, history=None, images=None, image_data_urls=None, pending_quote=None):
    """一轮对话。返回结构：
    - {'type':'text', 'text':...}            普通回复
    - {'type':'quote', 'tool':..., 'params':..., 'cost':..., 'points':..., 'quote_token':..., 'pending_quote':..., 'explanation':...}  付费工具报价
    - {'type':'result', 'tool':..., 'result':...}  免费工具直接结果
    images: 已上传图片的 upload_id 列表，注入为图生图上下文。
    image_data_urls: 已上传图片的 base64 data URL，委派看图时自动填入。
    pending_quote: 待确认报价（上一轮报价存下来的），用户确认时自动执行。
    """
    image_data_urls = image_data_urls or []

    # 确认执行：存在待确认报价，且用户表达确认 → 直接委派执行（不再让 LLM 重复报价）
    if pending_quote and _is_approval(user_message):
        return _approve_and_run(pending_quote, history)

    if images or image_data_urls:
        # 不暴露 upload_id/base64 等技术细节，只告诉 LLM「用户附了图」
        user_message = (user_message or "") + "\n[上下文：用户已附一张图片。若要用这张图生成新图，委派 delegate_image 的「图生图」；若要查看/分析这张图，委派 delegate_image 的「看图」。图片数据系统会自动处理，你无需传递任何 ID。]"

    r = llm.chat(user_message, history)

    if r["type"] == "text":
        # 换音色兜底：用户说「音色N/第N个/N号音色」，LLM 却没委派时，强制换音色重新生成上一轮文案
        vn = _extract_voice_number(user_message)
        prev_text = _find_prev_voice_text(history)
        if vn is not None and prev_text:
            voice = _resolve_voice("音色" + vn)
            if voice:
                r = {"type": "tool", "tool": "delegate_digital_human",
                     "params": {"intent": "数字人口播",
                               "params": {"text": prev_text, "voice": voice}},
                     "text": ""}
        if r["type"] == "text":
            # LLM 偶尔返回空 content（推理模式），给友好提示而不是空白
            text = r["text"] or "抱歉，我刚才没想清楚，请换个说法再说一次～"
            return {"type": "text", "text": text, "assistant_content": text}

    if r["type"] == "error":
        return {"type": "error", "message": r["message"], "assistant_content": f"出错：{r['message']}"}

    tool_name = r["tool"]
    tool = tools.get_tool(tool_name)
    if not tool:
        t = f"抱歉，我无法识别工具 {tool_name}。"
        return {"type": "text", "text": t, "assistant_content": t}

    params = r["params"]

    # 委派 delegate_image 时，自动填入上传图片的数据（upload_id 供图生图，base64 供看图）
    if tool_name == "delegate_image":
        p = dict(params.get("params") or {})
        intent = params.get("intent")
        if intent == "看图" and image_data_urls and not p.get("image_url"):
            p["image_url"] = image_data_urls[0]
        elif intent == "图生图" and images and not p.get("image_upload_id"):
            p["image_upload_id"] = images[0]
        params["params"] = p

    # 委派 delegate_digital_human 时，自动补默认形象(avatar_id)和规范化音色(voice)
    if tool_name == "delegate_digital_human":
        p = dict(params.get("params") or {})
        if not p.get("avatar_id"):
            try:
                av = hq.run("video-avatars", {}, confirm=False)
                av_items = (av.get("result") or {}).get("items") or []
                ready = [a for a in av_items if a.get("status") == "ready"]
                if ready:
                    p["avatar_id"] = ready[0].get("id")
            except Exception:
                pass
        # voice 规范化：LLM 可能填名字或「音色N」，解析成 voice_key
        voice_resolved = _resolve_voice(p.get("voice"))
        if not voice_resolved:
            # 兑底：从用户原话里提取「N号音色」或「音色N」
            m = re.search(r"(\d+)\s*号\s*音色|音色\s*([一二三四五六七八九十\d]+)", user_message or "")
            if m:
                voice_resolved = _resolve_voice("音色" + (m.group(1) or m.group(2)))
        p["voice"] = voice_resolved or _default_voice()
        params["params"] = p

    # generate_audio(配音) 的 voice 也规范化（LLM 可能填名字或「音色N」）
    if tool_name == "generate_audio" and isinstance(params, dict) and params.get("voice"):
        params["voice"] = _resolve_voice(params["voice"]) or params["voice"]

    # 多图拆分：generate_image 的 count>1 时，黄雀引擎2 不支持多图（400），拆成多次单张
    batch_count = 0
    if tool_name == "generate_image" and isinstance(params, dict) and params.get("count", 1) > 1:
        batch_count = int(params["count"])
        params = dict(params)
        params["count"] = 1

    if tool["paid"]:
        # 付费：先报价，不扣费
        res = tools.call_tool(tool, params, confirm=False)
        if "result" in res and res.get("result"):
            rr = res["result"]
            if "quote_token" in rr:
                cost = rr.get("cost")
                total = cost * batch_count if batch_count else cost
                desc = clean_desc(tool.get('description', ''))
                if batch_count:
                    explanation = f"好的，我来{desc}，你要 {batch_count} 张，我拆成 {batch_count} 次单张生成，每张 {cost} 点（共约 {total} 点，余额 {rr.get('points')}）。开始吗？"
                    assistant = f"已报价：{desc}，{batch_count} 张拆成单张，每张 {cost} 点，等用户确认。"
                else:
                    explanation = f"好的，我来{desc}，费用 {cost} 点（余额 {rr.get('points')}）。开始吗？"
                    assistant = f"已报价：{desc}，费用 {cost} 点，等用户确认。"
                return {
                    "type": "quote",
                    "tool": tool_name,
                    "params": params,
                    "batch_count": batch_count,
                    "cost": cost,
                    "points": rr.get("points"),
                    "quote_token": rr["quote_token"],
                    "pending_quote": {"tool": tool_name, "params": params,
                                     "quote_token": rr["quote_token"]},
                    "explanation": explanation,
                    "assistant_content": assistant,
                }
            return {"type": "result", "tool": tool_name, "result": rr, "assistant_content": json.dumps(rr, ensure_ascii=False)[:300]}
        msg = res.get("message") or str(res)[:200]
        return {"type": "error", "message": msg, "assistant_content": f"出错：{msg}"}

    # 免费/读：直接执行
    res = tools.call_tool(tool, params, confirm=False)
    # 子 Agent 委派结果（SpecialistResult 状态机）→ 转成主 Agent 能处理的形式
    if res.get("_specialist"):
        sr = res["result"]
        status = sr.get("status")
        if status == "needs_user_input":
            q = sr.get("question", "")
            return {"type": "text", "text": q, "assistant_content": q}
        if status == "needs_approval":
            quote = sr.get("quote", {})
            return {
                "type": "quote", "tool": tool_name, "params": params,
                "batch_count": quote.get("batch", 1),
                "cost": quote.get("cost"), "points": quote.get("points"),
                "quote_token": quote.get("quote_token"),
                "pending_quote": {"tool": tool_name, "params": params,
                                 "quote_token": quote.get("quote_token")},
                "explanation": f"好的，我来{clean_desc(tool.get('description',''))}，费用 {quote.get('cost')} 点（余额 {quote.get('points')}）。开始吗？",
                "assistant_content": f"已报价：费用 {quote.get('cost')} 点，等用户确认。",
            }
        if status == "running":
            jid = sr.get("job_id", "")
            return {"type": "running", "job_id": jid, "summary": sr.get("summary", ""),
                    "assistant_content": f"已提交任务 {jid}，等待生成。"}
        if status == "completed":
            out = sr.get("result") or {}
            if isinstance(out, dict) and "description" in out:
                return {"type": "text", "text": out["description"], "tool": tool_name,
                        "result": out, "assistant_content": out["description"]}
            text = sr.get("summary", "完成")
            if out:
                text += "\n" + json.dumps(out, ensure_ascii=False)[:300]
            return {"type": "text", "text": text, "tool": tool_name, "result": out,
                    "assistant_content": sr.get("summary", "")}
        if status == "failed":
            return {"type": "error", "message": sr.get("summary", "失败"), "assistant_content": sr.get("summary", "失败")}
    if "result" in res and res.get("result"):
        rr = _fix_urls(res["result"])
        # 需要结构化展示的工具（形象/音色/资产）：直接返回，让前端渲染图片/列表
        if tool_name in DISPLAY_TOOLS:
            assistant = f"查询了{tool_name}"
            # 音色类：把「序号 + 音色 + ID」写进历史，让用户可说「音色N」指定
            if tool_name in ("list_voices", "list_voice_slots"):
                items = (rr.get("items") or [])
                lines = []
                for i, it in enumerate(items, 1):
                    name = it.get("display_name") or it.get("voice_name") or it.get("voice_key") or "未命名"
                    key = it.get("voice_key") or it.get("slot_id") or ""
                    it["index"] = i
                    lines.append(f"{i}. {name}（voice_key:{key}）")
                assistant = "当前可用音色（序号固定，克隆音色是你的声音）：\n" + "\n".join(lines) + "\n（用户可说「用音色N」或直接说音色名来指定）"
            return {"type": "display", "tool": tool_name, "result": rr,
                    "assistant_content": assistant}
        summary = llm.summarize(user_message, tool_name, rr, history)
        return {"type": "text", "text": summary, "tool": tool_name, "result": rr, "assistant_content": summary}
    msg = res.get("message") or str(res)[:200]
    return {"type": "error", "message": msg, "assistant_content": f"出错：{msg}"}


def execute_tool(tool_name, params, quote_token=None, batch_count=1):
    """确认执行一个工具（付费需 quote_token）。多图时循环单张生成。返回 hq 原始结果。"""
    tool = tools.get_tool(tool_name)
    if not tool:
        return {"error": f"未知工具 {tool_name}"}
    if tool_name == "generate_image" and batch_count > 1:
        # 多图：循环单张生成（每次重新报价 + 执行）
        jobs = []
        p = dict(params or {})
        p["count"] = 1
        for i in range(int(batch_count)):
            q = tools.call_tool(tool, p, confirm=False)
            token = (q.get("result") or {}).get("quote_token", "")
            if not token:
                jobs.append({"index": i + 1, "error": q.get("message") or str(q)[:200]})
                continue
            r = tools.call_tool(tool, p, confirm=True, quote_token=token)
            jobs.append({"index": i + 1, **r})
        return {"result": {"batch": True, "count": int(batch_count), "jobs": jobs}}
    return tools.call_tool(tool, params, confirm=bool(quote_token), quote_token=quote_token)


def get_task(job_id):
    """轮询异步任务。"""
    import hq
    return hq.task(job_id)
