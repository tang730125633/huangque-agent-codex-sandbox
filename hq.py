"""hq CLI 封装：把黄雀命令行能力包装成可调用的函数。"""
import subprocess
import json
import config


def run_hq(*args, input=None, timeout=180):
    r = subprocess.run(
        [config.HQ_BIN] + list(args),
        capture_output=True, text=True, timeout=timeout, input=input,
    )
    return r.stdout + r.stderr


def parse(out):
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out[:300]}


def capabilities():
    """88 个能力的权威契约。"""
    return parse(run_hq("capabilities", "--json"))


def describe(cid):
    """单个能力的参数 schema / 约束 / 成本。"""
    return parse(run_hq("describe", cid, "--json"))


def run(cid, params, confirm=False, quote_token=None):
    """执行一个能力。confirm=False 只报价（付费能力不扣费）；confirm=True 需带 quote_token。"""
    args = ["run", cid, "--input", "@-", "--json"]
    if confirm:
        args += ["--confirm", "--quote-token", quote_token]
    return parse(run_hq(*args, input=json.dumps(params, ensure_ascii=False)))


def task(job_id):
    """轮询异步任务状态。"""
    return parse(run_hq("run", "task", "--input", "@-", "--json",
                        input=json.dumps({"job_id": job_id})))


def status():
    """账号状态 + 剩余点数。"""
    return parse(run_hq("status", "--json"))


def upload_image(file_path):
    """上传图片，返回 upload_id（供图生图/参考图使用）。"""
    return parse(run_hq("run", "image-upload", "--file", file_path, "--confirm", "--json"))


def upload_video(file_path):
    """上传视频，返回 upload_id。"""
    return parse(run_hq("run", "video-upload", "--file", file_path, "--confirm", "--json"))
