# Lesson 2：公开契约怎样变成结构化测试用例

学习目标：沿一条实际调用追踪公开文档文本、模型输入、`DiscountCase` 工具调用、`structured_response` 和验证错误。这里的“工具调用”是 LangChain 的**结构化输出工具**，没有执行折扣 HTTP 接口。

## 最小代码地图

| 入口 | 职责 | 本节要看清的边界 |
| --- | --- | --- |
| `naiyue_test_agent/test_agent/discount_case_design.py:15-48` | Pydantic 请求、预期与用例 schema | 结构约束不等于业务预期正确 |
| `naiyue_test_agent/test_agent/discount_case_design.py:57-76` | 提供折扣任务的提示词、schema 与工具清单 | 测试策略层依赖运行层，运行层不认识折扣业务 |
| `naiyue_test_agent/agent_runtime/__init__.py:72-91` | 持有 `create_agent` 装配入口，接入模型选择与运行 context | `create_agent` 拥有模型/工具循环；本项目只装配 |
| `naiyue_test_agent/test_agent/discount_case_design.py:79-96` | 发起任务，拿到结构化结果并做引用/ID 一致性校验 | 读取的是调用方给的公开文档字符串，不读取 SUT 源码 |
| `naiyue_test_agent/agent_runtime/__init__.py:24-69` | 每次模型调用前按 `session_id` 选择模型 | 下层不 import 测试业务模块 |
| `tests/test_discount_case_design.py` | 用假模型穿过真实 `create_agent`，观察正常与错误路径 | 假模型接线证据不等于真实模型质量 |

## 一个具体对象从哪里来

调用方提供 `public_doc` 和 `session_id='s1'`。测试假模型在收到 LangChain 传来的 `SystemMessage` 和 `HumanMessage` 后，返回如下形状的 `AIMessage`（省略了若干用例字段）：

```python
AIMessage(
    content='',
    tool_calls=[{
        'name': 'DiscountCase',
        'args': {
            'method': 'POST',
            'request_body': {'user_level': 'black', 'scene': 'campaign', 'stock': 0, ...},
            'expected_body': {'reason_code': 'USER_BLOCKED', ...},
            'evidence_quotes': ['Rules are evaluated by priority. ...', ...],
            ...,
        },
        'id': 'case-call-1',
    }],
)
```

这里 `DiscountCase` 不是业务 HTTP 工具。`ToolStrategy(DiscountCase, handle_errors=False)` 让框架把 schema 作为可调用的**输出格式**描述交给模型。框架解析 `args` 为 Pydantic `DiscountCase`，把它放到 `result['structured_response']`；同时生成配对的 `ToolMessage(tool_call_id='case-call-1', ...)`。本机无网络假模型运行实际观察到：

```text
model_input_types: ['SystemMessage', 'HumanMessage']
result_message_types: ['HumanMessage', 'AIMessage', 'ToolMessage']
tool_call_name: DiscountCase
tool_call_id: case-call-1
tool_result_call_id: case-call-1
case_type: DiscountCase
selection_records: [('s1', 'fake-A')]
```

`SystemMessage` 参与模型输入，但不在 `agent.invoke` 返回的普通消息历史列表里；模型的 schema 工具调用成功后，这次运行直接有 `structured_response`，不要求再产生一条最终自然语言 `AIMessage`。这与先前真正执行 `switch_to_b` 业务工具的轨迹要分开理解。

```mermaid
sequenceDiagram
    participant App as 调用方
    participant Design as design_discount_priority_case
    participant Agent as LangChain create_agent
    participant Model as 选中的模型
    App->>Design: public_doc, session_id=s1
    Design->>Agent: HumanMessage(公开契约+任务), context=s1
    Agent->>Model: SystemMessage+HumanMessage+DiscountCase schema
    Model-->>Agent: AIMessage(tool_calls=[DiscountCase args])
    Agent-->>Design: ToolMessage 配对; structured_response=DiscountCase
    Design->>Design: request_id 相同? 引用原文存在?
    Design-->>App: DesignResult(case, messages)
```

## 按执行顺序读核心代码

1. 策略文件第 15–48 行声明三层 schema。请求字段保留公开文档的枚举、非负和必填约束；预期响应要求字段齐全；用例再包装 HTTP 方法、路径、预期状态和引用。`extra='forbid'` 拒绝模型凭空添加字段。这里没有把正确答案 `USER_BLOCKED` 写进 schema，所以“类型通过”不意味着测试设计正确。
2. 策略文件第 51–54 行的 `DesignResult` 将验证后的用例与本次消息一起交给调用方；消息保留在内存中，当前还不是持久化轨迹。
3. 策略文件第 57–76 行把折扣任务特有的提示词、`tools=[]` 和 `ToolStrategy(DiscountCase, handle_errors=False)` 传给 `build_runtime_agent`。`default_model` 满足创建 Agent 时对模型的要求；`resolve_model(session_id)` 与 `record_selection(...)` 是调用方注入的函数。schema 错误不自动重试；此处只创建 Agent，不发送请求。
4. 运行文件第 37–54 行才调用 `create_agent`，把上层策略接入框架，使用 `context_schema=RunContext` 声明运行 context，并安装已有选模 middleware。运行层不 import 折扣用例模块，模型/工具循环仍归框架。此单元未实现配置注册表或会话服务。
5. 策略文件第 81–82 行在模型调用前拒绝空文档。第 84–87 行才真正调用 `agent.invoke`：第一个参数提供用户消息，独立的 `context` 提供会话 ID。`public_doc` 的文本进入 `HumanMessage`，而不是 `request.runtime.context`。
6. 策略文件第 88–90 行取 `structured_response`。本机成功测试得到的是 `DiscountCase` 对象；若没有此对象则显式失败。第 91–92 行检查请求与预期的 `request_id` 一致；第 93–95 行逐条确认引用是 `public_doc` 的非空原文子串；第 96 行才返回用例和消息。

错误路径：空文档直接 `ValueError` 且模型未被调用；模型的结构化工具参数缺必填字段时，`StructuredOutputValidationError` 向上抛出；模型自身 `RuntimeError` 也向上抛出，已记录的选模仅表示尝试调用；引用不存在或 ID 不一致由项目代码抛 `ValueError`。本单元没有自动补造字段、重试模型或发送 HTTP。

## 当前证据与理解检查

先写测试得到预期红灯：导入缺少 `naiyue_test_agent.test_agent`。最小实现后，针对请求/预期 ID 不一致的新测试再次得到红灯：`Failed: DID NOT RAISE ValueError`；补检查后目标测试 `6 passed in 0.13s`。后来把 `create_agent` 装配归位到运行层，全量回归 `9 passed in 0.15s`。额外用真实公开文档文本喂给**假模型**，两段引用均在该文档中，输出 `full-public-contract-probe: PASS`。这仍不证明真实模型生成质量或业务接口执行。

学习状态：本节**代码和数据流已备课，待与用户逐段讲解**；引导后理解、可独立实现、可应对面试追问均待观察。下一次只检查一个问题：为什么 `result.messages` 里有 `ToolMessage`，却不能说 `POST /api/v1/discount/policy` 已经执行？
