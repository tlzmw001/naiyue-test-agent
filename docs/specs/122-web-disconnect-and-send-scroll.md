# Spec122：Windows连接清理与发送后滚动偏好

状态：2026-10-21，两项已完成本机实现与验收，未提交/发布。Spec121及并行学习改动保留；Windows原机网络重现仍未验证。

## 1. 触发、根因与范围

截图的异常为`_ProactorBasePipeTransport._call_connection_lost(None)`中`shutdown()`抛出Windows 10054。已用本机Python3.12同一标准库回调及合成reset socket复现：协议已通知一次，后续close/服务器脱离/已清理标记没有执行。不是凭此断言模型或MCP请求断开，截图不能确定连接所属；旁边shell_archive_failed属于独立归档警告，未复现其原因，不扩大修复。

消息区`Conversation`依据nearBottom和可见行锚点保持阅读位置，`useConversation.submit`没有发送成功的显示信号。需要可选择在服务器接受本次新消息后跳到最新，并保留现有阅读位置模式。

## 2. Windows最小处理合同

- 新增web/windows_disconnect.py，在Web应用lifespan的现有AsyncExitStack中安装、退出时恢复当前loop的exception handler；仅Windows生效，不替换事件循环，不改标准库全局类。
- 仅识别：ConnectionResetError、winerror=10054、实际绑定到Proactor标准库清理回调、transport正在关闭且尚未完成、没有被覆盖的前序异常。继续完成socket.close、服务器detach及完成标记，不重复通知协议，也不重新建立连接或重放请求。
- 记录安全的连接关闭诊断，不输出URL、访问令牌或原异常正文。其他回调/错误、已清理transport、后续清理失败均交回原handler/default handler；必要关闭失败额外保留安全诊断。若其他代码后来更换handler，退出不覆盖它。
- 正常路径：lifespan→安装→服务→关闭资源→恢复handler。失败路径：call_soon原回调在shutdown抛10054→loop handler识别→完成剩余关闭；不匹配的错误保留原上下文和委托行为。
- Python私有结构集中在一个兼容模块，通过真实标准库回调的确定性注入测试保护。macOS复现代码分支不等于Windows原机网络验证。
- 复核确认Python3.12的server._detach无参数，3.13/3.14需要transport。关闭前检查绑定方法签名，明确选择一种调用，不在捕获TypeError后盲目重试；两类签名均有回归。

## 3. 发送后滚动偏好

- 前端在输入框加号菜单中只显示一行“发送后滚动”，悬浮时在右侧子菜单提供选项，右上角无独立入口（按用户后续位置修订）；两项为“保持当前位置”（默认）、“回到最新消息”。选择保存在浏览器localStorage，不改服务器settings.toml、DB或真实配置。
- useConversation在api.start/API control成功回执时产生归属session的lastSubmission对象，供App→Conversation消费。空输入、阻止发送、停止期间本地暂存和HTTP失败不产生信号。主请求已接受但后续读取失败仍按已接受事实产生信号。
- Conversation只在收到新的、属于当前会话的发送信号且偏好为latest时设置nearBottom并滚到底部，之后复用已有自动跟随；用户再次上滑仍暂停跟随。切换偏好本身不抢位置；旧发送回执不影响当前其他会话。
- 不依赖draft清空、消息类型/条数猜测发送动作；历史分页和新增服务端消息都不能冒充主动发送。重新生成/审批/压缩沿用原滚动行为。跟进/插话提交接受也按同一偏好处理。
- localStorage无效值回退默认；存取失败有安全console诊断，本次选择仍可用。新字段逐一接线到hook返回、App参数及Conversation消费；useConversation保持500行内。

## 4. 影响文件与验收

生产：web/windows_disconnect.py、web/app.py；frontend的useConversation/App/Conversation、新偏好hook和Composer/ComposerControls（复用现有加号菜单）。测试：Windows原回调关闭/委托/恢复/失败；滚动默认/启用/关闭/失败/迟到回执/跟随/历史锚点/偏好存储及实际发送链。

先写失败测试，再最小实现；跑定向及全量后端/前端、TypeScript/Vite、源码语法/500行/依赖/diff、标准wheel及安装。实际页面验证开关、历史位置、发送跳转与失败保持；只使用临时应用、合成模型、独立工作区，不动真实设置/用户库或真实供应商。更新handoff短摘要，模型重试治理保持下一开发主题。

## 5. 实际实现与验证

### 5.1 根因与主链

