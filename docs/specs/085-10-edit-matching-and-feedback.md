# Spec085-10：Edit匹配、换行与结果详情（已实施）

接续085-09。用户已授权本片实现、验证后讲085-11；本文将总Spec已确认的Edit能力落到当前代码。公开path/edits参数、权限/批量审批/控制保持，标准库可实现的部分不新增依赖。

## 当前入口与目标

当前所有Edit实现都在`tools/edit.py`，没有edit_scan.py：工具入口require_file_target→_edit→_reserve_target→_scan_target(严格UTF-8/精确字节匹配/唯一性/摘要与stat快照)→非重叠检查→_copy_to_temp(分块复制/再次摘要及身份核对/临时文件)→_replace_temp→简短文本。现有源文件无全长限制，输入全部oldText/newText合计有限；这些合同保持。

目标增加有限Pi归一化、LF/CRLF/BOM兼容、首个修改行、局部diff/预览及预算内完整patch。精确唯一优先；精确多处立即歧义，不让模糊选一个。缺精确匹配才尝试模糊，不是编辑距离或模型选位置。

## 函数与文件

| 文件/函数 | 具体职责与调用方 |
|---|---|
| `tools/edit.py::_scan_target` | 返回每个输入的精确源起点（未命中为None；第二处立即歧义）、首种换行及原文件digest/size/stamp/mode；BOM不参与匹配但计入摘要/原坐标。缺匹配由计划层统一判断，多处精确/非法UTF-8和文件变化仍阻止写入。 |
| 新`tools/edit_matching.py::prepare_edit_plan` | _edit拿原扫描结果调用；决定是否需要第二遍有限归一化，汇总所有edits的原文件字节区间和编码后的替换，唯一/重叠校验、必要的局部旧新行片段。只返回内部计划，不读配置、做审批或提交文件。 |
| 同模块`scan_normalized_matches` | 需要模糊时单次分块扫描同时检查全部相关模式；换行、行尾空白、NFKC、引号/破折号/空格变体。以有界规范化单元保存“归一化位置→原始字节区间”，处理跨块组合/字符展开，不能把半个展开单元映射成整字符并误改。扫描前后与第一遍快照核对。 |
| `tools/edit.py::_copy_to_temp` | 消费计划中原字节区间，保留未修改的原始字节，只写替换区域；原BOM保持。替换内新行采用匹配区首种换行，再退文件首种，再LF；不全文件重写。复制时收集有界变化行并在替换前构造反馈，不新增全文扫描。原有冲突/身份/临时文件/原子提交保持，并在提交前沿原subject核对逻辑链接。 |
| 新`tools/edit_diff.py::build_edit_feedback` | 消费复制时由EditFeedback收集的有界变化区，标准库生成局部unified diff、firstChangedLine、preview及完整patch；不在成功后重新读取已被别的操作修改的文件，不借审批预览提前读未授权原文。 |
| `tools/edit.py::_edit/工具入口` | _edit协调扫描→计划→_copy_to_temp（收集/准备有界反馈→身份复核→提交）→返回已准备反馈。工具改原生response_format=content_and_artifact，返回(content, JSON artifact)，原ToolSpec/ToolNode生成同ID ToolMessage。内部计划可含bytes，artifact只含可保存JSON，不能把内部快照/全文直接塞给模型。 |
| `tools/settings.py::EditSettings`及样例 | 接总Spec既有五键：max_normalization_buffer_bytes/max_affected_line_bytes各1MiB，max_diff_bytes/max_preview_bytes各64KiB，max_patch_bytes为1MiB；逐项注释，分别约束对应缓冲/反馈，不当作源文件大小限制。 |

新增两个生产模块对应归一化定位、结果构造两种实际职责；原精确扫描/提交继续留在Edit，不先搬出一个单独扫描包，不保留旧平行入口。完整源扫描/复核最多三遍（精确、必要的归一化、复制提交）；局部变化区在扫描时有界收集，不逐edit重复扫全文件，也不为diff再读一遍全文件。

同组任一项需要模糊时，归一化扫描复核本组全部oldText的歧义与范围，最终全部映射回原文件字节坐标；不是只检查缺匹配项而忽略与其他精确项的关系。全部精确唯一时不必进入该分支。

## 关键数据与结果

内部计划的当前结构（字段示意值）：

```python
{
    'matches': [(3, 22, b'...')],  # 原字节start/end、替换字节
    'snapshot': {'digest': b'...', 'size': 2000000, 'stamp': (...), 'mode': 0o644},
    'used_fuzzy': True,
}
```

