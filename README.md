# DeepVocal

DeepVocal 是基于 LangGraph 的多 Agent 声乐辅助系统。当前仓库从项目书和数据准备库开始，先实现可替换的工程骨架，再逐步接入真实模型、GraphRAG 和音频算法。

## 当前状态

- 文档基线：`docs/`
- 数据准备库：`datasets/`
- 后端骨架：`apps/api/`
- 前端骨架：`apps/web/`
- Docker 基础设施：`infra/docker-compose.yml`
- 模型和歌声生成：当前为 Mock/disabled，不代表已接入

## 本地启动顺序

1. 复制 `.env.example` 为 `.env`，只填写本地服务配置。
2. 启动基础设施：`docker compose -f infra/docker-compose.yml up -d`
3. 安装后端依赖后运行 API：`uvicorn app.main:app --reload --app-dir apps/api`
4. 安装前端依赖后运行 Web：`npm --prefix apps/web install && npm --prefix apps/web run dev`

当前机器未检测到可用 Python 解释器，因此本轮只完成代码和配置骨架，未宣称后端已运行。

## 分支约定

- `main`：可复现基线
- `feature/platform-skeleton`：本轮整体框架
- `feature/data/*`：数据集整理和审核
- `feature/knowledge/*`：GraphRAG 与知识问答
- `feature/assessment/*`：声学测评
- `feature/planning/*`：练习规划
- `feature/singing/*`：未来歌声生成接口

GitHub 私有仓库需要在拥有远端 URL 和登录凭据后再绑定；在此之前本地提交仍可完整追踪。
