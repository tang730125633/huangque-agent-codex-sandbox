"""会话持久化：把每个 session 的对话历史存到磁盘（data/sessions/），跨刷新/重启不丢。"""
import json
import os
import time
import uuid

SESSION_DIR = os.path.join(os.path.dirname(__file__), "data", "sessions")
MAX_HISTORY = 30


def _path(sid):
    return os.path.join(SESSION_DIR, f"{sid}.json")


def new_id():
    return uuid.uuid4().hex[:16]


def get(sid):
    """加载会话历史（list of {role, content}）。"""
    p = _path(sid)
    if os.path.isfile(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            return d.get("history", [])
        except Exception:
            return []
    return []


def save(sid, history):
    """保存会话历史（截断到最近 MAX_HISTORY 条），保留 pending_quote。"""
    os.makedirs(SESSION_DIR, exist_ok=True)
    history = history[-MAX_HISTORY:]
    d = {"id": sid, "history": history, "updated_at": time.time()}
    p = _path(sid)
    if os.path.isfile(p):
        try:
            old = json.load(open(p, encoding="utf-8"))
            if old.get("pending_quote"):
                d["pending_quote"] = old["pending_quote"]
        except Exception:
            pass
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def append(sid, role, content):
    """追加一条消息并落盘。"""
    h = get(sid)
    h.append({"role": role, "content": content})
    save(sid, h)
    return h


def get_pending_quote(sid):
    """读当前会话的待确认报价（quote_token + 委派参数）。"""
    p = _path(sid)
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8")).get("pending_quote")
        except Exception:
            return None
    return None


def set_pending_quote(sid, quote):
    """写当前会话的待确认报价。quote 为 None 时清除。"""
    p = _path(sid)
    d = {}
    if os.path.isfile(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            d = {}
    if quote:
        d["pending_quote"] = quote
    else:
        d.pop("pending_quote", None)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
