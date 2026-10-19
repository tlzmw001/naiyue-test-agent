# Web 工具与审批：入口、数据流和验证

当前使用入口已按[Spec086](specs/086-subagent-foreground.md)、[Spec094](specs/094-composer-tools-and-permissions.md)、[Spec095](specs/095-subagent-sidebar.md)、[Spec096](specs/096-context-inspector.md)、[Spec097](specs/097-composer-commands-model-thinking.md)、[Spec098](specs/098-skills-picker-and-explicit-command.md)及[Spec099](specs/099-session-sidebar-management.md)更新；下方“本次验证”为Spec078的历史证据，最新验证见[交接](handoff.md)。界面沿用浅色聊天区和CSS Modules。

## 使用

在项目根目录启动，指定你准备供 Agent 操作的工作区：

```bash
.venv/bin/python -m naiyue_test_agent.web --config .env/models.json --workspace /绝对工作区路径
```

统一登记文件、搜索、双Shell及subagent定义，工作区新会话默认启用 read/ls/find/grep/write/edit/subagent。ls/find只获取名称清单，工作区内的隐藏目录也默认Allow，获准目录内包含隐藏/私有名称；列出名称不授予读取或修改正文的权限。工作区外目标仍经审批，显式Deny、符号链接真实目标及宿主权限继续检查，find保留原生ignore/glob规则。read/grep/write/edit的普通非隐藏工作区目标默认Allow，外部/隐藏/私密目标可审批；grep正文搜索仍跳过未授权隐藏/私密目标，Deny不因批准目录被覆盖。CLI/Web未传工作区默认启动cwd（Spec087）；程序化无工作区会话仍可用。名称权限当前合同见[Spec115](specs/115-name-listing-permissions.md)。

最新已保存的助手正文若只交付预览，页面自动分片补全；较早长消息和工具结果可点击“加载完整内容”。完整流式文字优先保留，不在运行结束时退回短预览；读取失败保留当前文字并可重试。此操作不重新执行模型/工具，不改变模型上下文或压缩记录，见[Spec114](specs/114-complete-message-content.md)。

左侧会话右键或点击省略号，打开“重命名、置顶／取消置顶、删除”。排序直接用鼠标拖动会话，置顶和普通组内分别排序；出现插入线后松开保存，拖动不会打开会话，跨组不改变置顶属性。没有上移/下移入口。名称、置顶和顺序存入会话SQLite，刷新/服务重启后保留，新会话在普通组首位。删除需确认，执行/恢复中的会话拒绝删除；成功清理该会话及关联子历史，工作区文件保留。失败保留列表并提示，不自动重试删除。触屏可用省略号菜单，本片不拦截滚动做触屏排序。

运行交付缓存有保留上限；旧运行淘汰时同步清理该运行的控制回执，过期控制请求返回run_not_found，不再返回旧停止回执的待发送文本。会话级启动/审批回执仍保留防重复执行，删除会话时一起撤销。

输入框内部左下角显示当前权限模式，点击向上打开Default/NoAsk/Auto/BypassApprovals；Auto未配置时显示不可用原因。输入框获得焦点时Shift+Tab轮换可用模式，其他控件保留正常焦点导航。模式选择保存到当前会话，下一批工具调用生效，已有审批仍须单独处理。

模式左侧加号列“压缩上下文 /compact”和“技能 /skills”，不重复已有按钮的功能。输入`/compact`并发送或点击该菜单调用同一压缩操作，不把指令当用户任务发给模型。已有会话空闲、无待审批/恢复/人工核对时可压缩；只调用摘要并保存，保留最近完整用户轮，不自动运行普通模型/工具。历史太少会跳过，摘要失败保留原文；替换/保存失败保持阻断。压缩期间显示状态且可停止，草稿保留，普通补充消息暂不排队。文字指令`/context`、`/model`、`/tools`、`/permissions`、`/agents`也可打开各自入口，首版不接受参数。

输入`/skills`并发送，或加号→技能，打开相同的可滚动技能列表；支持刷新、空态与失败重试。选择只将技能指令放到输入框前面并保留任务文字，不自动发送或创建会话。当前范围为工作区`.agents/skills`与`.claude/skills`，遵守已有发现限制，不扫描用户级/共享目录。agents普通名称使用`/name`，claude始终`/claude:name`，与内建指令重名的agents技能用`/agents:name`。Read未启用时只读查看，按提示先启用工具；不自动授权。发送时重新确认技能仍存在，正文仍由模型经原Read读取。列表代表可发现，不代表已读取或已使用；运行中新增技能在下一轮索引生效。

