# Spec114：预览消息的完整正文读取入口

日期：2026-10-18。状态：本地已实现并验证，未提交或发布；用户报告 Windows 页面代码在消息预览处结束。

## 1. 证据与当前链路

- 用户截图在代码中间结束，下方明确显示“当前为消息预览，完整内容仍保存在会话中”。这与代码块横向滚动、模型输出未完成是不同状态。
- Spec101 已提供有界历史页和 `GET /api/sessions/{sid}/messages/{sequence}/content`，按 revision、conversation_revision、可选 agent_id 和 offset 读取保存的消息 JSON 分片。
- `Conversation.tsx` 和 `ToolCall.tsx` 只有预览提示；`WebApi` 没有消费正文接口的方法。思考和修改详情已接各自入口，正文接线遗漏。
- `ls` 跳过隐藏子项是另一个问题：candidate_access 过滤发生在工具执行内部，不逐项触发 Auto。此次只解释其现状，不改变授权语义。

## 2. 最小修改

- 保留后端接口、归档、分页预算、流式/执行状态和模型上下文。
- 为有 detail、非 provisional 的预览消息增加“加载完整内容”；点击才读取该消息的全部有界分片，不预取整份会话。
- 复用现有正文接口，使用服务端 next_offset，不用 JavaScript 字符串长度推算 Unicode 偏移。验证分片版本、长度、前进和最终保存消息身份；失败不拼成伪正文。
- 完整正文替换当前浏览器中的预览，沿用现有 Markdown/代码高亮或工具原文渲染；复制回答/代码包含加载后的内容。尚有预览时复制按钮明确为“复制已加载内容”。
- 根会话与子会话使用同一实现；API 请求携带 session、agent、消息版本和会话版本，保持原认证和归属校验。
- 同一消息单一在途；加载失败保留预览并可重试。切会话/子任务、会话版本或消息身份变化时取消或忽略迟到结果，不污染新视图。
- 正在生成且只有预览的消息仍等待完成后同步，不把保存的旧正文当作当前生成完成。

## 3. 正常与失败调用链

正常：Conversation 预览按钮 → App/SubagentSidebar 的当前范围回调 → WebApi.messageContent → 现有 content GET → 既有归档查询 → JSON 分片拼接/身份校验/文本投影 → Conversation 局部视图覆盖 → 原 Markdown/ToolResult 与复制。

失败：网络或旧 revision → 保留预览，显示重试/重新同步提示；切换范围 → AbortSignal 取消，忽略旧响应。不调用模型、执行工具、修改 checkpoint 或增加数据库字段。

## 4. 影响文件

- frontend/src/api.ts、types.ts：正文加载合同与现有接口消费。
- frontend/src/components/Conversation.tsx、ToolCall.tsx：预览操作、范围隔离、正文替换和工具结果接线。
- frontend/src/App.tsx、components/SubagentSidebar.tsx：根/子范围回调。
- 新增正文 API、Conversation 回归测试；复用后端分页/权限现有测试。
- docs/handoff.md：切片完成时只更新顶部短摘要。

## 5. 验收

1. 先复现：预览已存在但没有完整内容操作；保存的长消息能通过后端分片取全。
2. 超过历史单条预算的代码可加载到尾部，保留 Markdown、高亮、原样复制及阅读位置。
3. 根/子、普通消息/工具结果、失败重试、未完成内容、并发点击、版本变化/迟到响应均有确定性测试。
4. 分片含中文/非 BMP 字符、空文本、多模态文本投影；无效分片不循环或跨版本拼接。
5. 前端全量测试与 TypeScript/Vite、后端全量、diff 检查；真实浏览器用临时离线会话验证加载前后和横向滚动。
6. 不升级依赖、不改用户配置/用户库、不发布；当前 Windows 安装包须在后续获授权的补丁发布后升级才能获得界面修复。

## 6. 实际结果与边界

