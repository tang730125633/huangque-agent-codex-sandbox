"""工具注册表：把黄雀能力封装成 OpenAI function-calling 工具。

每个工具 = { name, description, parameters(schema), capability(hq能力id), paid(是否付费) }
付费工具走两段式（报价→确认），免费/读工具直接执行。
"""
from typing import Any, Dict, Callable
import hq

TOOLS: list[Dict[str, Any]] = [
    # —— 生成（付费）——
    {
        "name": "generate_audio",
        "description": "生成配音/音频（TTS）。把文字转成语音。",
        "capability": "audio-generate", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要配音的文字"},
                "voice": {"type": "string", "description": "音色ID(voice_key)，如 S_d21F8OR62。用户说「用音色N」时，从之前查到的音色清单里取对应的 voice_key"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "generate_text_video",
        "description": "文案一键成片。把一段文案做成带模板的视频。",
        "capability": "text-video-generate", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "文案内容"},
                "template": {"type": "string", "description": "模板key(来自 text-video-templates)"},
                "style": {"type": "string", "description": "样式key(如 realistic_commercial)"},
                "voice": {"type": "string", "description": "音色id(如 public:zh-CN-YunjianNeural)"},
            },
            "required": ["text", "template", "style", "voice"],
        },
    },
    {
        "name": "lip_sync",
        "description": "原视频口型同步。给已有视频配上口播音频。",
        "capability": "video-lipsync", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "video_asset_id": {"type": "integer", "description": "原视频资产ID"},
                "audio_asset_id": {"type": "integer", "description": "口播音频资产ID"},
            },
            "required": ["video_asset_id", "audio_asset_id"],
        },
    },
    # —— 采集/获客（付费）——
    {
        "name": "search_content",
        "description": "搜索平台内容（抖音/小红书关键词搜索）。",
        "capability": "collect-search", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["douyin", "xhs"], "description": "平台"},
                "keyword": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["platform", "keyword"],
        },
    },
    {
        "name": "collect_content",
        "description": "采集抖音/小红书公开内容与评论（需真实链接）。",
        "capability": "collect-content", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "抖音/小红书公开内容链接"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "collect_video",
        "description": "采集原视频（抖音/小红书，需真实链接）。",
        "capability": "collect-video", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "抖音/小红书公开链接"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "extract_transcript",
        "description": "提取口播文案（从抖音/小红书视频转写文字）。",
        "capability": "collect-transcript", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "抖音/小红书公开链接"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "generate_leads",
        "description": "生成获客名单（从抖音/小红书/视频号按关键词找潜在客户线索）。",
        "capability": "leads-generate", "paid": True,
        "parameters": {
            "type": "object",
            "properties": {
                "platforms": {"type": "array", "items": {"type": "string", "enum": ["douyin", "xhs", "channels"]}, "description": "平台列表"},
                "keyword": {"type": "string", "description": "关键词"},
            },
            "required": ["platforms", "keyword"],
        },
    },
    # —— 读（免费）——
    {
        "name": "list_assets",
        "description": "查看资产列表（图片/视频/音频）。",
        "capability": "assets", "paid": False,
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["image", "video", "audio"], "description": "资产类型"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "list_avatars",
        "description": "查看可用数字人形象。",
        "capability": "video-avatars", "paid": False,
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_voices",
        "description": "查看可用音色。",
        "capability": "voices", "paid": False,
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_voice_slots",
        "description": "查看本人的声音克隆槽位（含试听预览）。",
        "capability": "audio-slots", "paid": False,
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "voice_clone_status",
        "description": "查看某个声音克隆槽位的处理状态。",
        "capability": "voice-clone-status", "paid": False,
        "parameters": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "string", "description": "克隆槽位ID(来自查看克隆槽位)"},
            },
            "required": ["slot_id"],
        },
    },
    {
        "name": "delegate_image",
        "description": "委派图片任务给图片子 Agent。当用户要生成图片、修改图片、分析图片内容时使用。",
        "capability": "__delegate_image__", "paid": False,
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["文生图", "图生图", "多图", "看图"], "description": "图片任务类型"},
                "params": {"type": "object", "description": "从用户话里提取的参数。用户给了图片链接时，把链接放进 image_url；生成时放 prompt、ratio、count 等"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认付费"},
                "quote_token": {"type": "string", "description": "报价的 quote_token(确认时必填)"},
            },
            "required": ["intent"],
        },
    },
    {
        "name": "delegate_video",
        "description": "委派视频任务给视频子 Agent。当用户要生成视频时使用。",
        "capability": "__delegate_video__", "paid": False,
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["生成视频"], "description": "视频任务类型"},
                "params": {"type": "object", "description": "从用户话里提取的参数(如 prompt, channel, duration, ratio)"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认付费"},
                "quote_token": {"type": "string", "description": "报价的 quote_token(确认时必填)"},
            },
            "required": ["intent"],
        },
    },
    {
        "name": "delegate_digital_human",
        "description": "委派数字人任务给数字人子 Agent。当用户要数字人口播时使用。",
        "capability": "__delegate_digital_human__", "paid": False,
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["数字人口播"], "description": "数字人任务类型"},
                "params": {"type": "object", "description": "从用户话里提取的参数(如 avatar_id, text, voice)"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认付费"},
                "quote_token": {"type": "string", "description": "报价的 quote_token(确认时必填)"},
            },
            "required": ["intent"],
        },
    },
    {
        "name": "get_account",
        "description": "查看黄雀账号与剩余点数。",
        "capability": "account", "paid": False,
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_task",
        "description": "查看某个异步任务的进度和结果。",
        "capability": "task", "paid": False,
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "任务ID"},
            },
            "required": ["job_id"],
        },
    },
]


