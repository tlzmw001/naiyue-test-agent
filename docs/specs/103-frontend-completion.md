# Spec103：剩余前端分片连续实施

日期：2026-10-18。初始授权为纯前端分片连续实施，保留GFM/U3/U4/B2成果，不调用真实供应商、不修改真实配置/用户库；实现时未提交。用户随后明确授权将当前全部成果与Spec110分别提交并一起push，提交结果以Git记录为准。

## 1. 当前数据边界与实施范围

| 项目 | 当前事实 | 本轮范围 |
|---|---|---|
| B3自动标题 | 需要后端有条件更新title | 留待后端方案，不用localStorage伪装持久会话标题 |
| U1回复块 | MessageRow有消息/工具父ID，但没有稳定用户请求归属；steering、审批跨run和分页不能按邻接猜测 | 保留现有消息边界，记录所需后端事实 |
| U2差异 | Edit正文已有firstChangedLine及有界unified diff；Web未投影artifact；Write无原始前后diff | 展示已存在Edit文本的修改行及增删行，保留原始结果/截断提示；不伪造Write或patch数据 |
| U6模型/上下文 | ModelMenu已接完整选择/保存；上下文圆环有真实估算 | 同一ModelMenu移到页头左侧，/model共用；上下文保留发送旁，接近实际压缩阈值时提示 |
| U7状态与重连 | 当前两次立即订阅与一次prepare；完成态常驻 | 正常完成清除常驻横条；有界退避重同步和重订阅、耗尽手动重试，保留失败/停止/审批/未确认 |
| U8日期 | 普通组已有手动排序合同 | 增加日期视图选择，默认手动顺序；日期视图显式只读排序，切回手动可拖动，不暗改持久顺序 |
| U9子入口 | 活动实例在run.agents，历史实例在既有GET subagents | 统一列表读取/合并后供页头与侧栏使用，无实例隐藏入口；失败提供明确重试，不把未知当空 |
| U10审批 | 单项/多项都是原整批接口 | 三种选择：允许、本会话允许、拒绝；单项直接提交整批，多项收齐后提交；不改变执行时机 |
| U11主题 | 现有CSS Modules/全局灰度颜色与各状态颜色 | 语义颜色变量、浅/深/跟随系统，浏览器保存选择；根子、Markdown/代码、工具、审批、浮层与焦点一起覆盖 |

U12编辑重发/重新生成、U13供应商思考展示继续各自独立Spec，不以可见按钮代替后端执行/恢复合同。

## 2. 调用链与职责

### Edit现有结果

ToolCall.result.content→ToolResult识别内置edit成功格式→首个修改行、原有unified diff逐行显示。未知工具/错误/格式不完整回退原pre；原始结果可核对，截断仍沿row标记和原edit通知，不读文件重建差异。data-message-id仍跟随结果容器，保持分页/展开滚动锚点。

### 模型和上下文

App持有模型菜单开关→同一ModelMenu→原chat.selectModel/selectThinking→原API。Composer只发openModel动作，删除旧菜单渲染和不再消费的模型props；/model也调用同一动作，不保留两个菜单。上下文告警使用estimate的实际trigger_threshold_tokens，不使用供应商累计用量冒充当前占用。

### 重连

原watch仍拥有本次AbortController和选中会话门禁；抽出有实际消费者的订阅重试算法，显式传api/run/session/初始view、显示/刷新和合法性检查。前端重连配置默认等待250/1000/3000ms，仅用于GET快照、prepare及SSE，不重放run/approval/control POST。等待可取消；每次恢复先按原prepare/history.synchronize补偿，再读run快照、按last_sequence重订阅。完成或中断即收尾，不为终态重启执行；耗尽保留已有消息/草稿并显示手动重试，错误身份/会话门禁保持。

正常：SSE短断→显示重连次数→等待→prepare补齐→run已终态则停止，否则从已确认sequence重订阅。失败：连续读取/订阅失败→有限次数后停止，固定提示与诊断；切会话/卸载取消等待及订阅，旧响应不得写入新会话。

### 日期与主题偏好

日期视图只依据服务已有updated_at，按本地日历划分今天/昨天/近7天/更早，未知日期单独列；置顶独立。手动视图保持服务rows原顺序与原reorder合同；日期视图不发reorder，提示切回手动排序。显式浏览器偏好保存视图选择，不改模型/权限配置。

