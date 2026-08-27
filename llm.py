"""LLM 大脑：支持 function calling（工具调用）。

- DeepSeek → 走 Responses API（/responses，Agent 专用，带 reasoning + function_call）
- GLM → 回退 Chat Completions（/chat/completions，智谱暂不支持 Responses）
统一返回格式：{"content": str, "tool_calls": [{"function": {"name","arguments"}}]}
"""
import json
import base64
import requests
import config

SYSTEM_PROMPT = """你是「黄雀媒体制作官」，能调用一组黄雀 AI 工具帮用户完成内容生产：
- 生成图片（文生图/图生图/多图）、视频、配音、数字人口播、文案成片
- 采集抖音/小红书内容、提取口播文案、生成获客名单
- 查询资产、任务、音色、数字人形象、账号点数

你有跨轮次的会话记忆（后端会持久化对话历史），能记住用户在本次会话中说过的偏好、需求和之前发起的任务。

图片任务（生成图片/改图/看图）请委派给「图片子 Agent」，用 delegate_image 工具；视频任务用 delegate_video；数字人口播用 delegate_digital_human。不要直接调用底层生成工具。

规则：
1. 用户提出需要生成/采集/查询的需求时，用 function calling 调用对应工具（只调一个最合适的）。
2. 用户只是闲聊或需求不明确时，用普通文本回复，问清楚再动手。
3. 涉及需要前置信息的（如数字人形象ID、模板key、音色id），先调用查询工具获取，或提示用户。
4. 图生图/参考图/看图：图片数据系统已自动处理（用户上传的图、生成的图都能直接用）。用户说“根据这张图/改成xx风格/看看这张图”时，直接委派 delegate_image 的对应意图（图生图/看图），不要询问用户要图片 URL 或 upload_id。
5. 不编造工具名或参数。
6. 「创建数字人形象（上传真人照片）」和「声音克隆（上传样音训练音色）」通过网页上的专用按钮完成（用户上传照片/音频后，后端自动调用黄雀创建）。用户提到这两个需求时，引导用户点击网页上的「创建数字人」/「克隆声音」按钮上传素材；创建完成后可用「查看数字人形象」/「查看音色」查到结果。
"""


def _llm(messages, tools=None, max_tokens=1000):
    """统一入口：返回 {'content': str, 'tool_calls': [...]}。
    deepseek/openai(luna) → Responses API；anthropic → Messages API；其余（glm/openai 兼容）→ Chat Completions。
    """
    cfg = config.llm_config()
    provider = cfg.get("provider", "openai")
    if provider in ("deepseek", "openai"):
        return _responses(messages, tools, max_tokens)
    if provider == "anthropic":
        return _anthropic_messages(messages, tools, max_tokens)
    return _chat_completions(messages, tools, max_tokens)


def _chat_completions(messages, tools=None, max_tokens=1000):
    """Chat Completions API（GLM / 通用兼容）。"""
    cfg = config.llm_config()
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
    body = {"model": cfg["model"], "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls")}


def _proxies(cfg):
    p = cfg.get("proxy", "")
    return {"http": p, "https": p} if p else None


def _responses(messages, tools=None, max_tokens=1000):
    cfg = config.llm_config()
    url = cfg["base_url"].rstrip("/") + "/responses"
    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
    # system 消息拆成 instructions，其余进 input
    instructions = ""
    input_msgs = []
    for m in messages:
        if m["role"] == "system":
            instructions = m["content"]
        else:
            input_msgs.append({"role": m["role"], "content": m["content"]})
    body = {
        "model": cfg["model"],
        "instructions": instructions,
        "input": input_msgs,
        "max_output_tokens": max_tokens,
    }
    if tools:
        # chat 格式 tools → responses 格式（去掉 function 包裹）
        body["tools"] = [
            {"type": t["type"], "name": t["function"]["name"],
             "description": t["function"]["description"], "parameters": t["function"]["parameters"]}
            for t in tools
        ]
    r = requests.post(url, headers=headers, json=body, timeout=60, proxies=_proxies(cfg))
    r.raise_for_status()
    resp = r.json()
    # 解析 output（message / function_call / reasoning 等）
    content = ""
    tool_calls = []
    for item in resp.get("output", []):
        t = item.get("type")
        if t == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    content += c.get("text", "")
        elif t == "function_call":
            tool_calls.append({"function": {"name": item.get("name"),
                                            "arguments": item.get("arguments", "{}")}})
    return {"content": content, "tool_calls": tool_calls}


