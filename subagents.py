"""子 Agent：对「业务结果」负责，而不是对单一工具负责。

ImageAgent = 图片业务专家：
- 文生图 / 图生图 / 多图 / 看图（describe）
- 参数完整性检查（缺参数 → needs_user_input）
- 报价（付费 → needs_approval）
- 执行 → 返回 job_id（running）→ 完成（completed）

对齐文档《黄雀 Coordinator 架构》的 SpecialistResult 6 态状态机。
"""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel

import hq
import llm


class SpecialistResult(BaseModel):
    """子 Agent 的统一返回合同（对齐 SpecialistResult Schema）。"""
    status: Literal["completed", "running", "needs_user_input",
                    "needs_approval", "failed", "cancelled"]
    summary: str = ""
    question: Optional[str] = None           # needs_user_input 时的问题
    missing_inputs: List[str] = []           # 缺的参数
    quote: Optional[Dict[str, Any]] = None   # needs_approval 时的报价
    job_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None  # completed 时的结果
    retryable: bool = False
    error_code: Optional[str] = None


# 图片任务的参数规范：每种 intent 必填什么
_IMAGE_REQUIRED = {
    "文生图": ["prompt"],
    "图生图": ["prompt", "image_upload_id"],
    "多图": ["prompt", "count"],
    "看图": ["image_url"],
}

# 可选参数（有默认值或可不填）
_IMAGE_OPTIONAL = {
    "文生图": ["ratio", "quality", "provider"],
    "图生图": ["ratio", "quality", "provider"],
    "多图": ["ratio", "quality", "provider"],
    "看图": ["question"],
}

# image-generate 实际支持的字段（黄雀 CLI 的 input_schema）
_IMAGE_ALLOWED_FIELDS = {
    "prompt", "ratio", "count", "provider", "quality",
    "image_upload_id", "reference_upload_ids", "variant", "model", "mask_upload_id",
}

# 图片渠道优先级（便宜稳定优先；openai/黄雀引擎2 最近频繁 Remote end closed，放最后）
IMAGE_PROVIDER_ORDER = ["seedream", "xiaole", "banana", "openai"]


def _with_default_provider(params):
    """默认用最便宜稳定的渠道（seedream，火山方舟 12 点）；用户指定了 provider 则用指定的。"""
    p = dict(params or {})
    if not p.get("provider"):
        p["provider"] = IMAGE_PROVIDER_ORDER[0]
    return p


def _clean_image_params(params):
    """清洗图片参数：把不支持的字段（如 style）合并进 prompt，过滤非法字段。"""
    clean = {}
    extras = []
    for k, v in (params or {}).items():
        if k in _IMAGE_ALLOWED_FIELDS:
            clean[k] = v
        elif k in ("style", "风格", "effect", "特效", "mood", "氛围"):
            if v:
                extras.append(str(v))
        # 其他未知字段直接丢弃，避免 unknown input field 报错
    if extras and clean.get("prompt"):
        clean["prompt"] = clean["prompt"] + "，" + "，".join(extras)
    elif extras:
        clean["prompt"] = "，".join(extras)
    return clean


def _check_missing(intent: str, params: Dict[str, Any]) -> List[str]:
    """检查缺哪些必填参数。"""
    required = _IMAGE_REQUIRED.get(intent, ["prompt"])
    return [k for k in required if not (params or {}).get(k)]


def _ensure_upload_id(params):
    """图生图时，如果有 image_url（http URL）但没有 image_upload_id，自动下载转 upload_id。
    （用户引用「刚生成的图 URL」时，LLM 传的是 URL，而黄雀图生图需要 upload_id）"""
    if params.get("image_upload_id"):
        return params
    image_url = params.get("image_url") or params.get("reference_image_url")
    if not image_url or not isinstance(image_url, str) or not image_url.startswith("http"):
        return params
    try:
        import requests as _rq
        import tempfile
        import os
        import io
        data = _rq.get(image_url, timeout=30).content
        suffix = ".jpg"
        # 超过黄雀 10 MiB 上传限制时，压缩（缩放到最长边 1280，转 JPEG）
        if len(data) > 10 * 1024 * 1024:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                img.thumbnail((1280, 1280))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=85)
                data = buf.getvalue()
            except Exception:
                pass
        else:
            lower = image_url.lower()
            suffix = ".jpg" if (lower.endswith(".jpg") or lower.endswith(".jpeg")) else \
                     (".webp" if lower.endswith(".webp") else ".png")
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        r = hq.upload_image(tmp.name)
        os.unlink(tmp.name)
        uid = (r.get("result") or {}).get("upload_id")
        if uid:
            params["image_upload_id"] = uid
            # URL 已转成 upload_id，删掉 URL 字段（image-generate 不接受 image_url）
            params.pop("image_url", None)
            params.pop("reference_image_url", None)
    except Exception:
        pass
    return params