ToolMessage.content包含成功、firstChangedLine及有界diff，模型据此继续；artifact保存JSON详情如firstChangedLine、diff、preview、预算内patch及各自省略/截断标记。已有注册表支持原生content_and_artifact，checkpoint保存；当前消息事件/Web只显示content，专用详情UI后排，不新增永久产物平台。

复制阶段的EditFeedback消费原字节流，保留每个变化区前后最多三行；单行和总收集量均有界。firstChangedLine按实际首个不同字节定位，不把oldText含有的未改变前缀行当首个修改行。变更区在收集时落入`regions`，不是计划阶段另扫全文。归一化工作预算计UTF-8原行、归一化文本和坐标槽；它限制工作数据规模，不宣称等于Python进程RSS。

## 正常与失败链

示例原文件带UTF-8 BOM、CRLF、`print(“旧值”)`，模型提供oldText=`print("旧值")`、newText=`print("新值")`：权限通过后才读正文→精确0次→归一化引号后唯一→映射回原文件的真实字节区间→准备反馈→临时文件分块替换，仅改该区间，BOM和其他CRLF字节保持→原子替换→content给首个修改行/局部diff，artifact给完整且预算内patch。该例无需额外Read审批。

精确或归一化后多处命中、多个输入范围重叠、原文件扫描/提交期间改变：具体ToolException，同ID error且不提交整组。需要模糊而归一化/受影响行超预算：本组不提交，提示缩小匹配范围；整组均精确唯一的调用仍可执行，不被模糊缓冲限制连带禁用。纯精确场景在超预算时不额外保证Pi式全归一化歧义复核，这个取舍沿用户已确认合同。

diff/preview预算不足只限制相应反馈，不能让已成功的精确修改被误报为未写；完整patch超过预算则patch_omitted，不给半个patch冒充完整。diff/preview/patch及成功摘要全部在提交前准备，提交后直接返回已准备的(content, artifact)，不保留提交后格式化分支。权限批准前只展示调用中的old/new参数，不能提前读原文件生成预览。

完整patch采用POSIX unified格式（a/b前缀、文件名后Tab、无尾换行标记），含空格文件名经本机patch验证。受影响源行超预算、CR-only行格式或文件名包含Tab/控制换行导致无法保证标准patch时，也明确省略整份patch并标记反馈不完整；编辑和有界文字反馈仍保留。不是返回半份patch。

## 验证顺序

先以原链测试写红灯：CRLF/引号等无精确匹配，及缺少firstChangedLine/artifact；随后先接有界匹配与原坐标，再接反馈。逐字节覆盖BOM/混合换行/Unicode组合与展开边界/跨chunk匹配/大文件、精确唯一/多处/缺失、模糊歧义/范围重叠、超预算精确可用与模糊整组不提交。检查完整patch可重建预期新文本，patch省略/局部diff截断有明确标记，无额外全文件扫描。故障注入原子提交/写冲突/换链/临时清理，content/artifact同步异步配对与SQLite JSON往返，模型输入/摘要不混artifact。最后跑原全量及前端/build/配置/结构检查。

## 实际接线与验证（2026-10-16）

新增生产模块edit_matching.py/edit_diff.py；修改edit.py/settings.py及当前配置样例五键。没有新增通用结果类、回调服务、依赖或DB字段。`_edit`内部入口改为完整EditSettings和必填subject，迁移原三个直接IO调用；工具公开path/edits保持。旧只有精确缺失就退出的分支由prepare_edit_plan替代，错误常量归匹配模块拥有，无旧平行匹配入口。原精确扫描、分块提交、reserve/摘要/stat/临时清理继续真实接线。

两份新测试38用例，首批11例**9 failed/2 passed**为预期红灯；迁移test_edit_tool和test_file_tool_settings的结果/设置断言，保留异常、顺序、配对、内容、权限、busy、未知提交和清理验证，无conftest修改或skip。另发现BSD patch不接受双引号含空格路径，先以回归测试复现，再单点改Tab分隔文件头。独立本机patch三例（无尾换行、BOM/CRLF、多处编辑伴行数变化）全部退出0、结果字节吻合。

正常链：原许可require_file_target→_edit校验/真实目标reserve→_scan_target→prepare_edit_plan（必要时单次scan_normalized_matches）→_copy_to_temp边复制边EditFeedback.feed→校验原digest/stat→build_edit_feedback→准备成功content→flush/fsync→原subject和目标stamp再核对→_replace_temp→返回原生tuple。精确两遍/模糊三遍源打开由测试计数；diff没有第四遍全文IO。

