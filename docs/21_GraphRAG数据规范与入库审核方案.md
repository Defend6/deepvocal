# GraphRAG 数据规范与研究级审核

本期不要求教师参与，按用户选择以知识库原文和规则进行研究级证据核查。raw→normalized→candidate→evidence_checked→active是操作状态；expert_verified是独立字段，当前false。候选不是自动active，研究级审核不称教学认证。中文、权利、证据支持和范围任一不满足就不激活。

## 元数据与稳定标识

source保存来源URL、作者、语言、许可/证据、使用目的、版本/日期和原件SHA256；document保存正文定位、规范化hash和语料版本；chunk保存原文Unicode码点左闭右开span、hash和分块配置。前端UTF-16偏移须转换，不能直接拿Python位置切JS字符串。图片依赖段落无图不可补猜。

生产UUID由固定namespace与source/version/locator的UUIDv5形成，在seed_id_mappings落账；知识来源/实体/关系各有稳定ID。源版本变化建立新corpus，旧引用不静默改指新文本。curve/audio保持原文件、声部/小节或archive_member/秒定位，符号拍位不能混为秒。

## 六类实体与关系

VocalConcept、Technique、AudioIssue、Exercise、SongSegment、PracticeRecord分类型schema，别名只是链接候选，不同唱法/适用条件不可合并成通用定义。每条语义边包含subject/object、predicate、source_chunk/span、条件、抽取和验证版本、review_level与scope/corpus。

CO_OCCURS_WITH只表示同片段命中，不作为生产教学语义边。LLM候选需原文精确定位、实体存在、关系白名单、否定/条件保留、跨owner检查和支持性核对；不能仅看模型confidence。证据不支持时拒绝，不“修成”ADDRESSES。Exercise推荐须有可操作步骤/适用/停止条件，时长缺失可标工程上限但不能伪称来源建议；高风险或临床练习不发布。

## 中文活动语料

source.language、document.language和正文抽查共同检查zh原生；中文label不改变英文来源语言。当前禁止自动翻译英文seed激活。对于无教师研究模式，生成回答仍标研究信息，LLM解释只依赖已检索原文，不能据声学量给个体病因诊断。

构建corpus_versions与Milvus/Neo4j投影均带同一不可变版本；建库事务不需要跨库原子，但激活前核对两投影完整、映射/权限/语言有效、来源未撤回。用CAS原子切活动指针，同一channel/scope仅一个active；失败保留旧版。回滚也服从当前删除/许可ledger，不能把已撤回内容激活回来。

## 删除与授权传播

公共知识与私人练声图分域，owner来自服务端，图遍历每跳过滤。撤回从source→document→chunk→entity/edge/link→索引→缓存→派生记忆和checkpoint传播。读路径立即过滤，物理清理后台。关系多来源只删除失效支持，剩余支持不足则停用，不因共享节点保留私人证据。

用户记录只按analysis/practice_storage/cross_session用途进入相应链路；不同用途不能互推，research_improvement默认关。永久tombstone或其最小化版本保留到所有备份删除期后，避免旧数据恢复；不在ledger存已删音频或原始敏感内容。

## 评测边界

独立人工gold由用户负责，问题/答案不得进入调参或Skill候选；音频按可验证歌手/会话/曲目分组，缺歌手ID不声称歌手独立；A1/A2是同片段两标注，不是两个独立声音。工程仍测试源位置、否定支持、去重、版本、权限、撤回与故障。已有小样本不足以证明中文流行演唱泛化。
