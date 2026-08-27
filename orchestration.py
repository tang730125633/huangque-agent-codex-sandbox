"""最小多意志纵向切片：Coordinator → Production Agent → Policy → hq。"""
from copy import deepcopy

import agent as legacy_agent
import llm
import tools


COORDINATOR_PROMPT = """你是黄雀 Coordinator Agent。
你只负责理解用户目标、决定是否需要生产 Agent，并统一回答用户。
涉及生成/修改图片、视频、音频、数字人口播、文案成片，或查询账号点数/余额、生产任务、Job进度、资产、形象、音色时，必须调用 delegate_production。
例如“我有多少点数”“这个视频做到哪了”“查看我的音色”都属于 Production，不得回答“无法查询”。
普通解释、闲聊和目标不清楚时直接回答或询问，不得调用任何底层黄雀能力。
路由选择不是执行授权；你不能确认付费、提供 quote_token、创建 Job 或发布内容。
"""

PRODUCTION_PROMPT = """你是黄雀 Production Agent。
你只处理媒体生产、生产资源查询、报价准备和 Job 查询。
根据用户原话选择一个最合适的生产工具，并提供完整参数；缺参数时直接用文字提问。
用户明确要求数字人口播时，直接调用 delegate_digital_human：text 使用用户文案，voice 使用用户给出的“音色N”或名称；avatar_id 和未指定的 voice 由运行时代码自动补默认。除非用户明确要求查看或选择列表，否则不要先调用 list_avatars/list_voices。
用户要求生成配音时直接调用 generate_audio；text 是唯一必填参数，用户未指定 voice 时省略 voice，让黄雀使用默认音色，不要追问内部参数。
用户询问账号点数、余额或会员状态时调用 get_account；询问已有 Job 进度时调用 get_task。不要在工具可用时回答“无法查询”。
不得研究市场、制定内容战略或执行发布。
不得生成 confirmed、quote_token 或假装用户已经批准；付费审批由外部 Policy Engine 处理。
不得编造 Job、Artifact、余额或工具结果。
"""

COORDINATOR_TOOLS = [{
    "name": "delegate_production",
    "description": (
        "把媒体生成、媒体修改、配音、数字人口播、文案成片、生产资源查询或"
        "Job 查询交给 Production Agent。不要用于研究、策略、发布或普通知识问答。"
    ),
    "capability": "__agent_production__",
    "paid": False,
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "用户希望得到的生产结果"},
            "context": {"type": "object", "description": "已明确的对象、参数和约束"},
        },
        "required": ["goal"],
    },
}]

PRODUCTION_TOOL_NAMES = {
    "generate_audio", "generate_text_video", "lip_sync",
    "list_assets", "list_avatars", "list_voices", "list_voice_slots",
    "voice_clone_status", "delegate_image", "delegate_video",
    "delegate_digital_human", "get_task", "get_account",
}


def production_tools():
    return [t for t in tools.TOOLS if t["name"] in PRODUCTION_TOOL_NAMES]


def _strip_model_authority(value):
    """模型可以提参数，但不能携带审批凭证或无效的空可选字段。"""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in {"confirmed", "quote_token"}:
                continue
            item = _strip_model_authority(item)
            if item in (None, "", [], {}):
                continue
            cleaned[key] = item
        return cleaned
    if isinstance(value, list):
        return [_strip_model_authority(v) for v in value]
    return value


class PolicyEngine:
    @staticmethod
    def coordinator(decision):
        if decision.get("type") != "tool":
            return True, "", decision
        if decision.get("tool") != "delegate_production":
            return False, "Coordinator 试图调用未授权工具", decision
        return True, "", _strip_model_authority(deepcopy(decision))

    @staticmethod
    def production(decision):
        if decision.get("type") != "tool":
            return True, "", decision
        if decision.get("tool") not in PRODUCTION_TOOL_NAMES:
            return False, "Production Agent 试图调用越权工具", decision
        return True, "", _strip_model_authority(deepcopy(decision))


class CoordinatorAgent:
    def plan(self, user_message, history=None):
        return llm.chat(
            user_message,
            history,
            system_prompt=COORDINATOR_PROMPT,
            tool_defs=COORDINATOR_TOOLS,
            max_tokens=500,
        )


class ProductionAgent:
    def plan(self, user_message, history=None, goal="", has_images=False):
        message = user_message
        if goal and goal != user_message:
            message = f"Coordinator goal: {goal}\nUser request: {user_message}"
        if has_images:
            message += "\nContext: the user attached an image; the runtime supplies its data."
        return llm.chat(
            message,
            history,
            system_prompt=PRODUCTION_PROMPT,
            tool_defs=production_tools(),
            max_tokens=800,
        )


def _plain_result(decision):
    if decision.get("type") == "error":
        message = decision.get("message", "Agent 调用失败")
        return {"type": "error", "message": message, "assistant_content": message}
    text = decision.get("text", "")
    return {"type": "text", "text": text, "assistant_content": text}


def run_turn(user_message, history=None, images=None, image_data_urls=None,
             pending_quote=None, coordinator=None, production=None):
    """运行两层 Agent；任何真实报价/执行仍交给现有 agent.py → hq。"""
    if pending_quote and legacy_agent._is_approval(user_message):
        return legacy_agent.run_turn(
            user_message, history, images, image_data_urls, pending_quote)

    coordinator = coordinator or CoordinatorAgent()
    production = production or ProductionAgent()
    route = coordinator.plan(user_message, history)
    allowed, reason, route = PolicyEngine.coordinator(route)
    if not allowed:
        return {"type": "error", "message": reason, "assistant_content": reason}
    if route.get("type") != "tool":
        return _plain_result(route)

    params = route.get("params") or {}
    decision = production.plan(
        user_message,
        history,
        goal=params.get("goal", ""),
        has_images=bool(images or image_data_urls),
    )
    allowed, reason, decision = PolicyEngine.production(decision)
    if not allowed:
        return {"type": "error", "message": reason, "assistant_content": reason}
    if decision.get("type") != "tool":
        return _plain_result(decision)

    return legacy_agent.run_turn(
        user_message,
        history,
        images,
        image_data_urls,
        decision=decision,
    )
