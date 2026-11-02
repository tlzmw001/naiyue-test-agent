# B云端模型配置与复验

适用S05，实际协议与失败合同见[143r](specs/143r-s05-model-relay.md)。本说明不表示已经修改公司配置或完成真实供应商验收。正式云端入口切换仍在S09，本阶段用既有`nyt cloud`内部CLI/API。

## 配置放在哪里

真实模型key保留在开发机Coordinator服务账号使用的原模型JSON中，格式继续沿README的schema_version=1。不要把该文件放入项目输入、任务挂载、镜像或tool_environment，也不要将Windows整套环境复制给Runner。应用不会自动发现本机默认models.json；仅在云端settings里显式配置model_proxy时启用。

以下是要合入云端settings的片段示意，路径/ID/地址替换为当前部署的真实配置；本文件不执行写入：

```json
{
  "model_proxy": {
    "config_path": "/srv/nyt/private/models.json",
    "upstreams": {
      "openai-provider-id": "https://configured-openai.example.invalid/v1",
      "anthropic-provider-id": "https://configured-anthropic.example.invalid"
    }
  }
}
```

upstreams的key必须对应原JSON里实际被模型使用且kind为openai/anthropic的provider_id；缺少或多余均拒绝。openai_compatible和ollama沿原JSON的base_url，不重复填upstreams。公网根地址必须HTTPS，HTTP仅允许显式loopback/私有IP服务。服务根不含凭证、query或fragment；任务不能指定/覆盖上游。

Coordinator启动时纯解析并校验模型/预算，不构造Agent或供应商SDK，不用一次启动预检冒充供应商已连通。配置错误会明确拒绝服务准备。没有model_proxy时，任务仍为waiting_model；开启后新任务bootstrap包含不带key和上游的模型profile，Runner用现有SDK装配catalog并在hello中报告真实模型ID。使用`cloud task show`核对runner_phase/runner_model_ids/model_profile_id，再按S07.1/S04.2b内部CLI发送原命令。

## 已有任务与凭证轮换

同一代任务固定模型profile。修改模型集合、参数或固定上游后重启Coordinator，旧profile请求会明确拒绝；按143q停止/确认旧环境，再显式启动新代，才采用当前策略。不通过refresh或令牌过期重写同代profile。运行中改配置文件不自动热加载。

只更换真实key、保持模型与上游策略时，重启Coordinator使用新key，profile身份保持。受限任务令牌经既有控制通道续期，Runner每次HTTP发送/SDK重试读取当前值，无需重建模型对象。旧token不能准入新请求；已经通过鉴权/额度预留的请求按其有界租约完成，出站前仍核代际/profile/任务状态。显式stop会关闭模型流并收束本轮，不重放旧工具。

任务命令能读取本任务令牌并调用允许的模型；这里保护真实key与其他任务，不承诺同UID下只有Agent进程能用令牌。上游必须是受信服务；不要让任务输入配置真实key、临时上游URL或任意HTTP头。

## 可调的边界

ModelProxySettings还接受这些字段；未设置时使用以下默认值：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| max_request_bytes | 33554432 | 单次JSON请求有界读取；图片编码也计入 |
| max_response_bytes | 134217728 | 成功响应总大小；超限中止流 |
| error_bytes | 65536 | 供应商错误正文上限，已知key/token脱敏 |
| chunk_bytes | 65536 | 发送的最大块大小 |
| per_task / total | 4 / 16 | 同时在途请求额度，超额429、不无限排队 |
| body_timeout_seconds | 30 | 请求body读取时限 |
| max_duration_seconds | 3600 | 单次绝对总时限，含下游发送等待 |

上游HTTP连接/读取沿模型request.timeout_seconds；SDK重试沿request.sdk_max_retries，代理每个attempt只发送一次。保留原HTTP错误、Retry-After/retry-after-ms等必要语义；不跟随重定向、不转发Cookie/Set-Cookie或任意认证头。JSON/SSE/NDJSON成功体逐块返回，原SDK负责解释，不在Coordinator重建模型事件。

## 公司复验顺序

1. 在最终Coordinator服务身份下，逐个验证配置的真实供应商DNS、TLS、代理和认证。不能用Windows代跑证明开发机可达。
2. 使用最终任务容器，核对bootstrap/挂载/env/argv没有真实key，模型请求走私有task入口；令牌过期、错误模型和旧代请求拒绝。
3. 经内部CLI执行一个只涉及临时工作区的模型→write/Shell→回复→必要历史任务，核对实际结果、请求ID及调用次数；不要用未知副作用接口验证重试。
4. 验证模型流首块、较长思考空闲、显式stop上游关闭、Coordinator重连/令牌续期和新输入继续。网络错误保留原回执/unknown，按143q核对，不自动重放整轮。
5. 开发机任务禁外网时复验：依赖下载/Windows业务中转被阻止，但私有模型入口和控制/历史/结果仍可用。模型上游出站属于Coordinator网络策略。

记录版本、实际配置策略标识、成功/失败与脱敏供应商request ID；不记录key、提示词/响应正文或认证头。本机协议夹具与公司/真实供应商是不同证据，S05未替代最终B07/B20门禁。
