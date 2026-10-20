# MCP-05：子实例调用与持久恢复边界

状态：2026-10-20，用户批准后已完成本机实现/验收；依赖01—04。
不发布、不升级依赖、不改真实配置/数据库，不启动06/07/08。

## 现有主链和本片目标

04两工厂先发现/注册，后创建SessionService/编译图。MCP工具闭包持manager，执行时
固定root；03 SessionService.borrow_mcp已经校验会话、活跃子实例并带当前RunServices
control借用，child driver finally已经release_instance。子角色注册显式tool_names，
子图通过相同ToolSet/权限/审批/一次许可/错误反馈运行，不需要另一套运行时。

持久恢复先classify_recovery，restore_approval由validate_saved_approval重建subject
比较；新manager的HMAC身份不同，因此旧MCP待审目前会在用户选择恢复之后才失败。
continue已经能取消未审批批次、配对错误结果、清掉执行状态并保存，绝不执行原工具。

本片允许已明确配置的子角色调用MCP，以既有SessionService借用入口统一根/子路由；
恢复层明确拒绝重用旧MCP待审，提供已有continue动作取消旧批次后重新发起工具调用。
不承诺恢复远端业务状态，不复用旧一次许可或跨进程会话记忆。

## 接口及最小接线

MCP适配器的内部create_mcp_tool首参由resources改为borrow_mcp可调用对象，调用参数
是(session_id,agent_path,server_id)。两个工厂各用一个直接闭包转给装配完成后的
sessions.borrow_mcp；register_mcp_tools仍用resources.discover取得启动目录，再把
此闭包传给ToolSpec。实际执行只在sessions赋值并完成建图/返回之后发生。

比较：直接manager.borrow缺少结束子实例检查；给manager增加通用可变scope验证器、
向RunServices/Runtime注入新全局路由器、或拆出第二次初始活动选择均增加职责/状态。
直接传递已有借用函数只服务当前MCP执行消费者，不引入注册平台/新ContextVar，不把
SDK client绑定到工具。SessionPort无新增未被框架消费的方法，公开CLI/Web签名保持。

执行端从已有可信ToolRuntime取agent_path，复核原一次许可，再走SessionService。
根/子各有自己的(session,agent_path,config_identity) owner；同实例跨模型轮次复用。
SessionService同时核对当前publisher的session与source agent_path，避免一个实例
借用另一个实例的控制/连接；资源探针在无publisher时仍通过已加载主体/实例检查。

## 正常与失败分支

正常：根subagent许可 → prepare_subagent_instance冻结角色名单/模型/实例身份 →
child图产生MCP调用 → 同一JSON Schema和权限链 → 当前实例一次许可 → SessionService
校验活跃实例 → manager借用child owner → list复核 → SDK单次call → 错误/成功结果
回填child模型 → 最终报告回填root → driver finally释放child资源，root资源保持。

审批暂停：第二次MCP调用前中断 → child driver等待pending.future，已有连接保留；
提交只接受原batch/run/row，恢复同child thread，不重启parent、不重复第一次调用。
会话记住仍是用户确认的完整MCP工具名范围，同会话根/子合法参数可复用；每次一次
许可严格绑定具体agent_path/call/参数，不能跨实例消费，显式Deny仍优先。

失败：MCP普通调用错误仍变error ToolMessage，child可继续生成报告；响应丢失/停止
不重放。停止单child只作用于其control/owner，兄弟/root保持；根停止收束全部child。
最终结束后再借用拒绝，不重建已结束实例。新的必要清理失败仍上报，不能冒充完成。
子checkpoint写入失败沿既有SubagentCheckpointSaver反馈parent并保留失败事实；
根finish_run失败必须让arun失败/阻断后续执行，不能被MCP成功结果覆盖。

