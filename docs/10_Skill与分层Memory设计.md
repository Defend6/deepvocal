# DeepVocal Skill 与分层 Memory

## Skill 是受控模板

Skill是版本化任务模板、输入输出schema和允许工具组合，不是自动下载并执行的代码。发现只注入名称、描述、触发条件、ID和版本；选中后验证published状态、内容哈希、当前agent工具交集和上下文预算再加载正文。Skill不能增加网络、文件、系统或管理员权限。

生命周期 draft→candidate→reviewed→published→retired，rejected拒绝；critic审查不等于发布。脱敏轨迹只保留可复用步骤，不能含录音路径、身份、密钥、签名URL或会话事实。候选静态检查→假工具/合成输入回放→维护者明确批准；维护者不要求是声乐教师，也不把批准叫专业认证。发布内容修改必须新版本。

LazyRegistry缓存键包含skill_id/version/manifest_hash/scope/owner/registry_revision。缓存正文不含具体用户上下文，每次使用前复核退役/撤销；旧运行可保留审计哈希，但新执行不能继续用已退役版本。只比较同一tokenizer、模型、输入和工具schema的 eager/lazy 注入量；用户另行做效果实验，当前不声称节省50%～70%。

## 上下文预算

总输入预算由实际模型context_limit减output_reserve和safety_margin得到，再扣system、工具schema、当前消息与必要状态。其余初始配额历史4000、证据5000、记忆1000、Skill2000只是上限，不能相加后忽略基础提示。发送前用实际tokenizer重新计数；优先保留当前动作/权限和必要证据，裁历史、冗余记忆与未选Skill，仍超限则澄清或拒绝，不静默丢引用。

## L1 L2 L3 与事实类别

L1是原始提交事件，append-only表示业务日志不原地改写，不表示永远不能删除隐私。L2是具体可追溯事实：用户自述、明确偏好或测量观测，各有source、occurred_at、valid_from/to、confidence_class、revision、status；L3是对有效L2的目标、偏好、进度摘要，保存来源关系。用户“高音紧张”只能记为自述，不能升级为“声带闭合不足”。

明确修改偏好是confirmed；模型抽取进入candidate；声学测量是observed。L3可汇总已确认偏好和有来源的数值进度，不能把模型猜测变成人格/疾病事实；candidate需确认才作为稳定画像。私有PracticeRecord只来自真实授权行为。

## 异步与立即可见

用户提交业务成功时，同事务写L1、递增owner memory_revision，并把本轮明确偏好同步放入会话状态；原业务结果不等待LLM压缩。后台记录input_high_watermark与expected_revision，抽取L2再生成L3。读取时取L2/L3快照，加未处理L1中明确偏好/本轮必要状态的有界补丁，按source和revision去重，不能等待后台才看到刚修改的分钟。

未处理L1最多读取最近100条/既定token预算，超过则只用明确的当前会话补丁并标memory_pending，不把所有原始事件喂给模型。L1同一业务资源可多次变更，幂等唯一键含event_kind+source_version；同一事件重试稳定event_id。

## 冲突与并发压缩

按业务生效时间和revision比较，不简单按后台到达时间覆盖；用户当前20分钟优先于旧10分钟。压缩提交前CAS检查owner revision、用途授权revision和删除ledger；输入变化则丢弃旧候选并重跑有界任务，不覆盖较新事实。不能一边删事实一边让后台从同来源再次提取。

memory_fact_sources与memory_profile_sources保存owner一致的来源链。删除事实写稳定tombstone键（owner+source family+semantic key），后续压缩先查ledger。来源被删/过期/撤回时，立即从读取中过滤并异步重建L3、私有图、向量、缓存和checkpoint；备份恢复后先重放ledger。

## 用途授权和保留

分析、保存练习、跨会话记忆分别授权；无跨会话授权不消费长期整理任务，当前会话可只保留完成该请求所需状态。research_improvement不因个人使用默认开启。撤销跨会话用途阻断新更新并提供清除已有派生记录入口。原件与报告各有可选期限，缩短按原created_at重算，不以每次访问滑动延寿。

事实确认/更正API带version，冲突409；删除马上不可用，后台清理未完成不得伪报已经彻底删除。技术日志不写原始声音/完整提示/令牌，短期脱敏trace按配置过期。