def get_tool(name: str):
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None


def call_tool(tool: dict, params: dict, confirm: bool = False, quote_token: str = None):
    """执行工具对应的黄雀能力。付费工具 confirm=False 时只报价。视觉/委派工具特殊处理。"""
    if tool.get("capability") == "__vision__":
        import llm
        question = (params or {}).get("question", "图里有什么？请用一句话描述")
        try:
            text = llm.vision((params or {}).get("image_url", ""), question)
            return {"result": {"description": text}}
        except Exception as e:
            return {"error": f"看图失败：{str(e)[:150]}"}
    if tool.get("capability") == "__delegate_image__":
        import subagents
        image_agent = subagents.ImageAgent()
        p = params or {}
        result = image_agent.run(
            p.get("intent", "文生图"), p.get("params", {}),
            confirm, quote_token or "")
        return {"result": result.model_dump(), "_specialist": True}
    if tool.get("capability") == "__delegate_video__":
        import subagents
        video_agent = subagents.VideoAgent()
        p = params or {}
        result = video_agent.run(
            p.get("intent", "生成视频"), p.get("params", {}),
            confirm, quote_token or "")
        return {"result": result.model_dump(), "_specialist": True}
    if tool.get("capability") == "__delegate_digital_human__":
        import subagents
        dh_agent = subagents.DigitalHumanAgent()
        p = params or {}
        result = dh_agent.run(
            p.get("intent", "数字人口播"), p.get("params", {}),
            confirm, quote_token or "")
        return {"result": result.model_dump(), "_specialist": True}
    if tool.get("capability") == "video-avatars":
        res = hq.run(tool["capability"], params, confirm=confirm, quote_token=quote_token)
        # 数字人形象 image_url 是相对路径（/api/gen/file/...），拼成完整 URL 供前端显示
        items = (res.get("result") or {}).get("items") or []
        for it in items:
            iu = it.get("image_url")
            if isinstance(iu, str) and iu.startswith("/"):
                it["image_url"] = "https://huangquechuanmei.com" + iu
        return res
    return hq.run(tool["capability"], params, confirm=confirm, quote_token=quote_token)