Windows补偿集中在`windows_disconnect_handler → _finish_reset_close`：入口为Web原lifespan，当前loop原handler保存于闭包；标准库真实绑定callback及10054产生识别事实。匹配后只接续close/detach/完成标记，既有协议通知不重做，日志只有关闭阶段和错误代码。不匹配或清理失败将原context交给既有/default handler；退出按当前handler身份恢复。没有替换Proactor循环或修改标准库类，模型/子进程异步能力保持。

`useConversation.submit`在服务器接受start/control时产生`{session_id}`新对象；这是本次接受事实，不是历史或draft变化推断。App仅把当前session对象传给Conversation。Conversation对新对象比较并消费一次，只有latest偏好才恢复nearBottom；之后旧自动跟随和手动上滑暂停、历史锚点逻辑继续生效。偏好变更不会重放旧对象；HTTP失败、空发送及停止本地保留不生产对象。500行hook仅增加必要状态与两处真实回执接线，没有新增事件总线或后端字段。

页面输入框加号菜单内只显示一行“发送后滚动”，两项单选在右侧子菜单显示，复用已有菜单样式、键盘/外部点击关闭和焦点恢复；默认keep，与主题分别存储。本地偏好按浏览器站点保存，端口变化属于不同origin，不承诺跨origin共享。

### 5.2 实际结果

证据目录`build/web-fixes-gufxSc/`，汇总`verification.json`。

| 命令/验证 | 实际结果 |
| --- | --- |
| 原Python3.12 Proactor回调注入reset socket | 确认协议通知一次，10054上报且close/完成标记未执行；修复前分支已复现 |
| `.venv/bin/python -m pytest -q tests/test_web_windows_disconnect.py tests/test_web_startup.py --tb=short` | 10 passed；新9项含真实Web lifespan接线、接口仍可用、精确错误委托/原异常身份、退出恢复及两类detach签名。`windows-red.log`和`windows-version-red.log`保留红灯 |
| `npm --prefix frontend test -- --run src/send-scroll.test.tsx src/history-scroll.test.tsx src/theme.test.tsx` | 20 passed；新9项，覆盖真实Composer→Hook→Conversation、成功/失败草稿、默认/启用/偏好变更、流式跟随、存储异常及菜单键盘 |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **3743 passed in235.50s**，仅临时测试子进程PATH使用既有原生资源 |
| `npm --prefix frontend test -- --run` | **49文件394 passed** |
| `npm --prefix frontend run build` | TypeScript/Vite成功，原chunk大小提示保留 |
| AST/500行/compileall/pip check/diff | 164份生产Python及前端源码限制通过，无破损依赖 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/web-fixes-gufxSc/wheel` | 标准wheel构建成功，294文件/3,630,121字节，164源码及前端dist逐字节一致，Twine PASSED |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/web-fixes-gufxSc/wheel --output build/web-fixes-gufxSc/install.json` | 源外独立venv安装、CLI/Web/API/原生工具/审批/SQLite/MCP及进程关闭passed，8次合成MCP工具、0真实供应商 |
| 架构专项 | 发现Python3.13+ detach参数差异，补1红1绿后兼容；复核9 passed，原发现关闭，无新高置信度发现 |

页面使用临时SQLite、离线SDK及构建后的生产前端，在内置浏览器480px视口实际验收：默认保持发送前后top=3415.5；切latest本身不移动；发送后gap=0；刷新恢复latest；重新阅读历史后关闭临时服务，发送失败top=3789保持、草稿保留、显示操作未确认。`ui-check.json`、`scroll-settings.png`、`latest-settings.png`及`network-failure.png`记录证据。初次Playwright失败因Chrome扩展未连接，未修改/安装扩展；改用内置浏览器。临时服务与页面均已关闭。

### 5.3 边界与交付

本机通过标准库同一callback注入10054，另验证3.12与3.13+两类server签名；不是Windows原机IOCP/网络实测，不能宣称阻止远端强制断连。修复处理的是关闭阶段的清理缺口及误报堆栈，网络调用本身错误继续走原合同。

截图另有`shell_archive_failed error_type=ValueError`；输出归档调用链与连接清理分离，仅截图不足以定位。没有擅自修改目录所有权、配额或归档校验；需对应归档目录/运行场景才能独立复现。

真实配置、环境文件、用户数据库、运行依赖及版本保持；Spec121和学习改动保留。未提交、push或发布，公开PyPI0.2.0不含本片。两项授权修改本地收尾，开发主线下一项仍为讨论模型重试治理。


## 6. 入口位置修订（用户后续授权）