上下文与发送键之间的文字按钮显示当前模型名/推理强度，点击打开设置；不显示指针图标或展开箭头，顶部不重复选择模型。可用模型和推理档位来自启动模型配置，配置默认可解析时直接显示对应档位；未配置thinking时只显示配置默认并说明原因，不按模型名字猜能力。强度与模型按当前会话保存，运行中的请求不改变，下一请求使用新选择；切模型重置为其配置默认，子角色和Auto审查模型不继承根会话覆盖。保存失败保留已确认值并只读核对，不自动重发写请求。SQLite当前schema6自动迁移现行4/5，包含thinking_level和呈现字段，不改用户配置文件；旧全局库1/2/3仍不接受。

旁边“工具”显示当前启用数量，可勾选/取消已注册工具，下一次主模型请求生效，不中止当前调用、不改子Agent角色工具集合，也不代替权限授权。首次发送前即可选择，会创建空会话并保留草稿，不调用模型。保存失败显示未确认反馈并读取实际选择，不自动重复写请求。

运行中输入框右下角可选择“引导本轮”或“结束后跟进”，默认仍为前者；它们继续使用原steering/follow_up控制，停止和待重新发送语义保持。

发送键左侧的圆环/占用比例打开“上下文”侧栏，与子智能体侧栏切换使用。“当前预估”读取实际模型上下文，展示系统说明（项目文件、Skill索引、工具指导等）、工具定义和消息分项；“最近请求”按同一个调用ID展示发送前估算和供应商返回的输入/输出/缓存用量。缓存属于输入子集，缺失显示“未提供”，真实0保留。窗口及预留标明配置/默认来源，图片仅粗估；草稿/排队内容和子智能体内部历史不算主会话输入。运行中采用最近已发送的固定快照；待审批或未完成的checkpoint不显示为下一请求预估。查看不调用模型/摘要，不恢复工具。成功请求统计随官方checkpoint保存，历史没有记录的构成不补造。



顶部“子智能体”打开名称列表，同名实例按短标识和任务区分；选择后详情不重复显示名称，复用主会话的分割线、Markdown和可展开工具卡片。消息与进展同流，支持只停止选中的活跃子智能体。待审批只显示状态和“到主会话处理”，所有审批仍在主会话；出现审批不自动展开侧栏。375px侧栏覆盖聊天，返回审批时关闭侧栏。

两个只读接口 `GET /api/sessions/{id}/subagents`、`GET /api/sessions/{id}/subagents/{agent_id}` 沿原认证和工作区归属检查，由真实父调用派生身份后读取官方子checkpoint，不接受任意thread_id、不执行恢复或模型调用。根快照只保留子摘要及有预算的当前草稿，完整记录独立读取，详情超限不阻断主审批。列表受当前父checkpoint可关联范围限制，压缩前被移除的委派未另建永久索引；瞬时进度不跨服务重启。

Windows文件工具的提交/归档与路径比较已按[Spec088](specs/088-windows-file-commit-and-archives.md)/[Spec089](specs/089-windows-path-permission-identity.md)接入：合法普通/扩展盘符和UNC目标用同一比较语义，Deny仍优先，递归批准不绕过子目标过滤。归档不要求私密模式位，但标记/类型/链接和显式清理快照仍检查；已有文件替换保留系统访问设置，不自动清只读/提权。当前为本机IO与平台分支证据，Windows原机验收另做，页面/审批API未新增字段。

Bash/PowerShell可从输入框“工具”启用，后端仍复用`aset_active_tools(session_id, names)`。命令按显式规则/当前模式审批，默认策略Ask，仍是宿主机执行。双Shell环境/合流/归档见[085-14](specs/085-14-dual-shell.md)，共用进程runner/原生async及清理保护沿085-12；[085-13](specs/085-13-search-tools.md)已将Grep接原生rg分组搜索、Find接fd（Linux也识别fdfind），均有流式预算、权限过滤和不完整反馈；程序须在服务PATH中，不自动下载/安装。

