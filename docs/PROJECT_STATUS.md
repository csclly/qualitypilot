# QualityPilot 项目状态

最后更新：2026-08-03

## 当前架构

- PostgreSQL 17 与 pgvector 通过项目根目录的 `compose.yaml` 配置。
- 数据库数据保存在命名卷 `qualitypilot_pgdata` 中。
- 测试环境通过 Compose `test` profile 使用独立的 PostgreSQL/pgvector 容器、5433 端口、测试账号和 tmpfs，不挂载开发数据卷。
- FastAPI 后端位于 `backend/`，使用 Python 3.12、异步 SQLAlchemy、asyncpg 和 Pydantic Settings。
- 本地后端虚拟环境位于 `backend/.venv`。
- 后端运行时，开发环境 API 文档地址为 `http://localhost:8000/docs`。
- Alembic 已接管数据库结构变更；应用启动时只检查数据库连接。
- 上传原文件保存在配置项 `UPLOAD_DIRECTORY` 指定的目录，运行时上传目录不进入 Git。
- 已选定阿里云百炼 `qwen3.7-text-embedding`，固定输出 1024 维向量，后续使用余弦相似度检索。
- Embedding 的 API Key、专属接入地址、模型、维度、批量大小和超时时间已纳入 Pydantic Settings；真实密钥只保存在本地 `backend/.env`。
- 项目已初始化 Git，默认分支为 `main`，并连接到 GitHub 私有仓库 `csclly/qualitypilot`。
- WSL GitHub CLI 2.94.0 已持久安装在 `/home/qualitypilot/.local/bin/gh`，Git 凭据助手不再依赖 `/tmp` 临时路径。

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
- `POST /api/v1/knowledge/documents/{document_id}/embeddings` 单文档缺失向量幂等回填接口。
- `POST /api/v1/knowledge/search` 余弦相似度知识检索接口，返回分块、相似度、文档来源和字符偏移。
- TXT、Markdown、PDF 和 DOCX 纯文本提取。
- 20 MB 上传限制、文件名安全处理、类型/签名检查和 SHA-256 校验值。
- 约 800 字符、100 字符重叠、优先句子边界的文本切分。
- 文档元数据和全部文本分块的单事务写入；数据库失败时清理已保存文件。
- 文档文件元数据、处理状态、分块数量及分块字符偏移记录。
- PostgreSQL 异步连接与会话管理。
- `knowledge_documents` 与 `knowledge_document_chunks` ORM 模型和级联关系。
- Alembic 现有结构基线、文件导入迁移及 `embedding` 字段 1024 维增量迁移。
- 解析、切分、大小限制、现有接口回归、上传查询和事务回滚自动化测试。
- 阿里云百炼 Embedding 配置的类型化读取、范围校验与 `SecretStr` 密钥脱敏。
- 可替换的 `EmbeddingProvider` 协议与 `QwenEmbeddingProvider` 实现，支持批量拆分、顺序恢复、错误分类及响应契约校验。
- 新上传文档在文件落盘和数据库事务前完成分块向量化，成功后将文档、分块和 1024 维向量一次性写入。
- 历史分块回填只选择空向量，并以条件更新避免并发覆盖；重复调用会跳过已完成分块。
- 网络异常、超时、HTTP 429 和 5xx 使用可配置次数的指数退避重试。
- 非空向量使用 `hnsw + vector_cosine_ops` 部分索引加速近邻查询；空知识库不会调用 Embedding API。
- PostgreSQL `pg_trgm` 中文字符片段检索与 `gist_trgm_ops` KNN 索引。
- 搜索接口新增 `vector`、`keyword`、`hybrid` 模式，默认仍为向量检索；结果返回匹配模式和两路原始分数。
- 混合检索使用可配置候选倍率和 RRF 常数进行排名融合，评测命令可按模式生成可比较报告。
- 新增 5 份合成脱敏 PCB/SOP 文档、8 条标注查询和可重复运行的检索评测命令，计算 Recall@K、MRR 与引用正确率。
- pytest 会在收集到集成测试时自动升级隔离测试库，强制测试库名以 `_test` 结尾，并清空真实 Embedding API Key。
- GitHub Actions 后端工作流使用独立 pgvector 服务，执行依赖、语法、Alembic 迁移状态和完整测试检查。