将发送后滚动的两项单选移入ComposerControls现有加号菜单，App→Composer→ComposerControls传递原偏好对象；右上入口及无剩余消费者的独立SendScrollMenu移除。选择/存储/发送信号/滚动逻辑不变；加号菜单的方向键遍历包含原指令及新增单选，Escape恢复加号焦点。保留原无斜杠指令配置的定位，不增加新的slash command。测试迁移保留持久化、默认值、存储错误、焦点与键盘断言，并增加位于form内及无页头入口的断言。前节截图/构建为原位置历史证据。本次只移动入口，未更改滚动主链或后端，也未重建wheel。

本次证据`build/scroll-menu-OL4ayF/`：

| 命令/验证 | 实际结果 |
| --- | --- |
| `npm --prefix frontend test -- --run src/send-scroll.test.tsx`（修复前） | 3 failed / 6 passed，准确复现入口仍在页头、加号菜单缺少单选项；`red.log` |
| `npm --prefix frontend test -- --run src/send-scroll.test.tsx src/composer-commands.test.tsx src/session-controls.test.tsx src/slash-command-menu.test.tsx` | 4文件33 passed；`focused.log` |
| `npm --prefix frontend test -- --run` | 49文件394 passed；`frontend.log` |
| `npm --prefix frontend run build` | TypeScript检查及Vite构建成功，原chunk大小提示保持；`build.log` |
| 前端500行限制 / `git diff --check` | 通过，旧SendScrollMenu文件与生产引用已移除 |
| 生产构建页面（离线临时服务、1280×720） | 单选在输入框form内；右上旧入口为0；点击latest关闭菜单并归还加号焦点；reload后latest仍选中；`ui-check.json`、`plus-menu.png` |

本次仅使用合成SDK/独立工作区，0真实供应商请求。临时浏览器与服务已关闭；没有提交、push或发布。


## 7. 单行入口与悬浮子菜单（用户后续授权）

当前加号菜单直接显示两项单选，形成独立分组，与用户要求的单行菜单入口不符。调整范围仅为ComposerControls、原CSS Module及相关前端测试；沿用已有字体、行高、颜色、圆角、阴影及选中标记，不新增组件、依赖或配置。

正常路径：加号→“发送后滚动 ›”一行→鼠标悬浮展开右侧子菜单→选择keep/latest→原hook保存偏好→关闭菜单并恢复加号焦点。鼠标移动进入子菜单时保持打开，离开整组或选择其他菜单时关闭；点击支持触屏。键盘主菜单只遍历三行，右方向键进入子菜单、上下/Home/End只遍历当前层，左方向键或Escape返回父项，再按Escape关闭主菜单。外部点击、会话/菜单切换不能遗留子菜单状态。窄屏无右侧空间时将子菜单放在父项上方并保持在视口内。

失败路径：localStorage读写失败继续沿用既有安全诊断和本次选择，不改变发送/滚动合同。先迁移原9项测试保留各行为断言，再新增悬浮进入/离开、点击、分层键盘及重新打开收起的断言；确认红灯后最小实现，验证全前端/TS/Vite、500行/diff及桌面/375px生产构建页面。实际结果见下表，证据`build/scroll-submenu-oDlxDX/`：

| 命令/验证 | 实际结果 |
| --- | --- |
| `npm --prefix frontend test -- --run src/send-scroll.test.tsx src/composer-commands.test.tsx`（实现前） | 5 failed / 9 passed，准确指向缺少单行父项及分层子菜单；`red.log` |
| `npm --prefix frontend test -- --run src/send-scroll.test.tsx src/composer-commands.test.tsx src/session-controls.test.tsx src/slash-command-menu.test.tsx` | 4文件34 passed；`focused.log` |
| `npm --prefix frontend test -- --run` | 最终49文件395 passed；`frontend-recheck.log` |
| `npm --prefix frontend run build` | TypeScript及Vite成功；`build.log`，原chunk大小提示保持 |
| 前端500行限制 / `git diff --check` | 97份TS/TSX通过行数校验，diff通过 |
| 实际1280×720页面 | 加号只有单行父项；原生鼠标进入触发展开，右侧子菜单x734/宽190；点击选择关闭并恢复加号焦点；`desktop.png`、`ui-check.json` |
| 实际375×812页面 | 点击展开，上方子菜单x145/right335，完整处于视口内，选中latest保持；`mobile.png` |

首轮全量有1项模型菜单用例在点击后未出现dialog，原文件单独4项及未更改源码/断言的再次全量395项通过；该首次失败没有稳定复现，保留`frontend.log`与`model-recheck.log`，不据此声称已修复其原因。新增1项子菜单回归，原9项滚动/存储断言保留，主菜单顺序断言同步迁移。

没有更改后端、滚动逻辑或存储协议，未重跑后端/重建wheel。仅临时离线SDK/独立SQLite，0真实供应商请求；临时页面与服务关闭、视口复位。未提交、push或发布。
