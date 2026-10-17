# Spec 002：首个 HTTP 业务场景的契约与验收样本

> 2026-10-17版本边界：用户已撤销本历史需求，093C已删除Discount实现及对应测试。本文件仅保留当时的设计和验证事实，不作为当前功能或可运行入口。

状态：2026-09-29，**业务范围已选定，Agent 功能尚未实现**。本 Spec 只冻结第一个可验证的测试任务和依据；下一开发单元再确定应用接口、文件修改范围，并先写失败测试。用户指定 `discount_system` 作为首个待测项目。

## 任务与来源边界

任务：根据公开 API 文档设计一条能检验折扣规则**优先级冲突**的 HTTP 用例，随后逐步让 Agent 生成可审查的用例，再由确定性执行器验证真实响应。第一条场景是 `POST /api/v1/discount/policy`：`user_level=black`、`scene=campaign`、`stock=0` 同时满足三条规则，预期优先命中 `USER_BLOCKED`。

| 材料 | 本阶段用途 | 是否进入 Agent 知识输入 |
| --- | --- | --- |
| `/Users/zmw/discount_system/specs/public_api_doc.md` | **业务契约**：字段、规则顺序、响应与查询/删除行为 | 可以，后续先限定为这一份公开文档 |
| `/Users/zmw/discount_system/src/discount_system/{app,models,policy,store}.py` | 核对当前 SUT 实现、异常与副作用；不能把实现细节冒充业务需求 | 不进入第一条用例的 Agent 输入 |
| `/Users/zmw/AIAutoTest/test_workspace/suites/discount_system/discount_policy_smoke/*.md` 与现有 generated pytest | 历史测试资产、覆盖对照和复用评估；其中已经有相近的 `TC-DP-002` | 不进入第一条用例的 Agent 输入，避免直接抄现成测试 |

来源文件可能变化；此处是 2026-09-29 的本机快照核对结果。`discount_system` 目录本身不是 Git 仓库，不能给这份快照标注提交哈希。正式评测前要固定可追溯的文档和 SUT 版本或内容摘要。

## 当前数据流与目标行为

当前项目只有真实模型→工具→模型诊断和 `agent_runtime.py` 选模 middleware。没有读取业务证据、生成用例、业务工具、HTTP 执行或评分器。待测服务的公开行为是：收到 `DiscountRequest` → 按优先级计算 `DiscountDecision` → 成功结果按 `request_id` 保存在服务进程内 → 可用 GET 查询，用 DELETE 清理。

本场景输入示意（值仅用于说明契约；真正运行时使用本次任务唯一的 `request_id`，目标地址从受控配置读取）：

```json
{
  "user_id": "u_priority",
  "user_level": "black",
  "item_id": "item_priority",
  "item_price": 120.5,
  "scene": "campaign",
  "stock": 0,
  "request_id": "<本次运行唯一ID>"
}
```

可核验的预期：POST 返回 HTTP 200，响应 `code=0`、`eligible=false`、`discount_rate=1.0`、`reason_code="USER_BLOCKED"`，并原样回传这次的 `request_id`。这来自公开文档的第 1 条规则高于第 2、3 条；本机源码 `policy.py` 的判断顺序与其一致。成功后通过 GET 查询同一 `request_id`，应得到相同决策；由本次任务负责 DELETE 清理，再 GET 应返回文档规定的 404。查询/清理是下一步执行链路的验收，不要求首个“设计用例”小单元立刻具备网络执行。

```mermaid
flowchart LR
    D[公开 API 文档] --> A[Agent 设计冲突用例]
    A --> V[确定性校验字段/引用/预期]
    V --> H[受控 HTTP 执行]
    H --> R[响应与断言结果]
    R --> E[结果和轨迹评测]
```

## 下一功能小单元的最小验收