class ImageAgent:
    """图片子 Agent。无状态：每次 run() 都是「给全参数+确认 → 执行 → 返回」。"""

    INTENTS = ["文生图", "图生图", "多图", "看图"]

    def run(self, intent: str, params: Dict[str, Any],
            confirmed: bool = False, quote_token: str = "") -> SpecialistResult:
        """执行一个图片任务，返回 SpecialistResult。"""
        intent = intent or "文生图"
        raw = dict(params or {})
        image_url = raw.get("image_url", "")

        # 1. 看图：直接用 image_url（不经过 image-generate 清洗）
        if intent == "看图":
            return self._describe({"image_url": image_url, "question": raw.get("question", "")})

        # 生成类：清洗（丢弃 image_url 等非法字段）
        params = _clean_image_params(raw)
        # 图生图：URL 自动转 upload_id（用 raw 的 image_url）
        if intent == "图生图":
            p2 = _ensure_upload_id(dict(raw))
            if p2.get("image_upload_id"):
                params["image_upload_id"] = p2["image_upload_id"]

        # 2. 检查参数完整性
        missing = _check_missing(intent, params)
        if missing:
            question = self._missing_question(intent, missing)
            return SpecialistResult(
                status="needs_user_input", summary="缺少参数",
                missing_inputs=missing, question=question)

        # 3. 报价阶段（未确认时先报价）
        if not confirmed:
            return self._quote(intent, params)

        # 4. 确认执行
        return self._execute(intent, params, quote_token)

    # —— 内部 ——
    def _missing_question(self, intent: str, missing: List[str]) -> str:
        label = {"prompt": "画面内容", "image_upload_id": "参考图",
                 "count": "张数", "image_url": "图片", "ratio": "比例"}.get
        msgs = [label(k) or k for k in missing]
        return f"{intent}还缺：{'、'.join(msgs)}，请补充。"

    def _describe(self, params: Dict[str, Any]) -> SpecialistResult:
        image_url = params.get("image_url", "")
        if not image_url:
            return SpecialistResult(status="needs_user_input",
                                    summary="看图需要图片", missing_inputs=["image_url"],
                                    question="请提供要看的图片 URL 或先上传图片。")
        question = params.get("question", "图里有什么？请用一句话描述")
        try:
            text = llm.vision(image_url, question)
            return SpecialistResult(status="completed", summary="看图完成",
                                    result={"description": text})
        except Exception as e:
            return SpecialistResult(status="failed", summary=f"看图失败：{str(e)[:120]}",
                                    retryable=True)

    def _quote(self, intent: str, params: Dict[str, Any]) -> SpecialistResult:
        params = _with_default_provider(params)
        # 多图拆成单张报价
        batch = 1
        if intent == "多图":
            batch = int(params.get("count", 1))
            params = dict(params)
            params["count"] = 1
        res = hq.run("image-generate", params, confirm=False)
        if "result" in res and res.get("result"):
            rr = res["result"]
            if "quote_token" in rr:
                cost = rr.get("cost")
                return SpecialistResult(
                    status="needs_approval", summary="已报价",
                    quote={"quote_token": rr["quote_token"], "cost": cost,
                           "points": rr.get("points"), "batch": batch})
        msg = res.get("message") or str(res)[:200]
        return SpecialistResult(status="failed", summary=f"报价失败：{msg}",
                                retryable=True)

    def _execute(self, intent: str, params: Dict[str, Any],
                 quote_token: str) -> SpecialistResult:
        params = _with_default_provider(params)
        batch = 1
        if intent == "多图":
            batch = int(params.get("count", 1))
            params = dict(params)
            params["count"] = 1
        # 多图：循环单张
        jobs = []
        for _ in range(batch):
            if quote_token:
                r = hq.run("image-generate", params, confirm=True, quote_token=quote_token)
                quote_token = ""  # quote_token 一次性
            else:
                q = hq.run("image-generate", params, confirm=False)
                t = (q.get("result") or {}).get("quote_token", "")
                if not t:
                    jobs.append({"error": q.get("message", "报价失败")})
                    continue
                r = hq.run("image-generate", params, confirm=True, quote_token=t)
            if "result" in r and r.get("result"):
                jobs.append(r["result"])
            else:
                jobs.append({"error": r.get("message") or str(r)[:150]})
        if batch == 1:
            first = jobs[0] if jobs else {}
            jid = first.get("job_id")
            if jid:
                return SpecialistResult(status="running", summary="已提交",
                                        job_id=str(jid))
            return SpecialistResult(status="failed",
                                    summary=first.get("error", "提交失败"), retryable=True)
        return SpecialistResult(status="running", summary=f"已提交 {batch} 张",
                                job_id=",".join(str(j.get("job_id")) for j in jobs if j.get("job_id")))


