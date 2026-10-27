# Spec130：输入框截图粘贴与图片消息

状态：**D1—D5 已确认，用户已授权连续完成五片；130-01—05均已完成本机验收**。不修改真实配置、调用真实供应商、提交或发布。最终证据见各片Spec及130-05。

代码基线：`codex/project-execution-root`，本轮核对 HEAD `581f833`，R1-13D 已交付；`.venv` 与 `research/` 是已有未跟踪内容。本 Spec 面向该工作树，主目录旧实现不直接套用。SSH14 仍等待公司环境，不与本功能绑定。

## 1. 用户场景与完成范围

用户截图后在消息框按 Ctrl/Cmd+V，看到可预览、移除的图片，补充“看下这个错误”，点击发送。模型实际收到图片；发送后以及重开会话后仍能查看图片。图片输入不依赖 read 工具，不要求把文件写入项目或沙箱。

本期范围：粘贴／拖拽／文件选择、多图、仅图片发送、缩略图及大图、草稿范围隔离、模型能力检查、失败保留、聊天持久化与历史查看。继续使用 textarea 和现有 React／消息／存储链。

**D1 已确认（2026-10-26）**：用户“可以，那就一起做了”确认本轮同时支持图片 steering／follow-up，普通发送、队列注入和停止保留共用完整输入。按五片规划，新增独立的队列与停止保留分片。此确认是范围决定，其余确认见第2节，不表示已经批准开始产品实现。

不纳入本期：通用文档／音视频附件、截图编辑／标注、OCR 或另一个模型代看、原生截图快捷键、剪贴板后台监听、浏览器重启后的未发草稿恢复、自动上传到沙箱／第三方文件服务。它们不因“看齐 ChatGPT”自动取得实施授权。

## 2. 已确认的需求决策

D1—D5 已由用户依次确认，下表为实施合同；若后续改变选择，先同步 Spec 与影响面，不由实现者自行替换。

| 编号 | 决策 | 已确认选择与理由 | 其他选择及影响 |
| --- | --- | --- | --- |
| D1（已确认） | 运行中图片是否进入 steering／follow-up？ | 本轮一起支持；扩展已有队列与消费边界，保留文字和图片的对应关系。用户2026-10-26已确认，五片规划。 | 原“空闲发送、运行中仅留草稿”建议已撤回；不因支持队列自动承诺应用重启后的未消费输入恢复。 |
| D2（已确认） | 不支持图片的模型如何处理？ | 用户2026-10-26确认：本次新附图必须使用视觉模型，未知也阻止发送并保留完整输入；图片引导／跟进提交及消费时均核对。历史图允许文字模型明确降级，界面与模型请求均说明未提供像素，已保存图片保留。 | 不自动丢图后只发文字、不自动切模型／OCR；不因历史曾有图片就禁止后续纯文字聊天。 |
| D3（已确认） | 未发送草稿保留多久？ | 用户2026-10-26选择A：当前页面内切换项目／会话保留对应文字和图片；刷新、关闭页面或应用重启后不承诺恢复，附件区明确提示。 | 本轮不增加 IndexedDB／服务端持久草稿；此决定仅限尚未提交输入，不改变已发聊天保存及服务已接受队列的D1归属合同。 |
| D4（已确认） | 图片数量／大小与缩放默认值？ | 用户2026-10-26确认采用第6节整组默认值，最长边提高到4096；数量、原始／编码／请求字节、像素、队列额度及处理并发数全部可配置，前端限制从服务获取，不另硬编码。 | 4096是上限；仍受单张／合计编码预算约束，必要时继续有界缩小或明确拒绝，不保证供应商接受任意大小。 |
| D5（已确认） | 保存原始截图还是应用发给模型的版本？ | 用户2026-10-26选择A：只持久保存经过方向修正、缩放／编码后实际发送的版本，历史预览／下载使用同一版本，不额外持久保存未经处理的上传原文件。 | 后续提高预算不能还原已经舍弃的原始像素，需要用户重新提供原图；本轮不建设原图双份保存／下载能力。 |

技术上无需重复确认：沿用 Python + create_agent、自有本地服务、现有 Pillow 和模型能力元数据；不根据模型名称猜视觉能力，不引入新依赖。D1—D5 已定，当前无已批准的实现片。D2的保留指已保存聊天图片不因模型切换被删除；D5明确只保存处理后的发送版本，不额外保存上传原文件。

