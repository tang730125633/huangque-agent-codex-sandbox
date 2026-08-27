"""自定义模型供应商管理：让用户添加自己的 LLM API（Base URL + API Key + 模型名 + 格式）。

存储到 models.json（600 权限，含 API Key）。格式支持 openai（chat completions）和 anthropic（messages）。
"""
import json
import os
import time

CUSTOM_MODELS_PATH = os.path.join(os.path.dirname(__file__), "models.json")
SUPPORTED_FORMATS = ("openai", "anthropic")


def load_custom():
    """加载自定义模型供应商列表。"""
    if os.path.isfile(CUSTOM_MODELS_PATH):
        try:
            return json.load(open(CUSTOM_MODELS_PATH, encoding="utf-8"))
        except Exception:
            return []
    return []


def save_custom(models):
    os.makedirs(os.path.dirname(CUSTOM_MODELS_PATH), exist_ok=True)
    with open(CUSTOM_MODELS_PATH, "w", encoding="utf-8") as f:
        json.dump(models, f, ensure_ascii=False, indent=2)
    os.chmod(CUSTOM_MODELS_PATH, 0o600)


def add_custom(name, base_url, api_key, model, format="openai"):
    """添加一个自定义供应商，返回新模型条目。"""
    if format not in SUPPORTED_FORMATS:
        raise ValueError(f"format 仅支持 {SUPPORTED_FORMATS}")
    if not name or not base_url or not api_key or not model:
        raise ValueError("name/base_url/api_key/model 不能为空")
    models = load_custom()
    entry = {
        "id": "custom_%d" % int(time.time() * 1000),
        "name": name.strip(),
        "base_url": base_url.strip().rstrip("/"),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "format": format,
        "custom": True,
    }
    models.append(entry)
    save_custom(models)
    return entry


def delete_custom(cid):
    models = load_custom()
    models = [m for m in models if m.get("id") != cid]
    save_custom(models)
    return models


def get_custom(cid):
    for m in load_custom():
        if m.get("id") == cid:
            return m
    return None


def list_custom():
    """列出自定义模型（脱敏 api_key）。"""
    out = []
    for m in load_custom():
        key = m.get("api_key", "")
        masked = key[:6] + "..." + key[-4:] if len(key) > 12 else "***"
        out.append({"id": m["id"], "name": m["name"], "base_url": m["base_url"],
                    "model": m["model"], "format": m["format"], "api_key_masked": masked})
    return out
