# Spec107 / M6：模型请求的提示词缓存控制

日期：2026-10-18。状态：**M6-01/02已实施并完成离线验收**。本稿替代上一版按调用点注入及动态key的讨论方案。用户确认：配置归模型对象统一持有；未设置策略时选该模型支持的最短保留时间；未声明/不支持不添加参数；能力来自模型profile，不维护型号推断表。无真实供应商请求、真实配置修改或发布授权。

## 1. 当前链与本片修改原因

load_model_catalog → _build_model → ModelCatalog中的真实模型对象。普通主/子模型经_ModelSelectionMiddleware取模型；摘要经_compaction_steps的输出上限binding → context_summary_io.arun_summary_steps；AutoPermissionReviewer.from_catalog直接取快/详审模型，再areview调用。它们入口不同，但都可继承模型构造时的默认请求参数。

改前request只接受timeout_seconds/sdk_max_retries，profile只接受image_inputs；本片已接缓存能力与覆盖字段。只需在模型配置边界解析能力和选择保留时间，然后把原生默认参数交给_build_model；不更改运行层各调用入口、不生成会话/用途key、不修改共享模型对象或checkpoint。缓存发生在供应商端，客户端仍发送完整输入；usage是效果证据，参数存在不是命中证明。

## 2. 参照和框架事实