## 3. 当前主链与已验证事实

| 当前入口 | 真实职责／缺口 |
| --- | --- |
| [ComposerEditor](../../frontend/src/components/ComposerEditor.tsx)、[Composer](../../frontend/src/components/Composer.tsx) | textarea 文字、键盘、指令／技能补全；没有 onPaste 图片入口、附件区及图片发送条件。 |
| [useConversationDrafts](../../frontend/src/useConversationDrafts.ts)、[useConversation](../../frontend/src/useConversation.ts) | 按会话保留 string 草稿／string[] pending；submit 只接受非空文字，新会话先 prepare/configure 再 start。 |
| [WebApi](../../frontend/src/api.ts)、[MessageInput](../../naiyue_test_agent/web/schemas.py) | start 的请求只有 request_id/text/conversation revision，文字不能为空；ControlInput 也是纯文字。 |
| [RunDelivery._produce](../../naiyue_test_agent/web/run_delivery.py) | 构造 HumanMessage(content=body.text)，进入 [RunAPI.start](../../naiyue_test_agent/application/sessions/run_api.py)。 |
| [tools/images.py](../../naiyue_test_agent/tools/images.py) | 现有 Read 的输入／像素预算、实际解码、EXIF、首帧和编码处理。read_image 依赖已打开文件的 fileno/fstat，不能直接假设 BytesIO 能调用整个 read_image。 |
| [prepare_model_messages](../../naiyue_test_agent/model_access/messages.py) | image_inputs 仅 True 才发送像素；否则将请求副本中的图片替换成明确文字，原始历史保留。CC 的工具图片载体只在请求副本中生成。 |
| [serialization](../../naiyue_test_agent/web/serialization.py)、[chat_history](../../naiyue_test_agent/web/chat_history.py) | 在帧／列表限制前投影成文字；当前没有可供前端请求的图片引用。 |
| [IdempotentCommands](../../naiyue_test_agent/service/receipts.py)、[ServiceOptions](../../naiyue_test_agent/service/settings.py) | 认证先于读取命令正文；默认 max_frame_bytes=1MiB 同时限制请求体与响应，图片请求不能直接复用这个额度。 |
| [ControlQueue](../../naiyue_test_agent/application/sessions/session_controls.py) | QueuedInput.text 进入 HumanMessage；停止把尚未消费的队列转成文字 retained，之后队列收尾清理，不能据此承诺图片队列恢复。 |

上一轮无网络请求探针核对安装版本 langchain 1.4.2/core 1.6.6/langgraph 1.2.12/langchain-openai 1.6.6：用户标准图片块在 CC／Responses 的 payload 中保留；image_inputs=False 时像素被省略、包含说明、原始消息不变。探针只装配合成 payload，不证明图片解码／供应商接受或识别效果；不是本次实现验收。

## 4. 推荐架构、对象与所有权

```text
ComposerEditor 的 paste / form drop / 加号选图
    → 当前 scope + session/new 的 ComposerDraft（文字、图片、版本）
    → WebApi.start：冻结 request_id 与图片内容
    → 认证、实例核对、图片请求额度与幂等
    → 应用图片校验／规范化（worker），随后按真实模型复核能力
    → HumanMessage(text + image blocks)
    → 既有准入／模型请求适配／checkpoint／聊天归档
    → Web 小型图片元数据 → 鉴权 GET 图片 → 本地 Blob URL 预览
```

