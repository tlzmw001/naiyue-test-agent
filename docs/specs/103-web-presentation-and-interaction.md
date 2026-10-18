# Spec103：Web消息呈现、输入与导航改进

日期：2026-10-17，更新2026-10-18。状态：此前U5、U2工具摘要、U8搜索/单行/折叠及B1/U3/U4/B2已完成；最新[纯前端连续分片](103-frontend-completion.md#5-实际实现与验收2026-10-18)已完成现有Edit差异、U6模型/阈值、U7有界重连/完成态、U8日期视图、U9实例入口、U10审批按钮、U11主题。前端280/本批后端快照2825及构建通过。B3、U1及完整Write/Edit投影仍需后端方案，U12/U13独立；没有把Spec103整体标为完成。

用户最新授权：剩余纯前端分片依次实施，无需逐片确认，范围见[连续实施Spec](103-frontend-completion.md)；需要后端事实/执行语义的部分仍单独设计。checkpoint安全修剪已记录在[Pi研究第7节](../research/pi-event-history-usage-2026-10-17.md#7-当前checkpoint保存与清理边界)，用户明确延后讨论；本稿不制定其淘汰规则。保留Spec100成果，不改真实配置或用户会话库；第18节和第22节分别授权新增GFM和高亮依赖，其余依赖变更另议。

2026-10-18联调更新：[Spec101四片已完成](101-web-history-pagination.md#21-101-04实际结果与spec101收尾2026-10-18)，当前工作树后端2266/前端186/TS与Vite210模块通过。第15/17节保留当时失败证据，当前不再阻塞于这些历史红灯。U1回复归属与U7后续重连呈现可消费已稳定的身份/覆盖/分页合同；本片没有实现这些剩余UI，U8单行/桌面折叠已按第16—17节交付。

## 1. 当前证据与三个问题的性质

以下为实施前源码与探针记录；B1已由第18—19节修复，B2已由第24—25节修复，当前源码/浏览器证据以对应结果为准。

| 用户指出的问题 | 当前证据 | 判断与建议 |
|---|---|---|
| B1 表格显示原始竖线，删除线/任务列表不支持 | package.json仅有react-markdown，没有remark-gfm；Conversation未注册GFM插件。用当前ReactMarkdown配置分别渲染三类输入，均没有table/del/checkbox元素 | 缺陷已在渲染层复现。复用现有渲染器增加remark-gfm与对应表格/任务列表样式；保持skipHtml和链接约束 |
| B2 首页误报未设置工作区 | 实施前App只取chat.session?.workspace_root；未选会话时没有服务级来源 | 已按第24—25节由认证GET /api/service提供真实绑定，WorkspaceLabel区分加载/未绑定/失败重试，并尊重选中会话的显式null；桌面与375px短标签/完整路径提示已验 |
| B3 侧栏标题是时间 | Sidebar.title对空title回退updated_at；当前已有手工重命名/持久化，没有自动命名生产链 | 功能缺口，非已实现自动标题的回归。建议先以首条有效用户消息提炼有限长度标题并保存，不覆盖手工名称；是否额外调用模型生成语义标题单独确认，不能暗增请求/费用 |

源码：[依赖](../../frontend/package.json)、[消息渲染](../../frontend/src/components/Conversation.tsx)、[页头](../../frontend/src/App.tsx)、[服务元信息现状](../../naiyue_test_agent/web/service.py:19)、[侧栏标题](../../frontend/src/components/Sidebar.tsx:25)。

官方机制：[react-markdown插件与安全边界](https://github.com/remarkjs/react-markdown)、[remark-gfm](https://github.com/remarkjs/remark-gfm)、[rehype-highlight](https://github.com/rehypejs/rehype-highlight)。本节原为实施前记录，后者现已按第22—23节精确接入7.0.2。

## 2. 用户界面清单与建议边界

| 编号/区域 | 实施前行为 | 建议与需确认点 |
|---|---|---|
| U1 一次用户请求的回复 | 每条AI消息都有hr，有正文即显示复制，包括中间工具消息 | 一个用户请求形成一个视觉回复块；保留中间文字顺序，工具步骤可折叠，操作栏只在回复块末尾。界面回复块不等于Runtime turn或run；审批可跨run，多次模型调用可跨turn。分组归属须跨重启/分页稳定，不能只按run_id或相邻消息猜测 |
| U2 工具卡 | 已完成仍展示phase和耗时；默认details已折叠，展开为参数JSON与文本结果 | 成功摘要收为图标、工具及关键参数、状态；运行进度仅在活动摘要显示，完成耗时/进度可留详情。错误/审批/未确认必须明显，不因折叠伪装成功。详情仍能核对完整参数/结果 |
| U3 流式Markdown | 实施前provisional为纯文本，完成才ReactMarkdown | 已按第20—21节接线：AI增量/未确认/终稿共用Markdown，原状态标记、工具/摘要分支保留；正常/停止、未闭合语法、根子、滚动与有限成本样本已验 |
| U4 代码块 | 实施前无专用组件/高亮/复制控件 | 已按第22—23节接线语言标签、独立复制及高亮；未知/无语言保留纯代码，复制当前代码文本。行内保持；流式反馈/竞争与8 KiB增量样本已验证，无新增截断或执行入口 |
| U5 输入框 | resize:vertical；停止与发送并列；已有/指令执行但无输入时补全 | 用户最新确定：运行中无草稿为停止，有草稿变为发送，并在输入框左上方显示方式下拉，默认follow_up，可切换steering；提交时直接调用现有控制接口，已发送消息的方式固定。自动增高与/菜单仍按本表建议推进 |
| U6 模型与上下文 | 模型选择位于Composer右下；圆环已有百分比、title及aria-label | 用户提出移到页头左侧，是对Spec097/099位置选择的重新调整。建议移用同一个ModelMenu，保持会话保存/档位重置与下一请求生效。圆环已有提示，不记为完全缺失；可改善可发现性，默认保留主动查看入口，接近上限才显示属于可选取舍 |
| U7 状态与重连 | App对所有run常驻状态和重新同步按钮；watch已有一次快照拉取后重订阅 | 正常完成清除常驻横条；运行/停止/审批、保存未确认、失败和恢复门禁保留合适提示。升级为有退避、次数上限、AbortController和选中会话校验的自动重连；耗尽后给手动重试，不无界重连，更不能重发run或审批POST |
| U8 侧栏 | 两行标题/模型，置顶/普通两组内持久拖动；无搜索/桌面折叠 | 单行标题，异常状态用紧凑标记；加当前工作区标题搜索和Cmd/Ctrl+K入口、桌面收起。省略号hover/focus显示，触屏可发现。最新连续分片已加可选日期视图；默认手动，日期视图只读排序并可切回，服务持久顺序保持 |
| U9 页头 | 实施前为全绝对路径；子智能体按钮在无实例时仍存在 | 第24—25节已完成真实目录短标签/完整路径title。最新连续分片已统一读取/合并实时和已保存实例供页头及侧栏复用；无实例隐藏入口，读取失败显式重试 |
| U10 审批 | 每项允许/拒绝单选与逐项记住，最终整批提交 | 现有remember_approved本就是逐项ID列表。可用允许/本会话允许/拒绝三个按钮表达决定。建议单项直接提交，多项仍收齐决定后整批确认；若要求每项点完立即执行，就涉及后端批次暂停/恢复合同，需另行批准。保留批次身份、去重、过期拒绝、根/子停止与失败后可核对 |
| U11 主题 | 全部CSS有59种颜色字面量表达式，var引用10处；global.css仅少量变量 | 先收成语义颜色变量，再支持浅色/深色/跟随系统，保存用户选择。Markdown、代码、工具、审批、错误/焦点/禁用态一起覆盖；不把颜色数量本身当验收 |
| U12 编辑重发与重新生成 | 当前没有对应入口与分支合同 | 独立Spec，确认旧版本/分支查看、审批和副作用处理，再实现。不能直接重放旧工具、改写原历史或把更新checkpoint指针等同于文件回滚；是否必须采用持久分支树先按需求判断 |
| U13 思考过程 | 前端MessageRow仅文本；delta只采text块，Web完整消息也做文本投影 | 涉及模型适配、事件、序列化、历史和前端，单独切片。仅展示供应商明确返回且适合用户展示的思考文本/摘要；未知/不支持时无面板，不能根据token数量或reasoning档位伪造内容，不将签名/不透明载荷当正文 |

### U5已确定的主按钮与草稿发送方式

以用户最新选择为准：在发送前选择方式，复用当前steering/follow_up后端队列，不要求排队条目转移接口，也不由前端另行调度跟进run。

| 状态 | 主按钮 | 输入框左上方 | 用户操作与结果 |
|---|---|---|---|
| 空闲 | 发送，空草稿禁用 | 不显示跟进/引导下拉 | 正常提交新请求 |
| 运行中，输入框为空 | 停止 | 隐藏 | 保持现有停止申请/收尾语义 |
| 运行中，有有效草稿 | 发送 | 显示下拉，默认结束后跟进(follow_up)，可选引导本轮(steering) | 选择仅改变当前草稿的提交方式，尚未发送 |
| 运行中，提交草稿 | 发送，提交中防重复 | 显示所选方式，提交中禁用 | 调用现有WebApi.control(run_id, selected_kind, text)，由后端按原边界消费 |
| 提交确认成功 | 若仍运行则回到停止 | 随草稿清空隐藏 | 清空已提交草稿，下一条草稿默认follow_up |
| 提交失败 | 按草稿/运行实际状态显示 | 保留当前选择 | 保留草稿和失败反馈，不显示为已排队，不自动重发 |

方式只在有活动运行时有意义。切换会话或开始一条新草稿默认follow_up，提交失败则保留当前草稿的选择；清空后重新输入也按新草稿处理。发送后沿现有确认反馈展示，已发送消息的方式固定，不提供发送后转为steering的按钮。

请求提交继续使用现有WebApi.control→RunDelivery.control→SessionControls.steer/follow_up链；属于前端交互调整，不增加后端API、队列身份或迁移逻辑。提交时按当前run状态及现有准入处理；stale_run、待审批、恢复或停止收尾导致拒绝时保留内容，不自动改成启动新run或重放请求。引导仍在下一个可注入边界交付，不打断正在执行的工具。

### Edit与Write的diff不能一概按前端小改处理

- Edit已经在执行时生成有界diff和artifact：firstChangedLine、diff、diff_truncated、preview、patch等；content也带文本diff。先复用该事实与预算，必要时以明确有限字段投影给Web；当前message_view没有专门传artifact，不直接全量序列化artifact。
- Write成功结果目前是创建/替换与字节数，没有覆盖前完整内容的统一diff合同。真实覆盖diff须在授权执行时安全捕获必要旧内容，并明确大文件/二进制/结果未知边界。不能事后读当前文件来假装原内容，也不能仅凭模型传入内容声称展示真实前后diff。
- 两者均保留工具结果与截断提示；可先完成普通工具卡收敛，再单独补结构化diff。

源码：[Edit反馈](../../naiyue_test_agent/tools/edit_diff.py:172)、[Edit提交](../../naiyue_test_agent/tools/edit.py:197)、[Write当前返回](../../naiyue_test_agent/tools/write.py:238)、[Web消息投影](../../naiyue_test_agent/web/serialization.py:44)。

## 3. 前端结构调整

本轮脚本核对：Composer直接props为25个，useConversation为467行，App.module.css为133行。数量支持维护风险判断，不将接近500行或props多本身当成功能缺陷。

1. Composer优先按真实职责组合/分组输入状态、提交动作和控件。模型选择移出后自然减少部分props；不要把整个chat对象塞进全局Context隐藏依赖。只有深层多个消费者确实共享同一状态且有明确所有者时才使用局部Context。
2. useConversation按现有职责拆分，优先抽出运行订阅/重同步或输入草稿队列；复用已存在useSessions/useContext/useSubagents。选择代际、controlsRevision、AbortController、请求身份和跨会话隔离保留一个清晰所有者，不把竞态保护分散丢失。
3. App.module.css只保留应用壳/布局，消息、工具卡、审批、输入等样式归组件CSS Module；语义主题变量留全局。迁移同步检查全部class消费者，删除无消费者旧规则，视觉变更和纯搬迁分片验收。

## 4. 当前与拟议调用链

当前：App→useConversation加载models/会话→watch读取run快照并订阅SSE→events/StreamingMessageProjection按消息ID合并→Conversation逐消息呈现→ToolCall显示参数/结果。Composer负责输入、指令、控件并调用chat动作；审批按钮经ApprovalPanel→approve(batch, decisions, remembered IDs)→后端校验整批→恢复原执行。

正常目标例：用户提出任务→持久聊天记录保存请求归属→模型中间文字/工具调用/最终回答按原消息身份更新同一视觉回复块→工具成功收拢，最终正文完成后显示一处操作栏。浏览器重载或翻页仍按同一归属呈现；审批恢复跨run不会产生错误的新回复块。界面组合不改变模型消息、Runtime turn、工具配对或执行次数。

失败目标例：SSE断线/缺口→保留草稿、已显示内容和锚点→有界退避→获取当前状态/消息覆盖页→按身份合并后续订阅；旧会话响应被取消或丢弃。连续失败显示重试，运行/审批POST不重发；保存未确认和人工核对提示始终可见。Spec101分页、Spec102轻量读取共同复用，不能再下载完整历史或把显示失败变为准入阻断。

## 5. 建议切片顺序与依赖

以下为整体建议顺序；第4项中的U5、自动增高、斜杠补全及第3项的普通工具卡摘要已完成，其余尚未批准统一实施，各切片原子提交：

1. Markdown呈现：GFM、流式/完成统一、代码块标签/复制/高亮；仅新增必要依赖，不顺便升级现有依赖。
2. 服务工作区显示及标题：轻量元信息接线、项目标签、自动标题最小方案；命名算法需确认。
3. 回复块/工具卡：依赖Spec101明确归属与分页合同；工具摘要可先改，Edit结构化diff与Write采集合同分别验证。
4. 输入/页头：自动增高、/补全、按U5状态切换主按钮、运行中草稿左上方follow_up/steering下拉、移用模型菜单；复用当前控制API，保留运行中控制和全部模式。
5. 导航/审批/主题：按职责继续拆小片，处理标题搜索、桌面收起、按钮式审批、变量与主题；日期分组与批次执行语义先决策。
6. 编辑重发/重新生成、供应商思考展示各自独立Spec与全链路测试，不混入基础UI修复。

结构调整随相关切片最小进行，不单独启动全前端大重构。整个清单预计影响超过8个文件，实施前须把选中切片的接口、文件清单、迁移和验收补齐；本稿不以宽泛清单代替可执行Spec。

与101/102的同步约束（本轮只记录依赖，不修改本稿产品实现）：[Spec101第8—10节](101-web-history-pagination.md#8-同步时点比较与建议实施基线)采用推荐的finish同步＋打开/重连历史消费前补录；101-02与102第6项共同确定显式准备、GET只读及历史/live覆盖范围。U1需等稳定请求归属的实际生产字段与分页合同确认，不把run_id或相邻消息当成持久回复块ID；101-01基础表完成不自动满足该前提。U7获取补偿显示前按该准备协议处理归档缺口，运行中以冻结保存边界＋后续实时事件合并，不要求为补历史重发执行POST。已完成U5/自动增高不依赖101，Composer的props/控制API和独立文件分工继续保持。

## 6. 验收要求

- 先为选中缺陷/新行为补失败测试，再最小实现；本轮旧渲染探针不是修复验收。
- Markdown：表格/删除线/只读任务项；流式未闭合语法和最终一致性；代码原文复制、未知语言、长块横向滚动；原HTML/URL限制保留。
- 首页未选会话仍显示真实服务工作区；未绑定/读取失败明确区分；无额外创建会话/模型请求。标题跨重启保存，不覆盖手工重命名或切会话后的另一对象。
- 回复分组覆盖多模型turn、多工具、根/子、审批跨run、steering/follow_up、分页边界、重载与未确认内容；工具call/result按真实ID配对，不合并或重跑执行。
- 工具完成后摘要简洁，失败/拒绝/未确认仍明确；diff必须来自真实执行且带预算/截断，不用伪造数据或模型文字充当文件事实。
- 输入法Enter不误提交、菜单键盘和焦点可用、草稿不丢、增高有上限；运行中空草稿停止/有草稿发送，左上方下拉只随有效草稿显示，默认follow_up/可选steering且调用匹配现有API；成功清空并重置默认，失败保留草稿/选择，切会话/清空后新草稿不沿用旧steering。过期run、重复点击、审批/恢复/停止收尾与内容保留有确定性测试。
- 断网/事件缺口/服务重启/切会话竞态有确定性验证；自动重连有上限，耗尽可手动重试；不得重放执行POST。审批按钮保留认证/来源/批次校验与幂等。
- 侧栏保留既定排序与置顶，日期策略另确认；hover隐藏不能使触屏和键盘丢失菜单。桌面和375px验证布局/触达，主题检查全部状态与对比度。
- 新字段同步检查生产数据、事件、Web、历史、序列化/DB及测试；新增思考/分支能力单独覆盖失败与恢复。
- 实施片完成后执行全量后端、前端测试、TypeScript/Vite build与diff检查；实际浏览器验证后才声明视觉完成。

## 7. 前期调查证据（实施前）

- 使用现有react-markdown和react-dom/server的静态渲染探针：table/strikethrough/task三项均缺少对应GFM元素，退出0，复现当前能力缺口；没有安装依赖或写测试/产品代码。
- 源码/统计核对已完成，具体数值见第3节；未启动浏览器或提供截图验收，没有新增模型请求/环境变更。
- 本稿及future-work/handoff仅做文档链接、结构与空白检查；未运行或宣称待实现功能测试通过。

## 8. 已完成的独立切片：U5与自动增高

本会话承担Composer输入组件切片，保持现有props与send(followUp: boolean)合同；api/types/useConversation/Conversation及后端由101/102相关工作保留。本切片不含/菜单补全、模型菜单移位或其他103功能。

实现与文件：

- components/Composer.tsx：发送方式初始follow_up；活动运行有有效草稿时在编辑器左上方显示下拉；主按钮在发送/停止间切换。原有compact/blocked/pendingMode/stopping限制保留，不能接受补充时继续提供停止入口，stopping时按原合同禁用。
- 新增components/Composer.module.css：仅本切片的下拉布局与编辑器resize规则，沿用CSS Modules及当前输入框min-height/max-height，不改App.module.css共享样式。
- 自动增高根据真实scrollHeight和已有CSS高度边界计算，短内容/清空可收缩，达到max-height后内部滚动；宽度变化重新测量，保留焦点/光标和IME行为，不新增依赖。
- 新增components/Composer.test.tsx：通过真实受控组件验证按钮、发送方式、会话/草稿隔离、成功重置、失败保留和高度边界；jsdom没有布局时只在此测试提供明确的尺寸测量。
- session-controls.test.tsx现有发送方式集成测试迁移到新默认/显示时机，保留真实API入参和不启动新run断言，增加显式steering覆盖；App.test.tsx原steering案例显式选择该方式，保留原API断言。不改公共测试基础设施。

正常：busy且草稿非空→选择follow_up或steering→原send回调→既有useConversation.submit→WebApi.control；父组件确认后清空草稿，组件隐藏下拉/恢复默认，运行继续时主按钮为停止。

失败：提交失败/过期run由原应用反馈，草稿没有清空，方式保持；提交中禁止再次发送，切会话和清空后新草稿恢复follow_up；保留斜杠命令既有有效性与异步确认版本检查。

按先写失败测试、最小实现、回归推进；全量及真实页面证据见下节。本切片未修改App.tsx、App.module.css、api/types/useConversation/Conversation、后端或配置/依赖，未提交/push。

## 9. U5实施与验收记录

真实接线：Composer以send(busy && followUp)调用原回调，空闲普通/Skill发送保持false，活动运行默认true或显式steering的false；父组件确认清空草稿后恢复follow_up。现有异步Skill确认版本检查、停止后的pending保留与显式重新发送继续通过回归。

编辑器useLayoutEffect按CSS min/max-height与scrollHeight调整，监听窗口和父容器宽度；ResizeObserver忽略仅高度变化，避免自行触发测量循环。局部CSS禁用拖拽手柄，保留App共享尺寸与外观，原sendMode样式仍被左上方下拉消费，没有遗留无消费者样式。

测试阶段：前端基线18文件96项通过；新增Composer的13项确定性案例先运行，12失败/1通过，确认旧按钮/默认/高度等边界尚不符合新要求。实现后迁移两处明确依赖旧默认/显示时机的已有集成案例，保留steering及不启动新run断言。回归还发现空闲发送不应随默认方式改成true，源代码保留原false合同，新增局部测试同步按此既有合同断言；没有改Skills测试或放宽断言。

| 验证 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 19文件110 passed，3.14s；含新增13例、原发送方式参数化增加1例。日志`/tmp/naiyue-103-u5-frontend.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite205模块成功，最后构建118ms；日志`/tmp/naiyue-103-u5-build.log` |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 当前工作区2227 passed in91.41s；日志`/tmp/naiyue-103-u5-backend.log` |
| 本切片5个前端源文件结构/空白、git diff --check | 均通过，最大185行；未改公共测试基础设施 |

实际页面：首次Playwright扩展连接未就绪，改用IAB检查生产构建和真实本地Web服务。临时SQLite/工作区及既有OfflineModel提供BLOCK→流式等待，未请求供应商。1440×1000、375×812均验证左上方下拉、follow_up默认、steering显式提交、成功清空和停止按钮恢复。

真实测量：短内容48px；24行草稿scrollHeight=600、显示高度180px且overflowY=auto；改短后48px/hidden。相同长行文字随375→1440宽度变化，编辑器144→72px；375px时pageWidth=viewport=375，无横向溢出，浏览器warn/error日志为空。停止后两条尚未注入的补充由原机制返回待发送，不自动重发。

截图：[桌面](/tmp/naiyue-103-u5-desktop.jpg)、[375px窄屏](/tmp/naiyue-103-u5-mobile.jpg)。截图内容是明确的临时离线验收输入，非真实模型任务。临时浏览器页、预览服务和Web服务均已结束；工作区/SQLite随夹具清理，截图与日志保留。

范围自审为Standard：目标、props/控制调用、成功/失败/停止/IME与跨会话方式重置均核对；样式和测试变化未引入新的权限、API或模块调用边界，无需专项安全/架构代理。103其他功能及101/102仍由各自切片推进，本记录不扩大实施授权，也不将助手验证记为用户已掌握。

## 10. 已完成切片：输入斜杠时的指令菜单

用户授权继续实现输入`/`时的指令菜单，继续避开101/102文件。复用commands.ts内置定义和WebApi.skills()；不增加指令、依赖、配置、后端接口或执行方式。视觉沿用当前白底、圆角、灰色选中层级及CSS Modules，菜单定位在输入框上方，窄屏内部滚动。

- 只在草稿从`/`开始且仍是单个指令词时展示，按指令名/说明筛选；路径、普通文字中的斜杠、已进入参数部分不弹出。内置指令立即可见，技能异步读取并在同一次打开期间本地筛选，不逐键请求。
- 上下键选可用项，Tab/点击补全，未完整指令的Enter也补全；补全后添加空格并保留编辑器焦点，不自动执行。完整指令Enter保留原submit行为，技能发送仍重新校验当前目录与read，follow_up/steering不变。Shift+Enter、Shift+Tab和输入法组合输入保留。
- Escape/点击外部/切会话关闭；编辑新前缀可重新打开。打开原有其他菜单时隐藏建议。压缩、会话专属功能和read/控制受限项显示不可用原因，不通过补全绕过限制。
- 技能读取失败保留内置指令和草稿，显示失败及重试；无结果明确提示，诊断项按既有目录反馈；关闭、切会话或卸载后丢弃旧请求结果。技能来源与限定命令沿用真实目录，内置compact与来源限定技能不混淆。

文件边界：Composer.tsx只接入编辑器组件并传递现有状态；新增ComposerEditor.tsx封装原textarea与建议列表的交互，保持textarea为form直接子元素以保留现有尺寸样式/增高测量；样式放Composer.module.css。新增slash-command-menu.test.tsx从真实Composer入口验证。既有commands/SkillsMenu/控制菜单保持消费者，既有props与发送合同不变。

正常链：输入`/ca`→读取现有技能目录/本地筛选→选择`/case-review`→填入`/case-review `→补充参数并发送→原Composer.submit确认目录/read→原send回调。内置`/co`补全为`/compact `后再次Enter→原runCommand→现有compact入口。

失败链：skills查询失败→仅建议层显示诊断/重试，草稿及内置指令仍可用；执行校验和原异常反馈由现有submit/应用继续负责，建议列表不执行操作。切会话/关闭前缀后的旧查询不得重新打开或覆盖当前列表。

验收先跑新增失败测试，再最小实现；覆盖筛选/键盘/鼠标/焦点、完整指令与带参技能原路径、路径和IME、禁用/失败/重试、过期请求与会话切换。完成后运行全量前端/后端测试、TypeScript/Vite构建、边界及diff检查，并用真实本地离线页面验证1440与375px。仅更新本Spec、本片handoff/future-work，不修改101/102或其API/types/useConversation/Conversation及后端文件。

## 11. 斜杠菜单实施与验收记录

实际接线：Composer→ComposerEditor→更新原草稿；执行仍是Composer.submit→runCommand或skills校验→原回调。编辑器返回Fragment，textarea仍是form直接子元素，原CSS尺寸、父ref及自动增高测量继续生效。只改Composer.tsx、Composer.module.css，新增ComposerEditor.tsx与slash-command-menu.test.tsx；未改commands目录、既有SkillsMenu或任何101/102代码。本次不增加依赖、环境配置、接口或模型请求。

TDD：新增9项测试先运行，9 failed，原因是旧代码没有建议列表/补全行为；实现后全部通过。初次回归发现选项可访问名称被来源标签隔断，补足显式名称后保留原断言通过。覆盖真实Composer接线、内置与限定技能筛选、点击/方向键/Tab/Enter/焦点、关闭/草稿保留、路径/参数/IME/Shift快捷键、不可用原因、读取失败/重试与过期请求；未改既有测试或公共测试基础设施。

| 验证 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 20文件119 passed，2.62s；日志`/tmp/naiyue-103-slash-frontend.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite206模块成功，112ms；日志`/tmp/naiyue-103-slash-build.log` |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 最终2243 passed in94.06s；日志`/tmp/naiyue-103-slash-backend-final.log` |
| 专项只读架构复核及独立4组前端回归 | 未发现有证据的功能问题，35 passed in1.39s |
| 源文件行数/当前本地文档链接/diff检查 | 4个前端源/测试文件均≤500行，最大201行；链接与git diff --check通过 |

首轮全量后端运行期间，另一会话的101存储迁移进入共享工作区，跨进程重开库出现21项unsupported_store_schema失败（2206 passed）。本片未改存储；新测试进程单独重跑代表案例1 passed in1.27s，再按当前代码完整重跑得到上述2243通过。保留首轮日志`/tmp/naiyue-103-slash-backend.log`，不把失败结果隐去或归为本片修复。

真实页面：生产构建＋临时离线Web/SQLite/工作区，fixture中放入可实际发现的case-review技能元信息。1440×1000与375×812验证菜单、内置禁用原因和实际技能；`/ca`→Tab填入`/case-review `且保持焦点/不发送，`/mo`→Enter只补全→再次Enter打开原模型菜单，Escape保留草稿并关闭列表。textarea为FORM直接子元素且短内容48px；375px的pageWidth=375，面板left=17/right=358，完整菜单内部高度309/内容470px，无横向溢出，浏览器warn/error日志为空。

截图：[桌面](/tmp/naiyue-103-slash-desktop.jpg)、[375px窄屏](/tmp/naiyue-103-slash-mobile.jpg)。均为明确的离线夹具，不是真实模型业务证据；没有执行技能任务/压缩或供应商请求。临时页/服务已结束、viewport已复位、临时库/工作区已清理。未提交/push；下一步由用户试用本片，再选择其他不与101/102重叠的103切片。

## 12. 已完成切片：U2工具卡摘要收敛

用户授权继续选择103下一独立项，保持101/102文件不动。本片仅做现有ToolCall的展示：修改components/ToolCall.tsx、扩展components/ToolCall.test.tsx，新增局部ToolCall.module.css；不改Conversation、API/types/useConversation、事件/历史投影、App共享样式及后端。不新增依赖或配置。

当前链：Conversation先按原调用ID/批次配对call、result、phase与agents→ToolCall生成状态文字→details/summary展示。当前summary在结束后仍显示phase与耗时，也没有关键参数。本片维持配对与既有状态优先级，原Conversation和子Agent侧栏都自然消费同一组件。

- 默认折叠；摘要保留展开提示，增加状态图标、原工具名和有界关键参数。bash显示command，read/write/edit/ls显示path，find/grep显示pattern及实际提供的path，subagent显示角色；不将完整写入内容或编辑正文塞进摘要。未提供的数据不补假参数，未知工具只显示原名。长值压成一行并明确省略，完整值在原参数JSON中可查。
- 已结束/未确认/已有结果的摘要仅一行，长参数在可用宽度内省略，状态保持可见；运行中保留既有阶段、耗时和距最后活动信息。结束后阶段/耗时移到展开详情，原计数、进度尾部、参数、结果和原因继续可查。
- 成功、错误结果、执行异常、未执行、结果待核对、状态/结果未确认均沿现有字段表达，不把已有结果提升为确认成功；停止收尾、子任务等待审批与精确停止/打开入口保留。仅运行态启动计时器，终局停止更新。
- 样式沿当前浅灰工具卡/小号正文及CSS Modules，以原生details支持鼠标/键盘展开；窄屏摘要不横向溢出。复用App.module.css中仍被消费的基础样式，新增局部规则，不搬迁共享样式或留下新旧并行组件。

正常例：ls工具结束→同一phase.outcome=success与result→摘要“ls . 成功”→展开可看原JSON、阶段、耗时及目录结果。失败例：grep返回error_result或执行结果未知→摘要继续显示明确错误/待核对→展开原错误正文/原因，不伪装成功或触发重试。两者都只读原props，不改状态、执行或保存。

验收：先补失败测试，覆盖完成摘要/运行转结束/未知与失败/参数缺失、未知工具、长值与HTML转义/原生展开；保留既有批次配对、子Agent审批/停止及计时断言。再最小实现，执行全量前端、TypeScript/Vite build、后端回归、文件界限与diff检查；真实离线Web以实际工具调用检查桌面1440及375px折叠/展开与长参数。Edit/Write结构化diff及U1回复归属仍待独立交付。

## 13. 工具卡摘要实施与验收记录

实现保持ToolCall原props及状态优先级；结束后的阶段/耗时移入同一details，运行阶段与本地计时器保持。摘要从已有工具参数取关键值，空缺/类型不符/未知工具不推测；展示值压成一行并限制160字符，完整参数与结果原文保留。只有明确“成功”显示勾号，“已有结果”与未确认保持中性图标。新增CSS仅约束本组件的单行、长参数省略和键盘焦点；原App样式仍由同一组件消费，没有并行旧实现或新依赖。

TDD：前端基线119通过；新增19例后，旧实现14 failed/12 passed（含原7例），原因是缺少摘要参数及完成态未收拢。实现后保留既有断言，只将新增多行原文检查改为pre.textContent精确比较，避免Testing Library默认空白归一化影响定位。自审为“已有结果”增加中性图标断言，先单例红灯再修正源代码，最终26项ToolCall案例及全量均通过。

| 验证 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 20文件138 passed，2.92s；日志`/tmp/naiyue-103-toolcard-frontend.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite207模块成功，142ms；日志`/tmp/naiyue-103-toolcard-build.log` |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2245 passed in95.48s；日志`/tmp/naiyue-103-toolcard-backend.log` |
| 结构、边界与diff | ToolCall94行、测试205行、CSS8行；共享前端保护文件无diff，git diff --check通过 |

页面使用生产构建、临时Web/SQLite/工作区与明确的离线模型，实际执行三个只读原生工具：ls当前目录、read长路径真实临时文件、read缺失文件。两项成功与一项错误均默认折叠成单行；Enter可展开完整路径/原始正文，点击展开错误能核对原错误信息及阶段/耗时。最终构建刷新并重开会话后仍保持状态与摘要。

1440×1000及375×812实际检查：三条摘要高度均45.40px，完成态摘要无阶段/计时；375px的pageWidth=375，卡片clientWidth=scrollWidth=318，长路径显示区186px/内容1342px而正确省略，状态完整可见；浏览器warn/error日志为空。[桌面截图](/tmp/naiyue-103-toolcard-desktop.jpg)、[375px截图](/tmp/naiyue-103-toolcard-mobile.jpg)。这是本地工具/页面证据，没有供应商调用或业务效果结论。

自审深度Standard，范围仅同一组件内部展示、局部样式及测试，没有新增API、服务边界、执行或权限逻辑，无需专项代理。只修改第12节列出的3个前端文件和本片Spec/handoff/future-work；101/102及其Conversation/API/types/useConversation/后端、App共享样式未修改。临时页/服务已结束、viewport复位、临时工作区/库已清理，截图/日志保留；未提交/push。下一步由用户试用工具卡后继续选择独立103切片，U1与结构化diff保持原依赖。

## 14. 已授权切片：U8会话标题搜索

用户授权继续103下一独立项，保持101/102文件不动。本片实现侧栏“搜索会话”入口和Cmd/Ctrl+K搜索浮层；仅使用Sidebar已收到的当前工作区rows，不增加网络搜索/新API/配置或改变历史读取。日期分组、侧栏收起与单行侧栏布局仍待后续切片。

当前链：App→useConversation/useSessions→Sidebar.rows→现有标题/置顶分组→onSelect(session_id)。目标链：Sidebar入口/快捷键→SessionSearch读取同一rows和现有title格式化函数→按标题本地筛选→显式选中后调用同一onSelect；输入/浏览/关闭都不装载会话、不启动run或修改排序。

- 标题按去除查询首尾空白、忽略大小写的字面子串匹配；正则字符按普通文字处理，空查询展示当前列表，空列表和无匹配有明确空态。未命名会话沿既有时间回退，不新增自动标题或模型请求。
- 结果保持rows的既有顺序，显示真实标题、模型及必要未完成/置顶提示；只根据标题筛选，不搜索消息正文或把其他字段猜成标题。
- 点击侧栏入口或Cmd/Ctrl+K打开同一浮层，聚焦输入；上下键移动候选、Enter打开、Escape/关闭/点击背景取消。组合输入期间不触发快捷键/选择；对话框保留Tab焦点边界，取消恢复原焦点，选中后沿既有会话打开行为。窄屏使用浮层，无须改App的侧栏开关。
- 原disabled/managing门禁控制结果能否选择；仍可本地查阅列表，不能因搜索绕过准入。props更新、重命名/删除/排序后直接使用新rows，不能提交已消失的候选；搜索不维护第二份会话副本或持久缓存。
- 同一时间不叠加会话操作菜单与搜索浮层；关闭/卸载清理快捷键与焦点影响。不改原会话菜单、拖动算法或Sidebar公开props。

文件边界：Sidebar.tsx只增加入口/快捷键和浮层接线，Sidebar.module.css加入口样式；新增SessionSearch.tsx及SessionSearch.module.css封装搜索对话框，新增session-search.test.tsx从真实Sidebar验证。因对话框有独立焦点/筛选职责使用小组件，不建立通用命令平台。既有会话管理测试作为回归，不改公共测试基础设施。

正常例：已有三条会话→输入“接口”→按原顺序显示匹配标题→Enter调用原onSelect精确ID；失败/不可用例：没有匹配/列表被删除/操作期间禁用→保留查询并显示真实空态或禁用，不请求模型、不打开错误对象。现有打开失败仍交原应用反馈，不增加搜索自己的重试链。

验收先新增失败测试，再最小实现，覆盖鼠标/快捷键、匹配/空态、顺序/实时rows更新、键盘/焦点/IME、门禁、无读取与执行副作用。之后全量前端、TS/Vite build、后端、边界/diff检查；真实临时离线Web以实际保存的会话标题验证1440与375px搜索及精确选择，清理临时资源，不改用户库或调用供应商。

## 15. 会话搜索实施与验收状态（2026-10-18）

Sidebar接入入口/全局快捷键，SessionSearch只读取同一rows及既有标题格式化函数。搜索不触发读取、执行或改排序，显式选中才调用原onSelect；实施期间另一会话将打开协议迁为prepare，本片只同步新增App集成测试的mock/断言，不实现或复制该协议。实际写入仅第14节列出的5个前端文件及本片Spec/handoff/future-work，未修改101/102的API/types/useConversation/Conversation/App/后端或公共测试基础设施。

TDD：前端138基线通过；新增12例先12 failed再通过。架构复核复现“会话操作菜单→快捷搜索→Esc”焦点落body，补第13例红灯后，将恢复目标提前保存在Sidebar，菜单来源复用origin，重复快捷键不覆盖；修复后专项21例与独立原复现均通过。保留真实App无选择不加载/无模型执行断言，输入法、Tab边界、空态、字面筛选、顺序、删除/重命名后的候选及门禁均覆盖。

| 验证 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 最终21文件151 passed，2.59s；日志`/tmp/naiyue-103-search-frontend-final.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite209模块成功，118ms；日志`/tmp/naiyue-103-search-build-current.log` |
| 搜索＋既有会话管理专项 | 2文件21 passed；修复复核通过，无已确认遗留发现 |
| 后端全量及定向复核 | 首轮5 failed/2243 passed；定向2 failed/3 passed；最终5 failed/2077 passed，160.76s后中断，不是通过 |

后端检查期间工作区正迁移101-02：首轮新分页路由未接齐、schema7→8断言未同步；定向重查时分页3例通过、两处版本断言仍失败。另一会话更新后再次完整运行，当前失败变为context_inspector、manual_compaction、observation_isolation两参数及web_session_controls，约92%进度停留超过一分钟后中断，最终日志`/tmp/naiyue-103-search-backend-final.log`保留。未据此推定所有失败的根因，也未修改这些源文件/测试；按AGENTS连续三次失败停止后端重试，整库验收须待101/102收敛，不能用本片前端绿灯替代。

真实页面使用临时工作区/SQLite及四条实际保存的会话元信息，无模型调用。1440×1000与375×812验证点击、Cmd/Ctrl+K、标题筛选/原顺序、键盘和鼠标精确选择ID、空结果、背景取消、草稿/焦点保留；菜单切入搜索后Esc回原触发器。最新版prepare协议重新启动服务与构建后再次核对选择链路通过。375px的pageWidth=375，浮层宽343px、left16/right359；长标题内容1144px在305px区域省略，浏览器warn/error日志为空。[桌面截图](/tmp/naiyue-103-search-desktop.jpg)、[375px截图](/tmp/naiyue-103-search-mobile.jpg)。

5个前端文件最大191行，组件存在/实现/接线、文档链接及git diff --check检查通过；临时页面/服务/数据库已清理，viewport复位。未新增依赖/配置、未操作用户库或提交/push。下一步先由101/102会话收敛其当前后端回归，再整合验收；日期分组/单行侧栏/桌面收起等其余U8内容仍待独立切片。

## 16. 已授权切片：U8侧栏单行与桌面折叠

用户确认继续实施已列出的两项，顺序为单行化→桌面折叠。代码范围仅Sidebar.tsx、Sidebar.module.css、新增components/Sidebar.test.tsx；不改App/App.module.css、API/types/useConversation/Conversation、101/102的生产或测试文件，不增依赖/配置/存储。已修改文件和其他会话工作保持。

当前链：App传rows/selected/open及原操作回调→Sidebar分置顶/普通组→标题、模型第二行、未完成提示→选择/菜单/拖动。桌面260px侧栏与flex主区并排，窄屏≤760px由原open/onClose控制抽屉。目标沿同一链，变化只有展示及Sidebar局部collapsed状态。

- 单行摘要：会话标题占一行，模型信息移至title提示；完整标题/模型/未完成状态继续保留可访问名称，未完成行有紧凑标记。长标题省略；未知标题沿原时间回退。原选中态、置顶/顺序、菜单与拖动合同不变。
- 桌面折叠：默认展开，收起成窄栏，保留展开、新建和搜索入口；只隐藏列表，不销毁会话DOM和滚动容器。操作不切换会话、不修改草稿、不发网络请求，不保存新的偏好。折叠按钮有名称、aria-expanded/controls及键盘焦点。
- 折叠样式仅在>760px生效，移动抽屉继续由原open/onClose控制；桌面折叠后缩到窄屏仍能显示完整列表，返回桌面保持原局部折叠选择。宽度变化由现有Composer测量响应，不改应用壳。
- 菜单→折叠时先保存折叠按钮为焦点目标，关闭原菜单并取消未完成拖动手势，避免焦点进入隐藏列表。拖动清理如达到第三次复用则收为同组件短函数，不改变排序计算。搜索取消仍返回当前入口，折叠不绕过原新建/选择门禁。

正常例：选中会话/已有草稿→桌面收起→主区变宽→搜索/取消→展开，原ID、列表顺序、滚动位置和草稿保持。边界例：操作期间禁用→显示折叠仍可切换，新建/搜索选择继续受原门禁；移动抽屉打开/关闭不被桌面折叠值影响。

分两步TDD：先以原模型第二行/摘要为红灯做单行，再补折叠状态、入口、焦点及移动props边界红灯后实现。已有搜索/会话管理与全量前端回归、TS/Vite构建、真实临时Web的1440/375及来回缩放验证；检查长标题/触屏/键盘、列表滚动和草稿。后端整库结果单独报告；此前红灯不在本片偷偷修复或宣称已通过，源码收敛证据明确后才再跑一次有界回归。

## 17. 侧栏单行与桌面折叠的前端验收（2026-10-18）

当前接线为Sidebar局部collapsed状态→data属性及仅>760px生效的CSS；nav、会话行和原回调保持，模型移至title提示，未完成状态在同一行显示。已有sessionMeta样式用于紧凑状态，未引入无消费者的新包装。菜单转折叠将焦点目标改为折叠按钮；三处相同手势清理复用组件内cancelDrag，原排序算法和公开props不变。

TDD分两步：单行2例先2 failed；实现后与搜索/会话管理共23 passed。折叠新增6例先6 failed/2 passed，再实现；其中新建测试误断言React点击回调零参数，确认原接线会传事件后改为检查恰好调用一次，未改生产回调或放松选择ID/权限/焦点断言。最终8项新增测试覆盖完整标题/转义/回退/置顶、选择、折叠与DOM保留、入口与门禁、菜单焦点及移动open/onClose合同；CSS断点和滚动位置由真实浏览器补充验证，未把jsdom结果当布局证据。

| 命令/检查 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 22文件159 passed，3.33s；`/tmp/naiyue-103-sidebar-frontend-final.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite209模块，104ms；`/tmp/naiyue-103-sidebar-build.log` |
| 当前源码自审 | Standard，同一组件展示/局部CSS/测试，范围与第16节一致；未新增服务/API/依赖，无需专项代理 |

真实页面使用临时SQLite中实际保存的26条离线会话元信息，无模型请求。1440px侧栏260→64px，只剩展开/新建/搜索三入口；Enter/Space切换、搜索取消回焦、菜单关闭焦点通过。折叠前nav.scrollTop=566，展开后仍为566；329字符草稿和原选中ID保持。375px页面宽375、抽屉宽240、打开后left=0；桌面collapsed=true时移动列表仍display:block，回桌面仍64px，草稿/选择不变。会话行高44px；长标题内容1232px，143px桌面/123px移动区域省略，图标保持16px（初验约2px，局部flex-shrink修正并重构建复验）。warn/error为空。[桌面展开](/tmp/naiyue-103-sidebar-desktop.jpg)、[桌面收起](/tmp/naiyue-103-sidebar-collapsed.jpg)、[375px抽屉](/tmp/naiyue-103-sidebar-mobile.jpg)。

本轮写入仅Sidebar.tsx/Sidebar.module.css/新增Sidebar.test.tsx及Spec103、handoff、future-work；3个源码/测试文件最大151行。未修改101/102或其API/types/useConversation/Conversation/App/共享样式/后端/测试。当前另一个会话的`/tmp/naiyue-101-02-backend.log`显示1 failed/2255 passed（manual_compaction），仍无后端收敛全绿证据，本片遵守第15—16节停止重试约定，没有重启后端全量或擅自处理其失败。因此本片已通过前端验收，整库验收待闭合。折叠只保持当前页面局部状态，刷新默认展开，未增加偏好保存。

临时页面/服务/工作区/SQLite收尾后删除，截图和验证日志保留；未提交/push。下一步由101/102会话收敛后端回归，再做整合验收；U8日期分组及其排序边界仍待讨论，本片未实施。

## 18. 已授权切片：B1已完成消息的GFM呈现（2026-10-18）

用户确认先提交并push当前基线，再实施本片。基线7385beb已推送origin/main；提交前沿文档PATH运行后端2817 passed in116.70s，前端28文件198项和TypeScript/Vite211模块成功。首轮未带Pi工具目录的命令因缺少fd导致30项失败，按原文档PATH复跑后全部通过，无产品修复或放宽断言。

当前链：历史/实时投影产出MessageRow.content原文→Conversation按状态分支→已完成的人类/模型消息交ReactMarkdown，provisional/工具结果为纯文本，摘要为details/pre。现有解析未注册GFM，本机静态渲染复现table/checkbox/del均不存在。目标只扩展已有Markdown分支，主会话和SubagentSidebar共用Conversation，API、数据库、消息身份/配对、历史锚点、复制原文保持。

- 新增精确锁定remark-gfm 4.0.1，不升级React/react-markdown及其他既有依赖；复用官方remarkPlugins入口，不自写解析器或引入原始HTML解析。
- 表格生成语义table/thead/tbody，保留列对齐、行内代码及链接；外层局部横向滚动容器带名称/键盘焦点。正文中文正常换行，多列或长路径/URL只在表格内滚动，1440/375与窄子侧栏均不撑宽页面。表格等自定义渲染组件在模块内稳定定义，不能因Conversation滚动/复制等状态更新重新挂载并丢失焦点/横向位置；本机新回归已复现内联定义导致同内容DOM被替换。
- 任务列表只读，保留checked/unchecked/disabled与嵌套列表结构；普通列表样式不受影响。双波浪号删除线生效，singleTilde=false保留单波浪号普通文字及代码原文。
- 插件自带的自动链接仍经过safeUrl，skipHtml、图片占位和外链rel限制保持。标准脚注锚点须按Conversation实例及消息身份隔离，避免多条消息/根子视图同名脚注交叉引用；不增加独立脚注交互系统。
- 流式/未确认内容、工具结果和摘要保持原分支；流式统一渲染U3及代码块高亮/复制U4下一片单独讨论，不在本片一起实现。

文件：frontend/package.json及package-lock.json新增插件；Conversation.tsx在原ReactMarkdown上接插件和table容器；App.module.css补局部表格/任务项样式；Conversation.test.tsx补行为/原文复制/安全/分支测试；subagent-sidebar.test.tsx在既有实际侧栏链增加GFM断言。更新本Spec、handoff和真实待办，不新增生产模块或更改公开props。

正常例：模型回答“表格＋- [x]/- [ ]＋~~旧方案~~”→content原文保存→现有分支生成table/只读checkbox/del→根子视图一致；复制仍得到同一原文。边界例：单元格带脚本/危险链接/远程图片→沿原限制显示或跳过；长表格留在局部滚动区；生成中同样文本仍显示原文并保留未完成提示。

验收：先补测试并确认GFM缺口的预期红灯，再最小接入，检查表格/对齐/任务嵌套/只读/删除线/复制/根子及分支不变；回归历史分页/滚动/工具卡与原安全测试。执行全量前端、TS/Vite、带原生工具PATH的后端、依赖树/diff/500行检查；真实临时离线页面验证1440/375与子侧栏布局及控制台，无供应商请求、真实配置/用户库修改。首个基线提交已完成，本片完成后保留工作区供审阅，不自动再次提交push。

## 19. B1 GFM实际实现与验收（2026-10-18）

实际链：MessageRow.content→Conversation原已完成Markdown分支→remarkGfm(singleTilde=false)→ReactMarkdown→模块内稳定的markdownComponents。table局部滚动容器和原img/a限制统一接线；链接传递经框架处理的定位属性，AST node不传DOM。useId加消息ID限定脚注锚点前缀，不添加工具/会话协议或存储字段；主会话与子侧栏消费同一组件。原provisional/工具/摘要分支及复制原文保持。

新增8项组件测试并强化既有子侧栏集成断言。首轮专项8 failed/2 passed，7项新GFM用例均红灯；插件接入后检查到链接定位属性丢失并补齐。真实浏览器键盘检查发现内联table组件在会话滚动状态更新时重新挂载；新增第8项先1 failed，再将同一渲染映射稳定定义于模块内转绿。断言保留同一DOM、焦点和scrollLeft，未以深比较放宽身份断言。未修改公共测试基础设施。

| 验证命令/边界 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 28文件206 passed，3.17s；`/tmp/naiyue-103-gfm-frontend-final.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite304模块成功，120ms；`/tmp/naiyue-103-gfm-build-final.log` |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2817 passed in118.23s；`/tmp/naiyue-103-gfm-backend.log`；之后仅改前端和文档，后端未再变动 |
| 依赖/结构/范围 | remark-gfm精确4.0.1，新增18个传递包，既有lock条目版本0变更；4份本片前端源码/测试最大160行，diff检查通过 |

真实页面使用最终生产构建、临时工作区/SQLite和固定离线模型，通过原图实际完成父→子→父三次调用并保存消息；不是供应商或业务效果验收。1440px主表格768/768（可视/内容），375px为320/379，键盘ArrowRight后scrollLeft=40且仍聚焦原region；页面scrollWidth始终等于viewport。桌面同时开根子视图时为689/689与348/404，375px子表格319/379，未撑宽页面；任务项checked/unchecked/disabled、中文/长路径/链接、普通列表和原代码块均可查看，warn/error日志为空。有限指标`/tmp/naiyue-103-gfm-browser-metrics.json`，截图：[桌面](/tmp/naiyue-103-gfm-desktop.png)、[375px](/tmp/naiyue-103-gfm-mobile.png)、[桌面子侧栏](/tmp/naiyue-103-gfm-child-desktop.png)、[375px子侧栏](/tmp/naiyue-103-gfm-child-mobile.png)。

复制原文由单元测试精确比较；浏览器按钮反馈“已复制”，但浏览器隔离剪贴板接口未读回源文本，不将其计入实机读回通过。本片沿用原复制流程。Playwright扩展连接不可用后改用IAB完成布局/键盘/控制台检查；临时页面关闭、viewport复位、服务及临时库收尾，截图和日志保留。

本片按含lockfile累计约500行的Deep范围复核；架构独立复跑4文件23项、安全独立复跑2文件11项通过，没有确认的P0–P2阻塞问题。主代理同时核对内容限制、消息/组件身份、滚动重绘、局部预算及依赖变更。此前基线架构52/安全32项属于提交前结果，不冒充本片审查。0次真实供应商、未改真实配置/用户库。代码保留工作区待审阅，基线7385beb已push，不自动提交本片。下一步只讨论U3：让生成中/未确认模型正文复用同一Markdown渲染，保留状态标记、工具结果和摘要分支；重点验证未闭合语法、连续增量、停止/切会话、滚动锚点和有界大文本成本，不预建增量解析器。

## 20. 已授权切片：U3流式Markdown

用户确认按上一轮方案更新Spec后实施。第18—19节为已完成GFM的版本边界；本片扩展生成中和未确认的模型正文，保留已有9个未提交路径，不另提交push。无新依赖、后端/事件/API/数据库字段或公开props修改。

当前链：SSE message_delta→events.applyEvent/agentEvents.applyAgentEvent按消息ID累积MessageRow.content，标记provisional/draft→Conversation纯文本；message_completed/revised以snapshot校准原ID并改complete→切换ReactMarkdown；run_finished将未完成草稿标记unconfirmed。目标保留事件与投影原逻辑，只使AI正文在draft/unconfirmed/complete共用ReactMarkdown+remark-gfm；工具结果、摘要和非AI的provisional继续按原文本分支。

- Conversation仅调整正文分支，复用模块内稳定markdownComponents、safeUrl、skipHtml、图片占位、脚注前缀及表格滚动样式。状态由原字段决定，“正在生成/未完成内容”不能因Markdown能解析而消失，显示不构造终稿或改写content。
- 每次按当前累计文本重新解析，不向原文补齐星号/围栏/链接，不自建增量解析或节流框架。未闭合代码块按现有解析器展示，具名Markdown链接/加粗/表格收到必要语法后自然成形；GFM可先将未闭合链接中的字面URL识别为普通自动链接，不将“任何a元素都不允许出现”作为合同。终稿仍以框架快照为准，可能校准此前文字。
- article/message ID、分页锚点、根子范围、工具配对、原文复制保持；相同结构从增量到终稿不替换外部消息或稳定表格/代码DOM。原文不完整时复制得到当时原文，不带显示层补写。
- 沿用nearBottom/anchor：在底部时跟随增量；用户翻看历史时保持原可见消息位置，完成/停止也不强制跳底。根子共用一处生产分支，切换会话清理旧视图，旧会话事件仍由现有身份门禁拒绝，不加第二套控制。
- 在现有有界视图内用8 KiB/32次连续更新作确定性完整性验证及独立耗时记录，不在单测断言极紧时间上限；成本若有实际问题，再基于证据讨论优化。代码标签/高亮/专用复制仍为U4后续切片。

文件：修改components/Conversation.tsx；迁移Conversation.test.tsx里旧“AI草稿纯文本”断言，保留工具/摘要/人类消息断言；新增Conversation.streaming.test.tsx覆盖实际事件→增量呈现/终稿/停止/未闭合/内容限制/切换与大文本；history-scroll.test.tsx补增量排版和底部跟随；subagent-sidebar.test.tsx在现有真实App/侧栏链验证子draft呈现及完成/停止。只扩展测试，不改公共测试基础设施。更新本Spec/handoff/future-work。

正常例：第一段“**正在检查”保持原文→第二段补“**”并带GFM表格/任务项，provisional仍为true但立即显示排版→message_completed校准同一ID，状态完成。失败例：部分正文含已闭合加粗和未闭合代码→停止/失败终态→同一正文继续按Markdown呈现，并标为未完成；工具不重跑、无新请求、原文不被补齐。危险链接/HTML在每个中间片段均受原限制，不只检查终稿。

TDD先确认206项基线对应专项通过，再补预期红灯→最小生产改动→专项和全量前端、TS/Vite、原生PATH后端、依赖/500行/diff及文档链接。真实临时离线服务用事件门控制模型分段，分别在子、根生成中检查语义DOM/标记、终稿或停止与1440/375布局/滚动；页面操作和生成门由明确状态同步，不用紧sleep窗口。不调用真实供应商、不修改真实配置/用户库。

## 21. U3实际实现与验收

相对第19节GFM基线，生产代码仅修改Conversation正文分支的一处条件：tool或非AI provisional仍为文本，AI无论draft/unconfirmed/complete均交原ReactMarkdown。状态行、原文复制、模块级markdownComponents、脚注前缀、内容限制、nearBottom/anchor保持；没有新类/生产模块或下层改动。当前链为applyEvent/applyAgentEvent累积或快照校准→原history.merge→根/子Conversation→同一Markdown节点。正常终稿仍校准同一ID；停止/失败仍只改变原状态事实，不补正文或重跑工具。

TDD：既有专项16 passed；新增9项事件/增量组件用例、2项滚动用例并迁移1项旧草稿断言，先12 failed/14 passed，再作最小生产修改。剩余1项新断言错误地禁止GFM对部分URL自动链接，核对现有解析后改为验证完整具名目标，未修改解析器/原安全限制；另补2项真实App子侧栏完成/停止集成。总计新增13项前端测试，保留工具/摘要/人类消息、复制和消息/DOM身份等旧断言，未改公共测试基础设施。首次build发现新测试SseFrame导入来源错误，改为现有sse.ts后重建通过。

| 命令/检查 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 29文件219 passed，3.12s；`/tmp/naiyue-103-u3-frontend-final.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite304模块，148ms；`/tmp/naiyue-103-u3-build-final.log` |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2817 passed in117.25s；`/tmp/naiyue-103-u3-backend.log` |
| 8 KiB/32次更新样本 | 独立jsdom案例140.62ms，含事件累计/React渲染/断言；`/tmp/naiyue-103-u3-render-probe.json`，无紧时间断言，不代表浏览器FPS/所有规模的压力保证 |
| 范围与结构 | 相对GFM保存快照，package/package-lock/App.module.css逐字节不变；本片5份前端源码/测试最大160行，pip check和diff通过 |

临时真实Web/SQLite/原生图分四次固定离线模型调用：父委派→子分段完成→父分段后用户停止→用户另发请求正常完成。每段由独立Event及测试信号放行，按实际页面状态推进，无固定短sleep。原模型取消产生已有生命周期日志；两根持久诊断分别为stopped/closed和completed/closed，没有将取消当作Markdown成功终稿。0次真实供应商请求。

- 子仍在生成时已有表格/加粗/只读任务项与未闭合代码，下一段具名链接出现而“正在生成”和表格焦点仍在。375px页面宽375，子表格319/379（可视/内容）。
- 主生成时阅读历史：高度1535→1985，scrollTop始终175.5，同一锚点相对顶端偏移始终-26.703125，下一段不会拉到底；表格焦点保留。
- 停止后仍有table/strong和“未完成内容”，没有“正在生成”；375px主表格320/379、页面宽375。
- 另发请求并回到底部：高度3986→4416，底部间距均约0（亚像素值-0.5），同一最后消息ID保留；正常完成后最后消息无生成/未完成标记，table/code保留。控制台warn/error为空。

截图：[生成中的主会话](/tmp/naiyue-103-u3-root-draft.png)、[生成中的子视图](/tmp/naiyue-103-u3-child-draft.png)、[375px子视图](/tmp/naiyue-103-u3-child-mobile.png)、[停止后](/tmp/naiyue-103-u3-stopped.png)、[375px停止后](/tmp/naiyue-103-u3-stopped-mobile.png)、[375px正常完成](/tmp/naiyue-103-u3-complete-mobile.png)。有限布局/状态指标为`/tmp/naiyue-103-u3-browser-metrics.json`。临时服务/数据库/页已收尾，viewport复位；保留截图/日志，不操作真实用户库或配置。

本片按相对GFM基线的Standard范围主代理复核：只有已有正文分支变化，其他为测试/文档，无新边界/依赖，不额外派生专项代理；此前GFM专项结果不冒充U3独立审查。没有确认的遗留阻塞，未提交push，原GFM工作区保留。下一步先讨论U4的语言标签、块级复制和高亮，明确未知语言/无语言、流式更新/复制反馈、大块成本和样式，再授权实施；不引入编辑器或代码执行入口。


## 22. 已授权切片：U4代码块语言、复制与高亮

用户确认按U3收尾后的U4方案继续。保留第21节全部未提交成果，不提交push；本片只增加代码块呈现，不改事件、后端、配置、数据库或公开Conversation props。

当前链：MessageRow.content→根/子Conversation的ReactMarkdown→remarkGfm→默认pre/code，无语言栏、专用复制或高亮。目标链在同一ReactMarkdown追加rehypeHighlight(detect=false)；模块级稳定markdownComponents.pre接CodeBlock，行内code保持原渲染。新增精确rehype-highlight 7.0.2，既有依赖不升级；未知语言插件保留原文本且记录非致命VFile消息，未指定语言不猜测。依据[官方机制](https://github.com/rehypejs/rehype-highlight)及[7.0.2源码](https://github.com/rehypejs/rehype-highlight/blob/7.0.2/lib/index.js)。

- 新增components/CodeBlock.tsx及CSS module：pre外的工具栏显示围栏语言，无标记显示text；长语言截断但title保留完整值。pre/code仍为原子节点，代码局部横向滚动并可键盘聚焦；沿用灰色边框/等宽字体，只增加低饱和高亮色。无编辑/执行按钮或行号。
- 复制仅取pre当前textContent，保留解析器产生的换行与空格，不带围栏、语言、工具栏或高亮HTML；整回答复制继续取row.content原文。每块独立状态，剪贴板失败显示可重试反馈。内容更新时重置旧反馈，并以请求序号及当前文本检查阻止旧Promise完成后给新代码标为已复制；卸载后不更新。
- draft/unconfirmed/complete共用同一接线；未闭合围栏自然显示已接收代码。稳定pre组件类型保留代码DOM、焦点和横向位置，完成校准仍使用真实snapshot。工具结果与摘要保持原纯文本分支。skipHtml、safeUrl及图片占位保持，不新增HTML注入入口。
- 沿用现有消息预览/流式预算，不增加自定义代码截断、编辑器或工作线程。以已标语言的8 KiB/32次增量和有限大块验证文本完整/耗时，记录浏览器与构建体积；不以单样本承诺所有规模性能。若测量暴露明显阻塞，先据证据调整本Spec。

文件：package.json/package-lock.json精确新增依赖；Conversation.tsx接rehype插件和pre；App.module.css迁出旧messageBody pre样式，inline code/摘要保持；新增CodeBlock.tsx/CodeBlock.module.css及Conversation.code.test.tsx；扩展根子侧栏集成和必要流式/滚动断言。测试仅扩展现有Vitest/Testing Library，不改公共测试基础设施。完成后更新本Spec/handoff/future-work。

正常例：模型增量返回python围栏＋部分函数→原事件累积→高亮AST→代码栏显示python和复制→复制得到当时代码→追加内容使反馈复位→终稿校准同一code DOM。失败例：未知语言或未标语言仍有工具栏/原代码；剪贴板拒绝显示复制失败并可重试，其他块与消息正常；停止保留未完成标记和可复制代码，不把部分输出当终稿。

验收：先新增预期失败测试，再最小接入；覆盖已知/未知/无语言、行内不加工具栏、准确复制/失败/重试/块隔离、流式追加和异步复制竞争、DOM身份/局部滚动、停止及根子一致。跑全量前端、TypeScript/Vite、原生工具PATH后端、依赖差异和500行/diff检查。用临时离线真实Web/SQLite/图验证1440/375主/子、生成中及完成、长代码滚动与控制台；0次供应商，不改真实配置/用户库，收尾临时资源。


## 23. U4实际实现与验收

实际链：events.applyEvent/agentEvents.applyAgentEvent及历史投影→MessageRow.content→同一Conversation→remarkGfm→rehypeHighlight(detect=false)→模块级markdownComponents.pre→CodeBlock。组件从code的AST class读取语言，仅消费高亮后的React子节点；复制读取pre.textContent。useLayoutEffect比较当前代码文本，变化时递增复制请求序号/重置反馈；异步完成需同时匹配请求序号及当前DOM文本。pre卸载后引用为空，旧Promise不更新新视图。没有新增运行层/应用层对象、事件/公开props/存储字段。

正常：未闭合python围栏先显示已接收内容及语言栏→点击复制得到当时解析代码→后续增量高亮并重置反馈→终稿校准同一pre/code。停止仍显示原未完成状态；未知语言、无语言与代码内HTML保持字面文本。失败：剪贴板拒绝只在当前块显示“复制失败，重试”，再点可以成功，不改变其他块或消息。工具结果/摘要仍使用旧纯文本分支；整回答复制继续取原Markdown。旧messageBody pre样式已迁到组件，不保留重复呈现路径。

TDD新增15项，首次14 failed/1 passed（行内原行为通过），最小接入后三组34项通过；随后扩展真实App子侧栏的已有完成/停止两分支，保留事件次数和根子隔离断言。覆盖python/js/json/bash高亮、未知/无/长语言、原文空格与换行、块隔离、复制失败/重试、流式反馈/成功与失败Promise竞争、DOM身份/焦点/横向位置及未确认状态。公共测试基础设施未修改。

| 命令/检查 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 30文件234 passed，3.31s；`/tmp/naiyue-103-u4-frontend-final.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite512模块成功；`/tmp/naiyue-103-u4-build-final.log`。首包656.66 kB/gzip204.38 kB，相比U3增加168.85/53.54 kB，CSS增加1.36 kB；有超过500 kB的Vite提示，无构建错误，未通过提高阈值隐藏提示 |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2817 passed in117.38s；`/tmp/naiyue-103-u4-backend.log` |
| 8 KiB/32次增量 | 独立jsdom样本59.58ms，含渲染/断言；`/tmp/naiyue-103-u4-render-probe.json`。样本显式核对UTF-8输入不少于8192字节，无紧时间断言，不代表浏览器FPS/所有规模压力保证 |
| 依赖与结构 | 新增精确rehype-highlight 7.0.2及5个传递包，既有lock版本0变更；npm audit自动检查0漏洞，pip check通过。新增组件44行、CSS12行、代码测试133行，Conversation164行，均低于500；diff检查通过 |

真实浏览器使用最终生产构建、临时工作区/SQLite与原生图，4次固定离线模型调用完成父委派→子三段→父两段后用户停止→新请求正常终稿；Event/信号同步放行。试验脚本首次把已有call辅助函数返回的AIMessage当作tool_call字典，启动前失败，按真实类型修正；未修改产品/测试基础设施。0次供应商，未改真实配置或用户库。

- 子生成中复制反馈为“已复制”，下一段后为“复制代码”；python有实际高亮token与非默认颜色，未闭合围栏可用。桌面代码可视361px/内容2554px，小屏332/2554，ArrowRight后scrollLeft=40且保留焦点，页面宽分别1440/375。
- 主生成中三个代码块分别为python/unknown-example/text；后两块无高亮span，script字面显示。桌面可视766/内容2554，小屏318/2554；停止后保留高亮、复制按钮及“未完成内容”，页面不横向溢出。
- 新请求正常完成，python/json均有高亮；375px中python318/359局部滚动，JSON318/318，最后代码复制反馈为“已复制”；1440px均766/766。浏览器warn/error为空。剪贴板原文由确定性测试精确比较，真实页面只验证按钮反馈，不冒充系统剪贴板读回。

有限指标：`/tmp/naiyue-103-u4-browser-metrics.json`。截图：[桌面终稿](/tmp/naiyue-103-u4-complete-desktop.png)、[375px终稿](/tmp/naiyue-103-u4-complete-mobile.png)、[桌面子视图](/tmp/naiyue-103-u4-child-desktop.png)、[375px子视图](/tmp/naiyue-103-u4-child-mobile.png)、[桌面生成中](/tmp/naiyue-103-u4-root-draft-desktop.png)、[375px停止后](/tmp/naiyue-103-u4-root-stopped-mobile.png)。临时页关闭、viewport复位、服务及临时数据库已收尾，保留截图/日志。用户原GFM/U3工作区保留，未提交push。

本片在Plan Execution范围由主代理核对新增组件的真实接线、状态竞争、内容边界、依赖差异、根子/流式/滚动回归，无确认阻塞；保留明确的首包体积成本，不预建异步高亮/编辑器。下一步只讨论B2服务工作区显示，可结合U9的项目短标签/完整路径提示；先确认服务元信息读取、未绑定/读取失败和会话优先级，不修改实际工作区或会话装配。自动标题B3的命名算法和其余103切片继续单独审阅。


## 24. 已授权切片：B2服务工作区显示与U9短标签

用户确认按U4收尾的方案继续。本片保留原GFM/U3/U4未提交改动，只修复未选会话时错误显示“未设置工作区”，并将真实路径格式化为短标签、完整路径保留title。不更换实际工作区，不修改配置、会话装配、数据库或权限。

当前链：WebSettings已绑定workspace_root，但App仅取chat.session?.workspace_root，空首页因此回退为未设置。目标：认证GET /api/service→WebService.service_info()从settings生成{workspace_root:string|null, workspace_label:string|null}；WebApi.serviceInfo(signal?)→新增WorkspaceLabel独立读取并管理loading/loaded/error→App页头。服务信息加载与原models/sessions链分开，不让一次显示读取失败阻断模型/会话/发送。

- 无会话时以服务绑定为准；有会话时以session.workspace_root为准，包括显式null，不能用空值合并误回退到服务。服务返回短名由后端Path.name/anchor生成；不同会话的已返回路径仅作斜杠/反斜杠末段格式化，不推断真实位置或重新解析权限。根路径保留其本身。
- loading显示“正在读取工作区”；loaded null显示“未绑定工作区”；请求/解析失败显示“工作区信息读取失败”和明确重试。错误内容不直接回显供应商/服务器正文。重试只请求service元信息，不重读models/会话或执行POST。
- 每次读取及重试有独立AbortController；api对象替换/卸载取消旧读取，迟到成功或错误不得覆盖当前状态。选中会话后即使服务读取失败，也按该会话已知归属显示。
- 沿用页头CSS Modules；长目录名省略，title保留完整路径；375px也显示短标签，不沿用旧的隐藏整段路径规则。当前子智能体入口的存在/启用规则保持，U9另一部分以后单独讨论。

文件：web/service.py新增service_info，web/app.py新增只读受认证路由；frontend/api.ts新增ServiceInfo类型与serviceInfo入口；新增components/WorkspaceLabel.tsx，App.tsx替换页头路径分支，App.module.css调整标签/失败重试/窄屏。新增tests/test_web_service_info.py及frontend/src/workspace-label.test.tsx覆盖真实API和App接线，必要时在旧App测试的现有客户端fixture明确补新请求的返回，说明测试迁移，不修改公共测试基础设施。更新本Spec/handoff/future-work。无新依赖、环境/真实配置修改。

正常：服务绑定/work/demo→空首页GET service返回真实路径与demo→页头demo/title完整路径→选择会话时使用会话返回的路径/短名。失败：service返回503/无效JSON→页头失败和重试，模型/会话照常加载，发送仍由旧准入判断；重试成功后只更新标签。未授权/错误Host或跨Origin请求拒绝，不泄露路径，不装载或执行会话/模型。

先补App与后端API失败测试，再实现并逐项回归；覆盖未绑定/根路径/中文长名/Windows路径格式、读取中/失败重试、session null优先和迟到响应。执行全量前端/后端、TS/Vite及diff/500行检查；临时离线Web/SQLite在1440/375验证首页、选会话和错误提示重试，0次供应商/用户库变更。完成后保留工作区，不自动提交push。

### 剩余片数口径

按当前粒度，含本片还有10项基础UI规划：B2工作区、B3自动标题、U1回复块、U2结构化diff、U6模型/上下文位置、U7状态与重连、U8日期分组、U9子入口可见性、U10审批按钮、U11主题。本片完成后剩9项；这是规划口径，不把尚未讨论的取舍视为已批准实施。重连/主题等复杂项若需进一步拆分，届时明确调整。U12编辑重发/重新生成及U13思考展示是另2项独立扩展，各自另开Spec，不混入基础UI片数。


## 25. B2实际实现与验收

实际链：WebSettings.workspace_root→GET /api/service（原authorize依赖）→WebService.service_info返回路径及Path.name/anchor→WebApi.serviceInfo传AbortSignal→App内WorkspaceLabel按API对象/重试revision保存当前读取结果→页头。组件有会话时优先用session.workspace_root（包括null），无会话才用service数据；selected路径只格式化末段，POSIX根、Windows盘符根及UNC共享根保留完整根表示。原models/sessions启动链和运行准入没有接入此结果。

新对象仅ServiceInfo界面DTO及42行WorkspaceLabel组件。服务方法无应用/会话/模型访问，测试将service.application临时替换成无能力object仍可读取真实绑定；没有新增应用/运行层对象或存储字段。App旧的未设置回退分支已替换，原小屏隐藏workspacePath规则已删除；当前子入口规则保持。错误输出只给固定提示，console.warn仅记录workspace_info_read_failed，不带路径/响应或异常正文；正常取消和迟到失败不记录为新故障。

TDD：后端8项先全部失败（404/缺少service_info），前端14项先全部失败（缺少显示来源/状态/AbortSignal），实现后22项通过。再增加UNC根/目录及服务读取失败后切入已知会话3项，前端共新增17项。旧App测试8个文件10处现有客户端fixture明确补serviceInfo返回，避免正常测试产生未模拟的新网络故障；不修改公共测试基础设施、不放宽原断言。新故障测试显式检查固定日志且成功/过期响应无意外日志。首次build发现新测试误用了Playwright的exact选项，移除该不受Testing Library支持的选项后构建通过；字符串名称仍为默认精确匹配。

| 验证命令/检查 | 实际结果 |
|---|---|
| frontend目录 `npm test -- --run` | 31文件251 passed，3.47s；`/tmp/naiyue-103-b2-frontend-final.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite513模块成功；`/tmp/naiyue-103-b2-build-final.log`。JS gzip204.72 kB，比U4增加0.34 kB；原高亮引入的Vite体积提示仍明确保留，没有构建错误 |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2825 passed in120.25s；`/tmp/naiyue-103-b2-backend.log` |
| 范围/结构 | 无新依赖或配置/DB迁移；package及lock相对U4逐字节不变；本片源/测试均低于500行，diff/pip check通过。PureWindowsPath和前端盘符/UNC只是跨平台格式验证，不冒充Windows原机验收 |

临时真实Web/SQLite使用存在的长中文工作区及空会话，模型调用0次。1440px首页未选会话即显示目录名，762px长文本在260px内省略；375px可视222px，显示flex，页面宽375。title保存完整真实路径；选已有会话后相同目录归属正确。通过测试服务单次503注入，375px出现失败/重试，保留草稿且发送按钮可用；点击重试后目录恢复，草稿逐字不变。该次故障只产生预期workspace_info_read_failed警告，无其他warn/error；不能把有意注入故障描述成控制台始终为空。

截图：[桌面页头](/tmp/naiyue-103-b2-header.png)、[完整桌面首页](/tmp/naiyue-103-b2-home-desktop.png)、[375px首页](/tmp/naiyue-103-b2-home-mobile.png)、[375px失败](/tmp/naiyue-103-b2-failure-mobile.png)、[375px选会话](/tmp/naiyue-103-b2-selected-mobile.png)；有限指标`/tmp/naiyue-103-b2-browser-metrics.json`。临时页/服务/SQLite与脚本收尾、viewport复位，截图/日志保留；真实配置/用户库未变，0次供应商请求。

主代理按获批Plan Execution核对Web→API→App实际接线、全局认证/Host/Origin、冷读取、错误不影响执行、取消/会话优先及CSS范围，无确认阻塞。没有额外派生审查代理，原GFM/U3/U4未提交成果保留，本片未提交push。剩余片数按第24节为9项基础UI规划，另2项独立扩展；唯一下一步讨论B3自动标题的来源/写入时机/手工名称优先及失败边界，未实现命名。
