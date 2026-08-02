# QualityPilot 项目状态

最后更新：2026-08-02

## 当前架构

- PostgreSQL 17 与 pgvector 通过项目根目录的 `compose.yaml` 配置。
- 数据库数据保存在命名卷 `qualitypilot_pgdata` 中。
- FastAPI 后端位于 `backend/`，使用 Python 3.12、异步 SQLAlchemy、asyncpg 和 Pydantic Settings。
- 本地后端虚拟环境位于 `backend/.venv`。
- 后端运行时，开发环境 API 文档地址为 `http://localhost:8000/docs`。
- Alembic 已接管数据库结构变更；应用启动时只检查数据库连接。
- 上传原文件保存在配置项 `UPLOAD_DIRECTORY` 指定的目录，运行时上传目录不进入 Git。
- 已选定阿里云百炼 `qwen3.7-text-embedding`，固定输出 1024 维向量，后续使用余弦相似度检索。
- Embedding 的 API Key、专属接入地址、模型、维度、批量大小和超时时间已纳入 Pydantic Settings；真实密钥只保存在本地 `backend/.env`。
- 项目已初始化 Git，默认分支为 `main`，并连接到 GitHub 私有仓库 `csclly/qualitypilot`。

## 已完成功能

- FastAPI 应用启动与关闭生命周期。
- 用于本地前端开发的 CORS 配置。
- `GET /` 服务信息接口。
- `GET /api/v1/health` 进程健康检查接口。
- `GET /api/v1/ready` 数据库就绪检查接口。
- `POST /api/v1/knowledge/documents` 知识文档元数据新增接口。
- `GET /api/v1/knowledge/documents` 知识文档列表接口。
- `POST /api/v1/knowledge/documents/upload` 文件上传、解析、切分与入库接口。
- `GET /api/v1/knowledge/documents/{document_id}/chunks` 文档分块查询接口。
- TXT、Markdown、PDF 和 DOCX 纯文本提取。
- 20 MB 上传限制、文件名安全处理、类型/签名检查和 SHA-256 校验值。
- 约 800 字符、100 字符重叠、优先句子边界的文本切分。
- 文档元数据和全部文本分块的单事务写入；数据库失败时清理已保存文件。
- 文档文件元数据、处理状态、分块数量及分块字符偏移记录。
- PostgreSQL 异步连接与会话管理。
- `knowledge_documents` 与 `knowledge_document_chunks` ORM 模型和级联关系。
- Alembic 现有结构基线及文件导入增量迁移。
- 解析、切分、大小限制、现有接口回归、上传查询和事务回滚自动化测试。

## 已验证结果

- PostgreSQL 17.10 与 pgvector 0.8.6 运行正常。
- 现有数据库已从基线安全升级到 `0002_file_ingestion`，原有文档记录保持不变。
- `alembic check` 确认 ORM 模型与迁移后的数据库结构一致。
- 自动化测试共 21 项，全部通过。
- 真实百炼 API 调用成功：单条中文 PCB 文本由 `qwen3.7-text-embedding` 返回 1 条 1024 维向量，本次统计为 38 Token。
- WSL 首次 TLS 握手返回 `SSL_ERROR_SYSCALL`；强制 IPv4 和 HTTP/1.1 后专属 API Host 返回 HTTP 200。该结果属于手工调用验证，尚不代表 FastAPI 已接入向量生成。
- 新增 Embedding Settings 后，`alembic check` 无新迁移操作，21 项测试继续全部通过。
- 真实 Uvicorn 验收上传 10,930 字节 TXT，生成 9 个分块，上传和查询接口均返回成功。
- 验收及测试产生的文档、分块和上传文件均已精确清理，数据库保留原有 1 条文档记录。

## 已知缺口

- 已选定模型和维度，但尚未实现 Embedding Provider、批量向量化和失败重试，现有分块向量仍为空。
- 数据库现有 `embedding` 列为 1536 维；写入向量前必须通过经审查的 Alembic 增量迁移调整为 1024 维。
- 尚无向量检索、BM25、结果融合、Rerank 和引用溯源。
- 集成测试当前使用开发数据库并精确清理，后续应建立隔离测试数据库。
- 文档列表和分块列表尚未分页。
- 暂不支持扫描 PDF 的 OCR。
- 尚无 LangGraph Agent、MES/QMS 工具、人工审批、前端、可观测性和自动评测。

## 下一里程碑：向量化与基础检索

1. 实现可替换的 Embedding Provider，覆盖批处理、超时、响应数量和向量维度校验。
2. 在不重建数据库的前提下，将 `knowledge_document_chunks.embedding` 从 1536 维增量迁移为 1024 维。
3. 为新上传文档和已有分块实现批量向量生成、失败重试和幂等回填。
4. 增加余弦距离向量索引，并验证索引参数和查询计划。
5. 实现带文档来源和分块引用的向量检索接口。
6. 构造第一批 PCB/SOP 脱敏知识文档和检索测试集。
7. 建立隔离测试数据库并接入持续集成测试。
8. 在基础向量检索稳定后增加 BM25、融合排序和 Rerank。

## 当前实施决策

- 文件导入采用 20 MB 上限的同步处理；大文件或高并发需求出现时再引入持久化任务队列。
- 原始文件使用生成名称保存，数据库记录原文件名、MIME、大小、SHA-256、存储名和处理时间。
- TXT/Markdown 支持 UTF-8 与 GB18030；PDF 和 DOCX 使用独立解析适配逻辑。
- 文档元数据和分块在同一数据库事务中写入，不允许部分分块残留。
- 中文文本优先在段落和句子边界切分，并记录字符起止位置。
- Embedding 采用阿里云百炼 `qwen3.7-text-embedding`，显式指定 1024 维，向量检索使用余弦相似度。
- 当前只完成配置接入和手工 API 验证，尚不在文档上传流程中生成向量。
- API Key 使用 Pydantic `SecretStr` 承载，配置允许为空，使健康检查、旧接口和不调用模型的测试不依赖真实云端密钥。

## 安全约束

- 不得删除或重建数据库及其 Docker 数据卷。
- 不得运行 `docker compose down -v`。
- 数据库结构变更必须使用并审查 Alembic 增量迁移。
- 不得暴露 `.env` 的值或提交密钥。