- 红灯：正文 API 与 Conversation 两份新测试初跑 13 failed / 1 passed，失败对应缺少加载方法、正文按钮和部分复制标识。最小实现转绿后补齐摘要与子会话接线测试。
- 定向：`npm --prefix frontend test -- --run src/message-content.test.ts src/components/Conversation.content.test.tsx src/components/SubagentSidebar.test.tsx`，3 文件 22 passed；其中本批新增 16 项。
- 全量：`npm --prefix frontend test -- --run`，41 文件 305 passed；`npm --prefix frontend run build`，TypeScript 与 Vite 成功，526 模块。保留既有产物大于 500 kB 的构建建议，无构建错误。
- 后端：命令级 PATH 加入现有 `build/release-native-0ff7df8/bin` 后，`.venv/bin/python -m pytest -q`，2952 passed in 120.30s；没有修改环境文件。完整日志在 `build/message-content-ui/{frontend-tests,backend-tests}.log`。
- 实际浏览器：生产构建 + 临时 SQLite/工作区 + 1 次离线模型响应；保存正文 55,130 个 Unicode 字符，初始代码预览 2,051 个 JavaScript 字符，加载后代码 55,417 个 JavaScript 字符（含非 BMP 字符，计数口径不同）。DOM 与保存代码逐字一致，尾部 `END_OF_SAVED_CODE` 可见，Markdown 高亮与横向滚动正常，控制台 warn/error 为 0。截图在 `build/message-content-ui/{before,after}.png`。代码/回答的精确复制由组件测试断言；浏览器复制按钮显示成功，但 IAB 剪贴板读取为空，未将其计为系统剪贴板一致性证据。
- 权限离线对照：Auto 下 `ls('.')` 成功并返回 `.agents: unapproved_private_or_external`，随后注册 skill 的 `read` 成功，两个 Auto 审查模型调用均为 0，无人工审批。证明该现象可在当前实现下同时成立；不是用户 Windows 会话的完整请求轨迹。
- 没有供应商请求、后端/权限语义/数据库 schema/真实配置/用户库改动；本地源码修复不会自动进入已安装的 PyPI 0.1.0。发布需另行授权。

## 7. 用户确认的进一步体验：最新助手正文自动补全

本节是上一节手动入口之后的新切片，功能已实现并验证：最新非摘要助手消息若已保存且仅有预览，自动复用相同正文加载器；根/子视图一致。旧消息、工具结果继续按需加载；尚未保存的生成中消息不请求旧正文。超大高亮的后续方案与验收见第9节。

连续性合同：正常完整终稿不额外读取；已有完整正文不能被历史预览覆盖；收到的流式文字比保存预览更长且前缀一致、版本未变化时，保留已收到文字并明确仍是预览，等待全文校准。不同版本/不匹配正文不能沿用旧文字。

加载器一次取齐有界分片后只提交一次正文，不逐分片重渲染 Markdown；对未变化的正文复用 Markdown 渲染，保留代码 DOM、焦点和横向位置。失败保留已有内容并显示手动重试，不因重渲染无限自动重试。范围/版本变化取消旧请求；用户上滚阅读时保持原有锚定行为。

验收补正常流式→终稿→归档预览连续性、冷打开自动全文、未变正文不重复解析、只自动处理最新助手正文、失败/迟到响应与根子范围。实际浏览器使用用户截图中的 text 代码块检查深色样式和长正文加载，测得的渲染成本只作为本地证据，不承诺任意大小输出零卡顿。无需新端点、DB/config/schema 或依赖。

实际：新增7项（首跑5 failed / 2 passed，缺口为自动读取、连续性和重复解析），实现后5文件47项定向通过；手动入口测试改为较早消息，保留原复制/失败/取消断言，子视图验证自动接线。最终前端42文件312 passed，TS/Vite成功；后端2963 passed in118.59s，详见Spec115全量证据。生产页面冷打开保存正文55,316个Unicode字符（57,500 UTF-8字节），无需按钮即补全，代码与保存正文一致，尾部可见；控制台warn/error为0。截图 `build/latest-message-ui/{auto-full,text-dark}.png`，未用真实供应商。

