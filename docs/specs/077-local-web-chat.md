# Spec 077：最小本地 Web 对话与控制闭环

状态：2026-10-09，用户已确认自建页面、推荐技术栈及本片实施；已实现并验证，命令与结果见交接第224节及 Web 说明。整体Web里程碑按切片推进，本Spec只覆盖第一片，不把剩余工具/审批/完整历史宣称完成。

## 目标与参照

浏览器完成新会话/已配置模型选择→消息→文本增量→运行中Steering/Follow-up→活动停止→确认终局→再次发送；沿用默认持久化及安全会话恢复。视觉参照用户选择的ChatKit Playground聊天区域，保留白色主体、深色文字、圆角输入框、黑色发送按钮，扩展为会话栏+聊天区。SDK评估及源码证据见[接入评估](../research/chatkit-web-integration-2026-10-09.md)。

推荐React+TypeScript+Vite、FastAPI/Uvicorn、REST+官方SSE响应、普通CSS变量；保留Python/create_agent/现有middleware。CSS只使用这一套策略，不加入Tailwind、组件平台或第二套聊天协议。首次要跨三个新增职责并超过八个文件，按下面三个实现步骤逐步验证，避免一次改运行时。

## 技术选型建议的职责说明

- React拆分会话栏、聊天区、输入框和状态提示；TypeScript约束API字段/领域事件分支。前端状态只包含当前选择、草稿、交付游标与展示投影，后端run事实仍权威。
- Vite负责前端开发/构建；交付时FastAPI提供构建后的静态文件与API，用户运行阶段无需另一个Node服务。使用npm及lockfile，沿用本机Node24.14.0。
- React内置useState/useReducer与按职责的hooks先满足当前状态管理，不引入Redux/Zustand或第二套Agent状态机。
- 普通CSS变量定义颜色/间距/圆角，组件局部样式采用CSS Modules；先不用完整UI组件库或Tailwind。沿视觉基准实现，后续有真实可访问性/复杂控件需求再补必要组件。
- FastAPI处理HTTP schema、错误映射、本地访问检查、lifespan与SSE；现有AsyncApplication完成模型/循环/工具/控制/保存。ASGI HTTP层不能以请求结束代替run结束。
- 普通动作走REST；服务端事件用FastAPI官方SSE，浏览器fetch读取并携带授权头，不加Axios或WebSocket。使用独立动作请求保持Steering/Follow-up/停止入口响应，不把SSE当成唯一控制通道。
- Markdown用react-markdown，保持raw HTML关闭与链接策略；当前SQLite+官方checkpoint复用，暂不引入ORM、新数据库或完整聊天归档。
- pytest/httpx验证后端，Vitest/Testing Library/jsdom验证前端行为，实际浏览器验证流式/停止/键盘/布局；真实模型用独立有界探针，不替代确定性测试。

## 生产化与面试定位

这套推荐栈可作为本地产品的生产化起点，框架选择不等于当前系统已生产成熟。React/TS组件与事件契约、Python异步HTTP入口、REST动作/SSE交付、既有SQLite/checkpoint和确定性/真实集成分离，都能形成可解释、可验证的工程主题。面试应追输入→请求→应用→模型/工具→事件→页面→保存，并能说明失败、取消、重复请求、断开与恢复取舍。

当前单worker/持久单活动run是明确的本地约束。以后多用户SaaS需独立完成身份/会话归属、租户/凭证与工具隔离、共享任务/事件交付、并发存储及部署/性能验证；不能只把Uvicorn workers调大或开放监听。HTTP/UI模块可沿用，具体存储/调度扩展按瓶颈设计，不能承诺完全零改动。

技术选型不以简历堆组件为目标；当前CSS Modules/React内置状态/SQLite有实际适用范围。业务价值仍须HTTP/RPC测试闭环、固定样本与结果/过程评测；UI和框架接线通过不替代业务质量。用户贡献与掌握程度按独立复写/排错/验证证据记录，不将AI实现视为个人已独立实现。