- Pi本地a69bef789：coding-agent传入sessionId/stream选项，ai/api/openai-responses.ts的buildParams翻译cacheRetention、sessionId及compat能力；anthropic-messages.ts的getCacheControl/convertMessages/convertTools放置断点。统一none/short/long是偏好，不代表所有服务都可完全关闭缓存。OpenAI-compatible显式compat后才使用特定协议。
- AgentX本地3fcfa0e：LLMServiceFactory → LLMProviderService → LLMProviderFactory.getLLMProviderByStream → OpenAiStreamingChatModel/AnthropicStreamingChatModel.builder；检查的应用工厂只设置服务地址、凭证、模型、headers及timeout，没有主动缓存配置。这个有限结论不代表其依赖或供应商没有自动缓存。
- 本机openai3.19.2、langchain-openai1.6.6、anthropic1.9.0、langchain-anthropic1.7.5已有所需请求参数通道。ModelRequest.override(model_settings=...)可把单次调用参数交给框架；直接摘要/审查调用可用ainvoke的kwargs。无需升级依赖或新建缓存服务。
- [OpenAI当前官方说明](https://developers.openai.com/api/docs/guides/prompt-caching)：GPT-5.5类的key/retention与GPT-5.6及以后options/ttl不是同一能力；后者支持显式断点模式。配置选协议，不从模型名猜能力，更不从网关别名推定OpenAI端点能力。
- [Anthropic当前官方说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：可用顶层cache_control开启自动移动断点，默认5分钟，可选1小时；当前ChatAnthropic直连适配器会透传顶层字段。首版建议复用此入口，避免项目重复维护Pi逐内容块断点算法。

已核对本机官方AnthropicPromptCachingMiddleware及[框架说明](https://reference.langchain.com/python/langchain-anthropic/middleware/prompt_caching/AnthropicPromptCachingMiddleware)：它同时标记system/最后tool并传顶层cache_control，但实例固定TTL，只覆盖经过它的create_agent请求。首版建议先使用适配器已有顶层自动位置，统一覆盖主/子/摘要/Auto；若后续需要稳定system/tools独立断点，优先复用该官方实现，再讨论如何覆盖独立调用。这里是实现范围建议，不把“顶层自动”声称与多断点优化效果相同。OpenAI单次参数通道也已核对[框架参考](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI)和本机当前源码；未运行参考项目或真实供应商。

## 3. M6-01配置合同（用户已确认能力来源）

复用现有每模型profile和request，不修改用户真实models.json。profile.prompt_cache为false表示明确不支持；省略表示能力未知。两者在无请求覆盖时都不传缓存控制参数，不按kind、型号/别名、base_url或框架内置型号名称猜能力。

声明支持时，profile.prompt_cache必须为严格对象：

```json
{
  "profile": {
    "image_inputs": false,
    "prompt_cache": {
      "protocol": "openai",
      "retentions": ["in_memory", "24h"]
    }
  },
  "request": {
    "prompt_cache": {"retention": "24h"}
  }
}
```

profile只声明能力，request.prompt_cache只允许retention覆盖。省略整个request、仅配置timeout/retries、或prompt_cache={}，均从能力候选中按协议定义的短→长顺序选最短，与候选书写顺序无关。未知字段、错误类型、空/重复/非法候选、越过能力集合的覆盖，在建图/模型请求前抛ModelConfigError，不把原配置值/凭证写入错误。未知或false能力却显式配置request.prompt_cache属于矛盾配置，也明确报错；默认跳过与主动要求使用不支持参数不同。

| protocol | 支持声明的retentions | 未覆盖时选择 | 构造模型时的默认参数 |
|---|---|---|---|
| openai | in_memory、24h的非空子集 | 子集中最短 | model_kwargs.prompt_cache_retention |
| openai_options | 仅30m | 30m | prompt_cache_options={mode:implicit,ttl:30m} |
| anthropic | 5m、1h的非空子集 | 子集中最短 | model_kwargs.cache_control={type:ephemeral,ttl:选定值} |

例如仅支持24h的模型必须声明retentions=["24h"]，不能因全局偏好短就发送不支持的in_memory。openai/openai_options只适用于现有openai/openai_compatible两种kind及其两种API；anthropic只适用于现有直连anthropic kind。Ollama无本片适配协议，未知/false时不添加参数，声明其他协议报错。兼容Chat网关不能凭Claude别名使用Messages顶层cache_control。

省略或false仅表示本项目不添加缓存控制字段，不保证关闭供应商自动缓存。首版不提供统一off、不自动降级重发、不新增key字段及隐式模型请求。配置启动时读取，保持既有重启要求。

## 4. 文件、方法和职责

| 文件 | 本片改动 |
|---|---|
| model_access/prompt_cache.py（新增） | 纯函数prompt_cache_model_settings(profile,request,kind)校验声明、选定保留时间并返回新的构造参数dict；无IO、无应用导入、无状态/新类 |
| model_access/__init__.py | load_model_catalog接受profile.prompt_cache/request.prompt_cache，调用上述函数，固定字段错误包装为ModelConfigError；把默认参数合并进现有request_settings，经_build_model原有真实SDK构造入口接线 |
| tests/test_model_prompt_cache.py（新增） | 最短/覆盖/缺失/不支持/非法配置、真实Chat/Responses/Anthropic请求payload及输入/配置不变 |
| tests/test_prompt_cache_call_paths.py（新增） | 真实适配器+离线传输经过主/子图、摘要和Auto，检查同模型默认参数通用；输出上限binding、推理副本、工具绑定和并发隔离保持 |
| docs/examples/models.example.json、README及当前地图/交接 | 同步能力声明与默认最短、当前接线及验证范围；例子配置已获本片授权，真实配置不动 |

新增模块的原因：缓存协议校验与翻译是一项独立模型接入职责，原__init__.py已有387行；本片45行纯函数独立放在model_access内，保持配置加载与协议职责清晰且不超过500行，不引入运行层策略或通用平台。原_build_model签名和所有公开API保持，内部request_settings扩展沿原**kwargs构造。模型catalog继续保存真实BaseChatModel，不使用Runnable包装目录、不改客户端资源拥有权。

## 5. 改前改后调用例子

正常：加载模型A的openai能力[in_memory,24h]，request无缓存覆盖 → prompt_cache_model_settings生成model_kwargs.prompt_cache_retention=in_memory → _build_model创建A → 普通/子模型沿原图取A、摘要输出上限binding保留A默认、Auto取配置模型自身的默认 → SDK收到相同策略。不同model_id各自配置，不代表Auto必须使用主模型。工具权限、模型上下文、流式及必要保存均沿原链。

显式覆盖：同一A在request.prompt_cache.retention设置24h → 校验属于能力集合 → A所有用途都使用24h。声明候选[24h,in_memory]仍默认in_memory，不依赖JSON顺序。

失败：配置覆盖1h但A只声明in_memory/24h → ModelConfigError，图/模型请求尚未发生。声明错误导致实际端点拒绝参数时沿现有模型失败合同传播；子反馈/Auto Ask/SDK既有重试不被本片改写，不增加项目侧删字段重试。能力声明是配置者确认的端点能力，不是离线测试自动探测结果。

## 6. 验收与剩余分片

本片先失败测试，再最小模型层实现；通过真实适配器payload及MockTransport验证生产接线，不用假模型复制缓存逻辑。主/子/摘要/Auto读取同一模型默认参数，不为测试新增运行入口或修改conftest。单测覆盖返回值/异常、参数完整、工具/消息不被修改、按模型隔离、无额外请求；SDK重试测试保持既有合同。

通过专项后跑完整后端、前端及构建、语法/依赖/500行/分层/链接/diff检查；保留历史脏树。M6-01只做请求控制，0次真实供应商请求，不宣称命中或省费。

M6-01拆分时将缓存写入/读取及总输入口径、事件/快照/Web同步和有界实网入口留给M6-02，当前已获批的合同见第8节。Anthropic原始input不含缓存，但当前LangChain标准总输入已包含，不能再重复相加；缺失与0区分。持久调用账目仍在M6后单独设计。后续实网必须另行确认目标/预算，本片不执行。

## 7. M6-01实际结果与下一片

生产改动仅两份模型接入文件：新增45行prompt_cache.py，原__init__.py从387行变为393行。没有新增类、运行层middleware、会话key或单独的主/子/摘要/Auto参数分支；模型构造默认由所有既有调用自然继承。示例JSON与README同步，未改真实配置、SDK拥有权、依赖、公开API、DB字段或前端代码。旧动态key/三个入口逐次注入方案已被本稿替换，没有留下生产兼容代码。

测试先看到正确的功能红灯：tests/test_model_prompt_cache.py为46 failed/31 passed，支持声明及默认选择尚未被模型加载器接受。首次测试收集的保留参数名错误不计产品红灯；调用链测试早期的测试对象字段使用错误也已按真实类型修正，不计产品缺陷。最小实现后两份新增测试82项通过；无conftest或原测试断言修改。

真实适配器＋离线传输证据：主模型/子模型/Auto快审/详审及手动摘要放在一次应用生命周期中，分别5/2/2/2次请求，共11次，摘要保留256输出上限和当前推理档位；父/子真实工具结果、审批放行、默认参数及原模型不变都核对。不同会话并发请求保持同模型默认，端点拒绝参数时只有一次失败发送，不影响另一个会话且不新增项目重试。Responses实际SDK流式及工具绑定分别核对legacy/options参数，已有最终工具参数补齐继续有效；Anthropic实际SDK异步调用及输出binding保留5m控制。只捕获合成请求体，不保存headers或凭证。

| 验证命令 | 本轮实际结果 |
|---|---|
| `.venv/bin/python -m pytest -q tests/test_model_prompt_cache.py tests/test_prompt_cache_call_paths.py tests/test_model_access.py tests/test_model_image_config.py tests/test_responses_final_arguments.py` | 168 passed in5.20s，/tmp/naiyue-m6-focused.log |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2606 passed in109.71s，/tmp/naiyue-m6-backend.log |
| frontend目录 `npm test -- --run` | 27文件189 passed，/tmp/naiyue-m6-frontend.log |
| frontend目录 `npm run build` | TypeScript/Vite210模块成功，/tmp/naiyue-m6-build.log |
| compileall、pip check、AST/500行/分层 | 409份Python和67份前端源文件通过；core反向导入application为0，依赖完整 |

架构/安全复核独立复跑两份新增测试，分别82 passed in2.09s/1.99s，无确认遗留。文档链接、JSON样例实际加载和diff检查通过。原脏树保留，未提交或push；0次真实供应商请求。离线可证明参数接线与继承，不证明网关确实支持声明的能力、缓存命中、保留效果或费用节省。

以上为M6-01收尾时点；M6-02已获用户授权，实施合同见下节。持久调用用量/诊断继续后排，checkpoint修剪及两种项目重试保持延期；不自动执行本稿第6节的实网建议预算。

## 8. M6-02实施合同（2026-10-18用户批准）

本片只统一单次返回用量，接入已有观察/事件/快照/Web/UI，并更新有界验收入口。0次真实供应商请求；不改真实配置、模型选择/缓存默认、审批、DB表、共享模型、依赖或公开应用API。不建设持久账目、费用估算、额外补查请求或新的调用包装层。

### 8.1 数据与来源

新增model_access/usage.py的normalize_model_usage(usage)，纯函数接收LangChain的usage_metadata，返回四个可空非负整数：input_tokens（已含缓存读取及写入的总输入）、output_tokens、cache_read_tokens、cache_write_tokens。不把缓存分项再次相加到input。缺失/非法为None，明确0保留0；整份usage缺失时快照仍为None。SDK已把原始缺失变成0的信息无法逆推，不声称项目恢复了原始响应缺失语义。

缓存读取识别cache_read及priority_/flex_前缀；写入识别cache_creation及同样前缀。Anthropic存在ephemeral_5m_input_tokens/ephemeral_1h_input_tokens拆分时优先用有效拆分总量，不能与通用cache_creation重复相加；拆分均为0而通用值非0时沿当前SDK的通用值回退口径。非法类型（含bool）、负数及缓存分项超过已知总输入，记录固定诊断并将相应不可信缓存统计置未知；保留有效总输入/输出。只处理已知有限键，不记录原始usage、响应内容或异常正文。

依据：Pi本地a69bef789的openai-responses-shared.finalizeResponse先从总输入扣除缓存分项，anthropic-messages将三类输入分别保留，再由AgentSession.getSessionStats汇总。本项目保留LangChain总输入语义。AgentX本地3fcfa0e检查链onCompleteResponse→buildModelCallInfo只记录输入/输出，不据此断言其全部依赖没有缓存统计。当前LangChain Anthropic适配器已合入缓存形成总输入；本机零网络探针确认TTL拆分会把通用cache_creation置0，OpenAI服务档位使用带前缀的缓存键。

### 8.2 当前链与改后链

当前：SDK→AIMessage.usage_metadata→各入口各取部分字段→已有日志/事件/快照→Web通用序列化→ContextPanel。改后：入口仍在原处，只统一调用normalize_model_usage，随后走原观察/交付链。

正常例：Anthropic原始input=200/read=7000/write=1000，SDK给input=8200；项目输出input=8200/output=30/read=7000/write=1000。模型观察入口生成结束事件；上下文观察入口保存同一次快照，Web面板显示总输入8200及其中读取7000、写入1000。子调用、摘要及Auto各自记录，不叠到根上下文占用。

失败例：网络失败无最终响应→原异常不变，用量未知；最终响应已有用量但响应门禁拒绝→失败事件保留已有用量；非法统计→固定诊断/未知分项，不把观察故障转换为模型失败或覆盖原异常。停止/GraphBubbleUp保持原控制效果。流式网关未提供usage时不补请求、不推断命中。

### 8.3 文件及方法

| 文件 | 改动 |
|---|---|
| model_access/usage.py（新增） | 唯一的模型用量整理函数；多个现有消费者需要同一字段口径 |
| agent_runtime/call_observation.py、events.py | _accept/_failed及ModelCallFinishedData增加cache_write_tokens，失败保留已返回用量 |
| agent_runtime/context_usage.py | finish_snapshot/read_snapshot增加字段与校验；旧version1快照缺该可选项读作None，不增DB迁移 |
| agent_runtime/context_compaction.py、context_operation_observation.py、context_events.py | 摘要日志、summary_finished及结束事件补summary_cache_read_tokens/summary_cache_write_tokens |
| agent_runtime/tool_execution/permissions.py | _diagnostic按阶段统一缓存统计，审查结论合同保持 |
| application/run_events.py | 日志名单补新字段 |
| frontend/src/contextTypes.ts、components/ContextPanel.tsx | 新分项及说明，保留估算/供应商统计、根/子边界 |
| scripts/probe_prompt_cache.py | 迁为async、跨平台期限和关闭，复用现有RequestBudget/guard_catalog；初始两次实际HTTP发送，上限2，不自动扩张 |
| 对应tests及frontend测试 | 正常/缺失/0/非法/失败/关闭/预算及全链接线 |

Web沿用已有通用序列化，测试确认字段到达；不新增Web入口。脚本参数显式配置deadline/output/prefix预算，不用SIGALRM，不打印内容/headers/URL/异常正文；测试期临时加载的catalog上禁SDK重试、发前记数，finally关闭。仅支持已有远端OpenAI/Anthropic适配器，未声明缓存能力时在发前拒绝；任何端点错误停止，报告真实发送计数。完成请求与观察到命中是不同结果。

### 8.4 验证与后续依赖

先看到正确失败测试，再最小实现。覆盖Chat Completions/Responses/Anthropic实际SDK归一化，TTL拆分、服务档位、缺失/0/非法；根/子、摘要/Auto和失败事件；Web/保存重开、旧快照、UI。probe测试用MockTransport确认最多两次发送、错误不重试、取消/超时关闭及无敏感输出。前端现有断言增加一项未知计数而不减少原检查；不改conftest。

通过专项后跑全部后端、前端及构建，语法/依赖/500行/分层/diff检查。本片不执行实网验收，不把离线统计当缓存命中证明。下一块持久调用用量依赖此统一口径，另行讨论调用身份、保存/去重、根子摘要Auto归属、未知用量及汇总；持久诊断和checkpoint修剪不纳入。

## 9. M6-02当前实现与下一块准备

新增model_access/usage.py共50行；主/子调用观察、上下文快照、摘要结束事件、Auto诊断、应用日志名单及既有供应商验收报告同步消费四项口径。ModelCallFinishedData新增字段放在原failure之后，保留原位置参数含义。拒绝最终响应的失败事件保留已取得的用量，网络失败仍为未知且原异常身份不变。快照仍是version1，缺少新可选字段时返回None的分项副本，不改原数据或SQLite schema。旧局部分项解析被替换，没有并行兼容执行链。

ContextPanel沿既有入口展示总输入、其中缓存读取/写入和输出，本地估算继续独立。scripts/probe_prompt_cache.py不再使用同步invoke/SIGALRM；复用现有预算、输出binding、异步关闭及当前声明能力，OpenAI保持store=False，Anthropic不发送该参数。默认两次实际发送，参数失败/预算耗尽即停；完成两次请求与观察到缓存命中分别报告。未跑真实供应商。

测试红灯：归一化48项因入口缺失失败、组合接线因新字段缺失失败；UI1失败/3通过确认缺少写入显示。探针fixture缺api的初次配置错误不计产品红灯，修正后7项因旧同步入口不支持新参数失败。无效工具JSON首次可被框架修补，导致测试反复请求，停止后改为确定不可解析的字符串，不计产品缺陷。首次文件验收漏带既定fd PATH以及静态解析误用系统Python3.9均为执行环境错误，随后改用当前项目环境重跑；没有为此改生产行为或放宽断言。

三份新增测试70项，覆盖真实SDK归一化、实际根/子/摘要/Auto观察与日志、拒绝响应/网络失败/非法统计、旧快照及字段校验、HTTP发前预算、端点错误不重试、事件驱动取消/超时、Anthropic探针及非法参数。既有Web测试验证新分项随checkpoint重开，原断言只增加字段或更新新增行数，不删除原检查。无conftest改动。

下一块的零网络探索：在现有8次真实适配器＋MockTransport场景中挂框架AsyncCallbackHandler，观察到8次start/8次end/8个唯一run_id；模型为root5、child1、fast1、detail1；RunEventPublisher作用域7次、AgentEventPublisher1次，摘要和Auto既有tags均可识别。说明框架回调可覆盖当前四类调用，无需另建模型调用包装层；这不是持久用量实现，也未验证SDK内部每次HTTP尝试的独立计费。

另用既有上下文溢出恢复场景挂同一类回调，零网络结果为：框架3个唯一模型调用ID，2次returned、1次failed；normal_inputs=2、summary_inputs=1，而既有model_call_started只有1次。验证了首次溢出请求→摘要→重发应按3次框架调用识别；失败调用无报告用量时保留未知，不能补零或把它误记成摘要的一部分。

持久用量下一步先讨论：以框架单次模型调用ID为账目身份，保留根run/agent归属及用途；同一工作区SQLite独立记录，压缩不扣除旧消耗，未知与0区分；回调收集、应用保存、查询聚合各守边界。具体schema、失败处理和UI另写实施Spec，不把逻辑model_call_finished直接当成全部物理请求账目。

实际验证（本轮0次真实供应商请求）：

| 命令 | 结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q tests/test_model_usage.py tests/test_model_usage_paths.py tests/test_prompt_cache_probe.py` | 70 passed in1.13s；/tmp/naiyue-m602-new-tests.log |
| 同环境的组合专项，含缓存默认/上下文/Web/摘要/供应商验收 | 106 passed in3.12s；/tmp/naiyue-m602-final-focused.log |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2676 passed in108.79s；/tmp/naiyue-m602-backend-final.log |
| frontend目录 `npm test -- --run` | 27文件189 passed；/tmp/naiyue-m602-frontend.log |
| frontend目录 `npm run build` | TypeScript检查及Vite210模块成功；/tmp/naiyue-m602-build.log |
| 项目Python3.12的AST/500行/反向依赖检查、compileall、pip check | 413份Python、67份前端源文件通过，无超行文件/core反向导入；依赖完整 |

架构/安全按本片基线差异分别只读复核，最终各自70项独立复跑（1.15s/1.17s），无确认遗留。未改真实配置/用户库/依赖，未提交或push；历史脏树保留。只证明适配器字段和生产链消费正确，不证明缓存命中、费用或SDK不可见尝试的完整用量。下一步只讨论持久调用用量，不直接实施。

后续状态（2026-10-18）：[Spec108-01](108-persistent-model-usage.md#6-108-01当前主链与验收)已接框架调用身份与主子/摘要/Auto开始结束事实；2717后端/190前端及构建通过，0次真实供应商请求。[108-02](108-persistent-model-usage.md#8-108-02实际实现与验收)已完成schema9独立用量保存/缺口/重启未确认，2742后端/190前端及构建通过；[108-03](108-persistent-model-usage.md#10-108-03实际实现与spec108收尾)累计查询及第三页签已完成，2758后端/198前端及构建通过，M6旧结果作为当时记录保留。
