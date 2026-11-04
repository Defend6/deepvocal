# DeepVocal

DeepVocal 是基于 LangGraph 的多 Agent 声乐辅助系统。当前仓库从项目书和数据准备库开始，先实现可替换的工程骨架，再逐步接入真实模型、GraphRAG 和音频算法。

## 当前状态

- 文档基线：`docs/`
- 数据准备库：`datasets/`
- 后端骨架：`apps/api/`
- 前端骨架：`apps/web/`
- Docker 基础设施：PostgreSQL、Redis、RustFS（S3 兼容）、Neo4j、Milvus 与 etcd，定义在 `infra/docker-compose.yml`
- 模型和歌声生成：当前为 Mock/disabled，不代表已接入

## 本地启动顺序

1. 复制 `.env.example` 为 `.env`，只填写本地服务配置。
2. 启动基础设施：`docker compose -f infra/docker-compose.yml up -d`
3. 建立并安装 API 环境：`python -m venv .venv`，Windows 下执行 `.venv\\Scripts\\python -m pip install -e "apps/api[dev]"`。
4. 执行数据库迁移：`.venv\\Scripts\\python -m alembic -c apps/api/alembic.ini upgrade head`。
5. 在项目根目录运行 API：`.venv\\Scripts\\python -m uvicorn app.main:app --reload --app-dir apps/api`。
6. 安装并运行前端：`npm --prefix apps/web install && npm --prefix apps/web run dev`。
7. 后端测试：`Set-Location apps/api; ..\\..\\.venv\\Scripts\\python -m pytest`。

当前实现节点已完成：React 前端可访问 FastAPI 健康检查和结构化路由接口；API 提供只读 GraphRAG 语料状态端点，并明确返回审核/生产启用阻断原因；后端仅支持本地显式测试身份、按身份映射的 owner 和音频预签名上传申请。OIDC 参数仅保留配置位；实际 Bearer 校验、上传完成回调、对象校验、真实模型、知识检索、音频分析、上传前端工作流、任务 API 和后台 worker 尚未实现。申请的资产保持 pending，不能用于测评。页面中的音频上传按钮目前仅是入口占位。各阶段待开发项见 `docs/31_后续开发路线与未完成项.md`，本地身份和上传使用边界见 `docs/33_身份认证与音频上传实现记录.md`。

## 分支约定

- `main`：可复现基线
- `feature/platform-skeleton`：本轮整体框架
- `feature/data/*`：数据集整理和审核
- `feature/knowledge/*`：GraphRAG 与知识问答
- `feature/assessment/*`：声学测评
- `feature/planning/*`：练习规划
- `feature/singing/*`：未来歌声生成接口

GitHub CLI 使用 `gh auth login` 登录后创建私有远端；不要将 `.env`、用户录音或未经审核的数据集推送到仓库。