失败链：唯一/重叠/模糊缓冲/编码/变化等ToolException→既有ToolError→同ID error，原文件不提交；反馈格式化失败发生在替换前并清临时文件。软链接在反馈后换目标时原subject复核拒绝，两目标不覆盖。真正_replace_temp未知仍沿原具体结果未知提示，不误称未改。

原生ToolMessage保留JSON artifact，sync/async SQLite关闭重开均验证；Python canonical对象可仍带artifact，但官方OpenAI wire转换不发送它，现有token估算/摘要和Web/事件投影不额外消费它。没有修改这些消费者或增加新的序列化协议。

- `.venv/bin/python -m pytest -q`：**1429 passed in41.88s**；日志`/tmp/naiyue-08510-full.log`。
- 本片及旧Edit/设置定向回归92例通过；随后新增两例设置及换行合同纳入全量，两份新增文件合计38例。
- `npm test -- --run`（frontend）：**9文件27 passed**；`npm run build`：TypeScript/Vite **186模块**成功。
- `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、`.venv/bin/python -m pip check`、`git diff --check`：退出0；pip为`No broken requirements found.`。
- 245个Python文件AST正确且全部≤500行；当前注释配置样例经产品loader加载和五键覆盖测试通过。

## Windows稳定文件误报冲突修正（2026-10-22，0.3.0发布后）

用户反馈Windows上Read后Edit报冲突、期间未改文件。当前Read仅返回正文，不登记跨调用版本；Edit会重新扫描并建立自己的本次快照。源码缺陷在`_copy_to_temp`提交前：以`target.stat()`的完整stamp比较精确扫描时`os.fstat()`的stamp。CPython3.12 Windows句柄路径可分别报告ChangeTime与兼容保留的CreationTime，内容/身份/大小/mtime均未变时ctime也可能不同。

一手依据：[句柄ChangeTime装配](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c#L1036-L1040)、[路径stat将birthtime复制为ctime](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c#L2015-L2017)。本次在本机模拟这项平台差异，不将注入探针称为Windows原机实测；用户本次原始错误文本尚未取得。

修正限于同一提交校验：复制前保存路径完整stamp，与扫描句柄交叉核对dev/ino/size；复制中继续用完整句柄stamp和SHA-256核对原源文件；提交前完整路径stamp与路径前值比较。保留路径/权限重验、临时文件、同目标reserve、原子替换及清理；没有移除ctime保护，也不增加跨Read版本缓存或改公开入参。

新增`tests/test_edit_windows_metadata.py`，沿正式图执行Read→Edit→Read，覆盖精确及归一化两分支；稳定句柄ctime偏移时预期成功。另覆提交前路径五字段漂移、句柄ctime真实漂移及路径/句柄dev/ino/size不一致，均须拒绝、保留原文并清临时文件。首轮**2 failed / 9 passed**，两条正常分支按预期误报冲突；单点修正后定向169通过。局部stat替身只用于这份测试，没有修改conftest/共享helper。

发行安装门禁同步增强：`scripts/release_smoke.py`在原Web/审批/原生工具链中加入真实Read→Edit→Read，显式核对三个配对结果、diff首行和最终文件字节，报告`read_edit_read=passed`。临时输入固定写LF字节，避免Windows文本写入自动换行影响跨平台断言；不放宽原生工具/审批/重开原有断言。未来Windows CI和公开安装门禁会执行这条链，本轮尚未push触发远端验证。

同形扫描：`_scan_target`、复制中的两处句柄复核及`edit_matching.scan_normalized_matches`均为句柄与句柄快照比较，保留；`write`的反馈/提交stat同为路径来源，保留；`output.py`仅使用路径元数据列归档，不执行这类跨源冲突比较，保留。本次唯一修正的生产位置是Edit提交前的跨源比较。

证据目录：`build/edit-windows-64u5bzpg/`。

| 验证 | 实际结果 |
| --- | --- |
| `pytest -q tests/test_edit_windows_metadata.py`（修复前） | 2 failed / 9 passed；`edit-windows-red.log` |
| Read/Edit/Write及反馈/CLI定向组合 | 169 passed in3.53s；`edit-windows-focused.log` |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q` | **4239 passed in293.46s**；`backend.log` |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | **50文件415 passed**；TypeScript/Vite退出0 |
| 标准`build_release --target macos-arm64` / Twine /源码比对 | 构建及Twine通过，wheel中的edit.py与当前源码逐字节一致 |
| 标准`verify_wheel_install --wheel-dir .../wheel` | 全新venv、源码外正式Web调用Read→Edit→Read通过；原生工具/存储/MCP/后台/子实例门禁通过，0真实供应商请求 |
| AST/500行、compileall、pip check、diff | 通过；无破损依赖 |

