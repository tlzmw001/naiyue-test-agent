# Lesson 12：Steering 消息怎样进入同一次运行

状态：2026-10-01，已准备并讲解；用户理解反馈、独立复写与面试追问尚待验证。对应 [Spec 022](../specs/022-synchronous-steering.md)。

## 一个具体输入

会话 `s1` 的一次 `run` 已进入工具 `probe`，工具尚未返回。另一个线程依次调用 `app.steer('s1', '补充条件 A')` 和 `app.steer('s1', '补充条件 B')`。此时应用会话里的 `steering_queue` 是 `deque(['补充条件 A', '补充条件 B'])`，框架的消息状态尚未含这两句。工具结束后，框架先把 `ToolMessage` 回填到状态，再进入下一轮 `before_model`；两句才变为 `HumanMessage` 并按顺序加入消息状态。

测试中的最终类型顺序为 `human → ai(tool call) → tool → human(A) → human(B) → ai`。这是本机假模型与线程测试证据，不是在线供应商中途输入的证据。

## 代码地图与执行顺序

1. `application.LocalApplication.steer` 将输入转给 `SessionService.steer`。后者先校验非空字符串，再在同一把锁内检查会话正在运行且接收窗口仍开启，然后 `append(text)`；返回只表示入队。
2. `SessionService.run` 在启动时标记 `busy`、打开接收窗口并生成本次 `run_id`。`invoke_agent_run` 将 `session_id/run_id` 放到 `context`，把内部 `thread_id` 放到 LangGraph `config`。
3. 组合入口 `build_local_application` 把服务的 `drain_steering` 和 `finish_steering` 方法作为回调传给运行层。运行层没有导入应用层。
4. `@before_model` 注册的 hook 从 `runtime.context['session_id']` 找到对应会话，`drain_steering` 原子地复制并清空队列，返回 `list[str]`。hook 把每个字符串构造成 `HumanMessage`，返回 `{'messages': [...]}`。LangGraph 的 `AgentState.messages` 使用 `add_messages` reducer 合并更新，然后模型调用读取更新后的状态。
5. 若模型没有工具调用，框架进入 `@after_agent(can_jump_to=['model'])`。`finish_steering` 在锁内检查剩余队列：非空时 hook 返回新消息和 `jump_to='model'`，同一次运行再次进入模型路径；空时在锁内关窗、hook 返回 `None`，图正常结束。关窗之后即使 `busy` 暂时仍为真，新 `steer` 也会被拒绝。
6. `run` 的 `finally` 清掉任何仍待取的消息并释放 busy。未取消息会记 `steering_discarded`；已经取出但后续模型失败的消息不重新排队。`steering_injected` 只说明消息已从应用队列交给 hook 用于图状态更新，不单独证明模型成功完成。

## 理解检查

若用户在工具尚未返回时调用 `steer('s1', '补充条件')`，此时 `steering_queue` 与 LangGraph `messages` 分别有什么？工具返回后的下一次模型调用会看到哪种消息对象？若最终模型已经返回、`after_agent` 检查到空队列并关窗，此后提交同一消息会怎样？

验证命令：`.venv/bin/python -m pytest -q tests/test_application_steering.py`；2026-10-01 本次复核结果 `4 passed in 0.77s`。这验证接线与顺序，不代表用户已独立实现。

## 追加辨析：三个 hook 的时点

用户追问 `before_model`、`after_agent` 与先前选模 middleware 的关系。本机 `langchain 1.4.2` 的 `create_agent` 将节点式 hook 编译为图节点；官方文档给出的普通顺序是 `before_model → wrap_model_call → 模型 → after_model`，自然终局时再到 `after_agent`。本项目 `inject_before_model` 是节点式 hook，读取队列并返回 `{'messages': [...]}` 更新图状态；先前 `select_model` 属于 `@wrap_model_call`，收到 `ModelRequest` 与 `handler`，调用 `handler(request.override(model=model))` 来替换本次模型并围绕结果做观测/拒绝。二者每次模型调用前都会触发，但前者在模型节点**之前**，后者在模型调用**周围**。`after_agent` 不在每次模型响应后执行；无工具继续路径时到达自然终局边界，若本项目返回 `jump_to='model'` 则图会重新进入 `before_model`，之后也可能再次访问 `after_agent`。这段已讲解，用户理解反馈尚未收到。

## 追加辨析：返回新消息为何不会覆盖工具结果

