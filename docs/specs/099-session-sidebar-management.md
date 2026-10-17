# Spec099：会话右键菜单、持久排序与模型按钮文字

用户2026-10-17授权：左侧会话支持右键重命名、置顶、删除、拖动排序；输入框模型按钮只显示当前模型名/推理强度，去掉鼠标指针图标及展开箭头。复用现有浅色聊天/CSS Modules、AsyncApplication和SQLite，不改配置、依赖、工具、模型循环或真实用户库。

## 1. 当前链与目标

`useConversation.refreshSessions`→`GET /sessions`→`WebService.sessions`按固定工作区筛选→`AsyncPersistentApplication.alist_sessions`→`SessionStorage.list_sessions`按updated_at倒序。Sidebar用更新时间作标题，无管理接口。模型按钮有指针SVG、chevron，窄屏隐藏文字。

目标：名称/置顶/顺序属于应用会话元数据，不进入模型上下文。列表操作不加载/恢复会话、不调用模型。未命名沿用时间标题；置顶单独上组，组内拖动，跨组不暗中改变置顶属性。手工顺序不因新消息或改设置跳动，新会话插入普通组首位。右键及省略号共用菜单，重命名用输入对话框，删除用确认框；触屏用省略号，键盘菜单可用。

参考官方OpenAI文档：ChatGPT在侧栏省略号中提供Pin chat，删除采用菜单→Delete→确认；不声称本项目拖动实现来自ChatGPT内部。来源：[Release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)、[Deleting chats](https://help.openai.com/en/articles/8809935-deleting-and-archiving-chats-in-chatgpt)。沿用design/check完成实际页面与专项复核。

## 2. 数据与接口

- SQLite schema5→6增加`title TEXT`、`pinned INTEGER NOT NULL DEFAULT 0`、`sort_order INTEGER NOT NULL DEFAULT 0`。现行4先原4→5再→6；1/2/3仍不接受。迁移按原updated_at顺序初始化rank，事务失败回滚。原4迁移测试改用完整真实字段的fixture，版本断言更新6，不保留仅服务假fixture的生产分支。
- `SessionStorage`直接扩展名称/置顶更新、工作区全量排列校验及原子删除，不新增通用管理平台。排序入参必须是该工作区当前所有ID的一次排列，拒绝重复/缺失/陌生ID及跨组次序；旧列表发生增删返回stale_session_order，不覆盖新会话。SQL参数化，呈现字段修改不改updated_at/执行状态。
- `PATCH /sessions/{id}/presentation`仅接受非空trim名称（≤120字符、无控制字符）和/或严格bool pinned。`PUT /session-order`接受`session_ids`全排列；`DELETE /sessions/{id}`无路径或thread参数，成功返回ID。所有接口原认证+固定工作区归属检查，无自动恢复；跨区/已删除404。
- AsyncPersistentApplication薄转发到存储/必要删除生命周期。Web只投影`title,pinned`，不暴露rank或thread_id；排序由后端给出。前端SessionRow新增可选呈现字段，旧无字段测试数据按未命名/未置顶消费；真实接口始终返回新字段，无重复存储源。

## 3. 删除与原子边界

删除包含会话元数据、全部根checkpoint/pending writes及可从全部根历史关联到的子线程checkpoint/writes，不删除工作区文件/产物。当前保存器不裁剪旧checkpoint：通过官方`aget_state_history`逐项派生既有subagent_records，覆盖被压缩移除的调用；不只用最后快照，不新增子线程表或改线程ID合同。

应用删除用现有persistence锁和`_recovering`占用串行化生命周期，拒绝活动run/正在恢复及已加载busy；Web同样使用RunDelivery.start_lock，避免与普通启动/审批/恢复交错。为将元数据与官方SQLite的checkpoints/writes一起提交，SessionStorage在同一数据库事务内按可信thread集合参数化删除，失败全部回滚。这是当前已固定SQLite表的存储适配，不在Web拼框架SQL，不复制官方序列化。SQLite文件不会因DELETE自动缩小，不在本片VACUUM。

并发探针确认：普通restore必须从读取元数据之前到安装内存会话之后完整占用`_recovering`，不能只在入口/安装瞬间检查，否则已读取旧快照的GET能在DELETE后复活会话。HTTP取消也须覆盖“应用删除成功→delivery缓存撤销”整个段落；仅在AsyncApplication内settle不够，取消重抛会跳过Web清理。Web用同一await_settled组完成成功收尾，删除失败不撤销旧缓存。

成功后清理对应应用内会话/已结束子实例、阻断集合、Web已完成交付缓存/回执/最新映射。已有SSE消费者收到的内容无法撤回，但删除后不能通过run GET重新查询。失败保留可见会话及历史，记录安全错误并返回session_delete_unconfirmed，不自动重试破坏性请求。UI取消不调用DELETE，失败保持对话框/列表；删除当前会话后清空其草稿/队列/订阅并返回空白输入，不创建新会话、不改变另一会话的草稿。

提交前复核补充：RunDelivery按retained_runs淘汰旧运行时，须一并移除以该run_id为scope的控制回执，否则旧停止回执的retained文本会脱离会话删除的可追踪范围。只清理被淘汰run的控制回执，保留session级启动/审批回执的防重执行合同；没有该运行的控制重放应返回run_not_found。新增真实HTTP回归覆盖“排队文本→停止→运行淘汰→删除会话→重放旧停止请求”，以事件同步、临时库验证。

## 4. 前端链与边界

Sidebar右键/省略号→共用菜单（重命名、置顶/取消置顶、删除）→对话框或动作→useConversation操作→WebApi→新认证路由→应用存储→确认后刷新列表。所有写入先确认后显示，不在失败时伪造新名称/顺序。排序拖到同组目标前，持久化当前列表全排列；用户后续明确不需要上移/下移，菜单只保留重命名、置顶/取消置顶、删除，排序只提供鼠标拖动。请求期间防重复，旧GET不能覆盖已确认新列表，失败显示可重试状态；删除/重命名目标固定ID，切会话不误作用于新项。

为保持单文件≤500行，将独立列表查询/管理状态从已近上限useConversation移到`useSessions.ts`，实际创建/发送/运行继续原hook；不是平行会话执行器。Sidebar菜单/对话框可拆`SessionMenu.tsx`和独立CSS，复用现有按钮/字体。模型按钮直接删SVG/chevron和隐藏文字CSS，窄屏文字可省略显示完整title，点击行为保持。

桌面拖动使用Pointer事件，经过位移门槛才捕获指针，按实际命中行显示插入线，松开后调用同一ordered→reorder入口；取消、跨组和行外松开不提交。拖动后的click不切换会话。触屏保留列表滚动与省略号管理菜单，不拦截触摸滚动；本片不提供触屏拖动排序。只在源所在组显示末尾落点，避免向另一分组插入提示而改变目标坐标；实际浏览器先复现位移导致拖动未提交，通过临时事件日志确认Pointer已触发后定位此因，先红后绿补分组落点回归，诊断日志移除。不保留HTML与Pointer双套拖动机制，测试覆盖实际Pointer处理分支及排列/不切换会话断言。

## 5. 修改与验证

扩展session_storage/persistence/async_persistent，Web schemas/service/app/run_delivery；前端types/api/useConversation/App/Sidebar/ModelMenu及CSS，新增useSessions/SessionMenu与聚焦测试。同步README/web-tools/handoff/future-work及097按钮外观历史边界。无新配置/依赖，不提交push，不改课堂记录。

先红后绿：元数据认证/校验/工作区/无模型副作用，schema迁移与重开排序；乱序/缺失ID拒绝；运行/删除竞态；根子历史与pending writes清理、事务故障回滚、交付缓存撤销；UI右键/省略号/键盘、重命名/置顶/拖动/取消确认/失败保留、当前/非当前删除及过期列表；模型按钮只有文字且可打开。全量pytest/vitest/build，AST/≤500/diff/链接检查；临时SQLite离线模型实际页面1440/375验证，不操作真实用户记录。

## 6. 实现记录与实际验证（2026-10-17）

正常例：`[A,B,C]`中鼠标将C拖到A前，Sidebar的Pointer收尾计算`[C,A,B]`→`useSessions.reorderSessions`→`WebApi.reorderSessions`→认证PUT `/api/session-order`→`WebService.reorder_sessions`→`AsyncPersistentApplication.areorder_sessions`→`SessionStorage.reorder`。数据库核对完整ID集合及置顶组顺序后事务更新rank；响应确认再更新列表和只读刷新。这里保存的是元数据，不进入模型消息，也不加载会话。旧列表GET的版本失效，不能把顺序改回去。

失败例：打开删除A的确认框后取消，不发DELETE；确认后若A仍活动，Web/App拒绝，列表与历史保留。若SQLite触发器注入故障，`SessionStorage.delete`回滚元数据、根子checkpoint/writes，应用记录安全诊断并返回`session_delete_unconfirmed`，UI保留目标和错误。若已成功提交但HTTP取消，`await_settled`先完成应用清理及`RunDelivery.forget_session`，旧run查询返回404。恢复与删除交错由`_recovering`完整持有阻止旧快照重新安装已删除会话。

实际新增管理类为前端`SessionMenu`和`useSessions`，后端扩展已有存储/持久应用/Web入口；无新执行器/配置/依赖/通用resolver。模型默认推理档可解析时按钮显示实际档位，保存值仍是`model_default`，不改变原选择语义。删除框初始焦点在取消，菜单关闭归还行焦点；没有上移/下移API或菜单分支。

| 验证命令 | 实际结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2142 passed in87.00s，日志`/tmp/naiyue-099-backend-final-complete.log` |
| frontend目录 `npm test -- --run` | 18文件96 passed，日志`/tmp/naiyue-099-frontend-final-complete.log` |
| frontend目录 `npm run build` | TypeScript通过，Vite204模块成功 |

9项后端管理测试覆盖存储/工作区/删除/迁移及两项生命周期竞态，8项前端管理测试覆盖三项菜单、实际Pointer分支、刷新竞态和文字模型按钮。原schema4迁移测试保留真实数据/模型/推理断言，fixture补齐实际元数据；无放宽断言或skip。专项架构/安全复核最初发现restore生命周期与取消缓存两项问题，回归先红后绿，最终无剩余确证发现。

临时SQLite/工作区与确定性离线模型的实际构建页：IAB及Chrome均验证鼠标拖动，刷新和服务重启保留名称/置顶/顺序；最终1440px右键菜单只有三项，375px省略号同样三项，模型文字无箭头。浏览器只打开删除确认并取消，真实删除由临时库测试验证。截图：`/tmp/naiyue-099-ui/session-menu-desktop.png`、`session-menu-mobile.png`及`model-text-mobile.png`。这些是本机UI/存储证据，不是实网供应商或Windows原机证据；真实配置和用户库未改，未暂存/提交/push。

收尾检查：340份Python AST有效，340份Python及52份前端源文件均≤500行；compileall、pip check（No broken requirements found）、更新文档本地链接及git diff --check通过。

提交前安全补充已先红后绿：`test_delete_cannot_replay_control_text_from_an_evicted_run`在旧代码实际返回202而不是404；最小修复仅在`RunDelivery._prune`清理被淘汰run的控制回执。修复后管理与原Web测试14 passed in1.75s，独立安全复核新回归1 passed in0.75s、无剩余发现；活跃停止回执幂等及被淘汰运行的session级启动回执均保留断言，不重放模型。最终整合测试结果以handoff顶部为准。