1. 用固定的公开文档文本和任务描述作为输入，Agent 产出一条**结构化、可审查**的优先级冲突用例：请求字段、预期响应、引用到公开文档的规则。输出不得把 SUT 源码或历史测试用例当作证据来源。
2. 先写失败测试，覆盖：用例包含三个冲突条件及正确优先级；缺少必填字段或无依据的预期被确定性校验拒绝；模型/工具错误可观察且不伪装成成功。测试使用假模型/固定输入，不依赖真实网关稳定性。
3. 实现时只接通该小单元实际需要的层；模型接入、会话、Agent 运行和测试策略的具体函数签名及文件范围在该单元技术 Spec 确定。不得为了四层命名创建空模块或把整套长期 RAG/执行平台一并写入。
4. 后续单元再接真实 HeiyuCode 模型、受控 HTTP 执行和独立评测：分别保留模型原始输出、用例校验结果、HTTP 请求/响应、清理结果与引用证据。没有实际请求就不宣称已验证 SUT 行为。

## 环境与安全边界

- 待测服务位于 `/Users/zmw/discount_system`，只作参考和受控测试目标；本项目不修改其源码、依赖或配置。已有 README 使用环境变量配置服务监听地址/端口；本项目接入时目标 base URL 也由受控配置提供，不把模型生成的 URL 直接当访问目标。
- POST 会在待测服务的内存 `DecisionStore` 写入决策记录；即使没有外部数据库，仍是有副作用的调用。实际执行前必须限制允许的目标、HTTP 方法和路径；为本次运行生成唯一 `request_id` 并在结束时经公开 DELETE 清理，清理失败需留下可见证据。
- 公开文档只说校验失败遵循框架格式，不固定精确错误 body。本场景先验证成功优先级；字段校验、重复 `request_id`、重启后状态等另选样本，不凭源码推断成已确认业务契约。

## 已有核验与未完成

已核对 SUT `app.py`、`policy.py`、`models.py`、`store.py` 和公开 API 文档。只读运行 `/Users/zmw/discount_system` 的 `PYTHONPATH=src python3 -m pytest -q tests/test_api.py tests/test_policy.py`，实际输出 `11 passed in 0.16s`。另用 FastAPI `TestClient(create_app())` 对本 Spec 的三条件冲突样本做进程内探测，POST→GET→DELETE→再次 GET 实际输出 `priority-contract-probe: PASS`、HTTP 状态 `[200, 200, 200, 404]`、`reason_code: USER_BLOCKED`。这证明当前 SUT 的进程内 HTTP 行为与本样本预期一致，**不是**本项目 Agent 生成/执行测试通过，也不是已启动真实网络服务的证据。新项目 `3` 个运行边界测试仍与业务无关。

复现此样本的命令（在 `/Users/zmw/discount_system` 执行；只使用进程内 `TestClient`）：

```bash
PYTHONPATH=src python3 - <<'PY'
from uuid import uuid4
from fastapi.testclient import TestClient
from discount_system.app import create_app

request_id = f"naiyue_priority_{uuid4().hex}"
payload = {
    'user_id': 'u_priority', 'user_level': 'black',
    'item_id': 'item_priority', 'item_price': 120.5,
    'scene': 'campaign', 'stock': 0, 'request_id': request_id,
}
with TestClient(create_app()) as client:
    evaluated = client.post('/api/v1/discount/policy', json=payload)
    queried = client.get(f'/api/v1/discount/decisions/{request_id}')
    deleted = client.delete(f'/api/v1/discount/decisions/{request_id}')
    after_delete = client.get(f'/api/v1/discount/decisions/{request_id}')
assert evaluated.status_code == 200
assert evaluated.json() == {'code': 0, 'eligible': False, 'discount_rate': 1.0,
                            'reason_code': 'USER_BLOCKED', 'request_id': request_id}
assert queried.status_code == 200 and queried.json()['decision'] == evaluated.json()
assert deleted.status_code == 200 and deleted.json()['deleted'] is True
assert after_delete.status_code == 404 and after_delete.json()['error'] == 'DECISION_NOT_FOUND'
print('priority-contract-probe: PASS')
print('http_statuses:', [evaluated.status_code, queried.status_code,
                         deleted.status_code, after_delete.status_code])
print('reason_code:', evaluated.json()['reason_code'])
PY
```

未完成：Agent 业务输入、用例 schema、确定性校验、模型接线、HTTP 执行、清理与轨迹评分。下一单元从结构化用例设计开始，先画 Agent 输入→模型消息→输出→校验的数据流，再写技术 Spec 与红灯测试。
