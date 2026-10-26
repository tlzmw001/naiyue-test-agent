# R1-13D-03：输入框环境选择与首次发送

版本边界：本片记录03验收时点；后续[04](R1-13D-04-sandbox-lifecycle.md)已增加全局管理入口、运行/保留额度、空闲停止及根服务退出停止原容器，原有会话/模板快照不变。

2026-10-26用户“继续吧”授权承接02实施本片。01/02未提交修改保留，基线ce617d0，不提交/push，不改真实配置、用户库或Docker资源；04资源额度/回收和05真实镜像验收仍未实施。

## 当前链与改动

目前ExecutionSelector点选沙箱后直接create → useConversation.newSession → WebApi.createSelected → POST sessions，显示笼统Docker名称；首次发送没有环境选择路径，只调用api.create。02已提供模板列表、固定文件刷新、ID/指纹和创建幂等。

本片保留现有项目/环境在输入框上沿的位置、字体/圆角/表面/箭头和独立结果审查。列表以此计算机和真实配置模板作为选项，默认项仅作标注。选择完成只更新草稿；首次发送才准备输入与容器。无配置时不造示例环境。

正常链：ExecutionSelector读取execution-options → 选择具体模板、明确勾选未跟踪文件/信任 → 确认用于新会话，保存ExecutionDraft → useConversation.submit首次发送 → useSessionCreation冻结模型、模板ID/指纹、文件列表和请求ID → 必要时按确认的revision信任仓库 → WebApi.createSelected显式转发 → 服务原创建链 → 读取会话实际环境 → 准备可用才api.start。草稿仅在消息成功提交后清空。

失败链：目录/模板变更由服务以env_profile_changed等拒绝，消息保留，原创建没有副作用；解除未发起创建的选择锁，要求重新选择。网络丢回执则保留原请求/模型/文件/模板，关闭菜单、刷新、切换项目回来不改请求；再次发送只核对原请求。已有会话准备未完成时保留消息和会话，执行前显示状态，由原环境准备入口继续，不能自动换环境或直接start。

## 合同与职责

- 增加纯UI ExecutionDraft（执行类型、文件列表、模板ID/指纹/显示名称、可选信任revision）。SessionCreation仍是API请求，首次发送才冻结model_id/requestId，显示名称不发给服务。
- useSessionCreation只负责新建草稿选择、冻结请求和准备结果；确认尚未执行的模板拒绝要求重新选择；不推断未知网络失败为无副作用，不自动重试start。
- useConversation目前499行，不能再叠入创建状态机；将原有草稿/待发送map和写入口原样移入useConversationDrafts，再接创建协作者，不引入通用状态框架。各scope仍使用自己挂载的hook，已有会话草稿与pending独立保留。
- newSession改为进入本地新会话草稿，不立即POST；从已有会话选择环境时保留原会话草稿，新草稿为空时带入正在编辑的文字。新建与切换仍受当前发送/选择门闩保护。
- ExecutionSelector扩展为模板列表及刷新按钮，确认用“使用此环境”，明确首次发送时准备。现有环境生命周期与查看改动入口保留，旧会话更换环境进入新草稿。
- 刷新调用认证全局POST /api/v1/environments/refresh，再读取项目选项。只成功响应更新列表，失败保留旧列表，丢弃过期读取；选择的指纹变化不自动替换选择。HTTP刷新每次新请求ID，不读任意路径。
- Session status投影增加可空profile_id/profile_name/profile_revision，优先从原setup.selection、旧登记事实派生。离线/失效仍显示原名称，不读取当前模板；不暴露镜像、完整settings或连接信息。
- Composer发送期间呈现“正在准备环境”，创建未确认时锁定更改环境和模型，但保留消息/再核对入口；运行/恢复/审批和结果取回规则不变。
- 普通对话与本机执行不要求Docker；GitSource本机创建仍由原业务边界明确拒绝，不自动选沙箱。无新依赖、表、版本变化。

复核补充：`changeControls`原先在无会话时直接`api.create(model)`，会把已选择的沙箱草稿转换成本机会话。必须删除该创建旁路。权限/工具/推理强度同属新草稿；首次发送冻结这些设置，确认所选环境后才保存设置，全部确认成功才start。Composer/ModelMenu分别读取草稿字段，不伪造SessionView。设置保存失败保留已创建会话和消息，后续发送继续确认同一会话的未完成设置，不再创建环境；已有会话设置入口保持原行为。切换新草稿模型清除旧模型的推理档位，保留工具/权限。无会话时仅改本地设置，不请求服务。

后续设置改选同样须确认：有未完成草稿设置时，用户主动改选先将待确认字段更新为新值，保存成功后才移除，连续失败不能跳过并start；模型变更以undefined明确废弃旧模型thinking。设置失败提示下一次发送继续保存所选设置。已知创建回执的session_id之后GET失败，仅重读该ID；创建回执未知才核对原POST。

## 影响文件与验证

前端types/api/useConversation及新草稿/创建hook、App/Composer、ExecutionSelector及样式、真实保存环境投影、相关组件/API/集成测试、README/handoff/本Spec。旧测试的“点新建即POST”迁移为“选择不POST，首次发送才POST”，保留历史只读、旧请求冻结、草稿、权限/异常等原行为断言，不删除用例或放宽条件。无conftest或全局测试配置修改。

