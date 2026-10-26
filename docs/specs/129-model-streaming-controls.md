# Spec129：按模型配置工具参数流式与数据块等待超时

状态：实现与本机验收完成（2026-10-24）。用户授权支持两个配置项；不以此认定此前 Windows 超时的根因。真实用户配置、依赖、版本及发布状态未改，未提交或推送。

## 当前链路与问题

`load_model_catalog` 校验 `models.<id>.request` → `_build_model` 构造 ChatOpenAI / ReasoningChatOpenAI / ResponsesChatOpenAI → catalog 准备独立 HTTP 客户端 → 运行层异步调用 SDK。改前只暴露 HTTP `timeout_seconds`，没有流式数据块等待配置。SDK 默认从 `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S` 读取阈值，缺省 120 秒。

Chat Completions 的工具参数可以逐块返回；智谱另提供 `tool_stream` 扩展。正文的 stream 开关与工具参数流式不同。此处只提供显式配置，不按模型名字推测支持情况、不全局启用、不修改 Responses 适配逻辑或 UI。

## 配置合同

仍使用 schema_version=1，在每个模型的 request 中增加：

| 字段 | 值及含义 | 适用范围 |
| --- | --- | --- |
| tool_stream | 严格 boolean；省略不发送，true/false 原样发送 | 仅 openai_compatible + chat_completions；使用者需核对网关支持 |
| stream_chunk_timeout_seconds | 有限 int/float，>=0，拒绝 bool/null；0 关闭 SDK 数据块等待阈值 | openai / openai_compatible 的 chat_completions 与 responses |

显式数据块阈值覆盖环境变量；省略不传构造参数，保留 SDK 环境变量/默认规则。它不是整次任务总时限，不改变 HTTP timeout、重试、工具执行期限。错误在配置边界报 ModelConfigError，只报告字段及合同，不回显配置值或凭证。对不支持的供应商/协议拒绝配置，不静默忽略。

`tool_stream` 使用 ChatOpenAI 的 `extra_body` 进入实际 JSON 请求顶层；等待阈值作为 `stream_chunk_timeout` 构造参数，仅在本地消费，不发给模型服务。不开放任意 extra_body 配置。推理档位 model_copy、输出上限 bind 保留两项配置，其他模型保持隔离。

## 正常与失败例子

正常：兼容模型 request={tool_stream:true, timeout_seconds:300, stream_chunk_timeout_seconds:600} → loader 生成 extra_body={tool_stream:true} / timeout=300 / stream_chunk_timeout=600 → SDK 发送工具参数扩展并按 600 秒等待每个已解析数据块 → 完整参数仍由现有工具调度执行。配置本身不执行任何工具，也不保证网关即时转发。

失败：字段值非法或协议不支持 → load_model_catalog 抛 ModelConfigError，未发网络请求；已装配模型的流停顿 → 原 SDK 抛 StreamChunkTimeoutError，现有错误分类/取消/资源收尾保持，不自动重试未知工具副作用。

## 影响文件与验证

- model_access/__init__.py：在现有 request 解析处校验并传递两个字段，保持一个生产入口，不新增通用配置平台。
- tests/test_model_streaming_controls.py：新独立测试文件（已有 test_model_access.py 接近行数上限），复用现有配置 fixture；先红后绿。覆盖默认不发送、类型/协议拒绝、按模型与环境优先级、推理档位/输出绑定保留，以及通过真实 SDK + mock HTTP 检查线上的 JSON 和等待阈值/超时传播。无需真实供应商请求或紧时间 sleep。
- docs/examples/models.example.json、README.md：可复制配置与易懂说明；settings.toml 不新增重复字段。
- docs/handoff.md：仅在顶部补本片状态，保留并行 R1 和学习记录。

先专项红灯，再最小实现与专项回归；最终全后端测试、前端测试/构建、标准 Mac ARM64 wheel/Twine 检查。Windows 原机、公司网关行为与真实模型效果不由本机 mock 证明。

## 实际实现与验收

生产逻辑仅扩展现有 request 解析处 19 行（另改 1 行字段名单）；没有新增生产模块或公开 API，没有变更 Agent 循环、工具执行、UI、数据库或 Responses 流适配。

1. 先运行新测试，实际红灯为 **18 failed, 22 passed / 1.26s**；正向配置因两个字段尚不受支持而被拒绝，符合预期。最小实现后首批 **40 passed / 1.34s**。
2. 新测试最终 45 项，含真实 SDK + mock HTTP 的 true/false 请求体、标准与可见思考适配器、600 秒逐块等待与 0 关闭、强制超时异常分类和响应关闭；等待测试使用确定性到期，不依赖紧时间 sleep。加上模型接入、缓存、思考、Responses 生命周期和结束原因回归，**215 passed / 6.00s**：

   ```bash
   .venv/bin/python -m pytest -q tests/test_model_streaming_controls.py tests/test_model_access.py tests/test_model_prompt_cache.py tests/test_visible_reasoning.py tests/test_responses_stream_lifecycle.py tests/test_model_stop_reason.py
   ```

3. 全后端命令（PATH 提供已验证的包内 fd/rg 测试资源）为 **4687 passed / 321.67s**：

   ```bash
   PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q
   ```

4. `npm --prefix frontend test -- --run`：**50 文件、422 项通过**；`npm --prefix frontend run build`：TypeScript / Vite 成功，无构建错误（既有大 chunk 提示仍在）。`.venv/bin/python -m pip check`：**No broken requirements found**。
5. 标准构建 `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec129-20261024/wheels` 与 `.venv/bin/python -m twine check build/spec129-20261024/wheels/*.whl` 成功。候选包仍使用现有 0.4.0 版本号，只用于本机验收，不上传；206 份包内 Python 与当前源字节一致。候选 wheel 以 `--no-deps --target` 安装到独立 build 子目录后，从源码目录外、Python `-I` 入口验证两项配置接线成功；这是复用本机依赖的包验证，不冒充干净公共安装。
6. 完整 JSON 示例通过真实 loader；可见思考＋缓存＋推理档位切换组合保留两个参数。变更 Python 语法及单文件行数检查通过，生产文件 434 行、测试文件 206 行；`git diff --check` 通过。

原始日志、候选和汇总位于 `build/spec129-20261024/`。真实模型请求数为 **0**，未验证公司网关透传/缓冲行为或 Windows 原机。本片不宣称此前超时已经修复；下一步是目标网关实际使用验证，发布由用户另行授权。

## 官方依据

- [智谱工具流式输出](https://docs.bigmodel.cn/cn/guide/capabilities/stream-tool)
- [智谱对话补全 tool_stream 参数](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)
- [LangChain 数据块超时说明](https://kb.langchain.com/articles/4695245229-streamchunktimeouterror-during-async-streaming-with-langchain-openai)
