"""独立黄雀 Agent 服务（FastAPI）—— 工具调用版。

/chat  自然语言 → 调用计划（旧接口，兼容）
/agent 自然语言 → 自动工具调用（function calling）；付费工具返回报价待确认
/agent/execute  确认执行工具（付费带 quote_token）
/task/{id}  轮询任务
/status  账号点数
"""
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import os
import tempfile
import base64
import subprocess
import config
import llm
import hq
import agent
import session
import web
import subagents

app = FastAPI(title="Huangque Agent", version="0.4.0")

# 上公网时鉴权：API 请求需带访问密码（页面本身不拦，供前端提示输入密码）
_PUBLIC_PATHS = {"/", "/api-test", "/docs", "/openapi.json", "/redoc", "/health", "/info", "/favicon.ico", "/auth"}


@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path
    if config.ACCESS_TOKEN and path not in _PUBLIC_PATHS and not path.startswith("/static"):
        auth = request.headers.get("Authorization", "")
        qtoken = request.query_params.get("token", "")
        if auth != f"Bearer {config.ACCESS_TOKEN}" and qtoken != config.ACCESS_TOKEN:
            return JSONResponse(status_code=401, content={"detail": "未授权，请提供访问密码"})
    return await call_next(request)


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = None  # 兼容旧前端
    images: Optional[List[str]] = None  # 已上传图片的 upload_id 列表（图生图上下文）
    image_data_urls: Optional[List[str]] = None  # 已上传图片的 base64 data URL（供看图）
    session_id: Optional[str] = None  # 会话 ID（后端持久化历史）


class ExecuteRequest(BaseModel):
    capability: str
    params: Dict[str, Any] = {}
    quote_token: Optional[str] = None


class AgentExecuteRequest(BaseModel):
    tool: str
    params: Dict[str, Any] = {}
    quote_token: Optional[str] = None
    session_id: Optional[str] = None
    batch_count: Optional[int] = 1


@app.get("/")
def ui():
    """网页前端。"""
    idx = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return {"service": "huangque-agent", "hint": "网页前端未安装，见 /info"}


@app.get("/api-test")
def api_test():
    """API 测试台页面。"""
    idx = os.path.join(os.path.dirname(__file__), "static", "api_test.html")
    if os.path.isfile(idx):
        return FileResponse(idx, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return {"service": "huangque-agent", "hint": "测试台未安装"}


@app.get("/info")
def index():
    return {
        "service": "huangque-agent",
        "version": "0.2.0",
        "brain": config.current_model(),
        "model": config.llm_config()["model"],
        "tool_count": len(__import__("tools").TOOLS),
        "endpoints": ["/agent", "/agent/execute", "/chat", "/quote", "/execute", "/task/{id}", "/status"],
    }


@app.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    """接收图片上传，返回 upload_id（供图生图/参考图使用）。"""
    suffix = os.path.splitext(file.filename or "")[1] or ".png"
    if suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "仅支持 png/jpg/jpeg/webp")
    data = await file.read()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(data)
    tmp.close()
    try:
        result = hq.upload_image(tmp.name)
    finally:
        os.unlink(tmp.name)
    if "result" in result and result.get("result"):
        return {"upload_id": result["result"].get("upload_id"),
                "mime": result["result"].get("mime")}
    raise HTTPException(400, result.get("message") or str(result)[:300])


@app.post("/upload/avatar")
async def upload_avatar(file: UploadFile = File(...)):
    """上传真人照片，创建数字人形象（走网页端 token，hq CLI 未开放）。"""
    suffix = os.path.splitext(file.filename or "")[1].lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    if suffix not in mime_map:
        raise HTTPException(400, "仅支持 jpg/png/webp 照片")
    data = await file.read()
    data_url = f"data:{mime_map[suffix]};base64,{base64.b64encode(data).decode()}"
    name = os.path.splitext(file.filename or "")[0][:40]
    try:
        result = web.create_avatar(data_url, name)
    except Exception as e:
        raise HTTPException(400, str(e)[:200])
    return {"ok": True, "avatar": result.get("avatar", result)}


