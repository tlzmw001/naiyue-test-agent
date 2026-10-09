# 真实摘要请求耗时排查（2026-10-08）

## 问题与范围

Spec 067 的第三次真实压缩组在 60 秒子进程时限内未返回，最后日志是 `context_summary_model_started`。用户随后授权继续独立排查。此前的探针预算不足已经有回归修正，本次只查等待耗时；不改压缩算法、公开接口、配置、SDK 重试或超时默认值，也不同时推进 CLI。

先核对当前模型构造、`bind_model_output_limit → ChatOpenAI._generate → OpenAI.request → _send_request`。现有最后日志只能说明进入摘要调用，不能说明 HTTP 是否发送、是否重试、是否返回或是否卡在本地事件回调。单组墙钟上限与 SDK 60 秒超时相同，可能先结束子进程；SDK 最多 2 次重试不代表整个请求在 60 秒内终结。

## 本机事实与诊断方法

- 当前默认 `model_id=heiyu-gpt-5.5`，ChatOpenAI / OpenAI SDK；`timeout=60.0`，`max_retries=2`，未显式设置 reasoning。不输出供应商 URL、凭证、headers、原始 payload、摘要或回答正文。
- `bind_model_output_limit(model, 2048)` 在当前 Chat Completions 路径生成 `max_completion_tokens=2048`；实际 HTTP 路由也确认是 Chat Completions。不能因模型名、历史 Responses 实验或 `use_responses_api=None` 推断本次协议。
- 对一次独立子进程临时 patch `OpenAI._send_request`，原函数和返回值保持。进入/返回/异常各打印有限枚举路由、尝试序号、耗时、HTTP 状态或异常类型，再调用真实 `_run_case(compacted)`；父进程 130 秒后会终止自己的 worker。这个上限仅为取得 SDK 尝试证据，不是产品配置变更，也不是修复。关闭 hosted tracing，不启用 SDK 原始 DEBUG 日志。
- 观测点在 SDK 请求发送边界；同一序号的开始/返回配对，结合既有摘要/回答阶段日志区分两条逻辑请求。临时 patch 随子进程退出消失，未保存到产品或测试源码。

## 本次诊断的实际结果

```text
http_attempt_started: attempt=1 route=chat_completions stream=false
http_attempt_returned: attempt=1 status_code=200 duration_ms=18813
context_summary_model_finished: returned duration_ms=18831 input_tokens=7053 output_tokens=254
compaction_finished: status=applied visibility=graph_values
http_attempt_started: attempt=2 route=chat_completions stream=false
http_attempt_returned: attempt=2 status_code=200 duration_ms=20455
case_result: elapsed_ms=39300 compaction_state_confirmed=true correct_count=4 parse_ok=true
diagnostic_worker_exit=0
```

两条逻辑请求各一次 HTTP 尝试，未触发 SDK 重试。摘要形成后，正式状态观察确认替换，四项固定事实均保留；整组 39.3 秒，小于原探针单组 60 秒时限。没有复现此前长等待，不能宣称找到了上次超时根因或修复了超时，也不能由这一次成功排除所有事件/网络异常。

## 标准探针与本地回归

本次重新执行的全量检查：

```text
.venv/bin/python -m pytest -q
723 passed in 16.52s
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
exit=0
.venv/bin/python -m pip check
No broken requirements found.
```

项目没有独立应用打包构建目标，语法编译不等于部署构建。本次没有产品修复、新功能或 TDD 红灯，不把重新运行现有测试称为新回归实现。

标准真实命令保持为 `.venv/bin/python -m scripts.probe_compaction_quality --total-timeout-seconds 120`，本轮退出0，两组均 returned：

| 组别 | 总耗时 ms | 摘要耗时 ms | 摘要输入/输出 token | 回答输入/输出 token | 四事实 | 状态替换确认 |
| --- | ---: | ---: | --- | --- | --- | --- |
| compacted | 42026 | 17964 | 7053 / 247 | 5385 / 66 | 4/4，JSON有效 | true |
| baseline | 3656 | 无摘要 | 无摘要 | 7721 / 41 | 4/4，JSON有效 | false，符合预期 |

原标准每组最多60秒，未加长时限或放宽状态/评分断言；临时 SDK patch 不在本次标准探针中。压缩组摘要与回答合计12751 token，对照7762 token，不说明节省成本。当前原标准验证通过只补本片真实压缩状态证据，不能反推上次超时原因。

## 遗留边界

间歇性长等待的历史根因仍未知。若再次出现，应在同一失败请求保留安全的 HTTP 开始/返回/异常/重试时序，再区分连接、读取、网关/模型处理、本地解析及回调。没有证据不修改 thinking、SDK 重试或请求协议，也不把加长父时限当成修复。

真实明确输入溢出恢复、其他供应商、并发压力、持续摘要质量与整个 run 的耗时/费用预算均未由本次验证覆盖。超时中止请求的供应商实际用量仍未知；成功请求的 token 数据也不等于生产业务质量或成本收益。