本轮仅本地源码修复及候选包验证，版本仍0.3.0，没有提交/push/tag/PyPI发布；公开0.3.0包不会因此改变。Windows原机确认、远端CI和后续补丁发布仍是独立步骤。

## Edit校验收敛实施约定（2026-10-22，按用户纠偏收回范围）

目标：删除同一阶段重复的路径解析，集中已有元数据比较规则；不增加拒写条件，不改变公开Edit入参、权限模式、匹配算法或Write合同。用户明确指出本次目的是减少过多校验，目录移动后的清理改进不属于本片。

当前数据流：`require_file_target → _edit首次_resolve → reserve → 精确扫描 → 必要的归一化 → 复制前verify+resolve → 复制/反馈/fsync → 提交前verify+resolve → _replace_temp`。元数据比较分散在精确、归一化及复制阶段。

实现后数据流：`require_file_target → _edit原首次_resolve及已有文件检查 → reserve → 精确扫描 → 必要的归一化 → 复制前verify → 复制/反馈/fsync → 提交前verify → _replace_temp`。仅去掉复制前和提交前紧邻verify的重复`_resolve_write_target`；verify仍核对原始subject的requested/resolved/anchor，不缓存跨阶段授权结果。扫描前不增加新的许可复核。

影响范围：生产仅`tools/edit.py`与`tools/edit_matching.py`；沿用`tests/test_edit_windows_metadata.py`，新增一个摘要保护回归用例；`tests/test_file_tool_stop.py`将旧解析调用次数的停止注入迁至当前提交前复核，停止/原文/清理断言保留；本Spec与handoff同步。上一轮`release_smoke.py`实际Read→Edit→Read门禁保留。本轮不修改共享paths/permissions/write实现，不新增模块/类/依赖，不改真实配置或版本，不提交或发布。

内部接口：在已有`edit_matching.file_stamp`旁增加`require_file_stamp(info, expected, *, same_source=True)`。同源完整比较dev/ino/size/mtime/ctime；跨源显式`same_source=False`只比dev/ino/size；不匹配抛原`WRITE_CHANGED`，成功返回当前stamp，以记录复制前的路径快照。沿用原五元组与snapshot字典，大小和SHA-256比较仍在扫描/复制消费者处，不创建通用resolver。`_copy_to_temp`移除因重复resolve删除而失去用途的私有path参数并同步调用方；公开Tool API不变。

正常链：扫描建立句柄快照，必要时归一化复核同一内容，复制阶段用同源句柄完整stamp及摘要核对源内容，以独立路径stamp保护最终目标；Windows稳定的路径/句柄ctime差异仍允许。同文件reserve、停止、UTF-8/BOM/换行、反馈、模式位/DACL与原子替换保持原合同。

失败链：沿用原有复制前/提交前路径复核、执行中内容冲突、同源stamp漂移及跨源身份/大小不一致的处理；不添加扫描前失效许可的新规则。现有临时文件清理逻辑原样保留，不扩大为对外部移动目录后临时文件的可靠清理承诺。共享Write允许缺失目标/建目录的行为保持。

验证：重构以已有行为回归为准，保留精确2遍/归一化3遍读取、并发Write/Edit、换链、复制中外改、停止/IO/反馈/替换失败及原Windows断言。新增摘要用例验证“同长度内容被改动，即使报告的元数据稳定也仍冲突”，不修改共享测试基础设施。

范围纠偏记录：最初新增阶段矩阵扩大到扫描前拒读及父目录移动清理，实施前5失败、扩展实现后仍1失败。用户拒绝扩展后，新增矩阵原文及失败证据保存在`build/edit-checks-uq0016ck/exploratory/`，未修改断言伪装通过；新增扫描前保护已撤回，目录移动清理缺口未解决，不计入本片已通过验收。原有产品测试未删除、放宽或skip。

首轮全后端4239通过、1失败：旧停止用例在第三次`_resolve_write_target`调用注入停止，但Edit已移除后两次冗余解析，导致注入未触发。按内部调用迁移规则，将Edit注入迁至现有第二次`verify_file_target`（提交前），Write仍在原第三次解析；不恢复冗余、不放宽停止/原文/临时清理断言，并显式断言停止确已请求。失败日志保留为`backend.log`。

完成后执行定向、全后端、前端/构建、AST/行数/依赖/diff和标准本机wheel/源码外安装，结果随后补录。此片为行为保持重构，不将退出范围的失败实验称作已修复的TDD用例。