## 8. 性能测量与深色问题（尚有待确认项）

- 重复解析已经消除：MessageMarkdown以正文和clobber前缀memo；进度、加载状态等无关变化不重跑解析。收到的较长流式前缀在同身份/版本且与保存预览前缀一致时继续显示；完整正文只提交一次，代码DOM/焦点/横向位置有断言。
- 实测同一ReactMarkdown/GFM/rehype-highlight配置的Node服务端解析与HTML生成：保存的57,500字节正文9.3—16.1ms；108,113字节语法较密集代码158.3—187.5ms；1,081,013字节代码8712.8—8753.8ms。后者去掉高亮为96.8—131.0ms。证据 `build/latest-message-ui/markdown-cpu.json`，不包含浏览器布局/绘制，不冒充Windows帧率。
- 先前提出整条助手正文128KiB阈值，未实施。用户核对Pi/Claude后确认第9节方案：按代码块后台计算，不采用整条消息大小开关。
- 用户截图已明确text代码块内部浅色。按相同text/未标语言/高亮代码在本机显式深色模式下验证：外框rgb(36,38,42)，pre/code透明，文字rgb(232,233,236)，无白色块；发布标签v0.1.0的theme/global/CodeBlock样式与当前相同。仅证明本机未复现，未确定用户Windows根因，未修改CSS。已请求应用主题选项、浏览器名称与是否强制深色/扩展的信息；不能将这项记为已修复。

## 9. 已确认：代码块后台高亮（2026-10-19）

当前链路：MessageMarkdown → ReactMarkdown/GFM → rehype-highlight同步转换整份树 → CodeBlock。高亮随每次正文变化在主线程重新执行，单份语法密集代码已测得秒级计算；普通memo不能保护正在变化的代码。

实施：移除主线程的rehype-highlight转换；CodeBlock先完整展示原文，以单个代码块的正文和显式语言作为任务身份。复用现有rehype-highlight依赖，在同源Vite Worker计算；正常大小也自动后台着色，不新增依赖或整条消息阈值。无语言、text/plaintext/txt及no-highlight保持纯色，不自动猜语言。标签来自模型代码围栏，缺少语言时UI默认text。

正常链：Markdown产生原文代码节点 → CodeBlock立即显示全文 → 共享单Worker依次计算 → 返回受限的文本/颜色片段 → 同一pre/code节点中更新颜色，保持逐字内容、字体度量、复制、焦点和滚动。已完成且未变代码块复用当前着色结果，不因其他块或进度更新重复计算。

失败链：Worker不支持/启动或运行失败/超时/着色节点超预算 → 只保留全文纯色，记录固定原因诊断，不记录正文、语言原值或异常正文。活动任务取消时终止该Worker并处理其余排队任务；组件卸载/正文或语言变化时移除过期任务，迟到结果不能覆盖新正文。无任务时释放Worker，不保留跨会话全文缓存。

保护预算属于渲染实现常量：单任务10秒（包含首次Worker加载），至多20,000个颜色片段；超出不返回巨大着色树，防止主线程插入无限span。该预算只影响颜色，完整文本、行数、复制不受影响；真实浏览器测量后可在本节记录调整证据。后台高亮不等于Markdown解析/浏览器布局零成本，不承诺任意输入无卡顿。

影响：Conversation.tsx、CodeBlock.tsx，新增Worker入口、转换算法、专用任务队列及对应测试；迁移原同步高亮断言为等待后台结果，最新全文测试改为观察Markdown解析次数，保留原合同。样式只允许颜色变化，不顺带修未复现的Windows深色问题。根/子仍复用Conversation，无后端/配置/数据库/API改动。

验收：先复现主线程立即着色且未创建Worker；正常语言/别名、纯文本/未知语言/HTML字面量、长正文精确复制、稳定DOM/焦点/横滚、流式过期/取消/换会话、排队隔离、超时/故障和节点预算均有确定性测试。前端全量和构建通过，生产构建真实浏览器验证Worker实际接线、长代码响应与排版，记录宿主范围和性能限制。状态：本地已实现并验证，未提交或发布。

