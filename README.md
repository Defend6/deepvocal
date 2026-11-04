# DeepVocal

基于 LangGraph 的多 Agent 声乐研究工作台。四类 Agent 分别承担知识问答、录音/全曲测评、练习规划与歌声生成能力查询。确定性路由、工具白名单和持久化检查点控制执行边界。

## 当前可用范围（2026-11-04）

- PostgreSQL 作业与 outbox、Redis Streams、重试/超时/取消/租约恢复，以及业务产物原子落库。
- 真正的 LangGraph Supervisor + 四子图，PostgreSQL checkpoint；每个 Agent 仅可调用自身工具。
- 中文研究资料门控、Milvus 向量检索、Neo4j 实体共现扩展、融合/重排/引用校验与模型 HTTP 适配器。无 Key 时仅展示资料预览。
- 单声清唱工程测量、长音与显式参考音符评分、分段报告；5～30 分钟约束计划、版本编辑和练习反馈。
- 声明式 Skill 发现/按需加载/审核发布退役；默认关闭的用户确认偏好记忆、来源与异步压缩。
- React + TypeScript 前端，风格参考 Look Alive；桌面和手机流程包含真实预签名 PUT 上传。
- 模型调用按月共享预算并发预占、保守结算、本人费用查询；音频在受限独立进程测量；GraphRAG 提供不调用模型的离线冒烟评测。
- worker 独立心跳、本人任务诊断面板、本地队列容量/故障汇总，以及默认预览的历史消息维护与通知恢复工具。

**本机研究版本，不是生产系统。** 按已确认范围，仅本地测试身份；上传资产始终 pending，浏览器不能提交测评。离线声学测试和预置已验证资产的集成测试可运行。真实模型需 Key、供应商计费确认、模型费率与预算配置，再做账户联调；默认阻止付费调用。歌声生成保持禁用。中文草稿尚未经专家审核，没有虚构教学效果或 Token 节省指标。

## 推荐：本地编辑，Docker 运行

在项目根目录，复制 `.env.example` 为 `.env`，保留本地示例配置。已有 `.env` 请勿覆盖。

```powershell
docker compose -f infra/docker-compose.yml -f infra/compose.app.yml up -d --build --wait
```

浏览器打开 `http://127.0.0.1:8080`，API 文档为 `http://127.0.0.1:8000/docs`。启动会迁移数据库、准备原创中文研究资料并配置对象存储 CORS。所有公开端口仅绑定回环地址。密钥通过本地环境传入，不提交 Git。

## 本地调试

需 Python 3.12、Node.js 24、Docker Desktop（Linux 容器）。项目及缓存、浏览器测试产物均放在 `D:\deepvocal`。

```powershell
.\scripts\dev.ps1 setup
```

分别在三个终端运行 `scripts/dev.ps1 api`、`scripts/dev.ps1 worker`、`scripts/dev.ps1 web`，打开 `http://127.0.0.1:5173`。若已启动整套 Compose，先停止其中 api/worker/web，防止 8000 端口冲突及混用代码版本；保留数据库服务与卷。

```powershell
docker compose -f infra/docker-compose.yml -f infra/compose.app.yml stop api worker web
.\scripts\dev.ps1 test
.\scripts\dev.ps1 e2e
```

E2E 需要 API、worker、前端及 RustFS 已启动。测试 Redis 使用 DB15；不要把它改成真实业务队列。完整复现、备份恢复及验证说明见 [运行与验收记录](docs/39_运行复现与验收边界.md)。

`dev.ps1 test` 使用独立 `deepvocal_test` 数据库，避免业务worker消费测试任务。本地故障定位、队列保留与恢复命令见 [任务监控与维护](docs/41_任务监控队列维护与恢复.md)。

## 文档和代码的对应关系

[阅读导航](docs/00_阅读导航与交付边界.md) / [真实进度与未完成项](docs/31_后续开发路线与未完成项.md) / [Skill 与 Memory](docs/38_Skill与Memory实现记录.md)。

模型启用检查、预算单位、音频进程限制及离线评测命令见 [预算与进程隔离实施记录](docs/40_模型预算音频进程与离线评测.md)。实际账本为月度人民币分预算，与完整设计中的日预算表仍有阶段差异。

`contracts/openapi.json`、`database/schema.sql` 是完整目标设计；实际服务器契约为 `contracts/runtime-openapi.json` 和 `apps/api/migrations/`。两者尚未完全一致，不能用设计 DDL 覆盖运行数据库。评分仍遵循 `contracts/scoring-rules.json`。`prototype/` 为历史原型，实际前端在 `apps/web/`。

## 数据与发布

只将明确授权的研究资料纳入对应分区；准备库、限制性来源和原创工程草稿均不等于生产知识库。用户音频、备份、Key、生成索引与运行状态不得提交。已有语料的许可状态见 [GraphRAG 实现记录](docs/36_GraphRAG研究链路与许可处理.md)。

当前 GitHub 仓库为私有，迭代推送 `feature/platform-skeleton`；默认分支当前也是该分支。后续经独立验收再决定合并/公开，不自动更改仓库可见性。