本片证据目录：`build/edit-checks-uq0016ck/`，`before/`保留实施起点，`slice.diff`记录本片增量。

| 验证 | 实际结果 |
| --- | --- |
| Edit/Read/Write/反馈/CLI定向组合 | 172 passed in4.96s；`focused.log` |
| 停止用例迁移后专项组合 | 107 passed in2.12s；`stop-focused.log`；原行为断言保留 |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q` | **4240 passed in293.15s**；最终`backend-final.log`；首轮失败已按内部测试注入迁移关闭 |
| `npm --prefix frontend test -- --run` | 50文件415 passed；`frontend-test.log` |
| `npm --prefix frontend run build` | TypeScript/Vite退出0；原有chunk大小提示保留 |
| 标准`build_release --target macos-arm64` / Twine | 构建退出0、Twine PASSED；`wheel-build.log`/`twine.log` |
| wheel源码字节 / 标准`verify_wheel_install` | 两份生产源码逐字节匹配；全新venv、源码外真实Read→Edit→Read及原安装门禁通过，0供应商；`wheel-sources.json`/`install.json` |
| AST/500行、compileall、pip check、diff | 通过；生产307/176行，Windows回归162行、停止用例386行；原11项Windows断言未变 |
| 独立只读复核 | 安全43项/架构50项/停止迁移32项通过，无阻断；`review.json` |

本片未增加拒写条件或目录清理机制。构建后的生产源码未再变化；停止测试迁移后不重复构建同一源码。Windows原机仍未验证，版本保持0.3.0，没有提交/push或发布。

## 0.3.1发行验收（2026-10-22）

用户随后授权发布0.3.1。范围为本次Windows Edit修复、重复校验收敛、对应回归与正式安装门禁；版本唯一事实改为0.3.1。README仅提交0.3.1条目，配置指南、实际配置、依赖、并行记忆/研究/学习成果不纳入。上方“未提交/未发布”描述为本地实施阶段时点。

发行前重新验证：后端**4240 passed in299.46s**，前端50文件415通过、TS/Vite构建退出0；225份Python源码AST/500行及103份前端源行数、两个修改测试、compileall、pip check、diff检查通过。四目标标准wheel构建、完整发行集合/版本/源码指纹与Twine通过；所有打包Python字节及README元数据与暂存内容一致。Mac ARM64全新venv、源码外实际Read→Edit→Read及原安装门禁通过，0供应商。安全50项/架构82项独立专项通过，无阻断。

实际发布：修复提交`62aec510e734a25105cba52ae87ff11773f07caf`已推送origin/main；[主分支CI39785682591](https://github.com/tlzmw001/naiyue-test-agent/actions/runs/39785682591)全部通过，后端4240 passed in572.38s。随后创建并推送[v0.3.1](https://github.com/tlzmw001/naiyue-test-agent/tree/v0.3.1)，标签指向同一提交；[发布39787367851](https://github.com/tlzmw001/naiyue-test-agent/actions/runs/39787367851)标签后端4240 passed in841.18s、前端/四平台构建和安装/完整发行检查/上传通过，[PyPI 0.3.1](https://pypi.org/project/naiyue-test-agent/0.3.1/)四份wheel的文件名和SHA-256均与标签产物一致。四份wheel的182个Python文件及README均核对发布提交，应用指纹与本地候选一致。

首次发布后macOS ARM64/Linux ARM64的pip索引仍只列到0.3.0，未进入应用执行；公开Simple索引出现四文件后仅重跑失败安装job，第二次流水线全绿。四份公开PyPI安装报告逐项匹配版本及发行SHA、Read→Edit→Read通过、0供应商，包括Windows原生runner；没有重传文件、移动标签或修改源代码。额外本机公开验证首次下载TLS EOF，重试又遇旧索引，未计作通过；本机候选安装与四平台CI公开安装分别为独立证据。

证据：`build/release-0.3.1-3g03njo4/{release-gate.json,main-ci.json,publish-final.json,publish-artifact-check.json,pypi-public-hashes.json,pypi-install-reports/,pypi-install-summary.json}`。真实配置/用户库/依赖未改，未验证真实模型或用户原始工作目录中的具体任务；下一步在目标Windows环境升级0.3.1并确认用户场景。

历史实施说明（发布前）：以上是本机macOS文件IO、假模型、官方payload转换和SQLite证据，无真实模型、Windows或沙箱验证。真实配置/依赖未改，未提交/push。下一步审阅[085-11图片](085-11-read-images.md)，不自动实现。当前发布状态以上方0.3.1实际结果为准。