### 实际结果与限制

- TDD：新组件回归先跑3 failed / 4 passed，确认同步着色且没有Worker；实现后转绿。新增27项（18项Worker交互、9项真实转换算法）；原同步高亮测试保留正文/复制/根子隔离断言，改为等待异步结果。jsdom无Worker，在原代码展示测试内使用协议回环、子视图测试局部替换请求为真实算法；不改全局测试设施。最新全文测试改为统计ReactMarkdown解析，因高亮已经离开主线程，旧高亮插件计数不再代表Markdown解析。
- `npm --prefix frontend test -- --run`：44文件339 passed，完整日志 `build/latest-message-ui/highlight-frontend-final.log`。首轮两项子视图同步断言需迁移；另有既有模型菜单用例瞬时失败，原样单跑及后续两次全量通过，未修改该用例或模型菜单逻辑。
- `npm --prefix frontend run build`：TS/Vite成功，321模块，独立Worker171.52 kB，主JS510.91 kB/gzip157.93 kB；既有大于500 kB建议仍在，无构建错误。日志 `build/code-highlight-ui/build-final.log`。`env PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q`：2963 passed in122.42s，日志 `build/latest-message-ui/highlight-backend-tests.log`。
- 当前宿主真实IAB、生产构建、临时SQLite与离线模型：74,412 UTF-8字节Python（66,012 Unicode字符/67,212 JS字符）全文自动加载并着色，7,201个span，DOM正文逐字一致。深色背景rgb(36,38,42)，无页面横向溢出。1,240,012字节代码（1,120,012 JS字符）自动补全到END_LARGE，保护后0个着色span，输入框可编辑；未采集输入延迟/帧率，不能据此承诺任意大小无卡顿。无语言/text继续纯色，HTML样本文字未生成script。截图与逐字证据在 `build/code-highlight-ui/`。
- 重要独立边界：最初使用默认1 MiB Web单帧预算时，超大消息的message_completed不能进入live投影，GET session仍可出现带detail的provisional/unconfirmed预览，阻止本切片按“已保存完成”自动读取。DeliveredRun超过预算标incomplete且不投影，history_window又覆盖归档的完成状态；重新同步仍保留此状态。为隔离高亮，最终1.24 MB浏览器场景仅在临时WebSettings提高max_frame_bytes到16 MiB；生产预算、后端及用户配置未改。此项记录待单独修复，不把高亮完成等同默认配置下任意大消息交付完成。
- 最终临时服务/库/浏览器均已收尾，离线模型3次、真实供应商0次，无新依赖/真实配置/用户库改动，未提交/push/发布。Windows原机深色原因仍待用户环境信息；本片未修改CSS。

## 10. 已授权：超大完成事件的有界交付与状态恢复（2026-10-19）

复现：默认1,048,576字节单帧预算，正文1,052,672字节；run completed/done，归档complete，session及再次同步却unconfirmed。已有正文接口129块逐字读回，模型1次。探针在build/oversized-message-repro/，无用户库或配置改动。

复核后的最小方案：

1. DeliveredRun先消费合法事件更新主/子消息、工具和控制投影，再按单帧预算保留出站事件。超帧不进入重放缓存；用现有_evicted_through水位要求旧游标重同步，不将可由快照恢复的运输缺口永久写成projection.incomplete。缓存后续淘汰只能提高该水位。真正事件序列/观察故障仍标incomplete并保留日志。
2. history_window增加明确的归档兜底参数，成功且已收尾的对应范围才用档案校正已覆盖同ID的旧provisional行；保留live的run_id等展示身份，内容、完成标识和正文引用以该档案版本为准。已确认live行继续保留其文本投影/图片标识，只补原有档案引用，避免大体积多模态档案的空预览替换有效文本。未归档消息不伪造完成。运行中、失败、停止和审批暂停保持实时优先，避免把旧档案冒充新草稿的完成结果。
3. 主服务先获取live快照，再查询归档，避免先读旧档案再看到已完成live的时间反转；原conversation_revision过滤保留。子服务同样先取live再读归档，只有根Web job已完成且子范围成功完成才启用归档兜底；父仍运行时，子消息完成由第一项正常投影提供，归档只补原有引用。

