# 最小本地 Web 对话：使用、代码入口与证据

## 调用观测与只读查询

0.2.0 已接[Spec119](specs/119-call-observability.md#17-119-03实际接线与验收2026-10-20)的CLI/Web展示。当前源码的右侧由[Spec125-06](specs/125-06-background-acceptance.md)统一为顶部图标打开的“子智能体／后台任务／上下文”页签；选择会话后，输入区的上下文入口仍直接跳到同一侧栏的上下文页签：

- **会话累计**：模型调用次数、token及已记录模型调用耗时，沿用途、根子来源和模型查看。耗时合计只覆盖有耗时记录的调用，并发调用耗时之和不等于整次运行经过时间。
- **运行诊断**：最近运行列表→某次运行的摘要与事件明细。显示run/工具耗时、HTTP尝试、响应头状态与耗时、未确认及主子来源。列表和明细均分页，整run摘要不随事件翻页重算。

本地查询不调用模型、不重跑工具；打开时读取，手动刷新或本会话新run终态后再读取，不持续轮询。历史缺字段显示未提供；HTTP采集不可用不显示零，采集不完整标明已记录数量。诊断随既有容量/保留期清理，记录不可用不能解释为没有发生调用。收到200响应头后仍可能流式失败，模型结果单独展示。读取失败可在本区域刷新，原任务继续。

[Spec126-03](specs/126-03-subagent-discovery-delivery.md)已接子实例查找与多次委派呈现：用户只启用subagent，主Agent按需得到配套只读subagent_list；选择不保存独立list，实际工具调用继续展示。侧栏同一ID保留多次报告，候选提示与当前执行分开；新委派清当前草稿，审批恢复保留，停止携带所观察到的子执行/审批身份。候选提示不等于执行许可，继续仍由主Agent按ID调用；无用户直聊。

[Spec126-04](specs/126-04-subagent-acceptance.md)已用同一固定模型/真文件/SQLite场景覆盖源码内与标准安装包的三次同ID委派、正常重开零重放和独立调用限额；真实浏览器补齐两次审批、重开历史、当前审批停止与375px表现。耗时原始样本与跨平台证据边界见该记录。

CLI在`nyt chat`当前会话中使用：

| 命令 | 查询 |
|---|---|
| `:usage` | 累计模型用量与耗时 |
| `:usage --after-model <model_id>` | 下一页模型分组 |
| `:diagnostics` | 最近运行 |
| `:diagnostics --before <cursor>` | 更早一页运行 |
| `:diagnostics <run_id>` | 指定运行摘要与第一页事件 |
| `:diagnostics <run_id> --after <sequence>` | 下一页事件 |

下一页命令由输出给出，可直接复制；未知和0分别显示。`--no-session`明确查询不可用。以上只读入口沿用应用原查询/身份作用域，HTTP尝试沿有限run诊断保存，不作为永久会话总账。原运行/审批/停止/保存和SDK重试策略保持。

以下为既有Web实现与历史阶段记录。

2026-10-20请求准备与上下文查看：当前源码的根/子Agent每次模型请求都检查上下文压缩条件。模型、思考档位、工具定义和系统提示词从本次准备固定到摘要、复核及主发送；准备期间修改设置在下一模型请求生效，同run后续请求也适用。工具实际执行仍检查权限、MCP停用/定义身份和原审批批次。

上下文面板的总量与构成明细分别记录可用性。总量可用而明细不可用时，总量继续显示，明细留空；未知估算不补零，供应商实际用量仍按原记录展示。输入草稿和未选入当次请求的队列不计入当次发送。软阈值用于主动压缩，发送容量扣除模型输出预留；普通首次估算未知会记录本地预算未验证并允许发送，已知超容量、明确overflow恢复失败及必要输入/配置错误保持失败。明确overflow只重试当前模型请求一次，不重放此前工具，也不代表整个任务具有次数或时间硬预算。实现与验收见[Spec118](specs/118-request-preparation-and-context-budget.md)；下方旧切片测试数字和阶段边界用于追溯。

2026-10-18当前：[Spec103纯前端连续分片](specs/103-frontend-completion.md#5-实际实现与验收2026-10-18)已完成现有Edit差异、模型页头与阈值提示、有界重连、可选日期视图、共享子实例入口、三按钮审批和浅/深/系统主题；此前GFM/流式Markdown/代码块/工作区短标签保持。前端35文件280项、TS/Vite523模块及本批后端快照2825项通过，真实1440/375离线页面验收通过，0次供应商请求。B3自动标题、U1请求归属和完整Write/Edit投影仍需后端方案，U12/U13独立；Spec110并行清理的验收另列，checkpoint修剪/两种重试/原机/PyPI范围保持。

工作区标签：页头从受认证的只读`GET /api/service`读取服务绑定，未选会话也显示目录短名，完整路径保留在悬停提示；选会话后按该会话归属显示，未绑定与读取失败分开。失败可重试，重试保留草稿且不阻止原运行准入；不会切换实际工作目录。新增后端路由随服务重启生效。

2026-10-18：[104-03](specs/104-03-concurrency-acceptance.md#6-实际结果与104收尾)已完成真实图/SQLite组合、离线限流隔离及双进程TCP验收，104三片和106 R1离线部分收尾；后端2524、前端189及构建通过。子模型具体ToolException现保留同run分类，根异常/父工具失败/停止及其他会话合同保持。下一步讨论[M6缓存控制草案](specs/107-model-request-prompt-cache.md)，尚未实施或授权实网；持久用量/诊断随后，checkpoint修剪/两种项目重试延后。真实TCP新探针为scripts.probe_web_concurrency：两会话delta、断连继续、定向控制、根/子审批、幂等及第2进程只读档案，固定7次离线模型/1次Write提交/1次Read，0次供应商请求。

聊天基础对应 [Spec077](specs/077-local-web-chat.md)，2026-10-09 已接线验证。前端参考用户确认的 ChatKit 浅色聊天外观；Agent 循环仍由现有 LangChain `create_agent` 执行。Spec077首片注册 `tools=[]`；随后 [Spec078](specs/078-web-tools-and-approval.md)已接入工作区工具及审批，最新操作见 [Web工具说明](web-tools.md)。下列测试数字和首片边界按Spec077历史时点保留。

2026-10-18：[Spec101-02](specs/101-web-history-pagination.md#14-101-02实施合同本轮授权)已将选择会话改为显式POST prepare，普通GET只查档案和轻量状态；最近窗口、历史游标及正文JSON分块分别交付。101-03已接根/子上滑分页、阅读锚点、重试/起点及SSE缺口合并；旧页与实时消息按ID/revision合并，失败不清空当前内容或草稿，工具跨页归属不折叠正在阅读的正文。超大消息明确预览，完整正文展开界面未增加；101-04继续组合验收。

## 启动与操作

在项目根目录执行。当前开发环境已经安装依赖并完成构建；新环境先按 README 建好 Python 虚拟环境并安装原有 requirements，然后执行：

```bash
.venv/bin/python -m pip install -r requirements-web.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

启动：

```bash
.venv/bin/python -m naiyue_test_agent.web --config .env/models.json
```

打开终端打印的本地访问链接。默认随机可用端口，访问凭据在 URL fragment，页面读取后移除；凭据仅留在当前浏览器 tab 的 sessionStorage，模型 API key 保持在后端。运行阶段只需一个 Python 服务，不需要常驻 Vite/Node 服务。Ctrl-C 请求服务退出并等待应用资源收尾；退出码 130 是用户中断的常规状态，入口不打印 Python 中断堆栈。

可选 `--workspace /绝对路径`、`--session-db /绝对路径/sessions.sqlite`、`--port 端口`、`--frontend-dist /绝对路径/dist`。`--host` 只允许 loopback；host/port 也可用 `NAIYUE_WEB_HOST`、`NAIYUE_WEB_PORT` 设置。未传工作区默认启动cwd。项目内`.naiyue/workspace.json`保存UUID，数据库默认`~/.naiyue-test-agent/data/workspaces/<UUID>/sessions.sqlite`；全局`settings.toml`的`[storage] data_dir`可把数据放其他盘，显式`--session-db`优先。同一库同时只能由一个应用占用，不同工作区可以分别开服务/浏览器窗口。关闭后携标识移动目录会自动重绑历史；目录副本重复ID需`--new-workspace`创建独立身份/空历史。旧全局库不兼容，详见[Spec087](specs/087-workspace-session-storage.md)。无需编辑 `.env`。

- 首条普通消息创建持久会话并启动 run。侧栏“新建会话”只创建空会话；列表显示实际记录与模型，不填充示例历史。
- 模型菜单来自实际配置目录，同一会话可切模型，下一次模型请求生效。切换失败保留先前确认的选项。
- 运行中普通输入作为 Steering；勾选“结束后跟进”作为 Follow-up。HTTP 接受只是排队回执，领域事件说明是否已交付上下文。
- 点停止后输入仍可编辑；停止期间发送只保留本地待发送内容。未执行的 Steering/Follow-up 回传并显示；终局后需用户明确“重新发送”，不会自动开启下一轮。
- 已完成答案按完整消息校准临时增量，可复制正文。未完成的增量保留“未完成内容”标记，不能当作保存成功的答案。
- 浏览器刷新后从侧栏选择会话；服务仍存活且run view尚保留时可找回服务端未执行内容。浏览器草稿/本地新增待发送内容只在内存，刷新会丢失。服务重启后按[Spec092](specs/092-session-restart-recovery.md#0-2026-10-17确认打开会话自动恢复)在打开会话时自动整理状态：执行中断用三次更新保留真实结果、补未确认说明并结束旧任务；前置审批通过本地复核后直接显示审批卡片。不会自动重跑工具、调用模型/Auto或发送草稿。失败保留历史与草稿、重读当前checkpoint，停止自动重试；用户可手动重试或明确结束失效旧批次。GET仍只读，不扫描全部历史，不复活旧进度/SSE或待发送队列。
- 事件断线可点“重新同步”。程序最多一次重读 view 并续接事件，绝不自动重发用户 POST。模型失败、保存未确认或受限会话会明确显示状态。

2026-10-18：101-04已完成压缩、独立进程重开、写入后硬退出与根/子审批恢复边界的组合验收；原始聊天从档案分页读取，模型上下文继续使用压缩后的checkpoint。根前置审批可恢复卡片，子内部审批中断后结束旧委派并保留历史，不复活旧等待任务；具体证据见[Spec101第21节](specs/101-web-history-pagination.md#21-101-04实际结果与spec101收尾2026-10-18)。

## 一条真实调用链

```text
Composer 输入 → useConversation.submit → WebApi.start（request_id）
  → POST runs → schema + token/Host/Origin 检查
  → WebService._session_status 读取轻量权威状态 → RunDelivery 去重/准入
  → 独立 asyncio Task 调用 AsyncApplication.arun
  → 原 SessionService / LangChain / middleware / 模型与 checkpoint
  → 领域订阅（只更新内存 projection/有界缓冲并唤醒）
  → GET events → FastAPI async generator SSE → fetch/TextDecoder
  → 序号检查 / 临时 delta / 完整 snapshot / 保存后的 run_finished
  → React 更新正文和状态
```

控制通过独立 REST 请求调用原 `steer`、`follow_up`、`request_stop`。SSE 消费连接和 Agent 生产任务分别拥有生命周期；断开连接只是停止交付，不取消生产。HTTP 请求等待者取消也不移除已经产生的 run 登记和回执。准入、控制接受、模型返回、消息确认、持久保存与界面显示是不同事实。

浏览历史链：选择会话→POST prepare→useMessageHistory.open安装近期页；接近顶部→loadOlder→GET messages(cursor,agent_id)→SQL有限页→按ID/revision前插→Conversation布局阶段恢复原正文偏移。根与选中子范围共用机制，审批仍由主会话处理。SSE缺口先prepare补录已保存边界，再synchronize补齐近期窗口与已加载末端之间的缺页，不重发执行POST、不把显示页装入模型上下文。

## 代码入口

| 模块 | 职责 |
|---|---|
| `naiyue_test_agent/web/__main__.py` | 参数、真实绑定端口、一次性本地入口、Uvicorn |
| `web/app.py` | 生命周期/HTTP schema/路由/SSE/仅构建目录静态服务 |
| `web/auth.py`、`headers.py` | 显式本地授权/来源/Host、CSP 与禁止缓存 |
| `web/service.py` | 调用现有应用、选择/恢复会话、公开 DTO，不重建循环 |
| `web/run_delivery.py` | 独立生产任务、幂等回执与控制，不生成全历史作为准入 |
| `web/delivered_run.py`、`chat_history.py` | 单run临时投影/回放；历史游标/有界消息wire，均不拥有永久档案 |
| `application/chat_queries.py`、`session_storage.py` | 档案有界SQL；连接、事务和锁仍由SessionStorage持有 |
| `frontend/src/api.ts`、`sse.ts`、`events.ts` | 请求、UTF-8 跨 chunk 解析、事件序号及完整消息校准 |
| `frontend/src/useConversation.ts` | 会话选择、草稿、订阅、控制与确认后的展示 |
| `frontend/src/components/` | Sidebar/Conversation/Composer，纯 UI 展示和用户动作 |

模型目录只读 `model_ids`、应用 `has_session` 和消息投影 DTO `seed`仍有当前消费者；原Web专用 `aget_session_view`全量包装已由轻量 `aget_session_status`及显式 `aprepare_session`/档案查询替代并删除。`aget_messages`继续服务模型当前上下文与CLI/测试，不改成聊天分页。运行层无新的Web依赖。公开视图只提供必要元数据和有界消息，不传SDK对象、供应商地址或密钥。

## 验证结果与边界

本片新增 13 个后端测试和 16 个前端测试。红灯证据包括：缺失产品模块、原生 SSE 端点形态错误、请求等待者取消导致 run 未登记、停止内容未进入 view、会话/模型回执乱序、停止回执覆盖终局提示、完整大帧绕过上限、保存失败仍准入，以及 Ctrl-C 堆栈。模型选择补测的初版断言漏算已有基础 SystemMessage，随后改为精确比较基础提示词加完整历史，未删除产品提示词或放宽判定；其他追加验收测试不冒称逐项 TDD。

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
npm --prefix frontend audit --audit-level=high
git diff --check
```

最终数量和耗时见交接记录；构建执行 TypeScript 检查与 Vite 打包。Web 新增 Python 包独立锁定并受原 requirements 约束，没有升级原运行时依赖；前端版本由 lockfile 固定。jsdom 从初次解析出的不兼容版本改为 Node24.14 可运行的 28.1.0，这属于新 Web 依赖初始化。

可重复的离线真实 TCP/两个进程验收：

```bash
.venv/bin/python -m scripts.probe_web --timeout-seconds 60
```

实际输出：

```text
web_probe=PASS mode=offline tcp=True sse_delta=True disconnect_keeps_run=True stop=True retained=True two_processes=True history_used=True duplicate_safe=True
```

该探针默认纯离线、临时 SQLite、原生异步假模型，没有真实模型 key 或收费请求。第一进程在模型尚未完成时收到文本增量，断开后生产继续，Steering/Follow-up 未执行内容经停止回传；第二个独立进程恢复已确认历史并续聊。ASGITransport 单元测试之外，这是真实 socket 交付证据。

另用真实默认网关 `heiyu-gpt-5.5` 在浏览器中完成两次逻辑调用：第一条合成请求精确返回 `WEB-CHECK-739`，第二条等待期间点击停止，最终 `status=stopped, done=true, busy=false, can_run=true`，未制造第二条助手答案。真实集成仅使用临时库、合成文本；SDK 可能的 HTTP 重试不计作额外逻辑调用，未统计费用，也不证明供应商停止计算/计费。没有再重跑收费探针。

桌面 1280px 与窄屏 375px 实际浏览器检查通过，scrollWidth 等于视口宽度，窄屏导航可打开/选会话后关闭，控制台未观察到 error/warn。截图保存在本机本次验证产物目录；视觉接受仍由用户判断，不将自动检查称为用户认可。

## 当前限制与下一片

- 当前单用户、单 worker、同一工作区不同会话可并行，每会话一个活动根run；默认最多10个根运行，含保存占用，满额拒绝且保留草稿/审批。切换仅取消显示订阅，停止按run路由；不是 SaaS 登录/租户隔离或任意多进程并发方案。
- 首片无 Web 工具、工具卡/审批、附件/图片、模型配置编辑。已有工具仍在原应用/CLI 路径可用。
- 恢复的是当前安全 checkpoint；被压缩的完整历史、分支、跨重启审批/异常恢复尚未做。
- 缓冲默认 256 个事件、view/frame 上限 1MiB、保留最多 16 个完成 run；过期或缺口显式报错。请求回执最多 4096 条，达到上限拒绝新请求；保留旧回执避免淘汰后重复执行，长期持久幂等仓库待后续设计。
- 当前临时草稿和队列不能保证浏览器/进程重启后保留；断线时请先同步状态，未知回执不能假定未执行。
- Web UI/传输通过不代表测试业务质量、生产负载、其他供应商实网停止或工具权限已生产成熟。

Spec077当时的下一步工具/审批已由Spec078完成。当前下一步先实际试用这一闭环，再以真实调用链进入系统代码学习；仍不将助手实现记为本人独立实现或面试追问能力。