090C已统一Bash/PowerShell/Grep/Find的timeout参数：省略/null/0不设执行期限，正整数指定本次秒数，不设300秒上限；Grep发现和各分组共用一次期限。用户仍可通过现有停止按钮结束协作工具，必要进程清理和模型网络期限保留，输出/归档预算不变。

页面默认折叠工具详情，显示工具名及处理中/成功/错误结果/未执行/待核对等状态。展开能看到模型实际提供的参数和对应 ToolMessage 正文；工具结果用转义文本显示，不当作助手回答或 HTML 执行。缺少历史执行来源时显示“已有结果”，不借用其他运行的状态。

[Spec091](specs/091-tool-progress.md)已接真实运行进度：折叠卡片显示排队/准备/运行/停止/收尾、具体阶段、自dispatch起的历时和距最后活动时间；无新活动仅更新时间，不自动判断卡死。展开显示实际计数和最新有界输出尾部，未知总量不显示百分比，尾部替换而非追加历史。工具不提供进度时仍有dispatch历时，已终局不会被迟到进度改回运行中。

原SSE缓冲溢出后走完整run快照，保留每call最新进度；同run后续批次复用call_id也只绑定对应AI行。接受停止后显示“停止中”，清理和保存未结束时按钮保持等待；真实finished/ToolMessage决定成功、错误或未执行，缺终局显示未确认。进度仅驻当前服务内存，不进入模型、checkpoint或新的数据库表，服务重启后的恢复仍由092另做。

审批区展示实际待审调用及后端规范化目标。每项默认无决定，全部选择批准或拒绝后“提交审批并继续”才可用。批准仅对该调用/目标一次生效；不提供前端修改参数/目标或授予永久权限的入口。HTTP失败保留选择，不自动重试；拒绝经原 HITL 返回错误 ToolMessage 给模型。