def _anthropic_messages(messages, tools=None, max_tokens=1000):
    """Anthropic Messages API（自定义 anthropic 格式供应商）。"""
    cfg = config.llm_config()
    url = cfg["base_url"].rstrip("/") + "/messages"
    headers = {
        "x-api-key": cfg["key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    system = ""
    msgs = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    body = {"model": cfg["model"], "max_tokens": max_tokens, "messages": msgs}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [
            {"name": t["function"]["name"], "description": t["function"]["description"],
             "input_schema": t["function"]["parameters"]}
            for t in tools
        ]
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    resp = r.json()
    content = ""
    tool_calls = []
    for item in resp.get("content", []):
        t = item.get("type")
        if t == "text":
            content += item.get("text", "")
        elif t == "tool_use":
            tool_calls.append({"function": {"name": item.get("name"),
                                            "arguments": json.dumps(item.get("input", {}))}})
    return {"content": content, "tool_calls": tool_calls}


def chat(user_message, history=None):
    """一轮对话：返回 {'type':'text','text':...} 或 {'type':'tool','tool':...,'params':...}。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or []):
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    try:
        msg = _llm(messages, tools=to_openai_tools())
    except Exception as e:
        return {"type": "error", "message": f"大脑模型调用失败：{str(e)[:200]}"}

    if msg.get("tool_calls"):
        tc = msg["tool_calls"][0]
        name = tc["function"]["name"]
        try:
            params = json.loads(tc["function"].get("arguments", "{}"))
        except Exception:
            params = {}
        return {"type": "tool", "tool": name, "params": params, "text": msg.get("content", "")}

    return {"type": "text", "text": msg.get("content", "")}


def summarize(user_question, tool_name, result, history=None):
    """把工具执行结果喂回 LLM，生成自然语言汇报。"""
    messages = [{"role": "system", "content": "你是黄雀媒体制作官。根据工具执行结果，用简洁的中文向用户汇报，不要输出 JSON。"}]
    for h in (history or [])[-8:]:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content":
        f"用户问：{user_question}\n工具 {tool_name} 的执行结果：{json.dumps(result, ensure_ascii=False)[:1500]}\n请用自然语言回答用户。"})
    try:
        msg = _llm(messages, max_tokens=500)
        return msg.get("content", "")
    except Exception as e:
        return f"（结果获取成功，但总结失败：{str(e)[:100]}）"


def to_openai_tools():
    """把工具注册表转成 Chat Completions 的 tools 格式（Responses 内部再转换）。"""
    import tools as T
    out = []
    for t in T.TOOLS:
        out.append({
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]},
        })
    return out


def vision(image_url, question, model_id="luna"):
    """用视觉模型（默认 luna）读取图片内容，返回文本描述。
    http URL 会先下载转 base64（OpenAI 服务器访问不了国内图片域名，直接用 URL 会 400）。
    """
    cfg = config.MODELS.get(model_id) or config.MODELS.get("luna") or {}
    url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/responses"
    headers = {"Authorization": f"Bearer {cfg.get('key', '')}", "Content-Type": "application/json"}
    # http URL → base64（下载失败则保持原 URL 试一次）
    if isinstance(image_url, str) and image_url.startswith("http"):
        try:
            data = requests.get(image_url, timeout=30).content
            lower = image_url.lower()
            mime = "image/jpeg" if (lower.endswith(".jpg") or lower.endswith(".jpeg")) else \
                   ("image/webp" if lower.endswith(".webp") else "image/png")
            image_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        except Exception:
            pass
    body = {
        "model": cfg.get("model", "gpt-5.6-luna"),
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": question},
            {"type": "input_image", "image_url": image_url},
        ]}],
        "max_output_tokens": 800,
    }
    r = requests.post(url, headers=headers, json=body, timeout=90, proxies=_proxies(cfg))
    r.raise_for_status()
    resp = r.json()
    text = ""
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
    return text.strip()