## 当前调用链与职责

```text
浏览器：页面/输入/临时展示状态
    │ HTTP动作 / fetch SSE
    ▼
web入口：本地访问与来源检查 → 请求schema → 应用调用
    │
    ├── RunDelivery：任务、事件交付缓冲、请求去重、临时view
    ▼
AsyncPersistentApplication → SessionService → create_agent/middleware
    └── AsyncSqliteSaver + 当前SQLite元数据
```

- 页面不持有模型密钥，不调用模型API/工具，不判断权限是否允许。运行、控制、审批状态以应用事实为准。
- HTTP启动/退出拥有一个应用实例；一个进程/一个worker/一个持久活动run。单会话正在收尾不能开始新run；其他会话仍可浏览、编辑草稿。
- RunDelivery用现有subscribe_events的快速内存观察器接领域事件，后台任务调用arun；不选择工具或重写循环。原astream_run内部消费队列溢出后不能提供完整view，因此这里用直接观察避免交付消费者反向影响生产；回调不做网络IO或重入控制。收到首个run_started后才将真实run_id返回给HTTP调用者；准入拒绝没有伪造run或成功回执。后台collector独立于浏览器事件连接，断开只退订交付。
- API参数、端口、模型配置与DB路径经启动参数/设置对象；页面API用同源相对路径。新增只读ModelCatalog.model_ids及应用只读会话视图接口，避免HTTP直接遍历模型对象或更改私有字典；不改现有调用参数。

## 首片功能及边界

1. 左侧实际会话列表/新会话/安全恢复；顶部配置model_id选择、当前工作区；默认浅色，移动端折叠会话栏。无会话时为空白列表，不塞示例会话。
2. 聊天区支持文本增量、最终Markdown与代码复制。输入框可一直编辑；idle发送新run，running普通发送Steering，可显式选择Follow-up；控制关窗/HTTP失败保留文字，不自动转成另一次run。
3. 停止按钮显示“正在停止”，等待run_finished后才“已停止”；停止期间输入成为待发送内容，显式“重新发送”启动。重复停止/过期run_id不影响新run。
4. completed/stopped/interrupted/failed/人工核对分别显示；暂定文本不因SSE结束而确认。首片工具列表为空，不显示附件/工具按钮，不启用宿主Bash；工具注册/展开/审批在下一片沿现有接线增加。
5. 加载旧会话只恢复当前安全checkpoint的可用记录；遇到摘要需显示早期内容已压缩，不伪造完整历史。failed/interrupted/running/review_required不自动恢复执行。只允许恢复启动时选定工作区对应会话，不让网页扩展工作区范围。
6. 仅本地单用户，无账号登录、SaaS、上传/图片、模型凭证设置、会话分支/删除/自动标题、全历史归档或多run并发。UI资产本地构建服务，不依赖ChatKit CDN。

## 页面结构与样式

```text
会话栏              顶部：当前模型 / 工作区
新建会话
真实历史会话        消息正文、代码、状态提示

                    待发送内容（仅有数据时显示）
                    多行输入框 / Follow-up选择 / 发送或停止
```

建议tokens：主背景#fff、侧栏#f7f7f7、主字#181818、次字#737373、边界#e5e5e5；平台系统中文字体，正文16px。对话/输入最大宽度768px，窄屏使用可用宽度；输入容器24px圆角，主动作圆形/胶囊形，同类按钮一致。错误/待审批仅用必要语义颜色。

按钮按压缩放0.95，hover仅浅灰反馈；停止状态变化保留输入焦点，不自动清空草稿。用户已滚离底部不强制滚回，提供“回到最新”。不放装饰背景/营销区/统计卡片。上述为项目拟定样式，不宣称提取到ChatKit的全部精确CSS值。

## HTTP契约

所有/api端点带本地授权、固定错误码，以下均为本片接口：

