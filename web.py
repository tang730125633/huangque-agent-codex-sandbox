"""直接调黄雀后端 /api/gen/* 端点（网页端 token），补齐 hq CLI 没有的能力：
- 创建数字人形象（上传真人照片 → HeyGen photo avatar）
- 克隆声音（上传样音 → 训练音色）
- 查形象列表 / 克隆状态 / 音色槽位
"""
import requests
import config

HQ_SITE = "https://huangquechuanmei.com"


def _headers():
    return {"Authorization": f"Bearer {config.HQ_WEB_TOKEN}"}


def _post(path, body, timeout=120):
    r = requests.post(HQ_SITE + path, json=body, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get(path, timeout=30):
    r = requests.get(HQ_SITE + path, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def login(username, password):
    """用账号密码换网页端 token（miniprogram-login 返回 token 到响应体）。"""
    r = requests.post(HQ_SITE + "/api/auth/miniprogram-login",
                      json={"username": username, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()  # {"token": ..., "user": {...}}


def create_avatar(image_data_url, name=""):
    """上传真人照片创建数字人形象。image_data_url 形如 data:image/jpeg;base64,..."""
    return _post("/api/gen/avatar", {"image_data": image_data_url, "name": name})


def clone_voice(slot_id, audio_base64, audio_format, name=""):
    """上传样音克隆声音。audio_base64 为纯 base64（不带 data: 前缀）。"""
    body = {"slot_id": slot_id, "audio": audio_base64, "audio_format": audio_format}
    if name:
        body["name"] = name
    return _post("/api/gen/audio/clone-vip", body)


def list_avatars():
    return _get("/api/gen/video/avatars")


def list_voice_slots():
    """查音色克隆槽位（克隆声音需要选一个槽位）。"""
    return _get("/api/gen/audio/slots")


def clone_status(slot_id):
    return _get("/api/gen/audio/clone-status?slot_id=" + slot_id)
