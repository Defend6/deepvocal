# DeepVocal 数据准备库 V1.4

已收集原件和审阅数据，共 35 篇正文、166 个片段、40 段真实演唱音频、4113 条双标注人音符记录、8 首曲谱及 16 个符号片段。SQLite 是数据准备库，不是应用业务数据库。

根据 D-004，当前应用知识库只允许中文原生资料。已有正文主要为英文，本轮仍全部未激活，只供研究参考和音高/曲谱算法核查。不要因原件存在就把它们导入当前中文活动语料。中文专业资料尚未收齐，20 章规定补齐与拒答条件。

实体、共现边、练习均为候选，不等于已审核语义知识。D-021 免除教师参与，不等于内容得到教师认证；未来按证据一致性校验研究级启用，expert_verified=false。用户记录为 0。

来源及许可在 source-registry.json；原件哈希在 raw-manifest.jsonl，下载定位在 retrieval-resources.json。NC/SA/署名义务按原件保留；代码开源不等于本目录可整包上传。原音频仍在 ZIP，audio_inventory 的 archive_member 可定位；符号片段时间是四分音符拍位。

从文档根目录运行 `py scripts/validate_dataset.py`；`py scripts/fetch_dataset_sources.py --verify-only` 只核对清单；`py scripts/prepare_graphrag_seed.py` 从本地原件重整数据，无模型调用。重新整理后 SQLite 字节或哈希可能变化，需刷新交付清单。

详见 [数据清单](../docs/20_开发前数据集收集清单与使用说明.md)、[数据入库规则](../docs/21_GraphRAG数据规范与入库审核方案.md)。
