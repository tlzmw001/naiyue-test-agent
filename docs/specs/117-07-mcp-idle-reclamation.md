# MCP-07：显式空闲连接回收

状态：2026-10-20，用户确认方案并授权后已实现，完成本机全量回归与干净安装；09后接本片。无提交/push/发布，
不修改真实配置、依赖、数据库结构或并行Spec118/课堂记录。

## 已定范围与当前链

默认idle_timeout_seconds=0；只有显式有限正数按server启用。连接仍由原应用manager
管理、会话/实例拥有、调用借用；回收后新调用按需新建，不重放失败/旧调用、不恢复
远端隐藏状态。启用中修改TTL沿09连接配置变更拒绝合同，停用/刷新后再启用生效。
不增加ping、跨会话池、设置页、通用定时任务平台或自动目录更新。

当前调用：SessionService准入 → AgentSession.admit_run → 模型/工具循环 → borrow
在server槽位后取得owner → 单次RPC → borrow finally → 保存/AgentSession.release_run。
根待审批在release_run后仍保存pending；子driver待审批时父run仍活跃。09的retiring/
invalid与_retire已负责关闭收尾和“只删当前owner”保护。当前正TTL拒绝，没有定时器。

## 最小实现与数据归属

修改mcp_client/settings.py、resources.py、application/agent_session.py及定向测试。
资源管理器已持有loop/owner/requests；定时器、单调时钟、借用声明、关闭任务集合
放在该管理器，不新建平行服务。借用声明按原owner key计数，从任何异步排队前取得，
直到finally清理后释放；有声明就不能回收，避免等待server槽位时被抢先关闭。

AgentSession已有manager引用，直接在准入/释放run时传递会话占用布尔值，构造恢复
会话时同步初始pending事实。无额外回调/ContextVar/父对象反向依赖。比较：只看RPC
借用会误回收模型思考/审批暂停连接；定时轮询会错过短run并错误计算闲置开始时点；
复制整个SessionState或增加通用resolver没有消费者。仅通知一个瞬时占用事实是最小
接线，授权、保存、会话事实仍由AgentSession拥有。

占用保护按逻辑session覆盖其现存根/子连接：run（含模型等待、保存和子审批等待）
活跃或根pending存在就保护；这是保守保护，不延长其他session。子实例最终释放仍
立即退出，不等待TTL。线程中的恢复/状态动作经loop.call_soon_threadsafe投递，应用
loop内准入同步生效；同一会话锁内发出通知；通知使用manager内单调序号，loop忽略过期投递，防止线程中
旧释放晚于新准入执行而覆盖占用。序号只服务该跨线程顺序，不进入会话存储。

空闲时按owner设置一次loop TimerHandle；从最后一次业务占用释放/借用释放开始计时。
0无定时器，不新增周期网络/进程IO。活动占用撤销计时；健康ping、读取目录、可见页
切换不专门刷新业务idle（执行中的真实borrow仍受保护）。定时回调同步复核owner
对象、deadline、session占用、借用声明、users/waiters、配置启用/关闭状态；到期则先
标记retiring/invalid，再创建异步_retire任务，由原拥有者退出SDK。

用户已选择“新调用先取得使用权”：进入borrow先登记声明并取消该owner计时器，
定时回调不能再提交关闭；若回调已经提交关闭，新借用按原retiring路径等待旧关闭
后建新连接。旧callback/旧清理不能操作同key的新owner。已关闭server不能因TTL重建。

_retire统一取消对应owner计时器。应用退出/删除会话/结束子实例收束匹配的TTL关闭
任务，清理失败记录原失败并保留原有最终关闭失败与同主体禁止替代合同；后台失败
必须记录安全错误，不能成为未取走的task异常。没有无界重试或故障自动重放。

## 正常与失败主链

正常：run借用调用 → RPC结束但模型继续（会话占用，未计时）→ 必要保存完成 →
release_run无pending → 记录单调截止时间 → 到期复核/退休 → 同owner任务退出SDK →
新run借用创建新client。目录/授权配置身份保持，远端状态可能重置。

失败：定时关闭抛错 → _retire记录/安全日志 → 后台任务完成 → 下一借用得到原有
mcp_previous_cleanup_failed而不新建 → 应用最终关闭报告ExceptionGroup。停用、退出、
取消或另一个session不改变该失败的归属，也不把旧调用重发。

## 验证与收尾

先红灯验证正TTL、边界前/后、默认0；用可控_clock和显式触发已注册回调断言deadline，
事件同步创建/排队/关闭，不用紧sleep；另让真实loop定时器触发一次证明实际接线。
覆盖新借用先声明、已closing等待、旧回调无效、关闭失败、后台任务退出/取消、09
启停和改TTL；正式图验证模型等待、根审批、子审批、保存中、内存/SQLite、stdio/
HTTP JSON/SSE以及其他会话独立。旧正值拒绝测试迁移为负数/非有限非法配置，正值
必须验证实际运行，不删失败断言或放宽验证。

