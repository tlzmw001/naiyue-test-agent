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

以上是本机macOS文件IO、假模型、官方payload转换和SQLite证据，无真实模型、Windows或沙箱验证。真实配置/依赖未改，未提交/push。下一步审阅[085-11图片](085-11-read-images.md)，不自动实现。