- 浏览器草稿是未发送输入的唯一编辑来源：文件和 Object URL 不进入服务端、不创建会话／沙箱。新建选择仍只改草稿。按已确认D3选择A，页面内切换项目／会话保留各自图文，不增加刷新／关页／应用重启恢复；未提交草稿与服务端已接受队列分开管理。
- 一次发送冻结独立的输入版本、请求 ID、文字及附件字节。处理中不变更该快照；新编辑不覆盖已发送内容，回执只确认对应版本。未知网络结果先核对原请求／运行，不换 ID 盲目重发。
- 应用产生经校验的标准 HumanMessage；用户不能上传 arbitrary role、additional_kwargs、文件路径、外链 URL 或伪造模型能力。
- 已提交的图片以标准内容块保存在现有 checkpoint／聊天 body_json 中，归属于会话；图片元数据只是派生视图。按D5仅保存处理后的实际发送版本，历史预览和下载均取该版本，不额外落盘原始上传文件。不新增文件库、独立上传服务或图片临时目录，不引入数据库表／DDL。
- 图片随数据库备份／恢复；与不包含在备份中的 Shell artifacts 不同。会话删除沿既有数据库删除事务清理，不按 checkpoint 修剪数量淘汰聊天图片。此合同不承诺文件物理空间立即归还操作系统。
- 每次只规范化有限图片，Pillow 运行在 worker。短会话／数据库锁不包住解码、编码、HTTP 或 await。
- 服务已接受但尚未进入 HumanMessage 的队列图片，由应用的会话所有者保留有界输入记录，不依赖最初的浏览器或 Web 事件缓存。停止后保留记录可从同一会话的其他浏览器查询；保存成功转入 canonical 后可释放重复数据。关闭应用不承诺恢复尚未消费队列，与当前文字队列的持久性边界一致。

### 浏览器与请求对象

`ComposerDraft` 为内部对象：`text`、有序 `images`、草稿版本。图片包含本地 id、File/Blob、展示名称、MIME、原始大小和预览 URL；这些浏览器对象不直接 JSON 化。

已有 POST `/api/v1/scopes/{scope_id}/sessions/{sid}/runs` 继续使用 request_id／expected_conversation_revision／text，新增可选 `images` 数组（缺省空）。每项只允许 `client_id`、`name`、`mime_type`、`base64`；图片字段不接受 data URL 前缀／远程 URL。text 缺省空，但文字去空白与 images 不能同时为空；原纯文字调用保持有效。

client_id 只用于本次草稿配对，不作为读取权限。名称按普通展示文本处理、限 255 个字符，不参与文件路径；MIME 必须在允许范围内且与实际解码一致。客户端 ID／图片顺序／字节均参加请求指纹；同一 request_id 改图返回 request_id_conflict。

包含图片的请求使用 `X-Image-Input: 1`，服务只在已认证、实例正确的上述 runs 路由及 steering／follow_up controls 请求启用图片请求预算。没有该标志仍受原小请求上限约束；标志不能给任意 API 放大正文额度，带标志而没有合法图片也明确拒绝。

ControlInput 对 steering／follow_up 增加相同 images 字段，允许仅图片；stop 不接受文字／图片正文。旧纯文字 HTTP／CLI 调用继续有效，内部队列只保留一套完整输入。停止响应保留已有 retained 文字投影，新增 retained_inputs 小型元数据给新 Web 使用；像素通过会话归属校验后的待处理图片读取接口取回，不进入控制回执／SSE。已有 CLI 是保留文字投影的实际消费者；有图片时明确提示可在 Web 待发送列表查看，不能假装只取回文字即已完整保留。

## 5. 模型能力与失败合同

- `/models` 在现有 model_ids／thinking_options 上新增 `capabilities[id].image_inputs`，来自实际加载模型的 profile；未知对用户图片输入按不可用处理。前端不维护另一个模型白名单。
- 新会话发送前，浏览器先核对视觉能力和本地预算，再进入现有 prepare/configure，减少明确不合格输入带来的空会话／环境准备；服务对现有会话使用其实际选择，不信任浏览器状态。
- 应用在执行前校验本次直接附图的能力，明确失败不启动模型／工具。规范化期间改模型时复核真实选择，不沿用旧布尔值。
- 运行开始后仍允许既有切模型行为；每次模型调用前校验本轮直接附图是否要求视觉能力。内部 additional_kwargs.naiyue_image_origin_run_id 记录实际 run_id，输入、重新生成和恢复分别核对；HTTP 不接受用户提供该标记。改为无视觉能力模型时在调用 SDK 前明确失败，不能把本轮新图当历史图悄悄省略。
- 其他轮次的历史图保持现有请求副本降级，界面提示“当前模型不会接收历史图片像素”；原始内容、摘要和 canonical history 不被修改。Read 工具的既有图片降级合同保持。
- 接受前的校验／容量／网络失败：完整草稿保留。接受之后模型或网关失败：已发送图片保留在消息中，展示错误，用户可明确重新生成；不自动复制为第二条任务或降级重发。
- image_inputs=True 仅为能力声明。网关明确拒绝会显示错误；网关成功但忽略图片无法由应用可靠自动识别，必须以真实视觉样本另证。