组合测试已复现应用close竞态：stop_loaded_sessions已标记control，但manager._release
直接cancel可能先于borrow.watch_stop，局部stopped=False，于是取消被透传成框架
NodeCancelledError。资源层须记录自己发起的关闭取消次数，只在当前control已请求停止
时消费这些已知取消；其他取消计数保留并传播。借用进入finally收束后不再追加关闭取消，
但关闭仍等待它完成，避免清理期间的取消覆盖已转换的停止反馈。新增记账只服务现有
borrow/_release两个消费者，不把control.requested单独当成吞掉外部取消的依据。

## 持久恢复

历史pending_approval含任意MCP subject时，只提供continue动作；reason使用明确的
mcp_approval_requires_new_call。restore_approval入口同样显式拒绝，返回既有
approval_restore_unavailable，即使手工提交或在同进程卸载后也不能绕过。
这是描述可执行动作，不建立外部连接、不调用模型、不更改检查点。

continue沿已有三阶段恢复：清调度 → 补cancelled_before_approval配对错误 → finished
→ 必要保存 → 安装会话，保持零tools/call。用户新任务产生新调用后，当前目录/配置/
权限重新评估与审批。无MCP的旧待审恢复保持现有行为。完成会话重开只恢复聊天/工具
选择，不恢复外部连接/进程状态或session_allow。

服务停用/工具缺失时保留历史可读，按现有saved_configuration_unavailable拒绝安装/
旧待审恢复；不静默换工具、不自动删除保存的工具选择。目录变化但工具仍在时可以
continue取消旧批次，下一次新调用使用新定义。应用启动启用服务无法发现时仍失败。

06再补统一CLI/Web来源/结果展示；当前已有恢复动作按钮显示continue，不新建界面。
现有历史提示把新增reason映射为“旧MCP调用需重新审批”的可读说明，不显示内部错误码。
不增加新的recovery action/DB字段/配置键，不自动重建过去的child driver。

## 影响文件和验收

生产：mcp_client/tools.py，application两个异步工厂、session_service.py，
session_recovery.py/session_persistence.py。已有session_subagents/停止/保存主链优先
不改；只有可复现回归才最小修复。测试替换04“所有child拒绝”的范围断言为未加载/
无效实例拒绝，保留许可篡改及零借用检查；新真实SDK stdio/HTTP确定性集成测试。

先红灯：child两次调用取得1/2并最终释放，root独立；当前publisher跨agent借用拒绝；
SQLite重开MCP待审只列continue且直接restore拒绝。再实现最小接线，补显式角色隔离、
记住/一次许可边界、已有连接审批暂停、child/root停止、普通失败继续、必要保存/
checkpoint/清理故障、服务缺失和定义变化及不重放。使用事件或状态轮询，不用紧sleep。

定向测试 → 全后端/前端/构建 → compile/pip/AST与500行/diff → 现有发行构建及源码外
新venv标准安装与MCP child专项。真实供应商、业务服务、五平台与用户掌握独立。
本片结束更新handoff/当前Spec/future-work/使用说明，并说明06方案等待用户确认。

## 实际实现与验收

两工厂的局部borrow_mcp闭包在执行时转至已完成装配的sessions.borrow_mcp；注册时
没有调用它，不新增启动可变路由对象。MCP执行不再固定root或直接绕过SessionService。
SessionService验证已加载会话、活跃实例与publisher来源路径，再交03 manager带当前
control借用。子角色、图、实例创建、审批/停止与driver finally释放均复用既有实现，
未向SessionPort/RunServices/checkpoint增加应用对象或SDK client。

session_recovery.validate_saved_approval显式拒绝含MCP subject的旧批次；历史投影只
提供continue及mcp_approval_requires_new_call原因，现有Web历史提示映射为中文。
continue沿原清调度/配对/finished和必要保存链；服务/工具不可用维持原恢复阻断，
历史仍可读。没有新DB迁移、自动热更新、旧subject改写或自动重放。

新增34项后端＋1项前端。初始三个测试确认为预期红灯：child固定root拒绝、publisher
跨主体借用、冷待审仍列restore动作；最小接线后转绿。Web新原因说明也先观察缺失红灯。
部分补充分支首次即通过，未称为逐项TDD。04旧“child拒绝”测试迁移为真实已加载会话
的inactive_child拒绝，保留参数/名称/身份/环境篡改及零执行/零新借用断言。

