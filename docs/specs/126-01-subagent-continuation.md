# Spec126-01：新建与按ID继续子Agent的执行闭环

日期：2026-10-22。主合同见[Spec126](126-subagent-continuation.md)，分片见[文件地图](126-subagent-continuation-file-map.md)。用户授权说明做法后实施本片，完成再介绍下一片。本片已完成本机验收，实际结果在第5节单列。

## 1. 本片行为

主Agent继续调用原subagent工具，agent/task必填，agent_id可省略或null。未传ID时创建干净子历史；传完整64位小写十六进制ID时，继续同一逻辑父会话、同一工作区中已成功且保存确认的实例。角色必须一致，旧格式、失败、停止、运行中及未确认实例拒绝继续。

模型可见结果为JSON对象：agent_id、agent、status、report、truncated。artifact保留既有字段并增加agent_id/delegation_id。原报告仍完整保存在子历史中，max_result_bytes限制整个JSON信封序列化后的UTF-8字节；固定身份字段都容不下时，在子模型执行前拒绝。max_result_lines及按完整行保留前缀的规则继续生效；第一行过长时允许在字符边界裁剪。

每次新委派重新计算max_model_calls，审批暂停/恢复共用本次计数；不配置时不限次数。旧历史的usage不再进入本次报告合计。模型、历史和稳定agent_id属于实例；RunControl、审批Future、委派输入/结果ID和当前父调用绑定属于本次委派。

Web工具卡按信封展示实例ID、报告正文和截断提示，原始JSON可展开；旧纯文本仍可读。CLI现有成功工具结果不作为主回答自动打印的规则保持，子历史查看/错误提示仍用原入口。本片不新增侧栏结构、子输入框、只读列表工具或续跑按钮。

## 2. 当前模块与真实主链

| 入口/函数 | 输入和来源 | 输出、状态及消费者 |
| --- | --- | --- |
| tools/subagent.delegate | 模型agent/task/可选agent_id，加框架ToolRuntime | 原require_tool_permit后交应用会话端口 |
| SessionSubagentControls.ainvoke_subagent | 当前运行上下文、父state/真实call ID | 定位拥有者AgentSession和已登记角色，不接受模型提供thread/path |
| AgentSession.prepare_subagent_instance | 原批次call、execution permit、当前父run/工作区 | 校验参数/许可；新委派ID、稳定实例ID、当前控制器；同ID占用返回busy，原call不可重放 |
| subagent_records.admit | 角色定义、可信实例记录、最新子checkpoint | 同作用域/指纹/清洁历史校验，版本CAS写running，确认本次claim所有权 |
| AgentSession.ainvoke_subagent | 已claim实例及共享角色图 | 锁内再次检查父/子停止，再建唯一driver；不为每次实例编译新图 |
| ainvoke_subagent_step | 新delegated_task/seed，或本次审批Command | 同child_thread_id执行；新委派before_agent重置本次计数，审批恢复保留 |
| AgentSession._subagent_steps | 模型—工具循环、实时观察及审批 | 确认无pending工作，保存final_snapshot，产生本次有界结果 |
| subagent_records.settle | driver终局、MCP资源收尾结果、已拥有claim | 成功写pending_parent及checkpoint引用；失败/停止保留不可续跑状态；输入前终止恢复旧记录 |
| SessionExecution._afinish_run | 子driver/审批均已收尾 | 释放活动实例，保留可信登记；内存模式确认ready，持久模式交必要保存 |
| collect_chat_updates → SessionStorage.finish_run | 本会话当前run登记范围和checkpoint、父状态/档案候选 | 原事务内写档案、确认子head与登记/最新checkpoint一致、pending_parent转ready |

固定模型的正常验证例子：首次“check this task”→“first report”，下一轮同ID委派“second task”→“second report”。子历史为4条消息，前两条ID不变；第二次模型看到原历史。max_model_calls=1时两次均能成功，第二次usage只统计其11个token，不重复加入首次5个token。这是离线正式应用入口测试，不是供应商实际推理证据。

## 3. 必要保存与失败链

持久登记表为naiyue_subagent_instances，schema13→14沿原备份/短事务迁移；旧库只建空表，不推断旧实例的执行身份。内存模式由AgentSession持有同形状字典；活动driver继续只属于ActiveRun。

正常次序：claim running → 子sync checkpoint及资源收尾完成 → pending_parent → 父状态/档案事务确认ready。父保存失败不提前开放继续。同一活动父run内，在子保存及收尾已确认后，可再次委派其pending_parent实例；其他run和应用重开不能借用未确认状态。

故障注入实际覆盖：

