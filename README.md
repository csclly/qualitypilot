# QualityPilot

面向 PCB / AOI 质量分析的知识检索与人工复核工作台。使用 React + TypeScript 前端、FastAPI 后端、PostgreSQL 17 + pgvector 和 LangGraph，支持百炼及自部署微调 Qwen2.5 文本生成。

## 核心流程

上传知识文档 → 检索证据 → 生成分析草稿 → 人工批准或拒绝 → 保存结果与审计记录。

- 知识库：TXT、Markdown、PDF、DOCX 导入，内容校验、文本切分、分页查询与向量补全。
- 检索：关键词、向量和混合检索，展示证据原文及来源。
- 分析：规则草稿或模型生成，结构化响应校验与显式降级。
- 复核：工作流检查点持久化、人工审批、幂等提交、不可变审计。
- 后端运维能力：认证与角色权限、错误事件、告警投递、检查点归档及 Prometheus 指标。

当前为本地开发版本。前端尚无全库运行列表、完整登录流程和运维管理页；MES/QMS 只有只读扩展协议，未连接真实生产系统。审批不会创建生产工单或修改生产参数。

## 目录

| 路径 | 内容 |
| --- | --- |
| `backend/` | API、Agent、模型适配、数据库迁移、测试与评测样例 |
| `frontend/` | 中文工作台、单元测试和浏览器测试 |
| `docs/` | 项目状态、模型接入和设计验证记录 |
| `compose.yaml` | 开发数据库及隔离测试数据库 |
| `.github/workflows/` | 后端持续集成检查 |

## 本地启动

需要 Python 3.12、Node.js 22.12+、Docker Compose。以下命令从仓库根目录运行，适用于 Linux / WSL。

### 1. 数据库与后端

```bash
docker compose up -d postgres
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
# 仅首次配置时复制；不要覆盖已有 .env。
test -f .env || cp .env.example .env
```

按需填写本地 `.env` 中的模型配置。新建空数据库使用以下命令创建版本化结构：

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

已有安装应复用现有 `.venv` 与 `.env`，先检查数据库实际结构和迁移版本，再执行适用的增量迁移。必须保留 `qualitypilot_pgdata` 数据卷，不通过删除数据库或数据卷解决迁移问题。

### 2. 前端

另开终端，从仓库根目录运行：

```bash
cd frontend
bash start.sh
```

打开 [中文工作台](http://localhost:5173)；后端接口说明见 [API 文档](http://localhost:8000/docs)。前端通过开发代理连接后端，正式部署需另行配置反向代理和认证。

### 3. 模型与离线开发

前端默认采用“关键词检索 + 规则草稿”，不请求生成模型。检索已有知识与人工复核可以在云端生成模型停机时使用。上传文档、补全向量、语义或混合检索仍依赖配置的 Embedding 服务；新空库需先导入知识才能获得证据。

自部署生成模型使用独立的 `GENERATION_API_KEY`，Embedding 继续使用百炼配置。接入步骤见 [自部署 Qwen2.5](docs/SELF_HOSTED_QWEN.md)。此仓库不包含模型训练代码、权重或私有训练数据。

## 验证

后端测试使用独立的 `qualitypilot_test` 数据库及 5433 端口，不使用开发数据卷：

```bash
docker compose --profile test up -d postgres-test
cd backend
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

前端测试与构建，从仓库根目录运行：

```bash
cd frontend
npm ci
npm test
npm run build
```

2026-09-03 本地同步前验证：后端 194 项测试（含 28 项数据库集成测试）、前端 9 项单元测试及生产构建通过；隔离测试库 `alembic check` 无结构差异。此前已完成 7 项浏览器用例验证，具体范围见项目状态；浏览器运行方式见前端说明。自动化测试不调用真实生成模型。

## 固定演示与模型评测

按 [固定演示手册](docs/DEMO_RUNBOOK.md) 可以复演上传、检索、草稿、人工拒绝与刷新恢复。

[八题固定证据评测](backend/evaluation/generation_v1/README.md) 已真实执行，5/8 通过结构与引用契约；这不是业务正确率。查看 [完整分析与失败案例](backend/evaluation/generation_v1/reports/2026-09-03-analysis.md)。目前没有基础模型对照，不能据此宣称微调提升。

## 文档与数据边界

- [前端使用与启动说明](frontend/README.md)
- [项目状态与验证记录](docs/PROJECT_STATUS.md)
- [自部署模型接入](docs/SELF_HOSTED_QWEN.md)
- [设计取舍与排查记录](docs/INTERVIEW_KNOWLEDGE.md)

本地 `.env`、密钥、上传原文件、数据库数据、虚拟环境、前端依赖和构建产物不进入 Git。示例数据库凭据仅用于本地开发；此版本的默认配置不用于直接开放公网访问。
