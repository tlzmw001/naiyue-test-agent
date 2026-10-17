# Spec085-09：Write自动建父目录与Ls反馈（已实施）

接续085-08。2026-10-16用户确认按本方案实施09，已完成本机回归，验证见文末。无图片/Edit模糊匹配、进程/Shell、沙箱或前端新功能。

## 当前事实

`paths.prepare_file_subject`已能以最近现存父目录作为anchor，为尚不存在的文件/父目录准备权限。权限、人工审批和Auto都不创建目录。Write的`_resolve_write_target`仍要求parent.is_dir，故授权后遇到缺父目录会失败。该函数也被Edit调用，不能全局放松父目录要求。

`ls._list_directory`已有一层扫描、排序、目录后缀、limit/字节预算与候选权限过滤；单项stat失败只记日志后跳过，结果未必说明不完整。开始扫描/单项处理缺合作停止检查；打开目录的OSError仍是通用提示。

## 准备修改

| 文件/入口 | 修改及边界 |
|---|---|
| `tools/write.py::_resolve_write_target` | 加内部keyword `require_parent=True`，只在Write准备创建父目录的首次检查传False；默认值和Edit的现有调用保持原要求。真实目标/常规文件检查保留。 |
| `tools/write.py::create_session_write_tool.write` | 仍先require_file_target验证许可，随后把已有permit.subject传给_write；不重新解析规则、不重新调用Auto或人工审批，不新增callback/权限对象。更新工具说明，移除“父目录必须已存在”。 |
| `tools/write.py::_write` | 先验证/编码输入，再取真实目标级_reserve_target，授权后逐级创建缺失父目录。创建前后和提交前复用verify_file_target核对原逻辑链接、真实路径与anchor；创建完成后恢复严格父目录检查，再走原临时文件/快照/flush/fsync/os.replace。 |
| `tools/write.py`错误反馈 | 创建目录失败具体ToolException；已经创建的目录可能保留，失败提示不声称“没有副作用”，不做递归回滚删除。提交结果未知仍沿原具体提示。不同会话的同目标busy合同保留。 |
| `tools/ls.py::_list_directory` | 保留排序/预算/过滤，扫描及条目处理之间读既有_run_control.requested。已得到允许的名称则返回带stopped/incomplete说明的部分结果；尚无结果则具体ToolException，不伪装empty。同步OS调用只能在调用返回后的检查点响应停止。 |
| `tools/ls.py`反馈 | 单项消失/不可访问同时记录安全日志及有界跳过原因；目录不存在/不是目录/OS拒绝/普通IO错误区分提示。名称转义与原权限跳过报告保持。列表不完整不能报告为空目录/完整清单。 |
| 测试及文档 | 扩展现有Write/Ls/权限/活动停止测试，必要时新建边界文件避免500行；同步当前设置注释/工具说明/Spec与交接。不新增配置开关、生产模块或依赖。 |

不把mkdir放到公共目标准备，否则拒绝/待审批也产生目录；不放到Edit共用默认检查内，否则编辑也会偷偷创建父目录。复用原subject直接传参用于目标身份核对，与已经确定的授权决定分开。

接线细化：`_write`的subject为必传内部keyword，不保留无subject的生产分支；原直接调用此IO函数的Write/Edit并发单测以prepare_file_subject构造确定性目标后传入，原状态/异常身份/冲突/清理断言保留。这不是新增授权入口，正式工具仍只接受Authorization发出的permit。父目录逐级mkdir、不用parents=True跨过中间检查；每级前后验证原subject，创建失败提示可能已有目录、文件未提交，不递归回滚。Ls枚举阶段停止时尚无经过过滤的可交付名称，返回具体错误；格式化阶段停止则保留此前已授权名称。stat故障复用skipped_scope_message并附incomplete说明，既有权限跳过/预算/排序不改。

## 正常与失败链

示意调用`write(path='reports/2026/result.md',content='ok')`，父目录尚无：Permission.prepare_local_batch记录workspace/真实目标/最近现存anchor→规则/模式授权（Ask则整批暂停，目录创建0次）→Authorization发permit→write入口require_file_target→_write验证字节/编码并reserve→创建reports、2026并核对→在真实父目录写临时文件→原子替换→同ID success ToolMessage。不存在额外模型/审批调用。