用户追问 `before_model` 只返回 `{'messages': [HumanMessage(...)]}`，为何可以与原工具消息合在一起并进入第二次模型调用。本机 `AgentState.messages` 声明了 `add_messages` reducer；节点式 hook 的返回字典是**状态更新量**，框架把旧列表作为 `left`、新增列表作为 `right` 交给 reducer。工具节点此前也以 `ToolMessage` 更新同一 `messages` 字段。新消息 ID 与已有消息不同时，`add_messages` 将其追加；相同 ID 则替换同 ID 消息，因此不是对所有输入都无条件 `list.extend`。

不落盘的 LangChain 1.4.2 假模型探针实际打印：初次 `before_model` 看见 `[human(original task)]`；首次模型请求工具，工具回填后第二次 `before_model` 看见 `[human(original task), ai(tool call), tool(tool result)]`；hook 返回 `[human(extra condition)]` 后，第二次模型输入为 `[human(original task), ai(tool call), tool(tool result), human(extra condition)]`；最终状态再加 `[ai(final answer)]`。探针退出码 0。工具节点的默认路由本来就回到模型入口（先经过 `before_model`）；hook 的返回值负责补状态，**不是**启动第二次模型调用的命令。这一观察只证明本机假模型链路，未证明真实供应商或用户独立掌握。

## 源码链：为什么框架知道要合并

用户要求追本机安装版源码，版本为 LangChain `1.4.2`、LangGraph `1.2.12`。`langchain/agents/middleware/types.py` 的 `before_model` 装饰器用 `type(...)` 动态建立 `AgentMiddleware` 子类，把被装饰的函数包装到 `before_model` 方法；此处只登记 hook，不决定消息如何合并。`langchain/agents/factory.py` 的 `create_agent` 扫描实现该 hook 的 middleware，构造 `RunnableCallable` 并 `graph.add_node`；工具后的 `loop_entry_node` 指向第一条 `before_model`，最后一个 `before_model` 节点连向模型节点。

合并规则来自 `langchain/agents/middleware/types.py` 中的 `AgentState.messages: Required[Annotated[list[AnyMessage], add_messages]]`。`create_agent` 把解析后的 state schema 交给 `StateGraph`；`langgraph/graph/state.py` 的 `_get_channels` 用 `get_type_hints(..., include_extras=True)` 取到 `Annotated` 元数据，`_is_field_binop` 发现双参数 reducer 后建 `BinaryOperatorAggregate(..., add_messages)`。运行时 `attach_node` 的 `_get_updates` 将 hook 返回 `{'messages': [new_human]}` 变成字段更新；`pregel/_algo.py` 的 `apply_writes` 把该更新交给 `channels['messages'].update(...)`；`BinaryOperatorAggregate.update` 调用 `self.operator(self.value, value)`，这里即 `add_messages(old_messages, [new_human])`。`add_messages` 对新消息 ID 追加，对相同 ID 替换。没有 reducer 的普通字段回退到 `LastValue`，更新会覆盖旧值。

本机只读探针通过框架自己的 `_get_channels(AgentState)` 得到 `channel_type=BinaryOperatorAggregate`、`reducer=_add_messages`；直接读取未解析的 `AgentState.__annotations__['messages']` 曾误得 `LastValue`，因源码采用 `get_type_hints(..., include_extras=True)` 先解析注解，不能把原始注解字符串当成实际 schema。最终正确探针退出码 0。上述为安装版源码与本机探针，不表示用户已经掌握。

## 追加辨析：`after_agent` 返回的 `jump_to`

用户问 `jump_to='model'` 的作用。它与 `messages` 同在 hook 返回字典中，但职责不同：`messages` 由 `add_messages` 合入消息状态；`jump_to` 是 `AgentState` 中标为 `EphemeralValue` 的短暂路由字段。`@after_agent(can_jump_to=['model'])` 让 `create_agent` 为该节点编译可跳回模型入口的条件边；源码 `_add_middleware_edge` 读 `state.get('jump_to')`，`_resolve_jump('model')` 返回 `model_destination`，即当前图的 `loop_entry_node`。若已注册 `before_model`，该入口是第一个 `before_model` 节点，再走到模型；不是直接绕过模型前的 middleware。队列为空时 hook 返回 `None`，条件边取默认 `END`，正常结束。`EphemeralValue` 在无新写入的后续步清除，避免永久携带一次跳转命令。此段已讲解，用户理解反馈尚未确认。
