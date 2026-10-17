# Spec085-11：Read图片与模型请求适配（已实施）

版本边界：下文保留该片实施时点；Bash旧Unknown/私有runner/线程及名字保护已在[085-12](085-12-process-lifecycle.md)迁移删除。当前下一步为[085-13搜索](085-13-search-tools.md)审阅，不能据本片历史下一步判断现状。

接续085-10。用户已授权本片实现、验证后讲085-12。沿Python/LangChain既有图及标准内容块；不增加图片专用UI、上传服务或永久图片平台。Pillow固定[12.3.0](https://pypi.org/project/pillow/12.3.0/)（PyPI已核对支持Python3.12），不升级已有依赖。

## 当前问题和目标

当前`create_session_read_tool→require_file_target→_read`只返回UTF-8文字。Read即使获得图片读取许可，仍会按文本解码；模型配置没有显式api/image_inputs，摘要前缀校验要求字符串，Web在投影前检查完整事件大小。这四个消费点必须一起接通，不能只让Read返回base64。

目标：获准读取→识别/处理图片→标准ToolMessage内容块进入canonical history/checkpoint→按本次选中的模型构造临时请求副本→官方适配器；持久历史保持原始标准块。当前模型不支持图片时，明确说明本次未提供像素；以后切换支持图片的模型仍能消费未被摘要淘汰的原块。

## 文件、方法和抽象

| 文件 | 新增/修改与真实消费者 |
|---|---|
| `tools/images.py`（新） | `read_image(source,...)/prepare_image_blocks`：Read唯一调用，消费已打开的同一流。有限文件头识别加Pillow实际解码，检查输入/像素预算，EXIF方向、最多四次等比例缩小/编码、GIF首帧、透明度及明确错误。只产标准内容块，不判断当前模型接口。 |
| `tools/read.py` | 获得原Read许可后在_read的同一次打开中识别图片；文字rewind后沿原分页/路径/IO错误链。图片不接受文字offset/limit，给明确参数反馈；不能借检测越过审批读原文件。 |
| `tools/settings.py`、两份配置样例 | 沿未来样例的`[tools.read.image]`增加ReadImageSettings及ReadSettings.image：max_input_bytes=32MiB、max_pixels=40,000,000、max_dimension=2000、max_base64_bytes=4.5MiB，逐项注释。最后一项按base64编码后的输出计；不混用文本64KiB预算。 |
| `requirements.txt` | 本片才引入已获用户允许的Pillow；实施时核对Python3.12兼容并固定版本，不升级其他依赖。 |
| `model_access/__init__.py` | load_model_catalog/_build_model解析OpenAI类必填api（chat_completions或responses），分别显式use_responses_api=False/True。解析可覆盖的profile.image_inputs布尔值，框架profile仅作缺省，未知保守；当前模型实例承载冻结的接口/能力元数据，不另建会话缓存。Anthropic/Ollama保持原适配器类型。 |
| `model_access/messages.py`（新） | `prepare_model_messages`及薄`ModelInputMiddleware.wrap_model_call/awrap_model_call`。消费当前request.model及canonical messages，复用官方标准块转换；仅Chat Completions把工具图片移到临时HumanMessage，成批工具结果配对完成后才插入。未知/不支持能力给本次明确文字说明。同步/异步共享纯转换算法。 |
| `agent_runtime/__init__.py` | 在现有overflow recovery内侧、最终请求观察前注册ModelInputMiddleware；选模和ToolSet仍在外层。不改create_agent循环或新建手写图。 |
| `agent_runtime/context_compaction.py` | `_validate_removed_prefix/_serialize_text_history`支持标准块，摘要只放文字及有限图片元数据；不放base64。沿用原切点/回退，未压缩的图片原块保留。当前文件452行；共有内容投影函数放messages.py，由摘要及请求实际复用，避免超500行或复制处理。 |
| `agent_runtime/context_recovery.py` | 原`_begin_recovery/_retry_request`算法已满足canonical重试，本片只注释明确外内顺序；测试确认不把载体写checkpoint或累加图片，无新恢复缓存/协议。 |
| `agent_runtime/context_observation.py` | 复用当前LangChain对标准image/image_url块的有限图片估算，不按base64字符数算文字；最终请求额外记录image_count和framework_approximate估值标识，不记录像素或称真实供应商计费。 |
| `web/serialization.py`、`web/run_delivery.py` | `project_event_for_web`只复制消息快照为文字投影，标content_projection=text并重算投影字节/哈希。DeliveredRun.observe先投影再wire/帧检查，投影缓冲也消费同一副本。原事件、ID/序号、工具状态、canonical/checkpoint不改；前端仍展示文字。 |
| 模型配置样例、脚本及受影响测试 | 迁移OpenAI条目的必填api和图片能力测试；真实用户模型/环境文件保持配置修改授权边界，提供迁移格式，不为旧配置保留隐式猜测。 |

不新增通用ToolResult类，图片属于ToolMessage.content内容块，不放artifact；artifact继续用于Edit的后端详情。新增两个生产模块分别对应图片IO和模型请求表示，职责沿总Spec文件地图，不散成多个无消费者的包装层。

## 数据与具体调用链

示例为结构说明，不是真实模型运行结果：模型调用`read(path="screens/error.png")`，参数及真实目标经过原权限管线；获准后Read处理图片，官方ToolNode生成同ID消息：

```python
ToolMessage(tool_call_id='read-1', content=[
    {'type': 'text', 'text': 'Image: PNG, 1200 x 800'},
    {'type': 'image', 'base64': '<encoded pixels>', 'mime_type': 'image/png'},
])
```

Read不需要知道下一次选择哪个模型。canonical/history保存这条消息；`make_model_selection_middleware`选好当前model，再到`prepare_model_messages`：

```text
canonical: AI(tool_calls=[read-1, read-2]) → Tool(read-1,image) → Tool(read-2,text)
  Responses / Anthropic / Ollama支持分支：官方适配器消费标准内容块
  Chat Completions请求副本：AI → Tool(read-1,图片引用文字) → Tool(read-2,text)
                             → Human(来自read-1的图片及来源说明)
  不支持图片的请求副本：AI → Tool(read-1,本次图片未提供说明) → Tool(read-2,text)
```

临时Human不是用户新输入/新turn，不加真人来源标记、不落checkpoint，不进入Auto意图输入。一次请求每批插一次；恢复或溢出重试都从canonical重建。模型正常返回后只原有AI结果落历史。正式实现前先用本机已安装版本源码及无网络payload探针核验各官方分支，不能只因工具内容能转换就声称远端已接受。

失败例：图片超输入/像素预算、损坏/不支持格式、有限缩放仍超输出预算→具体ToolException→原ToolError→同ID error，模型继续。未授权仍先暂停/拒绝，无图片正文IO。模型能力不足则只降级该次请求表示，不删除canonical图片。未知接口或非法配置在启动校验时失败，不推迟到工具执行。

## 验收

先写失败测试：真实临时PNG/JPEG/WebP/GIF首帧/BMP、错扩展名、损坏/不支持格式；EXIF/透明度、像素和编码预算、文字Read回归；三类官方payload转换、Chat Completions整批配对及多图、能力关闭/切模型；sync/async和SQLite恢复；摘要无base64且保留图片存在信息、预算不按编码字符串计；一次溢出重试不重复载体；Web大图快照在投影后不超帧且原序号/哈希合同一致。新字段全链同步，旧文本测试不放宽。最后后端全量、前端和build/依赖/500行检查。

本片默认验证为本地图片IO、假模型、官方适配器payload和SQLite；若未发真实供应商请求，交付明确保留该证据边界，不宣称各网关实测支持。图片专用显示仍后排。

## 当前迁移与真实接线

模型配置是启动快照。OpenAI/openai_compatible每个条目必须显式补`api`，例如`"api": "responses"`或`"api": "chat_completions"`；不提供auto或缺字段兼容。`profile`仅允许可选布尔image_inputs：显式值覆盖已安装适配器profile，未知保守。Anthropic/Ollama不接受伪造的OpenAI api字段。当前[models.example.json](../examples/models.example.json)只有占位服务/模型，不表示其真实可用或具备图片能力；填写真实值并确认模型能力后使用。真实用户配置未改。

当前处理格式为PNG/JPEG/WebP，GIF首帧和BMP转PNG；JPEG/WebP保留对应输出格式，透明度保留，先EXIF校正再缩小。检测采用有限格式文件头，Pillow负责实际解码；常见不支持格式如TIFF明确报错，其他二进制仍沿Read具体编码/非文本错误。BMP检查保留零字段，避免把BM开头的普通文档误判图片。输入先限大小、解码前检查宽×高，像素限制取配置和Pillow自身阈值的较严格者；不修改全局warnings过滤器或MAX_IMAGE_PIXELS，避免并行Read互相改变进程策略。炸弹warning/error给固定反馈和安全诊断。

原生ToolMessage.content为JSON标准块，当前Read/模型图片配置、同步异步图、SQLite和Web消费者已接通，没有通用结果类或图片artifact。模型接口/能力由当前request.model提供，无会话级重复缓存。普通文字消息不额外复制，保留恢复摘要在必要保存时回填相同ID的合同；含图片消息才复制并适配。Chat Completions每批只在全部配对之后增加一个载体，多图共用；批准恢复和溢出重试从canonical重新准备。

Web投影标记放在snapshot_json内部消息行`content_projection='text'`，复制MessageSnapshotData并重算其snapshot_bytes/sha256；原领域对象保持，缓冲/文字projection共用副本。恢复列表message_view同样带该标记，前端MessageRow增加可选类型字段，无图片组件。文字本身超帧仍沿原不完整/重同步合同。

## 验证结果（2026-10-16）

新增五份测试共57例：test_read_images、test_model_image_config、test_model_image_messages、test_image_runtime、test_web_images。行为红灯包括图片按文字解码、摘要拒绝图片、api缺失不阻止启动，以及Web先检查大图帧而不完整；实现后转绿。回归还发现Read检测时双重打开、请求对普通文字消息的过度复制影响摘要ID、全局warnings策略改动和BM文字误判，均先保留/补对应失败断言再修源代码。

旧test_model_access/config_for_all_kinds和test_model_limits的合法配置样例显式补api，未在write_config等助手中偷偷补默认以绕过缺api校验；原超时/重试/思考/预算/切模型断言保留。原“不支持多模态摘要”测试改用仍不支持的音频块，保留明确失败及零模型调用断言，新增测试另证图片摘要成功与像素不进入摘要；没有放宽断言、skip或修改conftest。

- `.venv/bin/python -m pytest -q`：**1486 passed in42.09s**，日志`/tmp/naiyue-08511-full.log`。
- 五份新增文件定向：**57 passed**。真实临时PNG/JPEG/WebP/GIF/BMP/TIFF、方向/透明度/首帧、四预算与损坏；批准前零正文IO、同ID和关闭重开后的sync/async SQLite；切模型不重读；多图每批单载体；旧图被摘要/近期图保留的两种溢出重试。
- 当前安装版本官方适配器无网络payload断言：Responses function_call_output含input_image，Anthropic tool_result含image.source，Ollama工具消息有images，Chat Completions工具结果为文字且配对后用户载体含image_url。
- 本地ASGI HTTP完整链：真实随机像素PNG的base64超过8KiB帧，文字事件/视图仍完整，canonical保存图片且没有临时Human；投影字节/哈希、原对象和ID/序号均检查。
- `npm test -- --run`（frontend）：**9文件27 passed**；`npm run build`：TypeScript及Vite **186模块**成功。
- compileall、pip check、git diff --check退出0；**252个Python文件**AST通过且均≤500行。当前设置经产品loader和内置工具权限校验、未来样例TOML解析、占位模型样例的严格schema/api/profile校验均通过。

只新增Pillow==12.3.0，无旧依赖升级。真实模型/环境配置未写、没有供应商网络模型请求、Windows或沙箱执行验证；无提交/push。元数据与payload转换不证明远端网关实际接受，需真实模型集成另证。唯一下一步审阅[085-12共享进程](085-12-process-lifecycle.md)，不自动实现。