## 已验证结果

- PostgreSQL 17.10 与 pgvector 0.8.6 运行正常。
- 现有数据库已安全升级到 `0005_trigram_search`；迁移只安装 `pg_trgm` 并创建内容 GiST 索引，开发库 7 个文档、6 个分块和 6 个向量保持不变。
- `alembic check` 确认 ORM 模型与迁移后的数据库结构一致。
- 已在事务中临时写入 1024 维测试向量并由 `vector_dims` 确认维度，随后回滚；数据库没有遗留测试分块。
- 使用事务内 200 条临时非零向量执行 `EXPLAIN`，确认查询计划为 HNSW `Index Scan`，随后整体回滚。
- 隔离测试库从空库顺序执行 `0001_existing_schema` 至 `0005_trigram_search` 后，完整 52 项测试全部通过；测试结束后文档、分块和向量计数均为 0。
- 不启动 PostgreSQL 时，44 项非集成测试通过，另有 8 项集成测试被正确排除。
- 隔离测试前后开发库计数均保持为 7 个文档、6 个分块、6 个非空向量，Uvicorn 进程持续运行；本轮未修改开发库数据。
- 自动化测试共 52 项，全部通过；新增覆盖 RRF 融合、中文关键词排序、模式校验和评测模式透传。
- 使用事务内 200 条临时中文分块执行 `EXPLAIN`，修正 SQL CAST 和次级排序后确认查询计划使用 trigram GiST `Index Scan`，随后整体回滚。
- 真实百炼 API 调用成功：单条中文 PCB 文本由 `qwen3.7-text-embedding` 返回 1 条 1024 维向量，本次统计为 38 Token。
- WSL 首次 TLS 握手返回 `SSL_ERROR_SYSCALL`；强制 IPv4 和 HTTP/1.1 后专属 API Host 返回 HTTP 200。该结果属于手工 API 验证；FastAPI 向量化流程使用可注入的假 Provider 完成自动化验证。
- Embedding、上传、回填、检索和评测接入后，`alembic check` 无待生成操作，52 项测试全部通过；自动化测试使用模拟 HTTP 或假 Provider，不调用真实 API。
- 真实 Uvicorn 验收上传 10,930 字节 TXT，生成 9 个分块，上传和查询接口均返回成功。
- 经用户授权，通过真实 Uvicorn 上传接口导入 5 份合成评测文档，生成 5 个分块；逐条确认状态为 `ready` 且向量维度为 1024。
- 使用真实百炼查询向量完成基线：Top-1 的 Recall、MRR、引用正确率均为 0.8750；Top-3 的 Recall 为 0.9375、MRR 为 0.9167、引用正确率为 0.3333。
- 两份逐查询 JSON 报告保存在 `backend/evaluation/reports/`；Q001—Q007 的正确文档均排第 1，Q008 在 Top-3 只召回两个相关文档中的一个且排第 3。
- 关键词基线：Top-1 Recall 0.6875、MRR 0.7500、引用正确率 0.7500；Top-3 Recall 1.0000、MRR 0.8750、引用正确率 0.5625。
- 混合检索基线：Top-1 Recall 0.9375、MRR 1.0000、引用正确率 1.0000；Top-3 Recall 1.0000、MRR 1.0000、引用正确率 0.3750。
- 混合 Top-1 相比向量 Top-1 的 Recall 从 0.8750 提升到 0.9375；指标只来自 5 文档、8 查询的合成小样本，不代表生产效果。
- 提交 `b065657` 推送到 `origin/main` 后，GitHub Actions `Backend Tests` 首次远端运行成功，用时 47 秒。

## 已知缺口

- 当前回填接口按单文档同步执行，尚无全库后台任务、持久化重试队列、失败任务状态和调用指标。
- 已完成合成小样本真实 Provider 基线，但没有真实企业 PCB 评测数据，当前指标不能代表生产效果。
- `pg_trgm` 是字符片段检索，不等同于中文语义分词或 BM25；尚未引入 Rerank，是否需要引入必须用更大真实评测集决定。
- 文档列表和分块列表尚未分页。
- 暂不支持扫描 PDF 的 OCR。
- 尚无 LangGraph Agent、MES/QMS 工具、人工审批、前端、可观测性和自动评测。