组合测试发现close竞态，两次正式App复现NodeCancelledError；独立资源测试确认
control已请求但manager取消抢先于watch_stop时仍透传CancelledError。单一修复限定
resources.py：记录关闭来源取消次数、只消费已知停止取消、保留其他取消；收束中的
借用不再追加manager取消，关闭仍等待它。新增4项资源回归同时验证外部取消和原异常
身份。修复后App close正常stopped；一次后续测试错误调用了已关闭的公共App查询，
改为检查仍在内存的会话owner，未放宽关闭API。三处取消点核对：borrow.watch_stop
保留自有来源计数，_release记录关闭取消，ConnectionOwner.close只取消其自身SDK
拥有任务，后者不消费工具任务控制，不修改。未把外部取消一概转换成普通工具错误。

| 命令/检查 | 实际结果 |
| --- | --- |
| MCP根/子/恢复/资源＋旧冷审批/子重启组合定向 | 114 passed in49.46s；之后新增HTTP/连接创建停止专项，最终子实例19 passed in13.75s |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q` | 3231 passed in187.83s |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | 44文件349 passed in4.39s；TS/Vite成功，保留既有chunk>500kB提示 |
| compileall / pip check / AST及500行 / diff --check | 154份生产Python通过；No broken requirements found；exit0 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp05-20261020/wheel-final` | exit0，284文件；154份生产Python与当前源码逐字节一致 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp05-20261020/wheel-final --output build/mcp05-20261020/install-final.json` | exit0；源码外全新venv原生工具/前端API/Find-Grep/审批恢复/SQLite重开/CLI全passed |
| 安装包子实例/冷待审专项 | 另一全新venv，子两次调用1/2、审批暂停保留、在途App关闭stopped；旧MCP待审拒绝直接恢复、取消后新调用重新审批；旧调用重放0、全部进程退出、供应商0 |

定向命令：`PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests/test_mcp_subagents.py tests/test_mcp_recovery.py tests/test_mcp_child_persistence.py tests/test_mcp_crash_recovery.py tests/test_mcp_resource_failures.py tests/test_mcp_lifecycle.py tests/test_mcp_agent_tools.py tests/test_mcp_agent_boundaries.py tests/test_mcp_application_resources.py tests/test_native_session_lifecycle.py tests/test_restart_approval.py tests/test_subagent_restart.py -q`。

安装专项：`.venv/bin/python build/mcp05-20261020/verify_install.py build/mcp05-20261020/wheel-final/naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl tests/mcp_discovery_fixture.py build/mcp05-20261020/child-install.json`，exit0。
报告archive-final/install-final/child-install及pytest-final.log位于build/mcp05-20261020。
三份报告对应wheel SHA256：`8963e9bde7f01ba1b540885dc3f11b9c77bfcc84423ff98875b2b603e07dd827`。

真实SDK stdio、legacy HTTP JSON/SSE均验证子调用/关闭；根/子独立PID、子跨轮次复用、
记忆跨合法参数及同会话根/子范围、显式角色隔离、审批暂停保留、排队/连接中/在途/
待审批停止、兄弟隔离、应用关闭、普通错误继续、必要根保存/子checkpoint/新清理故障
均有确定性断言。子进程os._exit分别在child待审批及MCP副作用后中断，SQLite重开后
只补父结果未确认说明，不复活child、不新增tools/call；检查外部stdio进程EOF后退出。

SDK服务是合成夹具，模型为离线实现；不证明真实认证业务、供应商效果、Windows/Linux
原机或五平台交付，也不证明用户已掌握。旧00—04和并行学习工作保留，未修改真实
配置/用户数据库/依赖版本，没有commit/push/tag/PyPI发布。唯一下一片06：在既有
CLI/Web呈现server/原工具/根子角色、限制/长结果归档与诊断，先说明方案待用户确认。
