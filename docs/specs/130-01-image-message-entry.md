# 130-01：图片消息入口与有界处理

2026-10-26用户授权连续完成Spec130五片。本片按已确认D1—D5实施，保留已有文档及`.venv/research`；不提交、push、发布或修改真实配置。队列和前端分别在03、04接入，不为等待下一片停止工作。

正常链：认证消息请求→图片请求名额／字节上限→严格ImageUpload→RunAPI准备输入（实际模型能力）→worker规范化→HumanMessage→现有准入→本轮图片来源标记→每次模型请求门禁→必要保存。纯文字仍走原协议；新增capabilities和image_input_limits为前端下一片提供真实信息。

失败链：非法MIME/base64、格式／像素／字节超限、模型能力未知或False均在执行前明确拒绝；处理期间取消排空worker，名额由实际生产任务释放；重复请求不再执行，相同ID改图冲突。已开始后切为文字模型，在SDK调用前拒绝本轮新图片，旧历史图继续既有明确降级。

配置：新增集中ImageInputSettings、AppSettings.image_inputs与严格TOML接线，十一个字段采用主Spec默认值（最长边4096）。完整请求预算包含原始图片base64和文字／元数据；不扩大普通文字、响应或SSE额度。受保护路由的X-Image-Input只选预算，不作为授权；认证仍先于正文缓冲。

复用：tools.images抽出同一套字节解码方法，Read仍在权限后打开流、fstat和有界读取；应用输入复用实际解码／EXIF／首帧／编码，不伪造文件路径或调用Read工具。标准内容块加上传名／client_id元数据，原始字节不额外落盘。

接口：MessageInput.images缺省[]、text缺省空，至少有一项有意义；RunAPI.start既有messages参数保持。增加准备输入入口给Web adapter；模型capabilities来自Catalog实际profile，通过DeferredModels和ModelAPI暴露。每次模型调用使用内部naiyue_image_origin_run_id及当前run_id区分新图／历史图，HTTP不能设置内部标记。

处理准入由CommandReceipts持有的图片lease管理：读取正文前非阻塞预留，交付producer后转移所有权，缓存命中／前置拒绝／断线未交付由caller释放。必要IO不在同步状态锁内。新增gate／嵌套同步锁表。

影响：image_input_settings、contracts/image_inputs、application/conversation/image_inputs、settings/示例、tools/images、模型Catalog/DeferredModels、Host装配/SessionService、RunAPI/RunLifecycle、模型消息middleware、Web schema/service/delivery/errors、service app/receipts及专用图片额度模块，相关测试。每文件≤500行，职责不超过既有模块边界。

TDD：先执行新增配置／规范化与HTTP测试，确认缺入口／图片字段导致预期红灯，再实现。检查非默认配置实际接线、仅图／图文、完整像素payload／canonical、严格拒绝及零模型调用、修改模型、大小／像素／编码合计、重复ID／改图、认证／额度／取消名额释放。复用Read图片、模型图片、服务receipt和Web基本测试；不修改conftest或安装依赖。

完成证据补本文件；01专项通过后连续推进02，不把仅后端接通称作输入框已能粘贴。

验证：新增12例先红（缺失模块），核心+Read+模型图片42 passed；Web/异步主链25 passed；receipt取消所有权+图片HTTP+Read/模型39 passed。配置和Host装配已接通；实际用户粘贴在04完成。HTTP图片额度与普通frame隔离，生产配置由Service启动传入。

收尾：新附图门禁仅对本场景规范化用户图绑定origin_run_id；已有程序化/历史/工具图片仍沿原投影，避免破坏既有middleware合同。无图输入直接构造HumanMessage，不启动图片worker。只为用户图片生成媒体hash，Read旧图不额外做canonical hash；非工具图片处理也不报告缺失工具进度上下文。SessionService触及500行上限后，将两处相同的actor装配搬到既有agent_session.build_session，新建/恢复均接真实图片配置；协调器490行，未新建空转层。保存/恢复/Read/middleware等52项专项通过。
