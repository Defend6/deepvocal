# DeepVocal GitHub 相似项目调研

## 调研结论

本次检索没有核实到一个可以直接替代全部 DeepVocal 能力的仓库。比较接近业务体验的是声乐教练与歌唱分析应用；GraphRAG、歌声合成、换声和 F0 提取分别已有可借鉴组件。建议自己实现四 Agent、用户资产和学习闭环，把成熟算法通过适配器接入。此结论仅限本次可检索材料，不能推断全网不存在同类项目。

检索及元数据核对日期为 2026 年 11 月 3 日。以下依据公开 README、GitHub 简介、默认分支提交及许可证字段；没有在本机运行这些仓库，不能保证开箱即用。完整 SHA 与固定引用见 sources/github-snapshot.json。

## 仓库比较

| 仓库 | 相似部分 | 许可证字段 | 主要缺口或限制 |
|---|---|---|---|
| [CHamilton02/harmonai-vocal-coach](https://github.com/CHamilton02/harmonai-vocal-coach/tree/e68a012ab5c1e5a9ac209536b653fc6e148c9d28) | 业务体验参考 | API 未识别，复用前人工确认 | 仓库简介为 Gemini 多模态声乐教练，Express 与 Vue；可参考交互与反馈组织，不能据此证明声学评分有效。 |
| [akhilvydyula/predict-voice-quality](https://github.com/akhilvydyula/predict-voice-quality/tree/78e30f766efde6c8c66d9e5a47441b44312d216c) | 录音及音高可视化参考 | MIT | 仓库简介强调实时歌唱分析、声乐工具和端侧音高检测；不覆盖完整 Agent 及 GraphRAG 闭环。 |
| [Alea4jacta6est/openai-vocal-coach](https://github.com/Alea4jacta6est/openai-vocal-coach/tree/7dcd97e3872eeec4f9605096044095a23ec075dc) | 音频建议和提示词参考 | Apache-2.0 | README 展示 HTML 与音频转录、建议流程；是较简单的建议应用，不应作为精准歌唱测评后端。 |
| [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG/tree/aaebfe1bb8d55deb6a4304d357a3bf4c92fcd40a) | 图检索工程参考 | MIT | 提供图结构检索和多存储接入文档；可研究实体关系、索引和评测，不等于本项目指定检索流程已实现。 |
| [openvpi/DiffSinger](https://github.com/openvpi/DiffSinger/tree/e4740fbd455cde1101063246b868f45828f23e2c) | SVS 候选 | Apache-2.0 | 歌声合成生态，需具体声库、模型和音素资源；适合有旋律条件的生成，不能直接假定零样本克隆。 |
| [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/tree/81eed5e8f68b6bed1789f682fe78cdd324495afc) | SVC 候选 | MIT | 语音与歌声音色转换方向，需要输入干声并核对模型训练要求，不是纯歌词到旋律。 |
| [Dream-High/RMVPE](https://github.com/Dream-High/RMVPE/tree/a6db1cd7d26014aa739383367afd9bab57fc624c) | F0 候选 | Apache-2.0 | 官方 README 提供人声音高估计与推理入口；可试用于 F0 提取，但准确性需在本项目数据验证。 |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/tree/3c916d750575b22c464790e9774494d810ccd322) | 编排基础 | MIT | 截图要求的编排框架；参考官方状态和持久化机制，项目自己的权限与任务规则仍要实现。 |

## 推荐接入组合
业务交互参考 HarmonAI 与 predict-voice-quality；编排使用 LangGraph；检索可参考 LightRAG，但按本项目的 Milvus、Neo4j、权限与证据契约实现；音高先试 RMVPE；生成模块按 D-014 禁用，DiffSinger/RVC 仅作为理解未来已有服务输入的研究参考，不要求当前选型或试接。代码优先少量试接，不把多个互不兼容环境直接拼装到 API。

## 选型试验
当前仅对所需编排、检索、声学候选建立独立试验目录，固定上述 SHA，记录环境、许可证正文、最小输入和输出。先验证一个真实授权样本，再记录 CPU/GPU、处理时长、可懂度、音色、旋律保持和失败条件。通过试验后才写入应用依赖锁。上表的主分支 SHA 是调研快照，不等于生产推荐版本，也不等于下载了权重。

## 许可核验
GitHub license 字段为 null 时不能默认自由复用；有代码许可也不能推断权重与声库可以商用。记录代码 LICENSE、模型卡、训练数据与音色授权；保留署名、NOTICE 和再分发要求。未经许可不得把原仓库的音乐样例直接打包为本项目资产。

## 不建议的捷径
不把音频转录文本当成完整声学测评，不把普通 TTS 直接写成歌声合成，不把 RVC 的换声能力写成纯歌词到旋律，不把开源框架自带示例结果写为本项目效果。截图中的指标仍须由本项目冻结测试集重新测量。
