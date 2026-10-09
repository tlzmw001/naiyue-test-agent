# 消息、状态与保存时点探针（2026-10-08）

范围：无网络研究，不改产品运行代码。当前本机 LangChain 1.4.2、LangGraph 1.2.12，使用现有应用装配、假模型、内存 checkpoint 和临时目录。脚本是研究产物，不是已实现消息订阅，也不称为产品 TDD。

命令：`.venv/bin/python -m scripts.probe_message_state_boundaries`

脚本以研究代理消费同一个 compiled agent 的同步 stream，收集 updates/values/tasks，再按照现有 invoke 的逻辑返回最终 values 和 __interrupt__。没有重新执行一轮来获取轨迹；正式适配器仍须用失败测试验证所有返回值/异常兼容。

## 当前事实

| 场景 | 实际观察 | 设计含义 |
| --- | --- | --- |
| 正常工具循环 | human → human/ai → human/ai/tool → human/ai/tool/ai；两次 accepted 时已观察 AI 数量为 0、1 | accepted 早于本次新 AI 的状态快照，不等同消息状态可见 |
| follow-up | 同一 run 出现 human/ai/human/ai，两次 model task | 用户请求次数、run 与 turn 数量不能混用 |
| 审批拒绝 | 暂停已有 human/ai；恢复时 AI id 相同，之后出现人工 error ToolMessage 和新 AI | 不重发历史 AI，拒绝结果必须进入内容观察；原 turn 跨 run 未完成 |
| 模型失败 | 初始 human 已进入 values，没有成功 AI；原 RuntimeError 上抛 | 失败前已确认输入也应可观察，不能只扫描正常最终结果 |
| 明确溢出恢复 | 初始 5 条历史，最终出现 human[summary]、保留后缀与新 AI；旧前缀 ID 消失 | summary 是上下文消息，不能渲染成新用户输入；不是单纯 append |
| 结构化输出 | human/ai/tool 同一步可见，structured_response 保持 Pydantic 对象；没有 tools 节点 | 完整消息不能只从工具 wrapper 采集 |
| steering | 工具期间接受后，下一模型前出现 human/ai/tool/human/ai | 入队与消息实际进入状态不同 |
| turn 后停止 | human/ai/tool 后 stopped；只有一次 model task | 不开始下一 turn，当前工具处理可完整结束；不是活动取消 |
| 顺序工具致命错误 | values 最后只有 human/ai；get_state 最新视图含额外 tool；指定 checkpoint 的视图仍只有 human/ai | 成功/跳过任务写入可能在 pending writes，不应把两个视图混为一次主状态提交 |
| 延迟保存 | 在实际应用装配图中阻塞 AI checkpoint.put，消费者已观察 AI values；释放后保存完成 | 当前状态可见不保证 saver 保存已确认 |

顺序错误探针里，待写 ToolMessage.id 已分配，连续两次最新 get_state 的 ID 相同；此结果只针对当前内存后端样本，不是所有 saver 的跨重启保证。

## 两次保存探针失败的原因

最初使用无后置节点的最小 create_agent 图，最终 AI 的 values 被排队，但图退出会等待后台保存，消费端未能在阻塞保存时读到最终快照。第一次断言失败，第二次仅补诊断，确认 saver 已进入、AI values 尚未交付。源码定位后改用项目实际带 after_model/after_agent 的装配图，将 AI 作为中间步观察，第三次通过；没有第三次失败，也未改变断言来掩盖结果。

不能推导“每个 values 都一定早于保存”或“一定晚于保存”。_loop.after_tick 先应用 channel writes、生产 values 再调度 checkpoint；stream 消费何时收到还受退出等待影响。

## 框架机制与一手依据

- `langgraph/pregel/main.py` 的 invoke 默认内部迭代 stream(updates+values)，收集最新 values，并把 updates 的 interrupt 合并到传统返回字典。正式产品可做薄适配读取状态，但必须保持 output_channels、结果对象、异常及暂停返回，不自己做 reducer 或图调度。
- `_loop.after_tick` 在 step 边界调用 apply_writes 后生产 values；这比任意 wrapper handler 返回更接近状态观察点。
- `get_state` 未指定 checkpoint_id 时应用 pending writes；显式 checkpoint_id 时不应用同样的 pending 叠加。最新恢复视图并非主 checkpoint 的完整 channel_values。
- API `stream` 是同步迭代；不因此获得 ainvoke/astream、token 展示或活动取消。v2 在这里是 stream-mode 的 StreamPart 封装，不是实验性 stream_events v3。

官方：[状态流模式](https://docs.langchain.com/oss/python/langgraph/streaming)、[检查点与持久化](https://docs.langchain.com/oss/python/langgraph/persistence)。源码以本机安装版本为准，公开文档不能替代本机行为证据。

## 下一片建议

先独立接完整消息观察，再做 turn。同步状态适配器消费 values，失败结束时只读最新恢复视图补充已知结果，并显式标记 visibility；不把 pending 视图当作完整主状态已保存，不执行/重放任何任务。

历史 ID/可见内容快照作去重基线；同 ID 内容变化单独修订事件；压缩删除属于 context_messages_changed，不表示用户会话历史被删除。快照是展示投影，不用它恢复模型运行；默认日志只元数据。turn 跨审批的 continuation 与失败/停止规则另片定契约。

## 实际结果与限制

最后完整输出：`message_state_boundaries=PASS cases=10 network=none product_changes=none`。每个场景输出角色/计数/布尔事实，没有提示词正文、文件正文、原异常内容或环境凭证。临时目录由脚本创建并清理。研究复用了现有 recovery 测试夹具，无依赖/配置变更。

没有正式消息消费者、跨重启恢复、生产 saver、任意多模态/非 JSON artifact、并发状态变更或完整 turn 事件证据。605 个既有测试是之前产品基线，本轮不冒充新功能验证。
