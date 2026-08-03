# QualityPilot FastAPI 后端

## WSL 本地开发

先在项目根目录启动 PostgreSQL：

```bash
docker compose up -d
docker compose ps
```

然后进入后端目录：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API 文档地址：<http://localhost:8000/docs>。

## 数据库迁移

Alembic 负责数据库结构变更，应用启动时只检查数据库连接，不再自动建表。

全新数据库直接执行：

```bash
alembic upgrade head
```

只有在数据库已经存在 `knowledge_documents` 和 `knowledge_document_chunks` 两张旧表、且确认其结构与 `0001_existing_schema` 完全一致时，才执行：

```bash
alembic stamp 0001_existing_schema
alembic upgrade head
```

不得为了迁移删除数据库或 Docker 数据卷。

## 当前接口

- `GET /api/v1/health`：进程健康检查。
- `GET /api/v1/ready`：进程及数据库就绪检查。
- `POST /api/v1/knowledge/documents`：新增手工知识文档元数据。
- `GET /api/v1/knowledge/documents`：查询知识文档列表。
- `POST /api/v1/knowledge/documents/upload`：上传、解析、切分并写入知识文档。
- `POST /api/v1/knowledge/documents/{document_id}/embeddings`：幂等回填文档缺失向量。
- `GET /api/v1/knowledge/documents/{document_id}/chunks`：按顺序查询文档分块。
- `POST /api/v1/knowledge/search`：支持 `vector`、`keyword` 和 `hybrid` 三种知识检索模式。

上传接口使用 `multipart/form-data`，支持 TXT、Markdown、PDF 和 DOCX，单个文件最大 20 MB。标题可选，未提供时使用原文件名。

示例：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/documents/upload \
  -F "title=PCB 质量规范" \
  -F "file=@./quality-guide.pdf"
```

搜索请求不传 `mode` 时保持原有向量检索行为。`keyword` 使用 PostgreSQL `pg_trgm` 中文字符片段检索，不调用 Embedding API；`hybrid` 使用 RRF 融合向量与关键词候选：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "回流焊桥接应检查什么？",
    "top_k": 3,
    "mode": "hybrid"
  }'
```

返回结果中的 `score` 是当前模式的排序分数；`vector_score` 和 `keyword_score` 分别保留两路原始分数，`match_type` 表示本次结果来自哪种检索模式。

## 测试

测试使用独立的 PostgreSQL 容器、数据库账号、端口和临时文件目录，不会连接或清理开发数据库。先在项目根目录启动测试数据库：

```bash
docker compose --profile test up -d postgres-test
docker compose --profile test ps
```

然后执行完整测试：

```bash
cd backend
source .venv/bin/activate
python -m pip check
DATABASE_URL=postgresql+asyncpg://qualitypilot_test:qualitypilot_test_password@127.0.0.1:5433/qualitypilot_test alembic check
python -m pytest -q
```

如果当前 WSL 环境的 pytest 输出捕获临时文件异常，可以使用：

```bash
python -m pytest -s -q
```

只运行不依赖数据库的单元测试时，不需要启动 PostgreSQL：

```bash
python -m pytest -q -m "not integration"
```

测试的默认连接信息与 `.env.test.example` 中的示例一致。`tests/conftest.py` 会强制要求测试数据库名称以 `_test` 结尾、覆盖应用数据库连接、清空真实 Embedding API Key，并为上传文件创建临时目录。只要测试集中包含 `integration` 标记，它会先对隔离测试库执行 Alembic 迁移。

测试结束后可以只停止测试容器；不要执行 `docker compose down -v`：

```bash
cd ..
docker compose --profile test stop postgres-test
```

GitHub Actions 会在独立的 pgvector 服务容器中执行依赖检查、语法检查、迁移检查和完整测试，不需要配置真实百炼 API Key。

## 检索评测

`evaluation/pcb_sop_v1` 提供 5 份合成脱敏 PCB/SOP 文档和 8 条标注查询。评测模块计算文档级 Recall@K、MRR，以及“文档来源正确且分块包含标注证据”的引用正确率。样例不是生产数据，不能把结果表述为真实业务指标。

确认后端已经运行，并且已主动将评测目录中的 5 份 Markdown 文档上传到知识库后，可以执行：

```bash
python -m app.evaluation \
  --dataset evaluation/pcb_sop_v1/dataset.json \
  --base-url http://localhost:8000 \
  --top-k 3 \
  --search-mode hybrid \
  --request-max-retries 2 \
  --output evaluation/reports/hybrid-baseline-2026-08-03-k3.json
```

评测命令只调用现有搜索接口，不会自动上传、修改或删除数据库数据。`keyword` 不调用 Embedding Provider；`vector` 和 `hybrid` 会使用后端已配置的查询 Embedding Provider，因此真实百炼配置会产生少量 Token 消耗。

评测客户端只对网络错误、HTTP 429 和 5xx 执行有限指数退避重试。项目已经保存向量、关键词和混合检索的 Top-1/Top-3 报告；它们来自小规模合成数据，只能用于开发对比。