对外错误保持现有 `{error: {code}}` 包装；新码为 `model_image_input_unsupported`（422）、`image_input_invalid`（422，字段/base64/MIME不合法）、`image_input_format_unsupported`（422）、`image_input_too_large`（413，原始／规范化预算）、`image_input_pixel_limit`（422）、`image_input_busy`（429，未预留执行）。现有未通过整体正文上限的 `request_too_large`（413）、幂等冲突 `request_id_conflict` 和会话忙 `session_busy` 不改名。只有确认执行前拒绝的图片错误可释放原命令预留；provider／网络未知结果不能套用这条。ControlInput 的 stop 携带 images 返回 invalid_request，不丢图后当纯停止接受。

## 6. 已确认配置默认值、格式与性能边界（D4）

用户已确认以下预算全部做成配置，最长边为4096。新增严格、不可变的 `[image_inputs]`，由既有 AppSettings loader 读取并通过服务能力／限制响应提供给浏览器；默认值集中定义，前端／请求入口／队列／编码器消费加载结果，不各自写死。无真实配置改写。前端仅提前检查，后端是权威。模型 profile.image_inputs 保持在模型配置，不搬到这个设置组。

| 配置项 | 已确认默认 | 语义 |
| --- | --- | --- |
| max_images_per_message | 4 | 单条用户消息图片数 |
| max_input_bytes | 10 MiB | 单张原始文件字节 |
| max_total_input_bytes | 20 MiB | 本条图片解码 base64 后的原始字节合计 |
| max_pixels | 40,000,000 | 单张解码像素，并同时服从 Pillow 更严格的安全阈值 |
| max_dimension | 4096 | 发送版本最长边上限，等比例缩小；不裁剪、不拼接，编码仍超限时继续有界缩小或拒绝 |
| max_base64_bytes | 4.5 MiB | 单张规范化后 base64 长度 |
| max_total_base64_bytes | 12 MiB | 本条规范化图片 base64 合计，超过整条拒绝，不丢最后几张 |
| max_request_bytes | 28 MiB | 图片专用路由的完整 JSON 字节上限，包含 base64／文字／元数据 |
| max_in_flight_requests | 2 | 同一服务图片请求从读正文到规范化／交付的并发数，忙时明确拒绝，不排队囤积大正文 |
| max_pending_image_messages | 16 | 单会话尚未进入已保存聊天的图片输入数，包含排队与停止后保留记录，不按每条队列分别绕过 |
| max_pending_image_bytes | 32 MiB | 单会话上述输入的规范化 base64 合计；移除、明确重发成功或转入已保存聊天后释放；应用关闭清理 |

字节配置以整数存储，名称明确是原始／base64／完整请求，文档显示 MiB。loader 验证正整数、数量与预算关系；请求上限必须容纳最大合计原始字节的 base64、现有 32768 字符文字上限及受限元数据。不能只验证每个数大于零。测试须用非默认值核对loader→服务限制响应→浏览器校验及服务接收／编码／队列的实际消费者，防止仅声明字段却未接入。现有 `[tools.read.image]` 的默认 32MiB 原始输入保持，两个场景独立配置，复用图片算法。

格式沿用现有 PNG/JPEG/WebP/GIF/BMP；GIF 使用首帧并显示说明。SVG、PDF、HEIC 和伪装格式明确拒绝，不当远程资源下载。实际解码确认格式，检查像素再 load，修正 EXIF，不将 EXIF／GPS 元数据复制进输出。保持有界缩放／编码次数和透明度规则，不修改 Pillow 的全局阈值／warning 过滤。

图片准入计数归既有 service command 生命周期，不创建通用资源注册平台：进入大正文读取前预留；断线且尚未交付 producer 时释放；已接受 command 后由所属 task 收尾释放，不能仅因 HTTP caller 取消而提前释放。解码／编码在 worker 内，取消等待已经启动的 IO 结束。实现如新增异步 gate，必须同步更新锁层级表，不能持同步短锁等待 gate。