若审批期间已有父级换链，require_file_target拒绝旧许可，mkdir/写文件0次。若第一层目录已建而第二层创建失败，文件未提交但第一层可能保留；具体反馈说明阶段与可能已建目录，日志不打印路径/原异常。若最终replace后结果未确认，继续使用“结果未知，先检查”语义，不改为“未写入”。

`ls(path='docs')`列名时，a.txt正常、b.txt在stat前消失：返回a.txt和有界b.txt未列出的原因；全部条目未能列出时也报告不完整，不用empty冒充。若途中停止，返回已有允许名称及停止说明，原运行层在工具收尾后终止run，后续排队工具保持原停止结果。授权/跳过范围不放宽。

## 实施前测试

先观察缺父目录Write失败、单项stat错误未报告的红灯。新测授权前零mkdir/拒绝零mkdir；嵌套创建；编码/输入限额失败零mkdir；真实目标链接保留；创建前/中/提交前换链；半途mkdir失败反馈与临时文件清理；同目标跨会话busy；Edit仍要求已有目标且不建目录；Ls单项失败/打开失败/empty区别、停止阶段、部分输出/预算/转义及权限跳过。用事件同步，不用紧sleep窗口。最后全后端/前端/build/compileall/依赖/500行及文档/消费者检查，Windows另依WIN1实测。

## 实际接线与验证（2026-10-16）

只修改`tools/write.py`和`tools/ls.py`两个生产文件，无新生产模块、类、依赖、配置数值或权限/DB字段。Write输入编码/大小校验后，`_resolve_write_target(require_parent=False)`→`_reserve_target`→原subject核对→逐级mkdir前后核对→严格父目录检查→原临时文件/flush/fsync→再次核对原subject与目标→原子替换。普通父级创建失败与目标变化分别给固定提示，明确文件未写、目录可能已创建；提交未知仍由原_replace_temp处理。Edit的_resolve_write_target调用仍用默认True。正式工具入口仍先require_file_target，只把已验证的permit.subject直接传给_write，没有重做权限决定。

Ls枚举/处理间检查既有控制；枚举阶段或尚无允许结果时停止给具体ToolException，已有允许名称时给partial/stopped结果；工具返回后原运行层停止整轮、同批后续顺序Write回未执行消息。stat失败给固定原因disappeared/permission_denied/io_error、安全日志和既有有界跳过报告，结果标incomplete；打开目录的缺失/类型/OS权限和其他IO分别反馈。完整普通清单/empty/排序/预算/权限过滤保持。

新增`tests/test_write_parent_directories.py`和`tests/test_ls_feedback_and_stop.py`，最终29用例。初始23例为**15 failed, 8 passed**，确认缺父级/未报告stat错误/停止未接的预期红灯；实施后增加跨会话busy、同批Write→Read、anchor替换、临时文件失败、Auto零重审及大量跳过有界报告。旧Write直接IO测试显式构造并传subject，保留busy/大小与编码/原异常cause/提交未知/权限位/清理检查；旧缺父级参数例迁移成正向创建断言，其他拒绝项和原文件不变断言保留。无无subject兼容路径，无conftest/skip或放宽断言。

- `.venv/bin/python -m pytest -q`：**1391 passed in41.72s**。
- `npm test -- --run`（frontend）：**9文件、27 passed**。
- `npm run build`（frontend）：TypeScript检查及Vite **186模块**构建成功。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`：退出0。
- `.venv/bin/python -m pip check`：`No broken requirements found.`。
- 241个Python文件AST解析且均≤500行；当前样例经产品loader/七工具规则校验、未来样例TOML解析及`git diff --check`通过。

证据限本机macOS临时真实文件/假模型和事件同步故障注入；没有真实供应商/Windows或沙箱验证，没有改真实配置、提交或push。已建目录可保留和同步OS调用只能合作停止仍是明确边界。下一步[085-10 Edit匹配与反馈](085-10-edit-matching-and-feedback.md)供审阅、尚未实施。
