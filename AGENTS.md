# QualityPilot 长期开发规则

## 项目结构

- 项目在 WSL 中的根目录是 `/home/qualitypilot/projects/qualitypilot`。
- FastAPI 后端位于 `backend/`。
- 执行后端命令时，使用现有虚拟环境 `backend/.venv`。
- PostgreSQL 17 与 pgvector 由 `compose.yaml` 定义，数据保存在命名卷 `qualitypilot_pgdata` 中。
- 项目状态和实施里程碑统一记录在 `docs/PROJECT_STATUS.md`。

## 数据安全

- 必须保留现有 PostgreSQL 数据和命名卷。
- 除非用户明确要求执行对应的破坏性操作，否则不得运行 `docker compose down -v`，不得删除 `qualitypilot_pgdata`，不得重建数据库或重置已有数据。
- 引入数据库迁移之前，必须先检查实际运行数据库的结构。
- 数据库结构变更必须采用增量、版本化且经过审查的迁移。应为现有结构建立 Alembic 基线，不得通过重建现有表来引入迁移。
- 文档导入不得留下部分写入的分块；一次导入中的文档与分块写入必须保持事务一致性。

## 后端开发约定

- FastAPI 路由统一放在 `/api/v1` 下，并使用 SQLAlchemy 异步会话。
- HTTP 路由、文件存储、文本解析、文本切分和导入流程编排应放在独立模块中。
- 上传文件必须校验大小、支持类型和实际内容；必须安全处理文件名并防止路径穿越。
- 上传文件应使用生成的文件名存入可配置目录；文件元数据和解析后的文本分块写入 PostgreSQL。
- 文档处理状态使用受控生命周期：`created`、`processing`、`ready`、`failed`。
- 在明确选定嵌入模型和向量维度之前，`knowledge_document_chunks.embedding` 必须保持可空，不应生成向量。
- 不得提交密钥或本地 `backend/.env`；示例开发配置保存在 `.env.example`。

## 质量检查

- 必须保持现有健康检查、就绪检查、文档新增和文档查询行为兼容。
- 新增功能必须补充文本解析、文本切分、上传校验、事务回滚和 API 行为测试。
- 数据库迁移必须针对现有数据库测试，不得删除或重建数据库。
- 汇报检查结果时，必须明确区分“通过源码确认”和“已在运行服务中实际验证”。

## 变更范围

- 只进行与当前任务有关的修改，避免无关重构。
- 编辑前检查用户已有变更，不得覆盖或撤销用户的修改。
- 完成里程碑或下一阶段计划发生实质变化时，更新 `docs/PROJECT_STATUS.md`。