主题由App持有浏览器偏好，映射documentElement.dataset.theme；CSS原生color-scheme和light-dark负责跟随系统，手动模式固定color-scheme，不增加JS媒体事件或测试专用生产回退。light-dark为2024起浏览器基线能力，本机实际浏览器还需验证支持与计算颜色；本轮不声明旧版浏览器兼容。读取/保存偏好失败记录固定诊断并以当前内存选择继续；不阻断业务。CSS沿现有结构用语义变量，不加新依赖/字体或通用样式框架。

### 子实例与审批

useSubagentList读取当前session的已保存实例，合并同session的实时来源；App据此显示入口，SubagentSidebar/useSubagents消费同一列表，删除重复的列表读取。切会话隔离与迟到结果拒绝保持；列表失败可重试，详情失败仍归详情。

审批三种选择直接生成既有decisions和remember_approved ID列表。单项选中后调用一次原submit；失败仍保留选择并提供重试提交。多项选择只更新本地状态，最后一次整批提交，不让其他工具提前执行；disabled及批次key继续隔离过期/重复操作。

## 3. 文件与顺序

按U2现有Edit、U6、U7、U8、U9、U10、U11顺序实施，各自先补失败测试，最小接入后回归。

- 新增ToolResult组件及局部样式/测试，修改ToolCall接线。
- App/Composer/ModelMenu与既有相关测试：模型开关迁到App，删除旧props和旧渲染；ContextTrigger消费真实阈值。
- 新增runSubscription.ts及显式前端重连配置，useConversation保留控制所有者并委托订阅；新增事件/取消/退避测试，App显示连接状态。
- Sidebar及其模块/测试新增日期视图；仅在必要边界增加日期分组纯函数。
- 新增useSubagentList，useSubagents保留选中详情/历史职责，App与SubagentSidebar传递列表；现有App fixture补真实新读取的空列表。
- ApprovalPanel及局部样式/原审批测试迁移，保留原协议/ID/提交次数断言。
- 新增主题选择组件/偏好hook/主题变量文件，替换现有CSS颜色消费者；主题测试覆盖三种模式、保存/读取失败及卸载恢复；系统配色由CSS原生规则响应。
- 本文记录结果；103主Spec链接本片，handoff/future-work/当前使用说明同步。单文件不超过500行。

## 4. 验收

每项先失败测试再接入；迁移旧测试保留原行为合同和断言强度，不改公共测试配置/跳过测试。全量前端、TS/Vite、带原生工具PATH后端通过后，真实临时离线Web/SQLite验证1440/375、根子、菜单/审批、长文本、深浅/系统主题；网络故障只对读取注入，确认无执行POST重放。记录实际结果和限制，清理临时页/服务/库，viewport复位。无新依赖，无额外供应商调用。

## 5. 实际实现与验收（2026-10-18）

本批已连续完成U2现有Edit文本差异、U6、U7、U8日期视图、U9、U10、U11。调用链与第二节一致；没有新增依赖、Python生产修改、配置或数据库字段。此前GFM/U3/U4/B2工作区成果保留；同工作区Spec110的并行后端改动不属于本批，也未修改或回滚。