@app.post("/upload/voice")
async def upload_voice(slot_id: str, file: UploadFile = File(...)):
    """上传样音克隆声音。任意格式（含录音的 webm/mp4），后端 ffmpeg 转 16k 单声道 wav 后提交 clone-vip。"""
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".webm"
    data = await file.read()
    tmp_in = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_in.write(data)
    tmp_in.close()
    tmp_out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_out.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_in.name, "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", tmp_out.name],
            capture_output=True, timeout=60, check=True,
        )
        with open(tmp_out.name, "rb") as f:
            wav_data = f.read()
        result = web.clone_voice(slot_id, base64.b64encode(wav_data).decode(), "wav")
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, "音频转换失败，请确认是有效的音频文件")
    except Exception as e:
        raise HTTPException(400, str(e)[:200])
    finally:
        os.unlink(tmp_in.name)
        os.unlink(tmp_out.name)
    return {"ok": True, "voice": result.get("voice", result)}


@app.get("/voice-slots")
def voice_slots():
    """查音色克隆槽位（供克隆声音选槽位）。"""
    try:
        return web.list_voice_slots()
    except Exception as e:
        raise HTTPException(400, str(e)[:200])


@app.get("/models")
def models():
    """列出可用模型。"""
    return {"current": config.current_model(), "models": config.models_list()}


class ModelSwitchRequest(BaseModel):
    provider: str


class CustomModelRequest(BaseModel):
    name: str
    base_url: str
    api_key: str
    model: str
    format: str = "openai"  # openai | anthropic


@app.post("/custom-models")
def add_custom_model(req: CustomModelRequest):
    """添加自定义模型供应商（Base URL + API Key + 模型名 + 格式）。"""
    import models as m
    try:
        entry = m.add_custom(req.name, req.base_url, req.api_key, req.model, req.format)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "model": entry}


@app.delete("/custom-models/{cid}")
def del_custom_model(cid: str):
    """删除自定义模型供应商。"""
    import models as m
    m.delete_custom(cid)
    return {"ok": True}


@app.get("/custom-models")
def list_custom_models():
    """列出自定义模型供应商（key 脱敏）。"""
    import models as m
    return {"models": m.list_custom()}


@app.post("/model/switch")
def model_switch(req: ModelSwitchRequest):
    """切换当前模型。"""
    try:
        config.switch_model(req.provider)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "current": config.current_model(),
            "model": config.llm_config()["model"]}


@app.get("/proxy-image")
def proxy_image(url: str):
    """代理黄雀图片：后端下载后返回，绕过浏览器代理拦截图片域名的问题。"""
    import requests as _rq
    # 只允许黄雀的图片域名，防 SSRF
    if not url.startswith(("https://video.huangquechuanmei.com/", "https://huangque-media-", "https://huangquechuanmei.com/")):
        raise HTTPException(400, "不支持的图片域名")
    try:
        resp = _rq.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"图片代理失败：{str(e)[:120]}")
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/png"))


@app.get("/health")
def health():
    return {"ok": True, "provider": config.current_model(), "model": config.llm_config()["model"]}


class AuthRequest(BaseModel):
    token: str


class SessionNoteRequest(BaseModel):
    session_id: str
    content: str


class SubagentRequest(BaseModel):
    intent: str
    params: Dict[str, Any] = {}
    confirmed: bool = False
    quote_token: Optional[str] = ""


@app.post("/subagent/image")
def subagent_image(req: SubagentRequest):
    """图片子 Agent 独立接口（可单独测试，不经过主 Agent）。"""
    image_agent = subagents.ImageAgent()
    result = image_agent.run(req.intent, req.params, req.confirmed, req.quote_token)
    return result.model_dump()


@app.post("/session/note")
def session_note(req: SessionNoteRequest):
    """记录一条任务结果到会话（如「任务 xxx 完成，图片 URL：yyy」），让 Agent 后续能看到自己生成的结果。"""
    session.append(req.session_id, "assistant", req.content)
    return {"ok": True}


@app.post("/auth")
def auth(req: AuthRequest):
    """验证访问密码，返回 token。"""
    if not config.ACCESS_TOKEN or req.token == config.ACCESS_TOKEN:
        return {"ok": True, "token": req.token}
    return {"ok": False}