普通文字的原请求额度、响应／SSE max_frame_bytes 与 receipt_cache_bytes 保持原语义。图片字节不写 response receipt，不存进 Web events/projection 缓存；快照只缓存摘要元数据。预算失败在图执行前返回，错误只含安全错误码和数值界限，不记录图片／完整请求。

## 7. 历史图片与展示协议

Web 消息保持文字 content，新增小型 images 元数据：index、name、mime_type、width、height、规范化字节数、image_digest、消息 source_revision。image_digest 是规范化二进制的 SHA-256；source_revision 按现有聊天归档 message_to_dict/body_json 的序列化规则计算，不能使用移除像素后的 Web 投影哈希代替。只从 canonical 消息生成；根消息 ID 与所属会话一起参与定位。没有图片的旧记录不造附件。

新增 GET `/api/v1/scopes/{scope_id}/sessions/{sid}/messages/{message_id}/images/{index}`，必填 source_revision 与 conversation_revision；只读，不带 token 到 URL，不接受路径／外部 URL。应用先核会话归属与消息版本，再从已保存 body_json 取图；当前 run 尚未归档时，只允许通过本会话官方 graph state 读取与消息 ID／内容哈希相符的版本。先释放 DB 读事务再 await saver，不反向嵌套数据库／saver 锁。

来源尚未提交时返回 image_not_ready（409），前端保留本地预览或明确占位，只在新消息快照到达或用户点击重试后重新读取，不设高频轮询；版本变化返回 image_revision_changed（409）并刷新消息，越界／不存在／跨范围统一 image_not_found（404），不泄漏内容。大图有独立字节上限，不沿用小 JSON frame 上限。只返回允许的规范化 image MIME，设置 no-store 与 nosniff；浏览器使用现有认证 fetch 取得 Blob 后显示，移除／替换／退出范围时撤销 URL。

本片只为用户直接附图增加可视化，历史 Read 工具图片仍保持现有文字投影。保存新用户图在 checkpoint、聊天归档和重新生成中保留同一组内容；压缩只在现有策略允许时用摘要替换模型上下文，不因此删除聊天原图，也不将 base64 当可检索文字或写入记忆摘要。

## 8. 正常与失败调用链

**正常**：Composer.onPaste 取得用户 File → useConversationDrafts 当前范围保留 → ImageAttachments 显示预览 → submit 冻结输入 → 确认模型能力 → prepare/configure → WebApi.start → IdempotentCommands 认证／额度／指纹 → 应用规范化并构造 HumanMessage → RunAPI.start／现有准入 → 模型请求含图 → 必要保存 → 历史投影给出图片元数据 → 鉴权 GET 取图。没有 read 调用，不产生工作区／容器图片文件。

**能力失败**：草稿有图片，模型选择后 image_inputs 不为 True → 前端提示且不创建会话；直接绕过前端请求则应用入口复核并在模型／工具调用前拒绝。文字和图片都保留，用户明确切模型或移除图片才提交。

**网络结果不确定**：冻结请求已经交付服务但回执丢失 → 同一请求身份查询／核对原结果 → 不创建第二个 run；当前模型／附件改选作为新的草稿，不能覆盖原请求正文。服务实例已变时按既有恢复入口核对保存状态，不把连接断开当成未执行。

**输入错误**：先按网络字节额度有界读取，再做 base64／实际格式／像素／规范化预算校验；整条原子拒绝，模型调用为零，不保留部分图片到会话。

**运行中附图（已确认 D1）**：同一消息的文字＋图片规范化后进入原 steering／follow_up 队列，沿原注入边界构造 HumanMessage；消费时再次核对实际模型能力。复用已有消息／控制 ID 和消费观察，不创建平行的独立消费状态机。只向队列增加输入，不另起并行模型推理。

**停止与不确定消费**：尚未从队列选取的完整输入进入待发送保留记录；已经选取但是否进入模型上下文未确认的输入显示不确定，先核对原消息／控制观察，不自动重发；确认进入聊天记录的图片继续留在已发送消息，不退回待发送造成重复任务。停止、删除、容量失败、caller 断线与保存失败均不得只保留文字或提前释放仍被队列／用户保留记录引用的图片。

## 9. 预计影响面与迁移纪律

