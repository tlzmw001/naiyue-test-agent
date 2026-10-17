# Lesson 55：完整消息快照、基线、修订与移除

> 第四章第4主题，2026-10-12。用户确认保留现有发布实现，后续自述理解完整消息观察。消息观察5个单元经助手核验，本人走读/独立实现尚未评估；delta分片见Lesson56。

## 输入与输出

MessageStateObserver输入已得到的完整消息集合，形成显示用快照事件，不执行模型、add_messages或存储。同步接线invoke_with_message_observation消费根图values并调用observe；异步入口相同责任，分片另由_StreamState.delta处理。

输出message_completed/revised(MessageSnapshotData)、context_messages_changed(ContextMessagesChangedData)、message_snapshot_unavailable。completed表示完整消息显示快照可用，不等于工具成功、任务成功或checkpoint已确认。ToolMessage.status=error也可被完整观察。

## 分支与已有测试

| 条件 | 结果 | tests/test_message_observation.py中的用例 |
|---|---|---|
| 此ID无已记录hash | 发布message_completed并记录hash | test_snapshot_is_independent_frozen_projection_not_provider_wire |
| 此ID的显示快照变化 | 发布message_revised并更新hash | 同上；test_same_id_changed_from_baseline_is_revision |
| 此ID快照相同，即使新Python对象 | 不重复发布 | 第一个用例的model_copy(deep=True) |
| seed历史基线 | 记录hash/ID，不发completed/revised | test_seed_skips_history_and_compaction_reports_removed_ids_only |
| 可信身份集合中旧ID消失 | 发removed/current IDs及reason | 同上 |
| 无ID/身份重复/不支持类型或正文投影失败 | 观察缺口，不伪造该条快照；同类诊断去重 | test_unavailable_projection_does_not_invent_content_or_removed_ids |
| 工具错误消息 | 保留错误正文、tool_call_id与status，排除artifact | test_tool_error_is_complete_content_and_keeps_tool_call_identifier |

## 数据生产与比较

_snapshot选择id/type/content/is_context_summary，按类型增加tool_calls/invalid_tool_calls或tool_call_id/status等；summary标记为受支持的additional_kwargs字段。response_metadata、无关additional_kwargs与artifact不整体复制。正文/工具参数仍用于可信显示，不把快照当公开脱敏日志。

row→确定性JSON(sort_keys=True,allow_nan=False)→UTF8 bytes→SHA256及字节长度→冻结MessageSnapshotData。snapshot_json是字符串，修改原消息嵌套内容不改变既发快照；消费者按需json.loads。hash比较的是所选字段，供应商元数据变化不必导致revised。

观察器只留_hashes(id→hash)、_ids(最近可信集合顺序)、_unavailable(已报告诊断key)，不持有完整消息历史或改写图状态。移除不删除原文件/回滚副作用。身份信息不可信时抑制移除结论；正文序列化失败时ID仍可能可信，不能一概说全部投影故障都禁用移除。

## 主要走读：一次完整测试含三个分支

[tests/test_message_observation.py](../../../tests/test_message_observation.py)::test_snapshot_is_independent_frozen_projection_not_provider_wire：AI id=ai-1，PRIVATE_BODY和工具args为合成输入。第一次completed→修改原内容/args后revised→深拷贝相同内容后不新增事件。初始observer辅助已发布run_started，因此最后events长度3代表run_started＋completed＋revised。

测试变量state其实是MessageStateObserver对象，不是AgentState；传给state.observe的消息列表才是观察输入。观察主体的方法与第二章普通故障隔离helper observe(...)分工不同。

```bash
.venv/bin/python -m pytest -q tests/test_message_observation.py::test_snapshot_is_independent_frozen_projection_not_provider_wire
```

本轮整个文件5 passed in0.80s，无生产/测试实现修改。上述单位只检消息快照/比较合同；实际图接线由已有tests/test_application_message_events.py覆盖。完整可见与保存确认分开，test_complete_message_visibility_is_not_saver_confirmation是已有集成入口，本轮未重跑。

入口断点state,events=observer()。辅助创建后events仅run_started。observe帧看baseline、message.id、previous、data.snapshot_sha256[:8]（创建后），判断后看self._hashes/self._ids；_snapshot帧看row/encoded，测试帧看[(e.event_type,e.sequence) for e in events]、json.loads(data.snapshot_json)。data是测试先前快照，之后revised看events[-1].data；不要把仍保留的旧data误认新版本。

下一主题delta→草稿→完整校准，此时再本人走读之前暂缓的delta测试。当前publisher策略保持，用户独立实现未验收。

## 类型集合是否过度写死：定向核验

用户提出_SUPPORTED/类型判断是否hard code。_SUPPORTED是具体类型元组，_PAYLOADS是事件类型到payload类的映射；当前属于显式协议/显示适配边界，不是部署参数。新普通工具返回现有ToolMessage即可，不要求按工具名增加消息分支。

实际限制：type(message)精确检查拒绝自定义子类；新增真正消息语义须定义投影/传输/消费者合同，不能凭“通用Agent”预建注册平台。临时断言证明AIMessageChunk是AIMessage子类却不在_SUPPORTED；改成isinstance会让分片通过第一道完整快照检查，后续message_type校验仍可能拒绝，不把一行改动当全部护栏。

本机Pi a69bef789：AgentMessage由标准Message与CustomAgentMessages联合，应用可扩展接口；模型调用在agent-loop中await config.convertToLlm，coding-agent转换器按role switch，未知default返回undefined再过滤。它同时使用显式类型集合与扩展转换入口。AgentX3fcfa0e：MessageType为TEXT/TOOL_CALL/RAG阶段等产品枚举，历史消息角色另以isUser/AI/System分支转换成框架消息；不可把产品类型和LLM角色当同一字段。仅源码核验。

当前判断保留现有协议集合；实际新类型出现时按消费者合同扩展适配。字段散落重复、业务工具名侵入通用链或每次扩展需改多处才值得独立整理。本轮未修改白名单、枚举或生产接口；delta继续按现有类型边界学习。