| 方法/路径 | 行为 |
|---|---|
| GET /api/models | default_model_id与model_id列表；无SDK对象、URL、key |
| GET /api/sessions | 实际元数据列表；不把thread_id当认证，传输只提供必要公开字段 |
| POST /api/sessions | {model_id}创建会话，工作区来自服务启动配置 |
| GET /api/sessions/{id} | 安全恢复/当前可用记录/是否压缩/状态；重复读取不重复restore |
| PATCH /api/sessions/{id}/model | {model_id}调用aselect_model，失败保持原确认值 |
| POST /api/sessions/{id}/runs | {request_id,text}，准入成功202并返回真实run_id；重复相同request_id与内容返回原结果，不同内容409 |
| GET /api/runs/{id}/events?after=N | 从序号N之后交付SSE；id为领域sequence，data为版本1的RuntimeEvent JSON |
| GET /api/runs/{id} | 当前状态/view/last_sequence，可用于连接丢失后校准 |
| POST /api/runs/{id}/controls | {request_id,kind,text?}；kind=steering/follow_up/stop，先核对活动run；stop回传保留内容 |

- request_id为浏览器生成非空UUID；text非空、最多32768字符，额外字段禁止。后端固定401/403/404/409/422错误，不返回异常正文或ValidationError输入值。
- 202仅确认接受；控制接受不等于消息已进模型上下文，终局/持久确认另见事件。控件按事件类型更新，不根据HTTP连接关闭推断模型完成。
- 缓冲默认256个事件，每个编码frame最大1MiB，保留最多16个已结束run；容量/上限为设置对象字段。超过范围显式resync_required，不能静默丢增量。live任务不得被缓存淘汰。
- RunDelivery临时projection复用StreamingMessageProjection；view与last_sequence在同一锁保护下获取，订阅从该序号续接；恢复view不用当作保存确认。服务重启后只恢复持久checkpoint，不承诺恢复此前内存事件、待发送内容或完整被压缩历史。
- 前端fetch SSE携带Authorization，TextDecoder流式处理UTF-8，正确跨chunk解析空行/CRLF，按session/run/sequence去重。断线显示未同步状态，显式刷新view/续接；不自动重发用户消息或把草稿变完整答案。

## 本地边界与资源

- 只绑定loopback。所有API验证本地随机访问凭据，校验Host及写操作Origin，拒绝任意跨域；首次启动访问凭据仅交给本地用户入口，不进日志/HTTP query/笔记/测试输出。启动链接若携带凭据使用fragment，前端读取后移除并在本tab sessionStorage保留，模型key始终在后端。
- 编译后的frontend/dist由同一HTTP服务提供；仅挂载该目录，不能将工作区或项目根当静态目录。Markdown关闭raw HTML、限制链接协议；首片不自动加载远端图片。
- lifespan打开/关闭原异步持久应用；停机先对活动run请求停止、等待必要收尾再关闭客户端/数据库。无不安全强杀线程或中断提交；不承诺任意自定义工具固定秒数退出。
- 应用库仍有实例锁，CLI同时占用同库时启动明确失败；不改表schema、不复制或删除用户DB。新Web元数据/view缓冲仅内存，不替代checkpoint。

## 影响文件与依赖

- 新naiyue_test_agent/web/：app.py(HTTP/lifespan)、schemas.py、auth.py、run_delivery.py、serialization.py、settings.py、__main__.py。按实际职责拆分，单文件≤500。
- 现有model_access/ModelCatalog增加只读model_ids；application层增加只读公开视图与必要恢复包装，保留现有执行/停止签名；runtime工具/压缩/选模算法不改。
- 新frontend/：React+TS入口、App/Sidebar/Conversation/Composer/RunStatus、api.ts/sse.ts/useConversation.ts、普通CSS及事件类型；组件只在确有职责时提取。
- 新requirements-web.txt固定Web新增依赖并约束现有requirements；frontend/package.json、npm lock及Vite/TS/test配置仅初始化，不改.env、原模型配置或现有依赖版本。使用npm，无额外包管理器。
- 候选版本已核对registry：FastAPI0.143.0/Uvicorn0.54.0、React19.3.0/Vite8.3.4；Node24.14.0可用。Python dry-run成功且无现有依赖升级；安装后仍需pip check与全部回归。前端React插件/TypeScript/测试工具在实施时按已核对Node/Vite约束解析并锁定，不自动升级已有环境。
- 前端测试采用Vitest+Testing Library+jsdom；Markdown用react-markdown，不启用raw HTML插件。无新增OpenAI账户/API key需求，真实集成沿现有配置并设置有界调用预算。