先红后绿覆盖：选项来自配置/默认标记/无假数据、刷新成功/失败/乱序、过期选择重确认、凭据不可勾选、键盘/焦点、选择不创建/首次发送只一次、真实API指纹转发、未知响应原ID/原模型重试、准备失败不start且消息保留、实际名称不随配置变化、跨项目草稿范围。

完成后跑前端全量、Python相关与全量、TS/Vite和标准构建/安装；浏览器在桌面、375px和明暗主题检查菜单、长名称、文件列表、焦点与溢出，使用隔离离线服务/受控transport，不调用真实模型或改真实配置。实际结果补在本Spec；下一步仅讲解04。

## 实际实现与验收（2026-10-26）

生产入口：`App`将草稿和锁状态传入`ExecutionSelector`；`newSession`进入new草稿并保留文字，只读取列表。`useConversationDrafts`接原文字/pending的按session映射，`useSessionCreation`接选择、原请求、信任和控制设置。`submit`依次prepare → applySession → configure → start；准备/设置失败均在start前返回，文字不清空。首次创建回执未知保留原POST，已知sid后GET失败只重读。已有会话的主动改选以override更新待确认字段，只有保存成功acknowledge才移除；模型变更只明确废弃旧模型thinking。没有新的直接api.create旁路。

正常例：Node模板/NoAsk/notes.txt作为草稿 → 仍0会话0环境 → 首次发送冻结请求 → 新环境准备好 → NoAsk确认保存 → 一次run完成。失败例：刷新使Node指纹变化 → 发送旧指纹返回env_profile_changed → 0会话0环境且原文保留 → 用户明确重选新版后新请求才执行。连续设置保存失败时，即使用户又改选Default且再次失败，下一次发送仍先确认Default，不进入start。

| 验证 | 实际结果 |
| --- | --- |
| 首批前端创建/API与后端状态测试 | 前端6红、后端2红；缺少指纹/草稿和保存名称，原因已核对 |
| 菜单首次测试 | 5红；之后补焦点、信任过期、本机请求ID、控制组合/双失败、已知sid只重读及锁定菜单提示的确定性先红用例 |
| 前端新增 | 22项，environment-draft 12、environment-api 3、EnvironmentMenu 7；后端新增2项 |
| `npm test -- --run`（frontend） | 63文件527 passed/6.12s，最终独立日志frontend-final-verified.log |
| `pytest -q tests/test_sandbox_profile_status.py tests/test_sandbox_profile_selection.py tests/test_sandbox_session_http.py tests/test_sandbox_sessions.py` | 22 passed/14.66s |
| 全量收集及 `python build/r1-13d-03/run_shard.py 0` / `1` | 520文件5458项，互斥覆盖全部；2828 passed/262.58s、2630 passed/363.70s |
| `npm run build` | TS/Vite通过，保留既有大chunk提示，0错误 |
| `python -m scripts.build_release --target macos-arm64 --output build/r1-13d-03/dist-final` | exit0；343 Python/4 Web/README与最终源码逐字节一致 |
| `python -m twine check build/r1-13d-03/dist-final/*.whl` | PASSED |
| `python -m scripts.verify_wheel_install --wheel-dir build/r1-13d-03/dist-final --output build/r1-13d-03/install.json` | exit0；源码目录外全新安装及CLI/API/存储升级备份还原/历史/MCP/后台/子实例/记忆smoke通过，0供应商请求 |
| 定向审阅 | 架构发现并修复控制项直建本机旁路；安全发现并修复改选再次失败导致跳过设置的边界，独立12项复验通过，无剩余阻塞 |
| 文件/变更检查 | useConversation 493行；新增/修改代码均不超过500行，diff无空白错误，无新依赖/DDL/真实配置/用户库改动 |

旧测试迁移保留断言：历史只读/草稿/新建能力改为选中不POST；辅助侧栏测试改为打开已存会话；ExecutionSelector原请求/模型冻结的实质断言移至首次发送hook，组件继续检查锁定/焦点/文件。session-controls按首次发送时序保留两次读取失败、同sid、只创建一次、文字与设置确认/启动顺序；局部mock模拟成功保存后的GET返回同一已保存NoAsk，避免静态Default伪造存储回退。未改conftest/全局测试配置，未删除、skip或放宽行为断言。

浏览器：Playwright扩展连接失败后使用IAB控制，不安装扩展或改系统配置。最终构建已reload核对；认证TCP服务、隔离项目/配置、OfflineModel和真实envd dispatcher经受控Docker transport验证完整链。桌面1280宽/菜单440，375宽/菜单325/右边界350，页面scrollWidth分别1280/375，无横溢出；明暗主题、长名称、刷新成功/失败/清空、保存名称/NoAsk均实际核对，控制台error/warn为空。模板/权限/文字选择时服务0会话0沙箱；旧指纹拒绝仍0；确认后1会话1沙箱且登记image=fixture:node-v2、profile=frontend-tests，run完成。清空模板后原会话仍显示保存名称并可用。截图和`browser-checks.json`在`build/r1-13d-03/`，为本机受控验收，不是真实Docker/镜像隔离或公司环境证据。

Python使用`.venv/bin/python`，pytest PATH前置`build/pre-profiles-push/test-assets/bin`。原始红/绿日志、全量节点分组、包一致性、安装结果在`build/r1-13d-03/`；临时页面/服务关闭，主题与视口恢复。01/02修改、.venv与790份调研原始资料保留，未提交/push/发布，无新增用户掌握证据。唯一下一步讲解并批准04，之后仍有05真实双镜像，SSH14暂缓。
