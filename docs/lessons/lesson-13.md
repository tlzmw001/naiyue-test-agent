# Lesson 13：Follow-up 在自然结束时怎样续跑

状态：2026-10-01，已讲解；用户理解反馈、独立复写与面试追问尚待验证。对应 [Spec 023](../specs/023-synchronous-follow-up.md)。

## 一个具体输入与真实代码路径

会话 `s1` 的模型先请求 `probe` 工具；工具运行期间，另一个线程调用 `app.follow_up('s1', '完成后总结用例')`。`LocalApplication.follow_up → SessionService.follow_up` 校验非空字符串、活跃运行与输入窗口后，把原字符串放入该会话的 `follow_up_queue`。返回只代表入队，LangGraph `messages` 此时不含它。

工具返回 `ToolMessage('tool-result')` 后，框架继续进入 `before_model`；这个 hook 只调用 `drain_steering`，因此下一次模型输入仍没有 Follow-up。模型回答 `tool-answer` 且本来要结束时，`after_agent` 调用 `finish_pending_messages`：同锁内先选剩余 Steering，否则从 Follow-up 队头取一条，否则关窗返回空列表。取出的字符串由 hook 构造为 `HumanMessage`，返回 `messages` 状态更新和 `jump_to='model'`；框架先经过 `before_model` 再调用模型，在同一 run 内得到 `follow-answer`。

对应测试观察的最终类型顺序是 `human → ai(tool call) → tool → ai(tool-answer) → human(follow-up) → ai(follow-answer)`。模型第二次输入末尾是 `tool-result`，第三次输入末尾才是 `完成后总结用例`。示例文本是教学用代称；测试实际提交 `private-follow`。本机 `.venv/bin/python -m pytest -q tests/test_application_follow_up.py` 复核结果 `7 passed in 0.86s`，属于确定性假模型证据。

## 关键状态与边界

- `steering_queue` 和 `follow_up_queue` 均在应用会话里，不在模型消息对象里。两种文本最终都变成 `HumanMessage`，差别来自提交时选择的队列及取用时点。
- `finish_pending_messages` 只在自然终局选择一批：Steering 取当前全部；否则 Follow-up 用 `popleft()` 取最早一条，并包成 `list[str]` 供 hook 统一处理。下次自然终局才取下一条。
- `@after_agent(can_jump_to=['model'])` 注册可跳回模型入口的边；`{'messages': [...], 'jump_to': 'model'}` 中前者经 reducer 合入状态，后者控制下一步路由。空队列返回 `None`，图走正常结束。
- `run` 的 `finally` 清掉仍待取的 Follow-up 并记录脱敏丢弃事件。已取出的消息不重新入队，但失败时 checkpoint 的部分状态和下次恢复/去重尚未验证。
- 当前 CLI 没有运行中输入界面，不能从 CLI 直接试这条并发提交路径；真实在线模型、持久交付和业务效果均未由假模型测试证明。

## 理解检查

若工具执行期间先提交 Follow-up `F1`，再提交 Steering `S1`：工具结果后的下一次模型调用会先看到谁？该模型回答且本来要结束时，`after_agent` 会取谁？若此后没有新 Steering，`F1` 何时才进入模型输入？回答时区分“在应用队列中”和“已经写入 LangGraph messages”。
