# LangGraph 编排与恢复

日期：2026-11-04。

POST /v1/assistant 接收 text、可选 action/input/input_asset_id 和幂等键。结构化显式 action 优先，未知 action 澄清；理论疑问优先于普通音频关键词，歌声生成意图要求独立分支。未明确意图不会随机调用工具。

SupervisorGraph 使用 TypedDict 状态与四个真正编译的 LangGraph 子图。每个子图经过 boundary，再执行固定授权工具；工具注册表按服务器端 Agent 白名单二次校验。客户端不能提供 tool_allowlist、owner 或 checkpoint thread ID。生成子图目前只返回 capability 与确认要求，不会调用付费生成服务。

checkpoint 使用 PostgreSQL AsyncPostgresSaver。线程键为服务端 owner:job 组合，重试读取同一线程，完成 checkpoint 不重复运行工具；进程退出后可重建 graph 恢复。业务任务状态仍由 jobs 表和 worker fencing 管理，checkpoint 不是任务所有权验证替代品。工作流节点有外部副作用时仍需业务幂等，checkpoint 不承诺外部服务严格一次调用。

Windows worker 必须使用 selector 事件循环（入口已设置），否则 psycopg async 连接失败。测试复现过该故障并覆盖实际持久化读取。

本节点 24 项测试通过，包括四子图执行、越权工具拒绝、owner 线程隔离、已完成工具不重跑、跨连接持久化恢复、任务系统故障场景。checkpoint 依赖有一项未来默认值弃用提示，不影响测试；后续依赖升级需用锁文件验证。

业务 handler 当前各自明确返回 disabled，后续节点逐个接入真实知识检索、工程声学分析和约束计划。不可将 graph 可执行解释成声乐业务已验证。