正常：完整事件→内存投影complete→超帧不发送/不缓存→旧SSE游标要求重同步→现有有界快照给complete预览→原正文分块自动补全→Worker着色。失败：真观察故障保留incomplete；模型中断/停止留下的草稿不升级；正文读取失败沿用已有保留/重试/版本隔离。消息确认与run/审批/必要保存状态独立。

影响3个生产文件：web/delivered_run.py、chat_history.py、service.py；新增主/子/竞态/失败回归，迁移已有frame_limit测试为“拒旧游标、接受快照新游标”，补前端从SSE缺口到自动全文的回归。不增依赖、表、端点、配置、事件类型或正文传输通道，不提高默认预算。

验收：先红灯复现超帧与旧草稿覆盖；主/子超大正文完整可读且响应/缓存有界；快照后恢复SSE，无永久不完整提示；缓存淘汰不能倒退丢帧水位；运行中旧档案不覆盖新草稿，失败/停止/审批、旧会话版本、范围切换均保持；真实归档正文逐字一致、模型/工具不重跑；读取顺序有确定性竞态测试；全量后端/前端与生产构建、默认预算真实浏览器验收。状态：本地已实现并验证，未提交或发布。

### 实际结果

- 先红灯：默认帧限制/旧草稿归档校正两项失败、运行中保护一项通过；再补的帧恢复/水位/子完成三项均失败。最小实现转绿后发现归档大图片空预览覆盖有效实时文本投影，按上面第2项收窄为只恢复provisional旧行，原图片断言原样通过。
- 新增后端14项：test_web_large_completion.py的12项覆盖默认1MiB、全正文分块精确性、成功后旧草稿、同ID新流、读归档期间完成的竞态、真实失败/停止、五种未成功收尾边界、父仍运行的超大子回复及子归档；replay_budget新增2项覆盖单帧缺口水位与子隔离。旧frame_limit测试迁移为保留旧游标拒绝、新游标恢复、原文预览/响应预算断言；新增测试的SSE帧断言保留实际id字段，没有删除检查。未修改conftest或共享helper。
- 前端没有生产改动；新增2项验证resync_required后读取终态快照且不发start/approve/control，以及超过1MiB的旧草稿确认后自动加载全文。既有加载失败、范围/版本隔离、父子与DOM连续性断言保持。
- 最终后端：命令级PATH加入现有build/release-native-0ff7df8/bin后，`.venv/bin/python -m pytest -q`，**2977 passed in121.58s**，日志`build/oversized-message-fix/backend-full.log`。前端`npm --prefix frontend test -- --run`，**44文件341 passed**；`npm --prefix frontend run build`，TS/Vite321模块成功、0错误（原有主JS>500kB提示仍在）。日志同目录frontend-full.log/build.log，pip check、compileall、500行与diff检查通过。
- 真实IAB/生产构建/临时SQLite/离线模型：默认1,048,576字节预算不变，1,240,012字节Python回复冷打开自动补齐，DOM为1,120,012 JS字符，与预期逐字一致且END_LARGE可见；预览/未完成/部分未同步提示均消失，重新同步按钮0个。截图full-default-budget.png、browser-result.json和服务ready.json位于build/oversized-message-fix/。只有预期高亮保护warn 1条，没有浏览器error；高亮保护不截断正文，不冒充Windows/帧率证据。
- 浏览器验证离线模型3次、供应商0次，临时页/服务/库已关闭清理。没有调整默认预算、实际配置、用户库、DB schema、依赖、模型/权限/工具执行链，无提交/push/发布；此前第9节发现的交付缺口在本节关闭，Windows独立深色问题仍待环境信息。