@app.post("/agent")
def agent_turn(req: ChatRequest):
    """一轮 Agent 对话：自动判断是普通回复还是工具调用。历史存后端 session，跨刷新/重启不丢。"""
    sid = req.session_id or session.new_id()
    history = req.history if req.history is not None else session.get(sid)
    pending_quote = session.get_pending_quote(sid)
    result = agent.run_turn(req.message, history, req.images, req.image_data_urls, pending_quote)
    # 把本轮对话写入 session（持久化）
    user_content = req.message
    if req.images:
        user_content += "（已附图片）"
    session.append(sid, "user", user_content)
    session.append(sid, "assistant", result.get("assistant_content", ""))
    # 报价时存 pending_quote（供确认执行）；running 时保留（供失败自动重试）；其余清除
    if result.get("type") == "quote" and result.get("pending_quote"):
        session.set_pending_quote(sid, result["pending_quote"])
    elif result.get("type") == "running":
        # 保留 pending_quote，并把渠道更新为「本次已用」（下次失败重试自动换下一个）
        pq = session.get_pending_quote(sid)
        if pq and result.get("used_provider"):
            inner = dict((pq.get("params") or {}).get("params") or {})
            inner["provider"] = result["used_provider"]
            pq.setdefault("params", {})["params"] = inner
            session.set_pending_quote(sid, pq)
    else:
        session.set_pending_quote(sid, None)
    result["session_id"] = sid
    return result


@app.get("/session/{sid}")
def get_session(sid: str):
    """查询会话历史（调试用）。"""
    return {"session_id": sid, "history": session.get(sid)}


@app.post("/agent/execute")
def agent_execute(req: AgentExecuteRequest):
    """确认执行工具（付费工具需 quote_token）。"""
    result = agent.execute_tool(req.tool, req.params, req.quote_token, req.batch_count or 1)
    if req.session_id and "result" in result and result.get("result"):
        jid = result["result"].get("job_id")
        if jid:
            session.append(req.session_id, "assistant", f"已提交任务 {jid}（{req.tool}），等待生成完成。")
    if "result" in result and result.get("result"):
        return {"tool": req.tool, "result": result["result"], "session_id": req.session_id}
    raise HTTPException(400, result.get("message") or str(result)[:300])


@app.get("/task/{job_id}")
def get_task(job_id: int):
    result = hq.task(job_id)
    if "result" in result and result.get("result"):
        return {"job_id": job_id, "task": result["result"]}
    raise HTTPException(400, result.get("message") or str(result)[:300])


@app.get("/status")
def account_status():
    result = hq.status()
    if "result" in result and result.get("result"):
        u = result["result"].get("user", {})
        return {"username": u.get("username"), "points": u.get("points"),
                "membership": u.get("membership_name")}
    raise HTTPException(400, result.get("message") or str(result)[:300])


# —— 旧接口（兼容，保留 /chat /quote /execute）——
@app.post("/chat")
def chat(req: ChatRequest):
    plan = llm.chat(req.message, req.history)
    return {"request": req.message, "plan": plan}


@app.post("/quote")
def quote(req: ExecuteRequest):
    if not req.capability:
        raise HTTPException(400, "capability 不能为空")
    result = hq.run(req.capability, req.params, confirm=False)
    if "result" in result and result.get("result"):
        r = result["result"]
        if "quote_token" in r:
            return {"capability": req.capability, "needs_confirm": True,
                    "cost": r.get("cost"), "points": r.get("points"), "quote_token": r["quote_token"]}
        return {"capability": req.capability, "needs_confirm": False, "result": r}
    raise HTTPException(400, result.get("message") or str(result)[:300])


@app.post("/execute")
def execute(req: ExecuteRequest):
    if not req.capability:
        raise HTTPException(400, "capability 不能为空")
    result = hq.run(req.capability, req.params, confirm=bool(req.quote_token), quote_token=req.quote_token)
    if "result" in result and result.get("result"):
        return {"capability": req.capability, "result": result["result"]}
    raise HTTPException(400, result.get("message") or str(result)[:300])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
