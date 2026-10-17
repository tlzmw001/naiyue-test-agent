# Spec085-08：Read文本、分页反馈与读取路径兼容

状态：2026-10-16按用户授权实施并完成本机回归。接续085-07；本片不实施图片、Write建目录、Edit、搜索/进程、双Shell或UI，不改真实配置和依赖。

## 当前链与改动目的

当前`PermissionMiddleware.prepare_local_batch`→`FilePermission.prepare`→`paths.prepare_file_subject`固定requested/resolved/anchor，按规则/模式决定；批准后`require_file_target`→`verify_file_target`核对，`read._read`执行有界读取，异常由ToolException/官方ToolError回填。Read默认2000行/64KiB，已有offset/limit；短文件原样返回。当前任一巨行会抛错并丢弃此前完整行，首行也不返回前缀；路径只支持相对/绝对/~。

本片保留权限、审批、输入schema及额度配置，只完善读取行为和反馈。单文件仍≤500行，不新增生产模块/类/依赖。参考本地Pi的read.ts/path-utils.ts：先返回巨行前的完整行，有限读取文件名变体；Pi巨型首行提示Shell，本项目沿已确认方案直接返回可容纳的UTF-8前缀。此为源码对照与项目选择，不宣称参考项目同样返回前缀或使用本项目的权限模型。

## 文件与函数

- `tools/read.py::_read`：保留快速短文件分支及按块跳过行；页循环遇到巨行时已有完整行则先返回它们，next_offset指向巨行。本页首行已超预算则返回完整UTF-8字符前缀，明确line partial、returned_bytes、byte_limit、next_offset=unavailable及行内余文未返回。仍不支持行内游标，不把下一行冒充未读半行的续读位置。
- `tools/read.py::_decode_read_text(content, *, partial=False)`：使用标准库UTF-8 incremental decoder，仅partial时允许尾部字符未完；中间非法字节、非文本控制字节仍错误，不用errors=ignore。完整文件尾部不允许残缺字符。配置预算不足放下一个字符时可返回0字节前缀并明确限制。
- 页头保留`[read: lines A-B; next_offset=N/none]`；仅有未读内容时追加原因及同path/offset/limit续读说明。区分行数限制、累计字节限制、下一行自身超长，不扫描全文件统计总行数。正文预算不含有界元信息；短文件和显式空页原合同保持。
- `_read`区分打开阶段FileNotFoundError/IsADirectoryError/PermissionError和其他OSError，给固定可行动提示，日志只有异常类型，不回显文件正文、路径或原异常。目标准备失败仍保留现有FilePermission提示机制。
- `tools/paths.py::prepare_file_subject(..., *, allow_read_aliases=True)`：原路径优先，lstat只在FileNotFoundError时尝试有限变体：@前缀、Pi列出的Unicode空格、AM/PM窄空格、Unicode NFC/NFD、直/弯引号及有限组合。只选已存在的路径；无匹配沿原缺失目标行为。原目录、断链、权限/IO故障不得用别名回退掩盖。不做任意拼写模糊搜索、自动大小写猜测或目录遍历。
- 变体选择仅用于read操作，在权限审查前发生；requested保存选中的逻辑路径，resolved保存真实路径，原始模型参数仍在PreparedToolCall.args/subject.args。原路径存在时即使变体也存在仍选原路径；原目标不合类型时直接错误。Write/Edit/list/search保持原路径语义。
- `verify_file_target`调用prepare时显式关闭变体选择，核对已保存的requested/真实路径/anchor；批准后删除或换链不会改读另一个文件。无新增授权字段、DB列、回调或路径注册表。
- `tests/test_read_text_pages.py`、`tests/test_read_path_aliases.py`：新增确定性边界/图集成测试。迁移`test_read_tool.py`、设置与入口/Skill测试中的已改变输出断言，保留旧权限、严格UTF-8、页边界及错误配对检查，不修改conftest。
- 配置注释和当前README/交接/Spec085状态同步；配置数值/真实配置不变。085-09只讲方案，不提前实现。

## 正常与失败例子

`read(path='large.txt',offset=1,limit=2000)`，文件前两行正常、第三行100KiB：原权限节点准备/允许→require_file_target核对→_read返回1—2行及next_offset=3、下一行超预算原因和续读参数。随后offset=3只返回第3行≤64KiB完整字符前缀，说明余文遗漏、行式offset无法继续其后半行，需Shell分段检查。偏移到第4行仍可按块跳过第3行，内存不随其全长增长。

`read(path='@outside-link.txt')`原路径不存在、去@后是外部目标链接：prepare在读正文前选择链接，保存逻辑/真实路径，外部Ask→整批暂停、IO次数0→批准→verify禁用别名搜索→读取获准真实目标。若链接等待中改指另一个文件，原许可失败、同ID error消息、两边正文均不读。若命中Deny，即使配置目录Allow/Bypass也不读取；隐藏/私密变体仍按实际目标默认Ask。

内容中间非法UTF-8/控制字节仍抛具体ToolException→官方同ID error→模型下一轮；巨行截断为正常的部分结果，不伪装执行失败。GraphInterrupt/UserStop/取消不在本片改写。

## 验证计划

先新增测试并确认当前巨行行为/路径兼容缺失的正确红灯，再最小修改；验证完整行保留、字节边界内的多字节字符、非法中间字符/残缺文件尾、CRLF、无末尾换行、配置小预算、跳过巨行和有界read/readline。路径验证原路径优先、有限变体、原路径故障不回退、非Read不变、Deny/隐藏/外部审批、resume不重选和同ID反馈。全后端、前端test/build、compileall/pip check、AST/500行、样例loader及diff检查。仅本机临时文件/SQLite与假模型，不以macOS测试宣称Windows或真实供应商验证。

## 实际接线与验证（2026-10-16）

生产只修改`tools/read.py`和`tools/paths.py`，未新增类/生产模块/依赖/配置数值。`_read`保留原短文件快路径，分页时追加真实停止原因和续读参数；巨型首行按partial解码返回前缀。`prepare_file_subject`只在read且原路径lstat为不存在时选有限变体；保存选中的requested与真实resolved，原模型参数仍由已有args字段保存。`verify_file_target`内部以`allow_read_aliases=False`调用prepare，不在执行时重新选择。

两个新测试文件合计39用例，初始**28 failed, 11 passed**，红灯确认为巨行失败、缺少新反馈和别名准备未接；随后39全部通过。迁移5份旧测试的已改变输出合同：巨行由error变带partial说明的success，分页保留原页头并增加原因/参数。完整内容、状态、消息配对、预算、权限、编码和敏感错误不回显断言仍保留；未修改conftest或skip，未为旧语义保留分支。相关130例通过后全量回归。

- `.venv/bin/python -m pytest -q`：最终**1362 passed in40.49s**。
- `npm test -- --run`（frontend）：**9文件、27 passed**。
- `npm run build`（frontend）：TypeScript检查及Vite **186模块**构建成功。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`：退出0。
- `.venv/bin/python -m pip check`：`No broken requirements found.`。
- 239个Python文件AST解析且均≤500行；当前设置样例经产品loader/七工具规则校验通过，未来样例TOML解析通过。

测试为本机macOS临时真实文件和假模型的确定性证据，含权限前零正文IO、批准/拒绝/等待换链、Deny优先、原路径故障不回退、有界read/readline和下一轮模型继续；无真实供应商、Windows或沙箱验证。未改真实配置、未提交/push。下一步审阅[085-09 Write与Ls](085-09-write-parents-and-ls-feedback.md)，不自动实施该片。
