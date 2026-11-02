# Spec143R：S05 原生模型转发与 Runner 正式模型接线

状态：2026-11-02按用户“继续吧”推进已确认S05；先冻结本详细合同，再连续TDD实施。当前基线S04.2b为6489后端/568前端；无发布、远端部署或真实配置修改授权，本片真实供应商请求不作为本机夹具通过的一部分。

## 1. 当前链与最小修改

当前`cloud_runner.entry.serve`读取私有bootstrap→等待输入安装→`open_runner(catalog=None)`→waiting_model；测试可显式注入ModelCatalog。现有`model_access.load_model_catalog`在一次函数中读取/校验配置并构造OpenAI Chat/Responses、Anthropic、Ollama；不能把其全量配置复制进任务。TaskBootstrap已有任务令牌、代际和控制续期，Coordinator task app与owner app分监听。

正常新链：显式云端模型配置→Coordinator纯解析/保管私密provider与固定上游→TaskBootstrap导出无密钥/无上游地址的模型profile→Runner复用原模型构造器/SDK及create_agent→每次HTTP发送前读取当前任务令牌→task模型路由显式鉴权/核对task代际/profile/model/用途/额度→固定上游认证替换→原生JSON/SSE/NDJSON返回→原SDK工具循环/必要保存。Coordinator不运行任务Agent。

失败例：工具已执行后模型流中断→关闭原上游响应/释放额度→原SDK/现有Agent失败链保存unknown/recovery描述；代理不透明重试整次业务、不补造终局。停本轮沿既有RunControl取消模型请求→ASGI响应结束/取消关闭上游→后台/必要保存收束。配置缺失仍waiting_model，不注入产品假模型。

## 2. 参考与版本边界