涉及超过 8 个文件，按现有职责拆分，不让 useConversation 超过 500 行。实施中的具体职责与文件名见各片Spec；发送逻辑独立到conversationSubmit，完整草稿由useConversationDrafts持有，图片请求状态与队列保留记录各自按实际所有者维护。

| 层 | 扩展现有文件 | 有当前消费者的新模块／职责 |
| --- | --- | --- |
| 配置／图片处理 | `settings.py`、`tools/images.py`、配置示例与 loader 测试 | `image_input_settings.py`：本场景严格预算；Read 与用户输入调用同一个字节解码／规范化算法，Read 保留权限后打开流和 fstat 的 IO 合同 |
| 应用／模型 | `application/catalogs.py`、`application/sessions/run_api.py`／`session_execution.py`、`model_access/messages.py` | `application/conversation/image_inputs.py`：输入验证、标准消息和本轮视觉要求；HTTP 与实际模型请求各自消费对应结果，不重复解码 |
| 队列／停止 | `contracts/replies.py`、`application/sessions/session_state.py`／`session_controls.py`、控制观察、`web/delivered_run.py`／`run_delivery.py`、CLI 回执和前端 pending 入口 | 扩展现有输入／消费记录，增加会话持有的待处理图片查询和容量释放；不把未消费图片伪装成已保存聊天，不用新浏览器缓存冒充服务端所有权 |
| 服务／Web | `service/receipts.py`、`service/settings.py` 的接线、`web/schemas.py`、`web/service.py`、`web/run_delivery.py`、`web/routes.py`、`web/serialization.py`、`web/chat_history.py` | `contracts/image_inputs.py`及`web/image_queue_routes.py`：受限 DTO／待处理媒体响应；历史媒体接既有routes；不把解码与 SQL 放 HTTP handler，不加第二套鉴权 |
| 历史／存储 | `application/conversation/api.py`、`storage/conversation.py` 与相应 SQL／消息查询入口 | 增加单条、版本校验后的图片读取；已有 JSON 内容承载，无新表；必要时沿现有 SQL 模块拆分，不把 SQL 提到应用 |
| 前端 | `App.tsx`、`api.ts`、`types.ts`、`useConversationDrafts.ts`、`useConversation.ts`、Composer/Editor/Controls、Conversation 及对应样式 | `useComposerImages.ts`：本地 File、预算和 URL 生命周期；`ImageAttachments.tsx`：真实缩略图与删除；`MessageImages.tsx`：鉴权图片查询与查看，不接管 Markdown |
| 文档／门禁 | README、配置样例、handoff、future-work、必要锁表与受影响测试 | 新测试使用下表固定入口，不修改测试框架、依赖、版本或真实 `.env/settings/models` |

先搜索再扩展；旧纯文字公开请求仍有 Web／CLI 实际消费者。迁移后内部草稿只能有一个权威对象，不能同时维护平行 string/image map 并要求各处人工同步。原 read 图片测试、指令／技能补全、中文输入法、XML 文字、环境准备／权限失败断言保持。

## 10. 五片规划与独立验收

