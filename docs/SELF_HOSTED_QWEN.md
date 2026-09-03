# 自部署 Qwen2.5 接入

当前目标是将 Agent 文本生成切换到微调模型 `pcb-qwen-lora`。Embedding 继续使用现有百炼模型、1024 维向量和密钥；无需数据库迁移或重新生成向量。

## 0. 在云端启动模型 HTTP 服务

已验证的部署目录为 `/root/autodl-tmp/pcb-qwen-server`，服务文件为 `model_server.py`，FastAPI 实例名为 `app`。云端读取 `PCB_LLM_API_KEY`，它必须与本地 `GENERATION_API_KEY` 的值一致。

在同一个云端终端中配置变量并启动（若旧服务仍在运行，先正常停止旧进程）：

```bash
conda activate qwen-sft
cd /root/autodl-tmp/pcb-qwen-server
read -r -s -p "请输入与本地 GENERATION_API_KEY 相同的密钥：" PCB_LLM_API_KEY
export PCB_LLM_API_KEY
python -m uvicorn model_server:app --host 0.0.0.0 --port 8001
```

输入只包含密钥值，不加 Bearer 前缀或引号。环境变量只影响该终端随后启动的进程，不会修改已运行服务。不要把真实密钥写入仓库或粘贴到聊天中。

看到 `Application startup complete` 和端口 8001 的 Uvicorn 运行信息后保持终端打开。仅看到“模型加载完成”后返回命令提示符，不代表 HTTP 服务持续运行。可在另一个云端终端检查：

```bash
curl --max-time 10 http://127.0.0.1:8001/health
```

健康检查成功只能证明服务报告就绪，生成接口的认证与结构化响应仍需后续验证。

## 1. 建立 WSL 到 AutoDL 的 SSH 隧道

在运行 QualityPilot 后端的同一 WSL 发行版中执行，并保持终端打开：

```bash
ssh -N -L 127.0.0.1:18001:127.0.0.1:8001 -p <SSH端口> -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 <用户名>@<SSH主机>
```

将命令中的占位符替换为云平台提供的 SSH 连接信息。首次连接核对服务器主机指纹，按提示在终端输入密码。成功后没有输出是正常现象。18001 是 WSL 本地端口，8001 是云服务器模型端口。云端模型服务必须持续运行。

## 2. 本地配置

在 `backend/.env` 修改对应项，不要覆盖其他配置，不要提交真实密钥：

```dotenv
GENERATION_PROVIDER=openai_compatible
GENERATION_API_KEY=<云端 FastAPI 的 Bearer Token>
GENERATION_BASE_URL=http://127.0.0.1:18001/v1
GENERATION_MODEL=pcb-qwen-lora
GENERATION_TIMEOUT_SECONDS=120
GENERATION_MAX_RETRIES=0
GENERATION_MAX_COMPLETION_TOKENS=1200
```

Base URL 只填写到 `/v1`，调用器会追加 `/chat/completions`。本次本地配置已写入以上模式、地址、模型和用户提供的 Token；示例文件不包含 Token。改完后重启后端进程，让缓存的配置重新读取。

自部署模式发送 `model`、`messages`、`temperature=0`、`max_tokens`，输出上限来自现有的 `GENERATION_MAX_COMPLETION_TOKENS`。它不会发送百炼专用 `enable_thinking` 或依赖服务未确认支持的 `response_format`。默认先关闭重试，便于联调定位；需要时再调整。

`DASHSCOPE_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION` 继续用于向量检索。自部署模式缺少独立生成密钥时会报告配置错误，不会向云服务器发送百炼密钥。

## 3. 验证结构化模型输出

在 WSL 中执行：

```bash
cd /home/qualitypilot/projects/qualitypilot/backend
.venv/bin/python -m scripts.generation_smoke
```

此命令真实调用当前配置模型，使用明确标记的合成 AOI 证据，不读写数据库。成功返回 `ok=true` 和 `generation_mode=model`；网络、HTTP 或 JSON 契约错误返回非零退出码，不使用兜底草稿掩盖失败。

云端必须将 messages 中的 system/user 内容传给模型，并让模型只输出下面五个字段的 JSON：

```json
{
  "summary": "目前证据不足以确认具体根因。",
  "suggested_actions": ["结合原始图像、显微复核与电测判断是否存在真实铜桥"],
  "risk_notes": ["缺少 MES 和 Gerber，尚未确认具体根因"],
  "citations": [1],
  "business_references": []
}
```

