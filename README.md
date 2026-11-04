# DeepVocal

基于 LangGraph 的多 Agent 声乐研究工作台。默认智能助手通过后端规则识别意图并调度知识、测评、规划、歌声能力四类子图；专业工作台保留结构化操作。

## 技术与当前边界

React / TypeScript、FastAPI、LangGraph、PostgreSQL、Redis Streams、S3 兼容对象存储、Milvus 与 Neo4j。支持作业投递、重试/取消、checkpoint、工具白名单、练习计划与反馈，以及受限进程声学测量。

当前仅支持本地测试身份。浏览器上传采用预签名 PUT，资产保持 pending，尚不能提交测评。真实模型调用需要 Key、已确认的费率和预算配置；默认禁用付费。歌声生成服务未接入。工程测量和研究资料不代表教学效果验证。

## 仓库范围

- `apps/`：应用、数据库迁移与测试；测试自行生成合成样例。
- `infra/`：Docker Compose 配置。
- `scripts/`：运行、备份恢复、数据处理和仓库检查工具。
- `contracts/runtime-openapi.json`：由实际 API 导出的工程契约。
- `.github/`、依赖锁文件及配置示例：构建和 CI。

项目书、详细开发文档、参考资料、历史原型、真实语料、训练/评测数据、用户音频、索引、运行结果和密钥只在本地或独立数据存储管理，不属于代码发布内容。数据处理脚本可以提交，其输入和生成结果不提交。不要用 `git add -f` 绕过数据忽略规则。旧提交历史未重写，可能包含整理前的文件。

## Docker 运行

需要 Docker Desktop（Linux 容器）。复制 `.env.example` 为 `.env`，已有文件不覆盖，然后在项目根目录运行：

```powershell
docker compose -f infra/docker-compose.yml -f infra/compose.app.yml up -d --build --wait
```

页面：`http://127.0.0.1:8080`；API：`http://127.0.0.1:8000/docs`。端口只绑定本机。

镜像只包含代码，知识资料在运行时从项目根目录的 `datasets/` 只读挂载；该目录被 Git 忽略。也可在 `.env` 设置 `DEEPVOCAL_CORPUS_DIR=D:/deepvocal/datasets` 使用其他本地路径。不要挂载到数据库或索引卷上。

全新代码检出没有真实语料仍可启动，规划与其他不依赖知识资料的功能可用；知识问答显示资料未准备，语料状态接口返回受控 503。不会自动下载语料、生成虚构回答或调用付费模型。空挂载会准备空知识包，不复用旧知识包冒充当前数据；数据只导入一半或 JSON 损坏会阻止准备。

## 本地知识资料接入

现有数据继续保存在本地 `D:\deepvocal\datasets`。数据导入器当前要求：

```text
datasets/                         # 本地目录，不提交
  project-original/knowledge.json # [{id,title,text,entities:[...]}]
  source-registry.json            # {sources:[{source_id,license,...}]}
  SUMMARY.json                    # 语料状态元数据
```

准备逻辑不会将已有抓取材料自动认证为已审核知识。研究种子保留未审核标识；生产许可、内容审核及引用要求仍需分别满足。`SUMMARY.json` 应包含 version、status、document_count、chunk_count、entity_count、candidate_relation_count 及真实审核/索引状态；不应伪造统计。原始数据与运行知识包、索引分别管理。

更新本地输入后重新准备：

```powershell
docker compose -f infra/docker-compose.yml -f infra/compose.app.yml run --rm migrate python -m app.knowledge.cli prepare
```

准备过程不计算模型向量。模型配置和审核条件满足后才可运行 `python -m app.knowledge.cli ingest`；变更语料或 embedding 模型需重建索引。`prepare --allow-missing` 仅用于允许无数据的开发启动，不忽略损坏或部分导入的数据。

## 调试与测试

本地调试需要 Python 3.12、Node.js 24 和 Docker。执行 `scripts/dev.ps1 setup`，然后在三个终端分别运行 `scripts/dev.ps1 api`、`scripts/dev.ps1 worker`、`scripts/dev.ps1 web`，打开 `http://127.0.0.1:5173`。若整套 Compose 已运行，先停止其中 api、worker、web，保留基础服务及数据卷，以免端口冲突和混用代码版本。

```powershell
.\scripts\dev.ps1 test
.venv\Scripts\python.exe scripts/check_repository.py
```

本地完整测试使用独立 `deepvocal_test` 数据库和 Redis DB15，包括可选 Milvus/Neo4j 集成；CI 使用临时 PostgreSQL/Redis，不依赖私有文档或语料。测试只验证工程契约，不代表真实数据集或专家效果评测。

浏览器回归需要已启动的 API、worker、网页和对象存储。有本地语料时运行 `scripts/dev.ps1 e2e`；无资料时设置 `E2E_CORPUS_AVAILABLE=0`，校验知识资料缺失提示，其他流程照常测试。整套 Compose 的入口使用 `E2E_BASE_URL=http://127.0.0.1:8080`。

备份：`scripts/backup.ps1`；恢复校验：`scripts/restore-test.ps1 -BackupFile <本地备份路径>`。目前只覆盖 PostgreSQL，不代表对象和图/向量索引的完整灾备。密钥通过本地环境配置，勿提交 `.env`。
