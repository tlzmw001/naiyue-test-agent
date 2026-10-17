# Lesson 53：关联ID、事件序号与数据合同

> 第四章第2主题，2026-10-12。已核对当前代码与确定性证据，用户本人走读/复述未评估。主要讲定位与关联，turn生命周期留第6主题、会话执行留第5章。

## 统一外层与按类型变化的内层

RuntimeEvent为冻结dataclass：event_type/session_id/run_id/sequence/timestamp/data。session/run属于本次发布器；sequence在该发布器接受类型正确的事件时递增，timestamp为UTC ISO时间；data为具体事件dataclass，不是随意字典。模型/工具/消息/turn的专属ID只在相关payload字段出现，不能假设每条事件都有所有ID。

| 标识 | 问题与来源 |
|---|---|
| session_id | 哪个逻辑会话；应用创建，多个run可共用 |
| run_id | 哪次运行；应用为本次执行提供，Publisher使用它关联所有观察 |
| model_id | 配置中的哪个模型，如A；可被多次调用 |
| model_call_id | 哪一次逻辑模型调用；_prepare生成，started/finished同ID，不等于底层HTTP attempt |
| tool_call_id | 模型提出的哪次工具调用；模型/适配器提供，ToolMessage与工具事件保留它 |
| message_id | 哪条消息；观察事件来自消息对象id，不由观察器伪造；修订可保留ID |
| turn_id | 本项目一轮主模型响应及对应工具批次；TurnObserver生成，普通两模型循环为两个turn，审批续接另讲 |

示意短别名：session S下run R包含turn T1(model_call M1，model A，AI A1提出probe-1，Tool TM1回应probe-1)及T2(M2，model A，AI A2回答)。S/R/M/T/消息ID用别名表达实际运行生成值，不冒充真实日志。model_call_id与assistant_message_id、tool_call_ids与tool_message_ids通过turn结束数据建立桥接，不混成一种ID。

## sequence的作用与分支

顺序按同run发布器的接收/编号定义，不是全应用时钟，也不是工具执行顺序或持久保存证明；并行工具事件交错合法。订阅过滤后序号有间隔不等于丢失。新run新Publisher从0开始，首条为1；消费定位须携带会话/运行范围。完整订阅缺口要结合过滤、交付故障和保留窗口判断，留后续。

当前元数据过滤用例序号1,4,5,7,8,12,13,16；完整消息循环用例收集全部观察事件，seq从1连续。两个测试不是同一run，字段/ID不跨它们硬配。

数据合同：构造payload时检查字段，发布时_PAYLOADS要求event_type对应准确type(data)。给tool_call_finished传ToolCallStartedData时记录event_payload_invalid、不发布/不加序号；最小临时探针断言通过。不要把CheckedPublisher测试辅助器的额外配对/生命周期检查当生产Publisher内置功能。frozen和snapshot_json等已有契约保障受支持数据形状，不把消息对象或原始SDK响应直接当任意payload。

## 测试入口和证据

- 复用上一节tests/test_model_tool_subscriptions.py::test_one_tool_loop_has_correlated_immutable_safe_events看模型/工具调用配对与过滤序号，本轮未重复运行。
- 本轮tests/test_application_message_events.py::test_tool_loop_publishes_all_content_once_and_default_logs_are_metadata验证事件message_id等于result消息id、Tool结果call_id配对、完整订阅seq连续。
- 本轮tests/test_run_events.py::test_separate_runs_and_applications_do_not_share_observers验证发布器/订阅范围及每run从1编号。

```bash
.venv/bin/python -m pytest -q tests/test_application_message_events.py::test_tool_loop_publishes_all_content_once_and_default_logs_are_metadata tests/test_run_events.py::test_separate_runs_and_applications_do_not_share_observers
```

本轮2 passed in0.72s；另payload错配/不消耗序号探针PASS。没有生产/测试实现变更，不是实网/持久交付保证。

## 单步观察

优先沿上一节元数据测试入口。测试末看session、events[0].run_id、[(e.event_type,e.sequence) for e in events]；starts/ends产生后看[e.data.model_call_id for e in starts/ends]，同model_id A对应两个model_call_id。tool_start/tool_end产生后看data.tool_call_id；ToolMessage的id与tool_call_id分开。

publish当前帧event_type/data/self._sequence；_deliver帧event.session_id/run_id/sequence及getattr(event.data,'model_call_id',None)、getattr(event.data,'tool_call_id',None)、getattr(event.data,'message_id',None)。不假设publish帧存在局部event变量，不在WATCH重执行业务函数。

下一主题发布器如何保证交付顺序、处理重入和监听故障；第一主题publish/_deliver分工有用户自述理解，复杂机制只在本主题之后展开。