`citations` 与 `business_references` 是请求中从 1 开始的临时证据编号，不能编造。客户端映射为真实分块/业务记录 ID。仅有普通文字的 HTTP 200 不能证明已通过项目接入验证；生产链路会在结构或引用校验失败时返回 `deterministic_fallback`，并继续要求人工审批。

## 4. 验证项目完整链路

模型冒烟成功、PostgreSQL 和后端启动后，通过现有 `POST /api/v1/agent/runs` 创建带知识证据的运行，确认草稿的 `generation_mode=model`、`status=pending_approval`。首次可用 `search_mode=keyword` 单独验证生成链路，随后再验证需要百炼 Embedding 的 `hybrid` 模式。没有检索或业务证据时，现有流程会直接使用规则草稿，不调用模型。

## 5. 本次验证结果与限制（2026-09-03）

- 自动化：144 项非数据库测试通过，包含 16 项新增自部署模式测试；28 项数据库集成测试未执行。
- 真实云端：WSL 隧道连接成功；云端最初返回混用中文引号的非法 JSON。新增明确格式示例和证据约束后，实际 Provider 冒烟返回 `ok=true`、`generation_mode=model`，证据编号正确映射为合成分块 UUID。
- 质量限制：实测输出仍出现“调用 QMS 历史案例”等与当前无可调用工具条件不一致的建议。JSON 及引用范围校验不能证明引用忠实度或事实正确性；仍需要真实业务评测和人工审核。
- 运行环境：当时 WSL 的 8000/5433 端口没有监听，Docker CLI 的 WSL 集成不可用，未完成运行中后端加真实 PostgreSQL 的完整端到端验证。没有修改数据库或数据卷。

### 同日补充：真实三条检索证据触发尾部文字

- 用户运行 `169e8f6a-c335-461a-b45d-e77fe30036a5` 检索成功，但草稿为 deterministic_fallback。按该运行的原始问题、三条证据和空业务记录复现，云端 HTTP 200 的 content 为“完整 JSON + 对象后的说明文字”，Pydantic 报 json_invalid / trailing characters，确认不是密钥或网络问题。
- 已通过源码确认：自部署模式遇到 JSON 语法错误时，保留原始证据，追加上一轮响应和格式纠正要求，再调用同一模型一次；响应超过 16000 字符不回传纠正。纠正后仍执行完整字段和引用校验，字段错误、无效引用或无依据动作不会触发格式纠正。百炼模式行为不变。
- 格式纠正与网络重试独立：每次 generate 最多两次 HTTP 请求；外层若配置 N 次网络重试，总请求上界为 2 × (N + 1)。本地 GENERATION_MAX_RETRIES=0，仍允许一次格式纠正；最坏耗时可能达到两次模型超时之和。
- 自动化：本轮新增 12 项测试，非数据库回归共 156 项通过，28 项数据库集成测试仍未执行。覆盖 JSON 尾部说明/代码块/纯文字、纠正失败上限、纠正后无效字段/引用、超长响应边界与 API 待审批；git diff --check 通过。
- 已在运行服务中实际验证：临时 Uvicorn 服务使用现有 PostgreSQL、真实关键词检索与云端模型，返回 HTTP 202、三条证据、generation_mode=model、pending_approval、final_response=null。新运行 ID 为 `fecd255d-e8a2-4888-8da2-4e6cf9d1aa45`；通过用户现有 8000 端口服务 GET 读回一致草稿，确认检查点已持久化。
- 临时验证服务已退出，新增待审批运行保留，原失败运行未重写；未审批、未操作生产系统、未修改数据库结构或删除数据。此前“完整运行态 Agent + PostgreSQL 验证未完成”的缺口已在本次关键词路径补齐，hybrid 路径尚未复测。
- 用户原后端以非 reload 模式启动，需要重启以加载修复，再创建新运行。格式纠正只解决输出格式，不能保证模型建议或引用忠实度，仍需业务质量评测。

## 6. 切回百炼

将 `GENERATION_PROVIDER` 改回 `dashscope`，清空 `GENERATION_API_KEY` 以复用现有 `DASHSCOPE_API_KEY`，同时恢复原百炼生成地址和模型（默认地址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，原模型为 `qwen3.7-max-2026-05-20`），然后重启后端。不要只改模式而保留自部署服务的地址和 Token。

## 7. 2026-09-03 最新联调结论

环境认证对齐并加强自部署提示末尾的必填字段与合法编号说明后，真实前端已跑通关键词及混合检索到 model 草稿、引用展开和刷新恢复；模型测试草稿的拒绝与审计持久化也通过。189 项后端测试通过，具体运行编号和限制见项目状态最新记录。输出仍需业务复核，两个示例不构成完整生成质量评测。