- SQLite拒绝claim写入：子模型0次，原必要保存异常传播，活动实例进入finalize并释放父busy。
- SQLite拒绝子终局登记：子结果不对外报成功，登记保留running/未确认；不重放已发生的工具效果。
- 父聊天归档写入失败：事务回滚，子保持pending_parent，父保存阻断，不标ready。
- claim IO执行期间取消：等待已开始的SQLite操作结束，只有确实成功的claim才发布给活动实例；settle撤销自己的claim，旧ready及checkpoint保持，没有新模型/子输入。
- 输入前停止父run或仅停止该子实例：driver启动前重验两份停止状态，恢复旧ready，不追加新seed。
- 审批恢复时已达模型上限：仍不能继续模型调用；已获批工具的执行事实不回滚。

普通观察失败沿原诊断合同降级，不能吞掉登记、checkpoint、资源收尾或父必要保存错误。单次usage取当前seed/reply ID的新AI消息。

## 4. 引用、归属和旧链迁移

显式agent_id只是定位符。原生列表/详情、SQLite列表和重生成引用、归档均核对当前会话可信登记的session/角色/path/thread；一次被拒绝的父调用不能认领外部实例。删除中的纯父历史解析默认不接受显式目标，存储另补入本会话登记的child threads。

实际回归：B会话引用A的ID被拒后，B无该实例列表、详情不可读、无外部子历史归档；删除B后A的checkpoint和正文保持。历史无agent_id的新建调用继续按session/batch/call派生身份，保持旧历史可读。

首片已前置本次run触碰范围归档、基础引用修剪保护和会话删除，防止新表指针悬空。126-02仍负责跨重开、压缩、最后回复重生成退役及修剪的组合闭环与故障验收；本片不把这些整体能力报告为完成。

已替换旧的两字段参数硬比较、一次调用即永久占用实例的判断、父报告纯文本合同、子历史全量usage重复相加、原thread_limit随实例累计的用法。保留共享角色图、内部审批Command、原权限与人类授权来源、必要saver适配及原同步短IO/锁。

测试基础设施仅随真实schema迁移：storage_backup_fixture/legacy_store_schema构造旧库时移除schema14新表，保留真实历史版本结构；原版本、恢复、身份和异常断言迁移到当前版本。已有报告断言改读JSON的report字段，未删除审批/停止/副作用检查。安装验收的旧库构造沿同样版本规则。

## 5. 验证与证据范围

初始行为红灯：新ID字段/模型可见信封/登记表及迁移缺失。后续故障测试分别先确认claim IO取消和跨会话引用缺口，再修生产代码。核心最终23项、独立schema回归64项通过；Web信封显示先1红后专项8项及全前端50文件412项通过，TS/Vite构建0错误（保留原体积提示）。

最终实际结果：

- `.venv/bin/python -m pytest -q tests --tb=short`：**4164 passed in280.50s**。通过子进程PATH加入build/release-native-0ff7df8/bin的项目原生fd；首轮28个旧版本/旧报告消费者断言失败日志保留，迁移后28项重跑及最终全量通过。
- `.venv/bin/python -m pytest -q tests/test_subagent_continuation.py tests/test_subagent_continuation_persistence.py --tb=short`：23 passed in1.85s；schema专项64项通过。安全/架构复核发现的跨会话引用、claim IO取消和子scope输入前停止均已回归关闭。
- `npm --prefix frontend test -- --run`：50文件412项；`npm --prefix frontend run build`：TS/Vite通过，保留原体积提示。181份产品＋41份脚本AST/500行/compile、pip check与diff检查通过。
- `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec126-01-0g305v08/dist-final`：标准wheel构建通过，311文件；Twine通过，181产品源码、前端资源及README元数据字节一致。SHA-256：d3dca79fcf7aa61d5b3def6359303a2b569c67a9c6ebdc3bd9806b54eb69a215。
- `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec126-01-0g305v08/dist-final --output build/spec126-01-0g305v08/install-final.json`：Mac ARM64源外干净安装通过，原生工具/Web-CLI/审批/SQLite/备份还原/现有MCP及后台任务验收通过，MCP合成业务调用8次、进程收尾通过，0供应商。安装门禁仍以既有闭环为主，新续跑的专用跨重开安装场景留126-04，不据此提前声明已完成。

证据目录：build/spec126-01-0g305v08（backend-final.log、build-final.log、dist-final、install-final.json、verification.json）；初始/故障红绿日志在build/spec126-01-*.log。未对本片新工具卡做真实浏览器截图，交互组合验收留126-04；前端本轮为组件行为测试及正式构建。

本轮未修改真实环境配置、用户数据库、依赖或版本；未提交、push或发布。固定模型和临时SQLite证明本机执行/保存合同，真实供应商、其他平台原机及业务效果另计。学习笔记和其他会话成果保留。

唯一下一片：126-02，收束正常重开、父/子压缩、重生成退役、checkpoint修剪/删除与备份还原的一致性验证；不进入126-03列表工具或新侧栏能力。
