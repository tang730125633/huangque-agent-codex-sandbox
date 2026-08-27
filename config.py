"""配置：从环境变量 / .env 读取，不硬编码密钥。支持运行时切换模型 + 自定义模型供应商。"""
import os
from dotenv import load_dotenv
import models as custom_models

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.getenv("GLM_MODEL", "glm-4.7")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_PROXY = os.getenv("OPENAI_PROXY", "")

# —— 可用模型注册表（provider 键 → 配置）——
MODELS = {
    "deepseek": {
        "name": "DeepSeek V4 Pro",
        "provider": "deepseek",  # deepseek 走 Responses API
        "key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
    },
    "glm": {
        "name": "智谱 GLM-4.7",
        "provider": "glm",  # glm 走 Chat Completions
        "key": GLM_API_KEY,
        "base_url": GLM_BASE_URL,
        "model": GLM_MODEL,
    },
    "luna": {
        "name": "OpenAI Luna（视觉）",
        "provider": "openai",  # openai 走 Responses API（支持视觉）
        "key": OPENAI_API_KEY,
        "base_url": OPENAI_BASE_URL,
        "model": "gpt-5.6-luna",
        "proxy": OPENAI_PROXY,  # api.openai.com 需走代理
    },
}

# 当前模型（可运行时切换）
_CURRENT_MODEL = os.getenv("LLM_PROVIDER", "deepseek")
if _CURRENT_MODEL not in MODELS:
    _CURRENT_MODEL = "deepseek"


# —— 黄雀执行层 ——
HQ_BIN = os.getenv("HQ_BIN", "hq")

# —— 黄雀网页端（创建数字人/克隆声音等 hq CLI 未开放的能力）——
HQ_WEB_USERNAME = os.getenv("HQ_WEB_USERNAME", "")
HQ_WEB_TOKEN = os.getenv("HQ_WEB_TOKEN", "")

# —— 服务 ——
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8787"))

# —— 访问鉴权（上公网必配；空则不校验）——
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")


def current_model():
    return _CURRENT_MODEL


def switch_model(provider):
    global _CURRENT_MODEL
    if provider not in MODELS and not custom_models.get_custom(provider):
        raise ValueError(f"未知模型: {provider}")
    _CURRENT_MODEL = provider
    return _CURRENT_MODEL


def llm_config(provider=None):
    p = provider or _CURRENT_MODEL
    if p in MODELS:
        return MODELS[p]
    # 自定义模型供应商
    c = custom_models.get_custom(p)
    if c:
        return {"key": c["api_key"], "base_url": c["base_url"],
                "model": c["model"], "provider": c["format"],
                "name": c["name"]}
    # 回退到默认
    return MODELS["deepseek"]


def models_list():
    out = [{"id": pid, "name": m["name"], "provider": m["provider"],
            "model": m["model"], "current": pid == _CURRENT_MODEL, "custom": False}
           for pid, m in MODELS.items()]
    for c in custom_models.list_custom():
        out.append({"id": c["id"], "name": c["name"], "provider": c["format"],
                    "model": c["model"], "current": c["id"] == _CURRENT_MODEL,
                    "custom": True, "base_url": c["base_url"], "api_key_masked": c["api_key_masked"]})
    return out