## 下一里程碑：Agent 工作流基础

1. 已完成第一版合成脱敏 PCB/SOP 文档、标注查询、指标模块和评测运行器。
2. 已完成真实样例导入，并保存 Top-1 与 Top-3 向量检索基线报告。
3. 已建立隔离测试数据库并接入 GitHub Actions 持续集成配置。
4. 已完成 pg_trgm 关键词检索、RRF 融合和同评测集三模式对比；下一步设计可追踪、可暂停恢复的 Agent 状态与最小工作流。

## 当前实施决策

- 文件导入采用 20 MB 上限的同步处理；大文件或高并发需求出现时再引入持久化任务队列。
- 原始文件使用生成名称保存，数据库记录原文件名、MIME、大小、SHA-256、存储名和处理时间。
- TXT/Markdown 支持 UTF-8 与 GB18030；PDF 和 DOCX 使用独立解析适配逻辑。
- 文档元数据和分块在同一数据库事务中写入，不允许部分分块残留。
- 中文文本优先在段落和句子边界切分，并记录字符起止位置。
- Embedding 采用阿里云百炼 `qwen3.7-text-embedding`，显式指定 1024 维，向量检索使用余弦相似度。
- 数据库向量字段使用受保护的 Alembic 增量迁移调整为 1024 维；迁移和回退在存在非空向量时都会拒绝执行，避免静默转换或损坏已有向量。
- 新文档上传默认生成向量；云端调用发生在文件落盘和数据库事务之前，调用失败不会留下文件或数据库半成品。
- 历史回填先结束只读事务再调用云端，写回时使用 `embedding IS NULL` 条件更新，避免长事务和并发覆盖。
- API Key 使用 Pydantic `SecretStr` 承载，配置允许为空，使健康检查、旧接口和不调用模型的测试不依赖真实云端密钥。
- 自动化测试统一覆盖数据库连接、运行环境、上传目录和 API Key；测试库名不以 `_test` 结尾时立即拒绝运行。
- 本地测试数据库使用 Compose profile 按需启动并存放于 tmpfs；CI 使用 GitHub Actions job 级 PostgreSQL 服务，两者都不复用开发数据卷。
- Provider 通过协议与工厂创建，普通自动化测试注入 `httpx.MockTransport` 或假 Provider，不会读取真实密钥、访问公网或消耗模型额度。
- 查询先检查是否存在非空向量；空库直接返回空列表。非空时生成查询向量，用 pgvector `<=>` 计算余弦距离，并返回 `1 - distance` 作为相似度。
- HNSW 索引只覆盖非空向量，使用与查询运算符匹配的 `vector_cosine_ops` 操作符类。
- 评测按稳定文档编号标注相关性，Recall@K 对相关文档去重后计算，MRR 使用首个相关分块排名。
- 搜索请求默认 `vector` 以保持现有接口行为；`keyword` 不调用云端，`hybrid` 才同时使用两路候选。
- 中文关键词第一版选择 PostgreSQL 内置 `pg_trgm`，避免引入镜像中不存在的中文分词扩展；它适合术语和字符片段匹配，但不是完整中文分词。
- 关键词距离使用 `<->>` KNN 运算符与 `gist_trgm_ops` 对齐；SQLAlchemy 使用 `type_coerce` 而不是 SQL `CAST`，避免破坏索引排序。
- 两路分数不可直接线性相加，因此使用 RRF 按排名融合；默认候选倍率为 5、`rrf_k=60`，均通过 Pydantic Settings 校验。
- 引用正确率要求检索分块同时来自相关文档且包含至少一个人工标注证据短语；当前按查询计算后取宏平均。
- 真实基线同时保存 Top-1 和 Top-3：前者衡量首条引用质量，后者衡量扩大候选集后的召回能力。
- 评测运行器只对网络错误、HTTP 429 和 5xx 做最多 2 次指数退避重试；普通 4xx 不重试，重试耗尽仍明确失败。

## 安全约束

- 不得删除或重建数据库及其 Docker 数据卷。
- 不得运行 `docker compose down -v`。
- 数据库结构变更必须使用并审查 Alembic 增量迁移。
- 不得暴露 `.env` 的值或提交密钥。
