# QualityPilot 项目状态

最后更新：2026-08-01

## 当前架构

- PostgreSQL 17 与 pgvector 通过项目根目录的 `compose.yaml` 配置。
- 数据库数据保存在命名卷 `qualitypilot_pgdata` 中。
- FastAPI 后端位于 `backend/`，使用 Python 3.12、异步 SQLAlchemy、asyncpg 和 Pydantic Settings。
- 本地后端虚拟环境位于 `backend/.venv`。
- 后端运行时，开发环境 API 文档地址为 `http://localhost:8000/docs`。
- Alembic 已接管数据库结构变更；应用启动时只检查数据库连接。
- 上传原文件保存在配置项 `UPLOAD_DIRECTORY` 指定的目录，运行时上传目录不进入 Git。
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
- 真实 Uvicorn 验收上传 10,930 字节 TXT，生成 9 个分块，上传和查询接口均返回成功。
- 验收及测试产生的文档、分块和上传文件均已精确清理，数据库保留原有 1 条文档记录。

## 已知缺口

- 尚未选择和接入 Embedding 模型，分块向量保持为空。
- 尚无向量检索、BM25、结果融合、Rerank 和引用溯源。
- 集成测试当前使用开发数据库并精确清理，后续应建立隔离测试数据库。
- 文档列表和分块列表尚未分页。
- 暂不支持扫描 PDF 的 OCR。
- 尚无 LangGraph Agent、MES/QMS 工具、人工审批、前端、可观测性和自动评测。

## 下一里程碑：向量化与基础检索

1. 确定 Qwen Embedding 模型、向量维度和可配置模型接口。
2. 为已有分块实现批量向量生成与失败重试。
3. 增加向量索引并验证索引参数和查询计划。
4. 实现带文档来源和分块引用的向量检索接口。
5. 构造第一批 PCB/SOP 脱敏知识文档和检索测试集。
6. 建立隔离测试数据库并接入持续集成测试。
7. 在基础向量检索稳定后增加 BM25、融合排序和 Rerank。

## 当前实施决策

- 文件导入采用 20 MB 上限的同步处理；大文件或高并发需求出现时再引入持久化任务队列。
- 原始文件使用生成名称保存，数据库记录原文件名、MIME、大小、SHA-256、存储名和处理时间。
- TXT/Markdown 支持 UTF-8 与 GB18030；PDF 和 DOCX 使用独立解析适配逻辑。
- 文档元数据和分块在同一数据库事务中写入，不允许部分分块残留。
- 中文文本优先在段落和句子边界切分，并记录字符起止位置。
- 当前不生成嵌入向量，必须先选定并固定嵌入模型和维度。

## 安全约束

- 不得删除或重建数据库及其 Docker 数据卷。
- 不得运行 `docker compose down -v`。
- 数据库结构变更必须使用并审查 Alembic 增量迁移。
- 不得暴露 `.env` 的值或提交密钥。
