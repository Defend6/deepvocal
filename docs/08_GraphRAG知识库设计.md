# DeepVocal GraphRAG 检索设计

GraphRAG 在本项目指 Milvus 语义召回、Neo4j 受限关系扩展和原文证据重排的混合检索，不要求使用某个同名开源框架，也不要求 One API 或特定模型供应商。embedding 负责文本/实体语义表示，Cypher 遍历依据 ID、关系和过滤条件执行。

## 中文材料与来源

当前仅中文原生正文可进入活动问答库；英文教材/术语表归档，不以中文label冒充正文。导入同时验证 language、中文正文、来源权利、用途和证据定位。研究模式允许相应非商业来源，但保持署名和分区；未获许可不能因本人研究自动公开再分发。

## 离线流程

解析/正文识别→源家族去重→分块→语言和许可筛查→实体/关系候选→逐关系原文支持校验→review_level=evidence_checked→嵌入与图谱投影→回读验证→原子激活。无教师要求，expert_verified 始终 false，研究级结论有明确边界。候选共现边不自动转 ADDRESSES，不允许从疾病名自动建立治疗推荐。

中文分块初始400～800字符、重叠80，章节/练习步骤尽量完整；遇图像依赖又没有图时标不可回答。生产配置一旦确定保存 hash，与已有166个英文准备片段的1400/180字符规则分别版本化。每一事实/关系保留源chunk、原文 span、限定唱法、适用场景、抽取/验证版本和状态。

## chunk 到实体映射

导入阶段保存 chunk_entity_links(chunk_id,entity_id,corpus_version,evidence_span,link_confidence)。检索时直接用稳定映射定位 Neo4j，不把 chunk 原文临时丢给模型让它猜任意节点。别名歧义（如声区与音域）保留候选和条件，不因字符串接近合并。query实体链接可以补充排序，但无法链接时仍可使用chunk映射。

## 在线预算与融合

1. 固定当前 owner、中文 corpus_version、用途许可与可见性；Milvus 召回top20，过滤在召回阶段执行。
2. 从top10 chunk的映射实体中，按问题相关性、显式实体和link置信排序，最多3个种子；达不到门槛就不扩展。
3. 只遍历 RELATED_TO/REQUIRES/ADDRESSES/ILLUSTRATES/SUPPORTED_BY 等审核白名单；每节点最多10个邻居、最多2跳、总50节点100边，每跳校验同版本和域。路径跨owner或过期边立即丢弃，LIMIT不能替代过滤。
4. 图路径回映原文chunk；无证据路径不可独立支撑回答。按chunk_id去重，向量/图两通道RRF合并，k=60、rank从1开始，每通道每chunk只计一次。
5. 最多40条统一rerank，top5最终Evidence，按token预算截取并保留准确原文位置；最后再检查当前删除/授权revision，防查询过程中撤回。

关系距离/来源可靠性用于有版本的排序特征，不能把未校准 confidence 说成事实概率。score相同按chunk_id稳定打破平局；配置变化记录retrieval_version，不能复用旧结果假装同实验。

## 回答与降级

回答中每个核心结论回指当前Evidence；校验引用存在还不够，还要核对所引用原文是否支持该陈述。译文、删节和限定条件不能丢。证据冲突时说明适用范围或无法确定。资料中的命令、恶意提示或“已获授权”不改变系统工具/权限。

Neo4j异常可降级到同一活动语料的向量证据，degraded=true；Milvus无效则拒答/澄清，不偷换英文库或其他用户缓存。缓存键含 owner/scope、data_revision、corpus_version、retrieval/model/config哈希、问题哈希，答案缓存不跨私有owner共享。

## 私有练声图谱与评测

PracticeRecord 只来自真实授权行为，初始0条；节点、边、索引和缓存均绑定owner。测量观测不推导生理成因。用户取消跨会话用途则不更新私有长期图，当前会话必要状态独立保留。

保留Vector、Vector+rerank、Vector+Graph、Vector+Graph+rerank导出协议和 hit@5/Recall@5/MRR@5 计算工具；人工100/200题和效果实验由用户另行安排，不阻塞功能实现。公式/过滤/映射/版本切换和反注入测试仍必须完成，合成6题不证明真实效果提升。
