# 黄雀 Agent（独立服务）

一个**独立、可部署**的黄雀内容生产 Agent 服务。大脑默认用 OpenAI GPT-5.6 Luna 理解自然语言需求并路由到黄雀能力，执行层调 `hq` CLI（报价 → 确认 → 轮询）。DeepSeek / GLM 保留为显式备用；不依赖 OpenClaw，可本机跑、可上服务器、可加网页前端。

## 架构

```
用户（HTTP API / 未来网页）
   ↓
app.py（FastAPI 服务）
   ↓
orchestration.py
   ├── Coordinator Agent（GPT-5.6 Luna，只能 delegate_production）
   ├── Production Agent（GPT-5.6 Luna，独立 Prompt + 生产工具白名单）
   └── Policy Engine（拒绝越权工具，剥离模型伪造的确认与 quote_token）
   ↓
agent.py / subagents.py（参数清洗、报价、确认、结构化结果）
   ↓
hq.py（hq CLI → capabilities/describe/run/quote/task）
   ↓
黄雀后端 huangquechuanmei.com（真正出图/出视频）
```

`/agent` 已使用这条最小纵向切片。用户确认付费时不再调用两个模型，而是使用会话中保存的原始 `quote_token` 直接恢复执行。

## 安装

```bash
cd ~/Desktop/huangque-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# 确保 hq 已登录：hq login --json
```

## 配置（.env，600 权限，勿提交）

```
LLM_PROVIDER=luna            # luna（默认）| deepseek | glm
OPENAI_API_KEY=<从安全凭据环境注入>
OPENAI_REASONING_EFFORT=low  # none | low | medium | high | xhigh | max
OPENAI_PROXY=                # 网络可直连时留空
# DEEPSEEK_API_KEY=<仅在手动切换 deepseek 时配置>
GLM_API_KEY=xxx
HQ_BIN=hq
HOST=127.0.0.1
PORT=8787
```

## 启动

```bash
source venv/bin/activate
python app.py          # 或 uvicorn app:app --host 0.0.0.0 --port 8787
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | `{message}` → 返回调用计划 `{capability, params, explanation}`，不执行 |
| POST | `/quote` | `{capability, params}` → 付费能力返回 `{cost, quote_token}`；免费/读能力直接返回结果 |
| POST | `/execute` | `{capability, params, quote_token}` → 执行，付费需带 token（先 /quote） |
| GET | `/task/{job_id}` | 轮询异步任务状态 |
| GET | `/status` | 黄雀账号 + 剩余点数 |
| GET | `/health` | 健康检查 |

## 使用示例

```bash
# 1. 理解需求
curl -X POST localhost:8787/chat -H 'content-type: application/json' \
  -d '{"message":"生成一张橘猫晒太阳的图片"}'

# 2. 报价（付费能力）
curl -X POST localhost:8787/quote -H 'content-type: application/json' \
  -d '{"capability":"image-generate","params":{"prompt":"橘猫晒太阳"}}'

# 3. 确认执行
curl -X POST localhost:8787/execute -H 'content-type: application/json' \
  -d '{"capability":"image-generate","params":{"prompt":"橘猫晒太阳"},"quote_token":"<上一步的token>"}'

# 4. 查结果
curl localhost:8787/task/<job_id>
```

## 安全合同

- 查询/报价：直接执行
- **扣费/生成/采集/上传/删除：必须 /quote 拿 token 后，用户明确同意再 /execute**
- 永不索取或打印密码/Cookie/API key

## 部署到服务器（隔离运行）

1. 服务器装 Python ≥3.10 + `hq`（`curl -fsSL https://huangquechuanmei.com/downloads/hq/install.sh | sh`）+ `hq login`
2. 上传本目录，填 `.env`
3. `uvicorn app:app --host 0.0.0.0 --port 8787`（建议套 nginx + 鉴权，勿裸奔公网）

## 相关

- 功能图谱与实测：AI-Memory `systems/黄雀-hq-CLI功能图谱.md`
- Agent Skill：`use-huangque-cli`（已装 pi/codex/openclaw）
