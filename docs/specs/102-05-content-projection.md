# Spec102-05：完整内容与流式文本投影

日期：2026-10-18。状态：已实施并通过本机验收；第1—4节保留实现前合同。上层方向见[Spec102第24节](102-session-state-lifecycle-refactor.md#24-102-05准备方案供审阅尚未实施)，结果见第5节。主Spec接近500行，本片细节独立存放，主Spec只同步索引/状态。

## 1. 当前链与修改目标

完整内容：模型/工具的canonical消息→content_text（摘要、子报告）或Web.text_content纯转发→Web快照/历史；CLI另有_text，文字规则相同但跳过图片。流式内容：原生图messages事件→_StreamState.delta→Runtime._text→message_delta；其规则不接列表字符串或非文字块。当前两个_text与Web纯转发均有明确消费者，先迁移再删除。

修改6个生产文件，不新增生产文件/类/依赖，不改消息/事件wire、存储、请求图片适配或图循环。model_access/messages.py扩展content_text(content, *, strict=False, delta=False)，保留默认完整投影及strict；CLI与Web完整内容直接调用，Runtime增量直接delta=True。作用只是投影，不修改输入内容对象。

## 2. 输入/输出合同

| 输入 | 完整delta=False | 增量delta=True |
|---|---|---|
| 字符串 | 原字符串 | 原字符串 |
| 列表字符串项 | 按顺序拼接 | 跳过，保持原流式合同 |
| type=text且text为字符串 | 按顺序拼接 | 按顺序拼接 |
| 已识别图片块 | 原有限mime/尺寸说明，前后换行 | 跳过，不产生图片说明 |
| 未知块/错误text字段/非字符串块类型 | 宽松跳过；strict=True抛unsupported_content_block | 同左 |
| 顶层非字符串/非list | 宽松空字符串；strict=True抛unsupported_content | 同左 |

已识别但不适用增量的图片/字符串项在strict增量中也可跳过；strict仅拒绝未知结构。非字符串type按未知块处理，不能让合并后的流式路径因图片类型集合查询抛TypeError；此检查局限在本投影函数，不改变全局is_image_block或模型输入适配。reasoning/thinking等未知非文字块在宽松投影忽略。image_description沿用现有实现，不输出图片块base64/URL；不新增脱敏/图片处理平台。

## 3. 文件、方法及调用链

- model_access/messages.py：content_text增加delta关键字并作为唯一文本解析入口；image_description与prepare_model_messages保持原职责。
- application/cli_display.py：删除_text，show_history/_message/finish_result调用content_text默认模式；_safe_text、非文本提示及去重、消息指纹/补显保持。CLI增加与Web一致的有限图片说明，完整事件与返回补显对同一内容使用同一指纹，不重复展示。
- agent_runtime/async_message_observation.py：删除_text；_StreamState.delta在身份/来源检查后只解析一次content_text(..., delta=True)，原namespace/step键、空响应ID锚定、非空文字序号/草稿、最终快照保持。图片/未知/空文字不发delta，但带最终response id的空内容仍能登记ID。
- web/serialization.py：删除text_content纯别名，message_view/project_event_for_web直接content_text；仍在投影后计算快照字节数/哈希及帧预算，不修改原领域事件。
- web/agent_projection.py及web/chat_history.py：从model_access.messages直接导入content_text；子消息、工具结果、历史预览/正文裁窗沿原入口，不保留旧别名或重导出。run_delivery只调用message_view，无需修改。

正常例子：['开头', text('正文'), image]的完整投影为“开头正文＋原图片说明”；同一内容若出现在chunk中，增量只有“正文”。Responses空首chunk登记resp_id，后续不同run_id文字仍归resp_id；最终快照确认同一消息。CLI显示转义后的文字/图片说明，原像素仍只存在canonical消息及模型图片请求。

失败例子：摘要严格投影遇到未知块仍抛原ValueError码，原压缩失败/回退边界处理；流式纯图片/未知数据无伪造文字，最终快照仍可确认内容。CLI收到重复完整事件或返回补显，用同一完整投影指纹避免重复；控制字符继续经_safe_text，不直接写终端。

## 4. 验证与边界

先记录相关基线，再写完整/增量/strict矩阵、原入口删除、CLI历史/实时/补显及流式ID回归的失败测试；保留已有图片请求/摘要/子报告、Web快照哈希/帧预算与历史/子页面行为断言。新增非字符串type未知块的测试锁定解析合同；不改测试基础设施/conftest，不放宽断言。Mock/假模型与临时文件/SQLite；不读取/修改真实模型配置，不发供应商请求。

完成后跑完整后端/前端/构建、compileall/pip check/diff、AST/单文件500行及退役消费者扫描，核对真实调用而非仅声明入口；同步README/交接/计划与主Spec。102-06设置异常映射只解释方案，不实施。


## 5. 实际结果（2026-10-18）

6个生产文件按合同接线，未新增生产类/文件。content_text仅增加delta选项和非字符串type的未知块处理；原图片识别/描述及模型请求准备函数不改。CLI三个完整入口直接调用默认模式，Runtime.delta调用delta=True且每chunk只解析一次；Web三个模块直接导入，text_content和两个_text均删除，无兼容别名。原canonical内容/事件、消息ID、哈希/字节重算、正文/帧预算与必要保存未改。

正常与失败链见第3节，现由真实调用验证：CLI历史/实时/返回补显各显示一次相同图片说明，ANSI仍转义、无URL/base64；重复事件/返回补显不误报观察不完整。流式空首chunk→图片→列表字符串→不透明块→两段文字，严格只有两个连续delta及一条最终completed，均用原response ID，最终canonical仍有原图块；Web投影后的快照无像素。strict未知块/顶层错误维持原错误码，图片和已识别字符串在增量中可跳过。

相关基线66 passed in1.53s。新增43项回归，旧实现42 failed/1 passed（/tmp/naiyue-10205-red.log）；原流式混合块/ID行为先通过，新增解析入口/删除旧入口/CLI图片说明为预期红灯。实施后专项109 passed in1.62s；补强流式事件类型与序号的完整断言后，当前专项109 passed in1.72s（/tmp/naiyue-10205-focused-final.log）。没有更改旧断言/conftest/helper或skip；只在两份既有测试文件追加行为断言并新增内容矩阵文件。

完整后端 **2345 passed in101.07s**（/tmp/naiyue-10205-backend.log）；前端 **26文件186 passed**（/tmp/naiyue-10205-frontend.log）；TypeScript/Vite **210模块成功**（/tmp/naiyue-10205-build.log）。最后全量之后只补强测试事件断言及文档，生产代码不变，增强后的专项已实际重跑通过。

实际命令：

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples
.venv/bin/python -m pip check
git diff --check
```

389份Python AST/单文件500行及退役投影消费者扫描通过；架构/安全独立只读复核无确证遗留发现，安全独立专项93通过。README、handoff、计划/路线与085/086当前文件地图同步。无依赖、公开App API、事件wire、DB schema、用户配置/用户库操作或真实供应商请求，Windows/Linux原机及浏览器人工验收未新增证据。未提交/push，保留原100/101/102-01—04/103脏树。本轮为助手实现/验证，不新增用户掌握声明。

当前7片完成5片，剩余102-06设置异常映射与102-07顺序/组合验收，下一片准备方案见[主Spec第26节](102-session-state-lifecycle-refactor.md#26-102-06设置异常映射已实施)。