class VideoAgent:
    """视频子 Agent：对视频业务结果负责（生成视频）。"""

    INTENTS = ["生成视频"]

    def run(self, intent: str, params: Dict[str, Any],
            confirmed: bool = False, quote_token: str = "") -> SpecialistResult:
        params = dict(params or {})
        if not params.get("prompt"):
            return SpecialistResult(
                status="needs_user_input", summary="缺少参数", missing_inputs=["prompt"],
                question="生成视频还需要画面描述（prompt），请告诉我想要什么画面。")
        if not confirmed:
            res = hq.run("video-generate", params, confirm=False)
            if "result" in res and res.get("result"):
                rr = res["result"]
                if "quote_token" in rr:
                    return SpecialistResult(
                        status="needs_approval", summary="已报价",
                        quote={"quote_token": rr["quote_token"], "cost": rr.get("cost"),
                               "points": rr.get("points"), "batch": 1})
            return SpecialistResult(status="failed", summary="报价失败", retryable=True)
        res = hq.run("video-generate", params, confirm=True, quote_token=quote_token)
        if "result" in res and res.get("result"):
            jid = res["result"].get("job_id")
            if jid:
                return SpecialistResult(status="running", summary="已提交", job_id=str(jid))
        msg = res.get("message") or "提交失败"
        return SpecialistResult(status="failed", summary=msg, retryable=True)


class DigitalHumanAgent:
    """数字人子 Agent：数字人口播（需先有 avatar_id 和 voice）。"""

    INTENTS = ["数字人口播"]

    def run(self, intent: str, params: Dict[str, Any],
            confirmed: bool = False, quote_token: str = "") -> SpecialistResult:
        params = dict(params or {})
        missing = [k for k in ("avatar_id", "text", "voice") if not params.get(k)]
        if missing:
            tip = ""
            if "avatar_id" in missing:
                tip += "avatar_id 可用「查看数字人形象」查到；"
            if "voice" in missing:
                tip += "voice 可用「查看音色」查到。"
            return SpecialistResult(
                status="needs_user_input", summary="缺少参数", missing_inputs=missing,
                question=f"数字人口播还缺：{'、'.join(missing)}。{tip}")
        if not confirmed:
            res = hq.run("digital-ip-text-generate", params, confirm=False)
            if "result" in res and res.get("result"):
                rr = res["result"]
                if "quote_token" in rr:
                    return SpecialistResult(
                        status="needs_approval", summary="已报价",
                        quote={"quote_token": rr["quote_token"], "cost": rr.get("cost"),
                               "points": rr.get("points"), "batch": 1})
            return SpecialistResult(status="failed", summary="报价失败", retryable=True)
        res = hq.run("digital-ip-text-generate", params, confirm=True, quote_token=quote_token)
        if "result" in res and res.get("result"):
            jid = res["result"].get("job_id")
            if jid:
                return SpecialistResult(status="running", summary="已提交", job_id=str(jid))
        msg = res.get("message") or "提交失败"
        return SpecialistResult(status="failed", summary=msg, retryable=True)
