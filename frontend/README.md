# QualityPilot 前端

中文质量工作台，使用 React + TypeScript + Vite，连接已有 FastAPI 后端。默认采用 **关键词检索 + 规则草稿**，云端生成模型停机时仍可开发和使用主流程。

## 启动

先保持 PostgreSQL 与后端运行：

```bash
cd /home/qualitypilot/projects/qualitypilot
docker compose up -d postgres
cd backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

另开一个 WSL 终端：

```bash
cd /home/qualitypilot/projects/qualitypilot/frontend
bash start.sh
```

打开 <http://localhost:5173>。脚本优先使用本机已准备的 `.tools/node`；新克隆项目需要自行准备 Node.js 22.12+，首次启动会运行 `npm ci`。本机便携运行环境与所有依赖目录均被 Git 忽略。

开发服务器通过同源代理转发 `/api` 和 `/openapi.json` 到 `http://127.0.0.1:8000`。可在前端 `.env` 设置 `API_PROXY_TARGET`，示例见 `.env.example`。该变量只由开发服务器读取，不能在此前端文件中填写任何模型或数据库密钥。

修改后端代码后需重启后端；前端会自动更新。页面检测后端是否支持 `use_model`，旧后端未更新时禁用规则草稿提交，避免旧接口忽略字段后意外调用模型。

## 功能

- **质量分析**：填写问题、选择检索方式与证据上限，创建规则草稿或模型分析。
- **来源核查**：区分模型草稿与规则草稿，显示摘要、动作、风险、引用原文、业务记录及工具失败。
- **人工审批**：审核确认后批准或拒绝，显示审批结果、时间与认证状态；提交重试复用相同请求 ID。
- **记录找回**：通过 URL 或运行编号加载历史记录。侧栏“最近打开”仅覆盖本机浏览器打开过的记录，不是全库运行列表。浏览器只保存最多 20 个运行 ID，实际内容仍从后端读取。
- **知识库**：读取分页文档、上传文件、查看分页原文分块、补全缺失向量。文件大小和扩展名先在页面校验，内容校验与事务一致性仍由后端负责。
- **证据检索**：支持关键词、向量和混合检索，明确匹配分不是结论正确率。
- **连接设置**：显示后端/数据库就绪状态；可填写审批凭据，仅存当前页面内存，刷新即清除，只在提交审批时发送。

## 云端停机时

`POST /api/v1/agent/runs` 新增可选字段 `use_model`，默认仍为 `true`，保持旧客户端行为。前端规则草稿显式发送 `false`，绕过生成模型，仍进行检索、保存检查点和等待审批。

关键词检索不调用 Embedding。上传、向量补全、语义及混合检索仍可能调用百炼向量服务；停止自部署生成模型不会替代或关闭这些依赖。

审批只形成最终答复或拒绝结果，不会创建生产工单或修改 MES/QMS。

## 验证

使用已安装的 Node 环境执行：

```bash
npm ci
npm run build
npm test
```

`npm test` 只收集前端单元测试。页面测试另行执行，先启动前端服务器：

```bash
npx playwright install chromium
npm run test:e2e
```

默认页面测试使用隔离的模拟 API，不调用模型或写入开发数据库。可通过 `E2E_BASE_URL` 调整前端地址，通过 `PLAYWRIGHT_CHANNEL` 或 `PLAYWRIGHT_EXECUTABLE_PATH` 选择独立测试浏览器。

仅在需要真实本地联调时使用：

```bash
E2E_LIVE_API=1 npm run test:e2e
```

该选项会在当前后端新建一条明确标记的“前端联调测试”规则运行，并以 `frontend-qa` 身份拒绝该测试草稿，保留真实检查点和审计记录；不会审批已有用户运行，也不调用生成模型。若后端开启强制审批认证，需使用适合测试的环境与身份配置。

## 部署边界

`npm run build` 生成 `dist/`，供静态服务器托管。Vite 开发代理不会包含在构建产物中，正式部署需要同源反向代理 `/api` 和 `/openapi.json` 到后端，并配置 HTTPS 与实际认证。

本轮不包含全库运行列表、用户登录与令牌刷新、多租户、真实 MES/QMS 连接、告警/归档管理页面。前端不会把未实现的能力显示为已连接或已完成。