定向 → 全后端/前端/TS/Vite → compile/pip/AST/500行/diff → 本机原wheel构建/干净
安装及源码外TTL探针。保持09统一按钮和现有权限/恢复/必要保存合同，不加UI状态。
同步mcp-client、总Spec、future-work和handoff；验证不推定真实业务/其他平台或用户
掌握。唯一下一片08交付验收，先说明范围，发布另行授权。

## 实际接线与本机证据（2026-10-20）

生产修改仅3份：MCPServerSettings接受有限非负TTL；MCPSessionResources在原owner
key上管理TimerHandle、借用声明/关闭任务；AgentSession构造、admit_run、release_run
同步占用事实。没有新生产模块、公开API签名/依赖/数据库结构变更，也没有旁路的
连接创建/关闭实现。临时发现、09启停、删除/子结束和应用关闭继续用原_retire。

| 数据 | 生产者 | 消费者/形状 |
| --- | --- | --- |
| 会话占用 | AgentSession在持状态锁时发出 | manager的session ID集合；工作线程通知以单调序号防过期覆盖 |
| 借用声明 | borrow进入异步等待前登记，finally释放 | owner key到计数；定时回调复核，取消排队不会遗留声明 |
| 截止时间 | 最后占用/借用释放后的_clock() + TTL | owner key到(owner对象, TimerHandle, deadline)；旧owner/旧deadline回调无权关闭替代 |
| 异步回收任务 | _expire_idle无await标记退休后建立 | task到owner key；_release按scope收束，失败由原清理失败集合保留 |

首批7项红灯：默认0缺定时状态，正TTL被旧校验拒绝；之后最小实现转绿。新增29项
后端：3份新文件共22项，原设置文件新增7项有限值合同。旧正TTL拒绝项改为负值
拒绝，原非法配置/保留旧服务断言保持；正值另验证实际回收，不用skip/放宽断言。
没有修改conftest/helper等测试基础设施。

最终后端全量：

```text
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
3313 passed in 221.25s (0:03:41)
```

定向实测：

```text
.venv/bin/python -m pytest tests/test_mcp_idle_reclamation.py -q --tb=short
7 passed in 0.82s（首批，后续补3项竞态/退出检查）

.venv/bin/python -m pytest tests/test_mcp_idle_reclamation.py tests/test_mcp_idle_application.py tests/test_mcp_settings.py tests/test_mcp_runtime_configuration_boundaries.py -q --tb=short
61 passed in 14.32s

.venv/bin/python -m pytest tests/test_mcp_idle_transports.py tests/test_mcp_lifecycle.py tests/test_mcp_resource_failures.py tests/test_mcp_runtime_configuration.py tests/test_mcp_runtime_configuration_interactions.py tests/test_mcp_application_resources.py tests/test_mcp_recovery.py -q --tb=short
54 passed in 21.47s

npm --prefix frontend test -- --run
46 files / 366 passed
npm --prefix frontend run build
TypeScript / Vite成功；原大chunk提示保留

.venv/bin/python -m compileall -q naiyue_test_agent
.venv/bin/python -m pip check
No broken requirements found.
157份生产Python AST/500行检查通过；resources.py为402行

git diff --check
exit 0

.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp07-20261020/wheel-final
Successfully built naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl
287文件，157份生产Python与wheel逐字节一致

.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp07-20261020/wheel-final --output build/mcp07-20261020/install-final.json
exit 0；原生工具、Web/API、工具/审批恢复、SQLite重开及CLI通过

.venv/bin/python build/mcp07-20261020/verify_install.py build/mcp07-20261020/wheel-final/*.whl tests/mcp_discovery_fixture.py build/mcp07-20261020/ttl-install.json
exit 0；源码外全新venv、待审批占用、释放后完整TTL、真实loop定时关闭、新进程1/1、0重放、全部进程退出
```

安装TTL探针用可控单调时间推进，等待实际注册的loop定时器及owner任务退出，不靠
紧sleep断言。正式图覆盖模型等待/RPC/根审批/子审批/必要SQLite保存；内存与持久
应用均验证同一合同。JSON/SSE及modern/legacy HTTP验证回收关闭transport、legacy
DELETE、按需新client、恰好2次业务调用；HTTP夹具counter为server全局，不推定远端
状态因client重建而重置。关闭失败/晚到线程通知/排队使用权/已提交关闭及外部取消
用事件和可控时钟验证，无网络自动重试或控制异常吞掉。

wheel SHA256：`2ebabda7ca7a38ca0e6a197f6a0b4cc8d0d9a31c57e2117f8b760fffc88ee9ab`。
证据：build/mcp07-20261020/{red,target-core,target-application,target-regression,
pytest-final,frontend-tests,frontend-build,build-final,install-final,ttl-install}.log，
以及archive-final.json、install-final.json、ttl-install.json。

边界：本机合成SDK服务/离线模型，供应商0，不代表真实业务/其他平台或用户掌握；
没有修改UI、真实配置/用户数据库/依赖/并行Spec118和课堂，没有提交/push/tag/发布。
TTL默认0；正值服务接受外部状态重置；启用中修改TTL仍须先停用再启用。保护当前
逻辑会话所有现存根/子连接是有意保守策略，永久待审批不会被当成idle强制回收。
唯一下一片08：整体交付验收及五平台证据，实际发布仍另行授权。
