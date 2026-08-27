"""hq CLI token 自动续期。

原理：hq CLI token 每 8 小时过期，且官方 `hq login` 需要浏览器手动授权。
本脚本用网页端账号密码（miniprogram-login）自动换 token → 以该 token 模拟 cookie
登录态 → 自动完成 device 授权（start → approve → poll），全程无需浏览器。

用法：
  python hq_renew.py           # 检查并续期（剩余 <1h 才续）
  python hq_renew.py --force   # 强制续期
"""
import argparse
import json
import os
import sys
import time
import requests

HQ_SITE = "https://huangquechuanmei.com"
CRED_PATH = os.path.expanduser("~/.config/hq-cli/credentials.json")
RENEW_THRESHOLD = 3600  # 剩余 < 1 小时就续期

SCOPES = [
    "profile:read", "ip12:read", "ip12:write", "ip12:chat", "prompt:optimize", "canvas:read",
    "canvas:write", "canvas:agent", "canvas:edit", "tasks:read", "assets:read", "assets:write", "assets:upload",
    "generation:quote", "generation:submit",
    "video-compose:read", "video-compose:write", "digital-presenter:read", "digital-presenter:write",
    "inspiration:read", "inspiration:write", "leads:read", "leads:write", "short-drama:read",
]


def _env():
    # 从同目录 .env 读账号密码
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    d = {}
    if os.path.isfile(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def token_remaining():
    try:
        d = json.load(open(CRED_PATH))
        return int(d.get("expires_at", 0)) - int(time.time())
    except Exception:
        return -1


def save_credentials(token, expires_in, scopes):
    os.makedirs(os.path.dirname(CRED_PATH), exist_ok=True)
    payload = {
        "access_token": token,
        "expires_at": int(time.time()) + int(expires_in),
        "scopes": list(scopes),
    }
    with open(CRED_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.chmod(CRED_PATH, 0o600)
    return payload["expires_at"]


def renew():
    env = _env()
    username = env.get("HQ_WEB_USERNAME", "")
    password = env.get("HQ_WEB_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(".env 缺少 HQ_WEB_USERNAME / HQ_WEB_PASSWORD")

    # 1. 网页端登录换 token（全自动，无需浏览器）
    r = requests.post(HQ_SITE + "/api/auth/miniprogram-login",
                      json={"username": username, "password": password}, timeout=30)
    r.raise_for_status()
    web_token = r.json().get("token")
    if not web_token:
        raise RuntimeError("miniprogram-login 未返回 token: %s" % r.text[:200])

    # 2. 发起设备授权
    r2 = requests.post(HQ_SITE + "/api/auth/cli/device/start",
                       json={"client_name": "hq-auto-renew", "requested_scopes": SCOPES}, timeout=30)
    r2.raise_for_status()
    start = r2.json()
    device_code, user_code = start.get("device_code"), start.get("user_code")
    if not device_code or not user_code:
        raise RuntimeError("device/start 缺少 device_code/user_code: %s" % json.dumps(start)[:300])

    # 3. 用网页端 token 模拟 cookie，自动批准授权（需 Origin 校验）
    cookie = {"Cookie": "hq_session=%s" % web_token, "Origin": HQ_SITE}
    r3 = requests.post(HQ_SITE + "/api/auth/cli/device/approve",
                       json={"user_code": user_code, "approve": True},
                       headers=cookie, timeout=30)
    r3.raise_for_status()

    # 4. 轮询拿 CLI token
    for _ in range(20):
        r4 = requests.post(HQ_SITE + "/api/auth/cli/device/poll",
                           json={"device_code": device_code}, timeout=15)
        r4.raise_for_status()
        poll = r4.json()
        if poll.get("access_token"):
            expires_at = save_credentials(poll["access_token"],
                                          poll.get("expires_in", 8 * 3600),
                                          poll.get("scopes", SCOPES))
            return {"ok": True, "username": username, "expires_at": expires_at}
        if poll.get("code") in ("authorization_pending", "slow_down"):
            time.sleep(2)
            continue
        raise RuntimeError("device/poll 异常: %s" % json.dumps(poll)[:300])
    raise RuntimeError("device/poll 超时未拿到 token")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="强制续期（忽略剩余时间）")
    args = parser.parse_args()

    remaining = token_remaining()
    print("hq CLI token 剩余 %d 秒" % remaining)
    if not args.force and remaining > RENEW_THRESHOLD:
        print("无需续期（剩余 > 1 小时）")
        return
    result = renew()
    print("✅ 续期成功: %s" % json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