- Edit：实际消费工具已返回的firstChangedLine及有界diff，增删行分色，原始结果可展开；未知工具、Write、错误结果仍是原文本，历史预览及工具截断告知保留。真实文件第二行替换为`new <tag> value`，页面没有tag元素；5条diff记录各占一行，原文逐字节保留。
- 模型：页头与`/model`打开同一菜单，删除Composer原模型props/渲染路径；上下文仍在发送旁，只用真实估算阈值标记告警，不发新模型请求。
- 重连：250/1000/3000ms有界退避，AbortSignal/身份门禁及终态确认接线；旧即时重试分支删除。故障注入两次SSE GET 503后恢复，草稿保持，既有prepare/history补偿执行；审批和run POST未重放。主动取消旧订阅触发一条既有`stream_reader_detach_failed`清理诊断，原结果/异常合同未改，未声称控制台零警告。
- 日期：默认/手动顺序及持久拖动合同保留，选择日期视图才分组，日期视图禁止发reorder；本地保存、跨午夜分组、选择及IO失败反馈均有确定性测试。真实页面验证今天分组及切回手动；其他日历分组由固定时钟测试验证。
- 子实例：App和侧栏复用useSubagentList一次读取，空列表隐藏入口，读取失败有重试；实时/历史实例合并，不保留两条列表加载链。真实冷历史重开可找回实例，根/子消息仍独立。
- 审批：三按钮生成原decisions/remember_approved。两项必须收齐再整批提交；单项直接提交整批，挂起提交防重复，失败保留选择及显式重试。真实根批允许/拒绝后恢复，子批单击允许后恢复，均在主会话处理。
- 主题：16份CSS消费者及上下文图颜色使用集中语义变量；根/子、代码、工具diff、审批、菜单/侧栏与焦点覆盖。1440/375页面宽与视口一致，375子代码332px可视/905px内容，在块内滚动。深色根背景rgb(27,29,32)/文字rgb(232,233,236)，浅色rgb(255,255,255)/rgb(24,24,24)；刷新保持深色。跟随系统在当前浅色系统下解析为浅色，CSS.supports(light-dark)为true；未切换用户OS偏好，不冒充多OS原机验收。

TDD记录：新增Edit、模型位置、阈值、日期/实例入口、审批及主题行为均先见预期红灯再实现；重连首轮为缺少模块导入的红灯，随后验证完整行为，不能算5项行为先红。审批另补2项挂起/拒绝回调覆盖。本批新增29项，旧断言按当前交互迁移，保留身份/顺序/原文/权限/调用次数合同。原工作目录测试的fetch默认返回服务信息，新增列表请求误取该对象；已明确mock空列表，不改生产来容忍无效API形状、不放宽日志断言，也未修改conftest/Vitest配置。颜色批量替换中曾误改伪类空格，构建先检出；从本批开始快照恢复选择器并重建通过。

| 验证命令/证据 | 实际结果 |
|---|---|
| `cd frontend && npm test -- --run` | 35文件280 passed，最终复跑3.82s；`/tmp/naiyue-103-front-final.log`（前轮完整日志为`/tmp/naiyue-103-front-full.log`） |
| `cd frontend && npm run build` | TypeScript及Vite523模块成功，142ms；JS gzip207.40 kB；原大块体积提示保留，未升级依赖或假装消除警告；`/tmp/naiyue-103-front-build.log` |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 本批测试时快照2825 passed in125.89s；`/tmp/naiyue-103-back-full.log`。随后Spec110并行迁移须以其自身回归为准 |
| 真Web/SQLite/原生异步图/Read/Edit/Subagent | 6次固定离线模型调用、0次真实供应商；SSE GET共5次、注入失败2次；run POST恰好1次、approval POST恰好2次，剩余模型回复0；`/tmp/naiyue-103-front-browser.log`的OFFLINE_RESULT |
| 结构/范围 | 90份前端TS/TSX/CSS均≤500行，package与lock相对本批开始逐字节不变，diff检查通过；原后端/B2与并行110成果保留 |

真实页面截图：[深色桌面](/tmp/naiyue-103-front-dark-desktop.png)、[浅色桌面](/tmp/naiyue-103-front-light-desktop.png)、[深色批量审批](/tmp/naiyue-103-front-approval-dark.png)、[375审批](/tmp/naiyue-103-front-approval-mobile.png)、[375子消息](/tmp/naiyue-103-front-child-mobile.png)、[375模型菜单](/tmp/naiyue-103-front-model-mobile.png)。临时标签关闭、viewport复位、服务优雅退出及临时工作区/SQLite删除；保留日志/截图。未提交push、未改真实配置/用户库；助手验证不新增用户掌握声明。

## 6. 剩余边界

本批纯前端项已做完，不再逐片请求确认。Spec103整体仍有B3自动标题、U1稳定用户请求归属、完整Write/Edit artifact/patch投影三项依赖后端事实的基础范围；U12编辑重发/重新生成、U13供应商可展示思考为两项独立扩展。都不能以本地假标题、邻接猜分组或伪造diff替代；下一前端开发批没有待确认的同类项，接下来先讲B3后端方案。Spec110是另一个已授权并行工作，不在本批变动中追加实施；checkpoint修剪、两种重试、原机/供应商/PyPI边界保持。
