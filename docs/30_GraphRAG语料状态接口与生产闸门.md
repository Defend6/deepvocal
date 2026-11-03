# GraphRAG 语料状态接口与生产闸门

日期：2026-11-04  
分支：`feature/platform-skeleton`

## 目标

API 通过 `GET /v1/knowledge/corpus/status` 只读暴露 `datasets/SUMMARY.json` 和来源注册表的状态，供前端工作台、运维检查和后续 GraphRAG 导入流程使用。该接口不执行向量化、不写入 Neo4j/Milvus，也不改变数据集审核状态。

## 当前数据状态

当前语料版本为 `seed-20261103-v1`，属于 `COLLECTED_PREPARATION_ONLY_NOT_PRODUCTION`。已有 35 篇文档、166 个片段、43 个实体、766 条候选关系和 5 个来源；专家审核、embedding 计算、Neo4j 导入、Milvus 导入均未完成。真实用户练声记录为 0。

## 生产检索闸门

`production_enabled` 只有在以下条件全部满足时才会为 `true`：语料状态为 `PRODUCTION_READY`、专家审核完成、embedding 已计算、Neo4j 和 Milvus 已加载，且所有来源均标记为 `production_enabled=true`。任一条件不满足，接口返回 `false` 和可读的 `production_blockers`，后续检索服务必须拒绝将该语料当作生产知识库。

## 配置

默认从项目根目录的 `datasets` 读取。部署时可以通过 `CORPUS_ROOT` 指向只读挂载的数据准备目录。数据准备库与应用业务数据库仍然分离，不能把 SQLite seed 当作 PostgreSQL 业务库迁移文件。

## 验证

API 测试覆盖接口状态、关键计数和审核闸门；数据内容完整性继续由 `py scripts/validate_dataset.py` 负责。后续接入真实 GraphRAG 时，应在导入作业完成并通过人工审核后再更新 `SUMMARY.json` 和来源注册表。