| 片 | 具体交付 | 主要验证／完成边界 |
| --- | --- | --- |
| 130-01 图片消息入口、能力与有界处理 | 配置加载／暴露、模型能力 API、可选 images 请求、规范化、请求额度与准入、幂等、应用／每次模型请求能力门禁；纯文字兼容，前端 UI 尚未接 | 新 `test_composer_image_inputs.py`、`test_web_image_inputs.py`；复用 `test_model_image_messages.py`／Read 图片测试。HTTP 从 PNG 到模型 payload／保存内容走通；伪格式、超限、未知能力、取消、改模型与重复请求分别断言，无供应商调用。 |
| 130-02 图片持久化查询与小型视图 | 消息元数据、认证图片 GET、实时／冷历史版本核对、压缩／重新生成／删除／备份恢复；不改模型与工具身份 | 新 `test_web_image_inputs.py`／`test_user_image_lifecycle.py`；保存→关闭→重开→取图一致，篡改范围／revision 拒绝；SSE／列表无 base64；备份恢复图片可用，删会话后不可访问；此时可通过 HTTP 验普通发送与历史后端。 |
| 130-03 图片队列与停止保留 | 结构化 steering／follow_up、消费时能力复核、会话持有的待处理图片、跨浏览器查询、停止／不确定消费、元数据回执、旧文字／CLI 消费者和容量释放 | 新 `test_image_control_queue.py`、`test_web_image_limits.py`，扩展控制队列／停止／HTTP 测试；两种队列、只图／图文、消费前／选取中／已入消息三种停止边界、重复请求、跨会话拒绝、保存／取消失败和配额释放。此时用 HTTP 验证完整后端，尚无普通用户图片 UI。 |
| 130-04 输入框完整图片交互 | 粘贴／拖入／选图、多图／仅图、缩略图与大图、能力提示、范围草稿、首次发送、失败／未知回执、图片引导／跟进、停止后待发送和历史展示 | 新 `image-composer-ui.test.tsx`、`MessageImages.test.tsx`、`image-inputs.test.tsx`、`image-submit-recovery.test.tsx`，扩展 Composer／环境草稿／会话测试。模拟剪贴板字节，不以 input[type=file] 通过冒充真实截图粘贴。验证 URL 释放、切范围、迟到响应和编辑期间回执归属。 |
| 130-05 集成验收与标准候选 | 前四片全链复核、浏览器真实粘贴、桌面／375px、明暗主题、性能／容量观察、全量与标准包安装，补实际主链／证据 | 认证隔离服务＋离线模型；macOS 实际截图粘贴，Windows 按其原机证据单列；恢复／停止／重新生成／预算边界。标准构建与源外安装验证包内入口，真实视觉模型另列有界集成证据。 |

顺序为 01 → 02 → 03 → 04 → 05。每片先见预期失败，再实现与回归；完成后报告真实结果及下一片，不把“已有合同”称为 UI 已接通。04 前仍无可供普通用户使用的粘贴按钮；05 前不报告完整验收完成。

03 实施前需在其子 Spec 固定待处理图片读取／保留／移除接口和原子交接，再检查 02 的复用边界。待消费截图还没有 canonical HumanMessage，不能套用第 7 节“只读已发送消息”的 GET；不能把完整 base64 塞进 retained 回执绕过帧额度。该工程接线设计不重新讨论已经确认的 D1 范围，也不先行实施。

## 11. 验证命令、证据与回退

01：`.venv/bin/python -m pytest -q tests/test_composer_image_inputs.py tests/test_web_image_inputs.py tests/test_model_image_messages.py tests/test_read_images.py`。

02：`.venv/bin/python -m pytest -q tests/test_web_image_inputs.py tests/test_user_image_lifecycle.py tests/test_web_images.py`，并运行实际受影响的恢复／重新生成／存储事务测试。

03：`.venv/bin/python -m pytest -q tests/test_image_control_queue.py`、`test_web_image_limits.py`，并运行受影响的控制队列、停止保留、控制观察与 Web／CLI 回执回归。

04：前端目录 `npm test -- --run src/image-composer-ui.test.tsx src/components/MessageImages.test.tsx src/image-inputs.test.tsx`、`image-submit-recovery.test.tsx`，再跑前端全量与 `npm run build`。

05：`.venv/bin/python -m pytest tests -q`（如沿现有分组门禁，必须覆盖全部产品测试文件且保留两组结果）、前端全量／TS-Vite、`.venv/bin/python -m scripts.build_release --target macos-arm64 --output <新构建目录>`、`.venv/bin/python -m twine check <新构建目录>/*.whl`、源码目录外全新环境安装 smoke、`git diff --check`。按既有构建方式验证标准资源，不改发布矩阵；实现验收阶段不提交／发布，后续Git交付按用户单独授权执行。

验收必须显式断言：新附图不被降级；文本模型／图像异常零供应商调用；真实选中模型改动被门禁覆盖；规范化后图片实际进入 SDK payload；取消不提前释放处理名额；同 ID 改图冲突／丢回执不重复任务；历史和待处理图片读取无法跨 scope／会话／版本；有图消息不撑爆 SSE／receipt；原工具图片载体不入 canonical；停止不丢未消费的图文输入、不重发不确定／已消费输入；摘要／记忆不收 base64。

真实模型验收单独选择用户授权且配置正确的视觉模型，使用不含敏感内容、含确定图形／短文字的截图，核对直接附图无需 read 即能回答。未授权则不调用，状态明确记录“本地功能验收通过，真实供应商未验”；本轮 Spec 不取用任何真实密钥、改配置或请求模型。