- Pi本机`a69bef789bc95abf0acee16f7b4660b70b650bb9`：`packages/agent/src/agent-loop.ts`在streamFunction前调用getApiKey；coding-agent ModelRegistry.getApiKeyAndHeaders通过runtime.getAuth解析当前auth。只借鉴按请求解析，不称其为云端密钥代理。
- AgentX本机`3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`：LLMServiceFactory.getStreamingClient→LLMProviderService.getStream→LLMProviderFactory.getLLMProviderByStream，把provider key/baseUrl/model交给原生Java适配器；保持供应商适配职责，不移植Java运行时，也不推断其隔离凭证。
- 固定研究源码见`research/cloud-b-runtime-and-transfer-2026-11-01/source-versions.json`。DeepSeek Harness `ce7073d…`的llm-deepseek-api-key.resolveApiKey按操作读取credentials，配合adapter请求解析；ZCode `a60ba6a…`的动态provider请求会respond(requestAuth)，不证明远端任务拿不到key；OpenHands SDK `9884945…`materialize_secrets会具化选定secret。因此这些项目不能替代本项目已选的真实key留Coordinator边界；不额外引入OpenSandbox命名网络下不适用的MITM Vault。
- 当前依赖：langchain1.4.2、langchain-openai1.6.6、langchain-anthropic1.7.5、langchain-ollama1.1.0、OpenAI3.19.2、Anthropic1.9.0、Ollama0.6.3、httpx0.28.1/httpx2 2.13.1。以已安装源码和实际协议探针为准，不升级依赖。
- [OpenAI Docs Python SDK](https://developers.openai.com/api/reference/python)确认可配置base_url/HTTP客户端、SDK重试和流式资源关闭；线上文档会随版本变化，实际SDK以本机版本验证。[HTTPX流式说明](https://www.python-httpx.org/async/#streaming-responses)要求关闭手动stream响应；[LangChain OpenAI集成](https://docs.langchain.com/oss/python/integrations/chat/openai)只作官方适配器参照。

## 3. 配置、所有权与模型profile

CloudSettings增加可选model_proxy；其config_path必须显式指定现有schema_version=1模型JSON，禁止自动发现开发者默认模型文件。upstreams仅为原schema没有base_url的openai/anthropic配置固定服务根地址；openai_compatible/ollama沿原配置base_url，不维护第二份地址。缺必要上游或多余provider覆盖在启动时拒绝。公开端点用HTTPS；HTTP只允许显式loopback/私有IP服务。所有地址/限额走配置。

提取现有读取/校验为model_access.configuration的纯解析结果；load_model_catalog保持原签名并调用同一解析与构造。Coordinator只解析，不构造SDK/图。Runner重用同一模型构造，内部连接覆盖单独传入，不放宽原用户模型配置schema。保留thinking/profile/请求超时/SDK重试/上下文/压缩限制的现有合同。

RunnerBootstrap增加可选模型profile（profile_id、无密钥configuration）；CloudTask持久model_profile_id。configuration只含providers的kind和原models/default_model_id，不含真实key、供应商base_url或配置文件路径。profile_id对公开配置和固定上游策略取hash，不含真实key，允许服务重启换key而保持策略身份。路由model_key为model_id的SHA256，避免任意模型ID在URL路径中的分隔/编码歧义。配置/profile与角色/工具环境一起计入既有48KiB运行配置/64KiB实际bootstrap预算，在服务准备阶段拒绝超限。

任务固定profile。Coordinator配置策略变更后，旧profile请求明确拒绝；不静默换模型/上游，后续显式新代准备才可换profile。仅key轮换保持策略身份；本片不做配置热监听/UI管理。真实key只在管理服务内存与指定原模型配置文件；不写cloud task/history/bootstrap/工具环境/镜像/argv/日志。

## 4. 任务认证与原生HTTP合同

复用现有opaque任务令牌与摘要存储，增加models:invoke用途；令牌绑定task/project/generation/有效期，再以任务持久profile限制provider/model集合。没有第二份根key或通用签名平台。models:invoke只在已配置模型的任务bootstrap/续期中授予；无该scope、跨task、旧代/吊销/过期、已停/删除环境、profile改变均拒绝。控制renewed和重新读取RO bootstrap后同步更新Runner模型认证；每次实际HTTP发送（含SDK重试）取当前token，不缓存构造期token，不把SDK认证头转发给供应商。

task专用入口：`POST /internal/cloud/v1/tasks/{task_id}/models/{model_key}/{native_path:path}`。只允许当前路由对应的OpenAI `chat/completions`或`responses`、Anthropic `v1/messages`、Ollama `api/chat`；根路径由各SDK base_url组合。其余method/path/query、压缩请求、重复认证/Host、错误Content-Type、JSON重复key/深度或大小超限、body.model不匹配拒绝且零上游请求。Anthropic使用x-api-key任务令牌，其余Bearer；只接收该协议唯一认证形式。

服务端认证先于body分配，额度先于body读取；生成固定task/generation/profile/model的请求准入租约，发送前再核对这些事实和任务仍可执行。令牌过期/轮换/吊销拒绝新请求，不取消已准入的有界请求，和已开始流的语义一致；不能在body上传结束重新校验已被轮换的旧token而误报401。显式停止通过取消模型流处理，代际/策略改变或环境停止仍在发送前拒绝原租约。仅固定method/path/服务根地址，不接受任务传入上游、Host/Authorization转发或重定向；3xx转换为明确网关错误。请求只透传所需Anthropic版本/beta和SDK可识别的必要协议头，其余默认过滤；真实认证由Coordinator重建，最终request清除共享客户端可能合并的Cookie。禁Responses后台脱离请求执行（background=true）；只支持当前Agent需要的创建推理接口，不开放files/batches/models/任意SDK自定义请求。

请求JSON有界完整读取以验证模型，响应成功体按有界块转发，保留JSON/SSE/NDJSON字节语义；不解析并重建token流、不攒完整成功正文。响应仅透传Content-Type、request-id/x-request-id、Retry-After/x-should-retry及必要rate-limit头；不返回Location/Set-Cookie/认证头。错误正文有界读取并去掉已知上游key/任务token后保留原生结构及HTTP状态，超限或不可读返回脱敏网关错误。日志仅错误类别/安全代码，无URL/正文/认证。

限制配置：max_request_bytes默认32MiB、max_response_bytes128MiB、error_bytes64KiB、chunk_bytes64KiB、每任务并发4/总并发16、body读取超时30s、最大请求持续3600s；上游连接/读取采用当前模型request.timeout_seconds并受整体上限约束。超额明确429，不无限排队。Coordinator只发送一次，重试归原SDK；取消、超时、上游断流释放响应/槽位，已发送响应头后不得伪造成功尾块。

## 5. 生命周期、影响与清理

CloudRuntime拥有纯解析的模型authority；async模型转发客户端/response lease由同一Coordinator owner loop管理。生产服务关闭先停止监听，按已准入请求的绝对deadline有界排空，再依次关闭控制/模型/后端和同步runtime数据库；不承诺SIGTERM瞬时完成。ModelRelay.aclose也可封口、取消并排空活跃request owner及其响应/HTTP客户端。每个lease在读取body前登记，成功流与有界错误体均由ASGI Response.__call__的deadline/finally负责关闭，包括响应尚未开始就取消；不依赖单独日志/BackgroundTask保证必要释放。不持CT1/DB跨HTTP等待；新增预留与排空顺序同步lock-hierarchy。

影响超过8文件但不新增服务：model_access的配置解析/现有构造与HTTP客户端准备；contracts/cloud_models及bootstrap/task credential字段；cloud model authority/转发与settings/runtime/bootstrap；task原生模型路由；Runner模型装配/动态认证及transport续期；server/backend关闭；测试与当前文档。文件≤500行，现有相似能力先复用。删除被提取的旧配置解析与无消费者包装；测试显式catalog注入仍是当前确定性调用方，正式A入口留到S09。

## 6. 验证与完成条件

先红后绿覆盖：配置纯解析与原有模型合同；无密钥bootstrap/策略固定；scope/task/gen/profile/model/path/method/query/header/body/并发/大小拒绝零上游；真实协议fixture下四类SDK单次/流式/工具回填；错误HTTP状态/Retry-After和SDK重试次数；动态token续期/过期/撤销；跨task并发不串；慢流首块可见、停/断线/超时释放；服务关闭排空。以事件同步，不用极短sleep窗口。

正常闭环用实际HTTP fixture、真实SDK与现有Runner执行两轮及至少一次write/Shell、必要历史/原回执；恢复失败沿143q不重放。测试夹具只用临时路径/假凭证，不读取真实models.json或调用供应商。

完整两分片、前端测试/构建、标准四目标wheel/Twine/同源比较/Mac源外安装、pip check和diff检查按143g执行，记录新旧测试身份。真实provider/公司Coordinator出站DNS/TLS/代理/SSE/取消仍按B07/B20独立门禁，未测不写通过；不部署或修改公司代理/真实配置。后续计划已于2026-11-02由143s替换：下一片S06.1a持久档案，再按143f的14单元推进；取消云端长期记忆。本片既有实现/证据不变，不跨UI、结果差异、自动更新。

## 7. 本机实现与验收（2026-11-02）

本片已按第1节接线：CloudRuntime.models纯解析配置→TaskBootstrap公开profile/冻结任务策略→RunnerModels复用原构造及SDK→task原生模型路由/ModelGrant→固定上游→原生返回→原Agent工具循环/必要历史。配置及公司复验步骤见[cloud-models](../cloud-models.md)。没有产品假模型开关；无model_proxy继续waiting_model，显式配置才产生正式catalog。

新增61项。四类原生SDK的非流式/流式均经真实loopback HTTP转发；兼容OpenAI另验证真实SDK到私有HTTP入口及固定HTTPS MockTransport上游，不能把后者称作公网TLS通过。Chat/Responses/Anthropic各3次SDK attempt对应3次上游请求，无网关额外重试。真实owner API/WS→Runner自动catalog执行write→Shell→回复→必要历史，续期后同一模型对象第二轮继续；真实SSE期间owner stop关闭上游，本轮stopped，下一输入成功。

红灯记录包括初次缺配置/Runner等待模型、转发入口缺失，后续deadline缓存/下游反压、Cookie跨请求、恢复模型关闭次错、模型ID预算、同代过期profile改变、body上传期间续期误401、错误体提前释放租约。新增URL负向等部分夹具在初次不存在字段时也会拒绝，不把这种已通过结果称作红灯；功能核心及各修复有独立失败日志。所有失败注入在测试末核对事件/必要保存/副作用，不放宽旧断言或skip。

扩大专项命令与实际结果：

```sh
PATH="$PWD/build/spec143-s05-20261102/test-assets/bin:$PATH" \
  .venv/bin/python -m pytest tests/cloud tests/test_storage_dependency_direction.py \
  tests/test_model*.py tests/test_responses*.py tests/test_async_model_middleware.py -q
# 912 passed in 35.16s
```

随后完整分片发现原默认模型文件测试依赖既有构造器位置参数；配置提取误改调用形状已修回源码，保留原测试全部断言，末次默认加载/关闭/流式回归30 passed。最终源码重新完整执行：

```sh
PATH="$PWD/build/spec143-s05-20261102/test-assets/bin:$PATH" \
  .venv/bin/python -m scripts.ci_shards --index 0 --count 2 \
  --output build/spec143-s05-20261102/backend-final-0.xml
# 3119 passed, 1 warning in 333.09s
PATH="$PWD/build/spec143-s05-20261102/test-assets/bin:$PATH" \
  .venv/bin/python -m scripts.ci_shards --index 1 --count 2 \
  --output build/spec143-s05-20261102/backend-final-1.xml
# 3431 passed in 371.35s
```

最终6550后端，逐JUnit身份比较保留原6489，新增61，无移除/失败/skip。warning为既有envd重复METADATA恶意夹具。前端首轮原菜单用例未找到dialog失败，前端源码/断言未改；原文件独立4 passed，原整套命令重跑568 passed/69 files。未确定该次失败根因，不宣称前端缺陷已修复，保留frontend-test.log与frontend-targeted.log；最终报告使用frontend-test-final.log。TS/Vite通过，保留既有chunk-size warning。

标准stage_frontend/build_release四目标候选（Mac ARM64、Linux x64/ARM64、Windows x64）重建于dist-final-*，Twine四份通过；458个Python文件同源且与当前源码逐字节一致，Mac源码外新环境安装/正式smoke通过。pip check无冲突、compileall/diff检查通过；1132份源码/测试及依赖清单hash无漂移，pyproject未变。最终汇总、XML、产物/安装hash及前次失败说明见[verification.json](../../build/spec143-s05-20261102/verification.json)。早期backend-*和dist-*诊断候选不作为最终通过证据。

架构复核4项已复现问题（总期限、关闭主异常、模型ID预算、过期profile）均修复复核关闭；安全复核期限/共享Cookie及租约/错误体后续复核通过，独立定向183/24及5/24/8 passed分组结果见汇总，不等同完整测试或生产审计。自查补必要错误体租约/关闭次错与续期窗口，未添加通用权限/凭证平台。

清理：旧load_model_catalog的重复解析迁入共享纯解析模块；原入口/真实消费者继续调用同一解析和构造，A默认文件/请求配置/模型参数断言保留。新增六个生产职责模块，各有实际生产调用，单文件≤500行；无过渡runtime或A/B兼容包装。真实HTTP测试准备第三次重复时提取本片model_http_helpers.cloud_task，原断言数量保留，不改conftest或原测试基础设施。A正式入口仍留到S09，未触碰Spec142/真实配置/用户数据或升级依赖。

限制：本机模型流量全为loopback/受控mock，0真实供应商/业务请求；本片未构建/运行最终Linux任务镜像或部署公司服务，不能替代真实DNS/TLS/代理、Windows中转和最终容器门禁。原S00用户报告不自动覆盖S05。未提交、push、发布或部署，无新增用户独立掌握证据。当前下一片按143t/143f为S06.1a持久档案，剩14独立单元；这是后续计划更新，不改变本片历史验收。