## 三个实施步骤（均属于本片）

1. TDD接本地HTTP准入/资源与run事件交付，假模型验证start→SSE→stop→safe终局、断开后生产继续、请求去重/隔离、保存失败；无界面时不宣称Web完成。
2. TDD实现前端事件/输入状态和已确定页面，接真实服务，验证增量/完整校准、Steering/Follow-up、停止保留/明确重发、模型选择；不新增第二套Agent状态机。
3. 临时SQLite跨进程恢复及浏览器交互/视觉验证，再做一次有界真实模型Web探针。更新README/交接/待办，给出操作入口与实际结果。

## 验证与验收

后端：缺/错token、Origin/Host、输入/额外字段拒绝无模型调用；正确模型/会话映射；保存前不得终局；同库/同会话busy；两个HTTP动作并发、late/stale控制、HTTP断开不取消；SSE首事件不丢/缓冲补读或显式重同步；已开始操作收尾与真实异常不改停止；SDK凭证不在响应/日志。

前端：空状态无假记录；普通多轮完整消息；UTF-8任意chunk/CRLF/事件重复/乱序/断线；运行中输入和Follow-up；停止未确认仍可编辑、未执行消息保留不自动发；不同会话草稿隔离；模型切换失败回滚；错误/受限恢复可见；Markdown脚本/危险链接不执行；键盘Enter发送/Shift+Enter换行、焦点和375px窄屏不溢出。

命令：`.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`、`.venv/bin/python -m pip check`；`npm --prefix frontend test -- --run`、`npm --prefix frontend run build`（含tsc检查）、`git diff --check`。UI用实际页面桌面和375px截图与视觉基准对照；服务数据使用临时测试库，真实模型探针只发送合成文本、父截止120秒、最多两个逻辑调用，不改用户业务文件。

回滚：本片模块及独立Web依赖可停用，原CLI/应用/模型配置仍可用；无DB迁移/删除，不用git reset处理未提交工作。前提是现有领域事件与保存终局仍是权威；若浏览器断线或缓冲无法续接则显示未同步并重读view，不静默确认或重放。

下一片边界：在同一接口/页面上注册既有工具、工具卡和审批；之后完善完整聊天记录展示与恢复体验，再冻结系统学习。上述是后续范围，不作为本片占位实现或完成声明。

实施修正：浏览器交付缓冲由直接领域订阅构建，与arun生产任务分开；JSON/view操作在短锁内，线程观察只调度唤醒。前端仍以同一RuntimeEvent为协议，保留既有astream API与断开不取消规则。

验收补充：原生 SSE 路由实际采用异步生成器与发送 header 前的依赖检查。start 等待者取消不影响生产任务登记；stop 保留内容加入 run view，页面重新选择会话可恢复尚保留的服务端队列。新 start 在幂等回执之后检查准入，保存失败留下 running 元数据但内存 busy 已清理的状态不会再次被接受。前端用选择代次防止旧会话响应覆盖新选择，模型回执只更新原会话，终局状态不被迟到停止回执改回等待。新增 13 个后端及 16 个前端测试；收费浏览器探针只跑两次逻辑调用，后续验证用离线真实 TCP/跨进程探针。

资源补充：请求回执上限 4096，达到上限明确拒绝新 request_id，旧回执保留不淘汰重放；服务端 retained 随 run view 保留但不跨服务重启。浏览器草稿及停止期间本地追加内容只在内存，不承诺刷新恢复。Web 工具/审批、完整历史与多用户继续作为后续边界。
