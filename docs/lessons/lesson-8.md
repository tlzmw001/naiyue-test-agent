# Lesson 8：最小 CLI 如何接进 Agent 运行链路

状态：2026-09-30，代码地图已准备；本轮讲解 CLI 入口的装配、命令分支、普通输入、输出与异常路径，并以现有假模型测试走一遍数据流。代码和测试由助手完成；目前只记录“已讲解”，尚无用户复述、独立实现或面试追问证据。

## 一条具体数据流

以下 `A`、`B` 和输入文字是说明机制的示意值；实际测试使用假模型的相应消息，真实 CLI 验证使用合成文字 `ALPHA`、`BETA`。

1. `main(['--config', 'models.json'])` 从路径装配一次 `LocalApplication`，以目录默认模型 `A` 创建一个 `session_id`。CLI 不自行创建第二个 Agent。
2. 用户输入 `第一问`，CLI 调用 `app.run(session_id, [('user', '第一问')])`。应用层找到内部 `thread_id`，运行层把该 ID 交给 `create_agent` 的 checkpoint 配置。middleware 通过会话服务解析当前模型 `A`。返回的最后一条 `AIMessage.text` 被打印。
3. 输入 `:model B` 只更新该会话的模型选择；CLI 不重建 Agent 或更换 `thread_id`。
4. 输入 `第二问`，CLI 仍只发送这一条新用户消息。框架按相同 `thread_id` 取回先前消息，middleware 在新模型调用边界解析出 `B`。测试中 B 实际看到系统消息、第一问、A 的回答和第二问。
5. 模型调用抛错时，运行层记失败事件，CLI 只输出异常类型；会话的 busy 状态由服务释放，下一条输入可再运行。`:quit` 仅在一次同步调用结束、CLI 重新读取输入时生效。

## 当前代码地图

| 文件 | 阅读问题 |
| --- | --- |
| `naiyue_test_agent/application/cli.py` | 哪一行只装配一次应用？输入何时转为 `('user', text)`？`:model` 为什么不会立即调用模型？ |
| `naiyue_test_agent/application/__init__.py` | `session_id`、`thread_id` 分别由谁持有，`run` 如何关联它们？ |
| `naiyue_test_agent/agent_runtime/__init__.py` | 模型选型与 checkpoint 分别在哪个框架边界生效？ |
| `tests/test_application_cli.py` | `b.seen` 的四条输入消息从哪里来？失败测试能证明哪些信息未被输出？ |

待复习：真实 CLI 的两轮输出证明入口接线和网关调用成功，不证明模型正确利用历史，也不证明停止、steering、工具或业务用例质量。下一课堂宜先逐行追 CLI 的装配、命令分支和普通消息分支，再让用户解释为何第二次只传新消息却仍有旧历史。

后续提问：用户确认 `:model` 是否能跨 OpenAI 与 Claude 供应商切换。当前代码的 `ModelCatalog` 用模型 ID 索引不同 `BaseChatModel` 对象，`SessionService.select_model` 更新会话选型，middleware 在下一模型调用边界覆盖请求模型；这是设计和本机确定性接线证据。现有私有配置只有一个 HeiyuCode 模型 ID，尚无同一真实会话跨 OpenAI/Claude 的运行证据。这个提问不计作独立实现或面试追问能力。