服务重启后的会话按[Spec092](specs/092-session-restart-recovery.md#0-2026-10-17确认打开会话自动恢复)打开时自动恢复：执行中断保留真实结果、补未确认说明、结束旧任务；确认是前置审批则重新核对当前本地权限和目标，直接恢复原审批卡片，批准前整批零执行。GET不写checkpoint，自动恢复使用原POST和准入锁；原待审复核失败不自动continue取消，保留历史/草稿与手动重试或明确放弃入口。恢复不调用模型/工具/Auto，不复活旧临时授权、进度/SSE或草稿发送。完整归档、分支和草稿持久化另做。

## 数据流与接口

当前管理入口：`GET /api/session-controls`仅返回工具名称/描述、默认选择与可用模式；
`PATCH /api/sessions/{id}/tools`接收`{"active_tools":["read","ls"]}`；
`PATCH /api/sessions/{id}/permission-mode`接收`{"permission_mode":"NoAsk"}`。
沿用Bearer/Host/Origin及工作区归属检查，只读恢复态拒绝修改，非法工具/模式422，保存确认后才返回。
菜单可刷新可信代码新登记的定义，不支持上传Python代码。

```text
启动 workspace
  → Web lifespan 登记工具与角色，工作区决定新会话初始活动集合
  → ToolSet固定本次集合/提示词/预算 → 按本批生成局部官方HITL
用户提示 → 既有 arun → 模型 AIMessage.tool_calls
  → message_completed snapshot → Web/React 工具详情
  → 工具开始/结束事件 → 仅临时状态投影
  → ToolMessage → 按所属 AI 消息＋调用 ID 配对正文

需要审批 → run_finished(interrupted) / 当前 pending 批次
  → GET session 返回 approval_batches（含来源与审批项ID）
  → 页面逐项决定 → POST approvals
  → 先查幂等回执，再在准入锁内重新读取批次并校验来源/ID/顺序
  → aresume_tool_approval → 原一次性 grant / Command.resume / 原 checkpoint
  → 新 run_id → 工具真实执行或拒绝回填 → 模型续跑 → 保存后终局
```

审批请求：

```json
{
  "request_id": "浏览器生成的 UUID",
  "origin_run_id": "本批次原运行 ID",
  "approval_batch_id": "当前真实审批批次 ID",
  "decisions": [
    {"approval_id": "第一个真实审批项 ID", "decision": "approve"},
    {"approval_id": "第二个真实审批项 ID", "decision": "reject"}
  ]
}
```

`POST /api/sessions/{id}/approvals`根批次返回202及新run_id，活动子批次沿当前根run投递，不新建根运行；JSON中的占位文字只说明格式，不是可直接提交的示例ID。严格schema拒绝额外字段/非法决定，目标、授权值不能由浏览器回传替换。过期原run、ID/顺序/数量不匹配409；重复相同request_id和正文优先返回原回执，正文冲突409。所有API沿原token/Host/Origin检查。202不是执行/保存成功，最终仍消费领域终局。

## 代码地图

| 位置 | 改动 |
|---|---|
| web/app.py、settings.py、__main__.py | 统一登记/初始活动选择/审批路由 |
| application/session_service.describe_session | 派生 approval_run_id及active_tools；schema v2保存活动名称 |
| web/service.py、schemas.py | 公共待审列表、严格请求、锁内当前批次校验 |
| web/run_delivery.py | 复用已有独立生产任务和回执；调用异步审批恢复；工具事件投影 |
| web/serialization.py | 保留 AI tool_calls、ToolMessage 调用ID/name/status |
| frontend types/events/api/useConversation | 字段接线、工具状态、审批动作、新run订阅和终局刷新 |
| frontend components/ToolCall、ApprovalPanel、Conversation | 可折叠参数/结果、逐项选择、所属消息内配对和去重展示 |

Pi a69bef789 interactive-mode 的 start/end 根据toolCallId建立/更新ToolExecutionComponent；AgentX3fcfa0e BuiltInToolRegistry根据provider.supports提供specification/executor。这些是已核对的职责参照，不称Pi拥有我们的HITL协议。循环、权限、串并行和工具错误机制仍由原框架/项目模块负责。

## 本次验证

- Python全量 `.venv/bin/python -m pytest -q` → **927 passed in 33.58s**，本片新增9例。
- 前端 `npm --prefix frontend test -- --run` → **9个测试文件、25个测试通过**，本片新增9例；tsc+Vite构建成功，186modules，JS363.27kB/gzip113.80kB、CSS10.50kB。
- compileall、pip check、git diff --check通过；源文件均不超过500行，无.env/真实模型配置/原依赖/关键知识点变更。
- 红灯包含工具尚未注册/DTO丢字段、工具事件/审批组件缺失、原型键被误认已选、跨轮复用调用ID导致旧结果错配；修对应源代码后通过。混合审批测试起初错误假定ToolMessage列表按申请顺序返回，核对框架可先写拒绝结果后，改为精确断言全部ID及对应正文/状态，没有更改框架顺序或放宽成功条件。其他追加验收不冒称逐项TDD。
- 真实文件/工具经HTTP验证六工具同批次write→edit→read/ls/find/grep的结果；Bash显式开启、命令审批后执行合成printf；read不存在路径错误回到模型后运行完成；工作区外批准/拒绝、非法/过期/重复请求、重启审批复核/恢复、条件变化拒绝均覆盖（当前092C覆盖取代最初阻断合同）。

离线TCP/两个独立进程：

```bash
.venv/bin/python -m scripts.probe_web_tools --timeout-seconds 60
```

输出：

```text
web_tools_probe=PASS mode=offline tcp=True approval=correlated mixed_decisions=True resume_run=new duplicate_safe=True tool_results=paired two_processes=True history_used=True
```

临时真实SQLite、原生异步假模型、真实read/ls工具、HTTP/SSE；恢复后工具ID和结果确实进入模型历史。该脚本复用原Web探针的进程/截止/关闭机制，原探针默认行为保留。

浏览器用同一生产UI和离线fixture服务，真实审批第一条批准、第二条拒绝，显示成功/未执行及后续回答；工具正文来自实际文件读取/拒绝ToolMessage。1280px和375px无横向溢出、控制台未观察error/warn；窄屏可滚动审批区选择并提交，刷新后结果仍正确配对。截图在本机本次Web工具验证目录。没有新增收费模型调用，不能将这些称为真实供应商工具决策质量或生产业务效果。

下一步先试用这一闭环，再沿入口→装配→消息/工具→审批→恢复→保存/显示进行系统学习。助手完成代码不等于用户已独立实现或能应对追问。
