# DeepVocal

**DeepVocal：面向个性化声乐学习的智能助手**

DeepVocal 将声乐知识、演唱表现分析与日常练习连接在同一段自然对话中。用户可以直接描述目标、提出问题或提交录音，逐步获得知识解答、演唱测评与针对性的练习建议，并根据练习反馈持续调整学习计划。

## 功能 Agent

- **Vocal Knowledge**：围绕声乐概念、发声技巧与练习方法提供知识问答，支持结合上下文连续交流。
- **Song Assessment**：分析录音或曲目演唱表现，呈现音准、节奏、稳定性等维度的测评结果。
- **Practice Planning**：结合用户目标、可用时间与测评结果安排练习内容，并根据反馈调整计划。
- **AISinging**：在用户提供必要素材与授权后，探索个性化歌声创作，为声乐学习体验提供延展。

四类 Agent 可根据用户的表达协同完成连续任务。例如，用户可以先询问某项发声技巧，再提交录音进行测评，并基于测评结果安排练习；各项结果在助手对话中衔接呈现。

## 安装与使用

### 环境准备

- Windows、macOS 或 Linux
- Docker Desktop，使用 Linux 容器
- Git

### 启动项目

克隆仓库，在项目根目录复制环境配置文件，并启动应用：

```powershell
git clone https://github.com/Defend6/deepvocal.git
cd deepvocal
Copy-Item .env.example .env
docker compose --env-file .env -f infra/docker-compose.yml -f infra/compose.app.yml up -d --build --wait
```

网页地址：<http://127.0.0.1:8080>；API 文档：<http://127.0.0.1:8000/docs>

### 开始使用

进入网页后，可在对话中提出声乐问题、描述练习目标，或上传录音发起测评。也可以进入专业工作台，按需设置测评与练习参数。模型服务、知识资料和歌声能力通过本地 `.env` 与运行环境配置。

## 项目结构

```text
deepvocal/
├── apps/
│   ├── api/          # API、Agent 编排、业务服务、数据库迁移与测试
│   ├── web/          # Web 助手、专业工作台与页面交互
│   └── singing/      # AISinging 独立推理服务
├── infra/            # Docker Compose 与服务运行配置
├── scripts/          # 启动、数据准备、维护与评测工具
├── contracts/        # API 契约
└── .github/          # GitHub Actions 持续集成配置
```

### 主要目录

- `apps/api/app/agents/`：四类功能 Agent 的路由与对话编排。
- `apps/api/app/knowledge/`：声乐知识资料处理与检索服务。
- `apps/api/migrations/`：数据库结构迁移。
- `apps/api/tests/`：后端功能与集成测试。
- `apps/web/src/`：前端页面与用户交互。
- `apps/web/e2e/`：浏览器端流程测试。
- `infra/`：应用及其依赖服务的容器配置。
- `scripts/`：项目启动、语料管理和本地维护脚本。