回退不需要数据库逆迁移；旧版本可读原有图片内容块并按旧文字方式展示，但不具备附件 UI。回退会丢失当前页面未发送草稿，必须先由用户保留。错误不自动 OCR／切模型／重跑工具；不删除用户图片或 lock 文件来恢复。

## 12. 参考证据与本轮记录

- [OpenAI 官方图片输入说明](https://learn.chatgpt.com/docs/image-inputs)：支持附件／粘贴／拖拽作为视觉上下文；不将官方未说明的草稿恢复／模型降级行为当成事实。
- [MDN paste](https://developer.mozilla.org/en-US/docs/Web/API/Element/paste_event) 与 [getAsFile](https://developer.mozilla.org/en-US/docs/Web/API/DataTransferItem/getAsFile)：使用用户触发的粘贴事件，保留普通文本行为，不调用后台剪贴板轮询。
- [LangChain messages](https://docs.langchain.com/oss/python/langchain/messages)：HumanMessage 标准多模态内容块；以本机安装版本 payload 探针和集成测试确认接线。
- 既有 [085-11 图片 Read](085-11-read-images.md) 的预算／canonical／请求副本合同可复用，不能将工具图片读取等同于输入框已支持附图。
- 上轮源码对照的 Pi `a69bef789`：`handleClipboardPaste` 将剪贴板图落临时文件并插路径，prompt／队列支持图片，transformMessages 对无视觉模型使用占位；AgentX `3fcfa0e`：用户消息记录 fileUrls，buildHistoryMessage 转 ImageContent。它们证明的是各自链路，不能证明本项目已经具备附件保存或直接照搬其降级。

设计确认阶段仅更新文档和待办，当时没有产品实现与功能验收；此处保留为历史证据。D1图片引导／跟进、D2视觉门禁／历史图明确降级、D3选择A页面内未发草稿、D4最长边4096及全部预算可配置、D5选择A仅保存处理后版本均已确认。用户随后已授权连续完成五片；本段为设计阶段历史说明。当前实现与验收记录见下文。


## 12. 实施与最终验收入口（2026-10-26）

用户明确授权持续开发五片，中间只为新决策停下。各片合同与验证：[01](130-01-image-message-entry.md)、[02](130-02-image-history.md)、[03](130-03-image-control-queues.md)、[04](130-04-browser-image-composer.md)、[05](130-05-verification.md)。使用说明见[图片输入](../composer-images.md)。

实际接口细化：控制与普通发送支持pending_input_ids显式释放已恢复的保留输入；控制的空text在有图时有效。规范化块的client_id标识本次用户图片，内部run来源标记只作用于这些图片；既有程序化历史/工具图片投影合同保留。读取运行身份的ModelInputMiddleware移至agent_runtime/model_input.py，模型接入层只做内容投影。历史metadata包括GIF仅首帧标记。额外请求状态只用于不确定图片请求的同ID重试，不是第二套草稿或队列。

当前正常链：Composer paste/drop/file→useComposerImages→useConversationDrafts完整输入→conversationSubmit冻结请求→WebApi→认证/图片额度→RunAPI.prepare_input worker规范化→RunDelivery/原准入→RunLifecycle来源标记→运行层ModelInputMiddleware→模型→现有必要保存→metadata→认证历史字节GET。

失败链：入场拒绝保留草稿；HTTP未知结果冻结原请求ID与正文，新草稿独立保留；图片队列未选中转retained、选中未确认转uncertain，必要保存确认后释放；历史GET检查会话归属、conversation_revision和canonical source_revision，像素不进入SSE/普通详情。Pillow不修改全局阈值或warnings过滤。

最终数字及平台边界在130-05追加，不把单元测试或离线模型当作真实供应商/Windows/Linux视觉证明。


最终结果：产品5532项覆盖通过（5528分批全量及4项随后新增配置用例专项），前端68文件543项、构建、Mac ARM64 wheel/Twine/源码外干净安装及图片smoke通过；供应商请求0。当前无剩余开发片，边界与复验命令见130-05。pending列表用服务器唯一输入ID及轻量metadata，完整文字按frame/detail预算分块，图片处理不持有控制锁；仅图草稿也按完整输入清空重置发送模式。
