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
- `GET /api/v1/knowledge/documents/{document_id}/chunks`：按顺序查询文档分块。

上传接口使用 `multipart/form-data`，支持 TXT、Markdown、PDF 和 DOCX，单个文件最大 20 MB。标题可选，未提供时使用原文件名。

示例：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/documents/upload \
  -F "title=PCB 质量规范" \
  -F "file=@./quality-guide.pdf"
```

## 测试

确保 PostgreSQL 容器 healthy，并且数据库已经升级到最新迁移，然后执行：

```bash
cd backend
source .venv/bin/activate
python -m pip check
alembic check
pytest -q
```

如果当前 WSL 环境的 pytest 输出捕获临时文件异常，可以使用：

```bash
pytest -s -q
```

集成测试只创建带有唯一 UUID 的测试记录，并在测试结束后精确清理测试文档、级联分块和上传文件。
