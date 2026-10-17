# Spec085-15：工具迁移终检与全项目 Windows 复盘（本机终检与兼容性复盘完成，Windows修复/实机待验）

接续085-14。本片回答两个问题：085各切片有没有遗漏接线或残留旧入口；当前仓库在原生Windows上，从启动到保存续聊还会卡在哪里。先复用已有测试和真实主链，不新增工具平台、沙箱、UI或SubAgent实现。

后续决策（2026-10-16）：用户确认取消模型配置的私密权限门槛，按[Spec004更新](004-local-model-config-format.md#字段规则)移除load_model_catalog的模式位拒绝；普通文件、真实读取失败、内容校验及错误脱敏保留。不新增配置ACL检查，不改真实配置文件；下面原085-15探针结果为取消前快照，会话/归档与锁仍独立待处理。

同日继续确认：会话数据库、锁及附属文件也取消私密模式位门槛，见[Spec070更新](070-persistent-session-implementation.md)。_check_store_file只保留普通文件/链接检查，实例独占/schema/实际IO不变；原模型/会话模式位探针保留为历史。当前无已确认的多应用进程共享同库需求，同进程Web多会话及前台SubAgent不属于该场景，不提前改为共享架构。

## 需要检查的当前链

正常例子：用户在Web会话启用powershell，要求执行测试。

```text
web.app/create_builtin_tools(load_tool_settings)
 → 原模型配置与持久应用装配
 → 会话活动集合：只启用用户选择的名字
 → arun/ToolSet：固定本批工具定义和提示词
 → prepare_local_batch：原command、模式、Allow/Deny及审查对象
 → 官方整批HITL：没有批准时用户命令零执行
 → 恢复已保存决定/ExecutionAuthorization：不再调用Auto
 → 工具require_tool_permit
 → PowerShellAdapter.build_request + build_environment
 → HostProcessRunner：进程/管道/consumer分别确认
 → OutputAccumulator + ShellOutputArchive：尾部/原始/条件文本视图
 → ToolMessage按call_id回填/观察投递
 → checkpoint与SessionStorage.finish_run确认
 → 最终响应，重启后恢复安全完成会话
```

故障例子：进程结束但消费者无法确认收尾。
`HostProcessRunner.run→ProcessCleanupError→ToolError保留上抛→SessionService标记必要核对→失败终局/必要保存`。不能变成普通ToolMessage后继续，也不能靠清理旧类型时删掉未确认资源、未配对调用和保存保护。普通非零命令/ToolException则按现有合同反馈模型，并分别验证事件和保存。

## 文件与具体工作

| 文件/入口 | 本片检查及预期产物 |
|---|---|
| tools/registry.py、builtin.py、settings.py；application/tool注册入口 | 八内置工具的schema、coroutine、ToolSpec、严格设置、描述和默认六文件活动集合逐项接线；运行中注册/切换按既有API，无旧专用开关。 |
| agent_runtime/tool_execution/及application/session_approval.py | 统一ToolError、保存决定、grant与permit分工，批量零提前执行、恢复不重审、串并行顺序、停止/取消/异常身份；复用现有确定性测试。 |
| tools/、scripts/、tests/、当前README/Web说明/样例 | 搜旧导入/包装/配置/错误类型/重复审批；逐个追实际消费者再删除已替代且无调用者的内部逻辑，迁移测试并保留行为断言。保留旧受管日志格式、真实同步入口、框架注册hook。通用进程故障仍用bash_event的旧诊断标签也列入核查，不能把名字自动当无用代码。 |
| model_access/__init__.py:load_model_catalog | 原POSIX私密模式位检查已按用户后续决定移除；读取配置使用正常OS访问与内容校验，不另建Windows配置ACL门槛。 |
| application/session_storage.py:SessionStorage.__init__/_check_store_file/finish_run；persistent.py、async_persistent.py | 私密模式位门槛已取消；仍需兼容顶层fcntl、flock/O_NOFOLLOW、普通文件/链接检查，保留同库独占和恢复状态。 |
| tools/write.py:_write/_replace_temp、edit.py:_copy_to_temp、paths.py、permission_rules.py | fchmod、临时文件占用/原子替换、盘符/大小写/UNC、软链接/junction真实目标；Deny不能因平台路径表达差异被绕过。 |
| tools/output.py:_directory/_snapshot/clear_shell_outputs | POSIX私密目录判断在Windows的等价保护、旧新日志读取/双份额度、精确预览清理；不删除用户既有文件或仅跳过安全检查换启动成功。 |
| tools/processes.py:run/_terminate；bash.py、powershell.py；CLI/Web入口 | Windows实际事件循环的subprocess支持、Git Bash/pwsh/5.1发现、启动编码/退出码、taskkill子树/持管道、超时/停止/外部取消；假transport和参数测试与实机证据分开。 |
| 新docs/research/windows-compatibility-audit.md | 问题按阻断程度列证据、函数、复现命令、影响、必要修复及待验证项；附macOS/Linux/Win5.1/Win7矩阵。环境未提供时明确“实机待验”，不填模拟成功。 |

复盘不直接新增一个泛化OS抽象层。已有替代关系明确的小清理可按本Spec迁移；Windows锁、ACL或必要依赖如存在多种语义/成本选择，先给独立修复小Spec讨论，再实现。不能为了完成一张矩阵，擅自安装依赖、提权、改执行策略或删私密访问保护。

本片实施细化：session_service.py两条运行/审批恢复异常路径的session_manual_review_required已由通用ProcessCleanupError触发，仍有bash_event诊断字段。没有生产或测试消费者按该字段读取；改为统一session_event，删除仅为选择旧标签的分支，不改异常身份、核对标志、控制或必要保存。新增tests/test_session_cleanup_diagnostics.py验证sync/async×普通运行/审批恢复四条真实应用路径；先红灯再修改。其余平台阻点先用隔离探针复现并记录，不在本片修改锁、ACL或路径授权语义。

## 验收与结果边界

1. L1文件/字段存在、L2真实实现、L3实际接线；正常与故障两条链都可从入口追至事件、状态和持久保存。
2. 迁移清单逐项标记删除项与保留项及实际消费者；旧测试迁到新入口，不能删断言、放宽结果或skip来变绿。当前文档一致，历史Spec/课堂保留版本边界。
3. 复用全量pytest、前端测试/build、compileall/pip check、AST/≤500行、实际配置loader/注册及diff检查。真实rg/fd与Shell案例注明程序版本，不将假模型当远端供应商验证。
4. Windows实际检查安装/导入→CLI/Web启动→模型正常读取/私有存储→文件/搜索/双Shell→人工审批/Auto/模式切换→停止/保存/安全重启恢复；Linux/macOS按相同合同回归。不能把macOS上改sys.platform或假taskkill当实机结果。
5. 本片可以先交付“工具迁移终检结果＋Windows阻点及修复方案”；原生Windows直接可用的声明，必须等相关修复和实机链完成。无Windows/Linux执行环境时，明确未验证范围，不将WIN1整体标完成。

完成后再按独立Spec086推进前台一层SubAgent；沙箱、工具管理/模式快捷键及专用展示仍按各自待办，不混进本片。

## 本片结果（2026-10-16）

完整证据、正常/失败链、删除与保留消费者、官方来源和平台矩阵见[兼容性复盘报告](../research/windows-compatibility-audit.md)。本片仅修改session_service.py两处诊断标签分支，新增四例回归；不改执行/权限/控制/存储协议，无新生产模块。旧符号/工具顶层定义引用及框架hook接线已核查；read/ls/write/edit是线程回退文件IO，四个进程工具是native async，不能把八工具都写成原生异步。

四例先红灯（sync run/resume缺session_event，async已有部分状态路径通过）→统一日志字段→四例转绿；相关15文件168 passed in7.82s，全量1597 passed in48.17s。前端9文件27 passed及TypeScript/Vite186模块通过；compileall/pip check、272个Python AST/≤500行、实际样例loader/注册与diff检查通过。无真实配置/依赖修改或提交/push。

13条隔离探针确认fcntl入口、模式位、fchmod、O_NOFOLLOW、命名空间规则与当前Uvicorn分支；都是macOS/模拟API事实，不是Windows/Linux原机证据。当前原生Windows仍有启动阻断、私密ACL检查、覆盖/Edit和Deny命名空间问题，WIN1保持未完成。另已复现正常前序任务仍运行却被排队期限判故障，原086移除期限目标仍待独立实现。

下一步先讨论Windows安全文件/数据库锁的具体实现范围；文件属性/路径授权及长命令排队独立切片，不静默合入本片。SubAgent086、UI和完整沙箱继续另排。

后续配置权限取消验证：十例回归先8 failed/2 passed，模型三文件转绿88 passed in3.93s；全量1607 passed in48.71s（/tmp/naiyue-config-permissions-full.log），前端27及构建通过，compileall/pip check与273个Python AST/≤500行通过。只删除model_access加载器的私密模式位判断，保留其余校验；不改变会话/归档/锁的语义，后续Windows范围已排除模型配置ACL校验。
