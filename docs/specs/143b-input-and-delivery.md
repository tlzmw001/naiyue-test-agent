# Spec143B：文件导入、基线复用与结果交付

状态：S03.1最小导入与S03.2完整Git来源/缓存/段续传已完成本机实施，见[143l](143l-s031-input-import.md)、[143o第7节](143o-s032-sources-cache-and-resume.md#7-本机实现与验收2026-11-01)；Windows原机待补证。S07.1单产物上传/下载已实现，见[143n](143n-s071-artifacts-and-cli.md)；完整S07.2结果/S08交付仍为拟实现合同。总入口[Spec143](143-cloud-task-b.md)，不保留远端提交历史。

## 1. 三种不同的数据

| 对象 | 意义 | 身份与寿命 |
| --- | --- | --- |
| 输入 I | 首次明确选择并冻结的项目文件 | 内容清单 SHA-256；Windows/云端缓存，可供多个任务只读复用 |
| 当前 workspace | 任务连续工作的可写目录 | 属于 task；后续消息不重新安装 I，不被本机变化覆盖 |
| 结果 R：I→O | 某个稳定时点的最终文件改动＋产物 | result_id＋固定 I/O 清单及各文件哈希；可独立下载/审查/交付 |

项目是逻辑分组，不是多个容器共享的可写目录。新任务显式选择某个输入；同任务下一轮沿用当前目录。后续“导入新的本地状态”创建新输入/新任务，不覆盖活跃任务，首版不做双向自动同步。

## 2. 为什么不用 Git 快照/完整 bundle 作主交付格式

ZCode 默认打开既有远端目录；其部署运行包不等于为本机未提交代码提供输入冻结。Codex 的公开云端环境资料不公开完整传输实现。OpenSandbox 提供文件流，未提供本产品的输入身份和结果合并语义。DeepSeek Harness 的轻量树比较证明比较可以不生成快照提交/bundle，其附件仍独立传输；临时 diff 缓存不能直接作为持久交付。

本项目当前 Git 输入约 20 次进程调用，原 Windows 同请求 Git 子进程中位约 312ms；完整流程也承担额外验证和历史对象成本。不能把“Git 调用多”推导为“所有 Git 都慢”，也不能因为当前实现已有 bundle 就保留它为主格式。参见[格式复评与数据](../../research/cloud-b-runtime-and-transfer-2026-11-01/format-reassessment.md)。

选定合同：初始输入 `tar.gz＋manifest`，无提交历史；内部比较可用私有 Git tree/index；最终代码 `git diff --binary` patch＋O 清单；测试产物原始字节流。大二进制只改少量字节时 patch 可能显著节省传输；首次已经压缩的文件可选未压缩 tar。默认 gzip level 3，属于配置默认值，不硬编码协议参数。

## 3. 本机捕获与清单

连接器先列纳入/排除摘要，用户选择后冻结范围/排除策略；默认遵循项目ignore并排除`.git`、`.venv`、`node_modules`、`__pycache__`、`.pytest_cache`及已知凭证文件，可显式选择普通未跟踪文件/proto。Git来源用批量`git ls-files -z --cached --others --exclude-standard`及`git ls-files -z --stage`获取路径/执行位，核对已删除/冲突条目；不每文件开Git或自行重写Git ignore。非Git来源使用明确规则；子模块不自动下载或递归纳入，需显式作为文件来源捕获。排除名单不保证识别所有秘密，选中真实配置不自动改写。

清单的规范形式：UTF-8 JSON，固定 schema 版本、键排序、条目按规范路径排序、无多余空白；身份中不包含 mtime、绝对路径、创建者或 Git HEAD。时间、来源目录与原 HEAD 仅在本地导入回执中保存。示意数据形状：

```json
{"schema":1,"selection":{"policy_sha256":"<64hex>"},"entries":[
  {"path":"proto/service.proto","type":"file","size":317,"sha256":"<64hex>","mode":420},
  {"path":"scripts/check.sh","type":"file","size":89,"sha256":"<64hex>","mode":493}
]}
```

示例字段值只说明协议形状，不作为 UI 运行数据。mode 420/493 分别是 0644/0755；链接条目记录目标字符串及其哈希。目录不作为结果差异承诺，空目录不保证交付；需要空目录的项目使用实际占位文件。

`selection`绑定规范化捕获范围/排除策略：Git ignore文件及标准exclude的实际规则内容、显式纳入/排除和固定依赖缓存规则冻结为独立selection.json，只有相对路径/规则，无来源绝对路径。其SHA进入manifest，随I上传/校验/只读安装，Runner捕获O消费同一份策略；策略改变即新input_id，避免同一文件清单却复用另一套结果范围。Git来源优先让Git批量计算，结果扫描可在私有比较目录使用冻结规则，不读取任务修改的ignore作为权威。

输入路径使用 `/`、规范 Unicode，拒绝绝对路径、`..`、重复规范路径、Windows 保留名/ADS、NUL、大小写/规范化碰撞。只允许普通文件及指向输入根内的相对符号链接；拒绝设备、socket、FIFO、归档硬链接及越界链接。Windows reparse point 必须识别类型/目标，不能当普通目录递归。源普通文件即使有硬链接也只按字节独立复制，不在归档中创建硬链接。

捕获时固定文件清单，逐文件 open/fstat、读取计数和哈希、同一属性来源的前后检查，再核对目录成员及最终清单；源变化/不可读就返回可见冲突，不静默遗漏。必要的稳定性复核不因追求零 Git 而删除。Windows 的 mode 可由批量 Git 索引元数据辅助确定，其余普通文件默认为 0644，明确的执行文件选择记录在回执；不复制 A 的临时 commit/ref、全历史 fetch 与每文件 Git 查询。

归档字节另算 archive_sha256；`input_id=SHA256(canonical_manifest_bytes)`。归档顺序/压缩差异不改变文件内容身份。捕获先写本机私有 staging，完成并校验后原子提交到输入缓存；失败不生成可使用的 input_id。

首版I保持工作区原始字节，不按Windows autocrlf或远端平台静默转换。捕获回执记录来源Git的text/eol/autocrlf等受支持转换策略及其摘要；导入预检识别模板需直接执行的CRLF shebang脚本并给出文件列表，项目准备失败，不擅自改源码。用户修正来源后显式导入新I。分支写入的仓库表示按第7节处理，不能把运行字节与Git blob字节混为一份hash；无转换回执不猜测。

S03/S07/S08复用141A的文件安全行为而非旧wire：固定根/父目录句柄、逐级不跟随链接、打开后核对类型/对象/字节预算；Windows用已有reparse/文件身份检查对应实现，不假装支持POSIX dir_fd。先resolve再按原路径open不足以防竞态。安全根内链接保持可用；FIFO/设备/socket和越界链接明确拒绝，前后防变更检查保留。

## 4. 二进制传输与断线续传

JSON/SSE 只传元数据/进度。二进制由独立 HTTP 流端点传输，不用 base64，不经过当前 `api.ts` 假设 JSON 的 response 帮助函数。HTTP 比 SFTP 更易复用鉴权和浏览器下载，但不预设它吞吐必然更高。

| 拟定入口 | 合同 |
| --- | --- |
| `POST /api/cloud/v1/transfers` | 用途、目标 input/result、大小、SHA-256、command_id；返回 transfer_id 和块预算 |
| `GET /api/cloud/v1/transfers/{id}` | 已持久确认 offset、总长、状态、错误；不返回宿主路径 |
| `PUT /api/cloud/v1/transfers/{id}/bytes` | `Upload-Offset`、段 SHA-256、Content-Length，raw body；确认新 offset |
| `POST /api/cloud/v1/transfers/{id}/commit` | 校验完整长度/哈希/清单后原子提交；重复返回原对象 |
| `DELETE /api/cloud/v1/transfers/{id}` | 仅清理所属未提交 staging；已提交引用不被取消删除 |
| `GET /api/cloud/v1/inputs/{id}` | 已提交清单/大小/校验状态；没有 ready 记录不能建任务 |
| `GET /api/cloud/v1/results/{id}/files/{file_id}` | 认证下载、固定大小/hash/ETag、支持单范围 Range；路径由服务端映射 |

应用层实现续传；不是 OpenSandbox 的 multipart 或 stream 自动提供。默认传输段 8MiB、流 IO 缓冲 256KiB，均走配置；内存不随文件总大小增长。服务端只接受当前 offset；已确认相同段/hash重发返回原 offset，不同内容409；跳跃偏移拒绝。先落盘并同步，再持久提交 offset；异常后截断未确认后缀，不能把未 fsync 字节算成功。进程在任一顺序点崩溃都由磁盘长度/已确认段摘要核对，禁止盲目 append。

commit检查归档总长度/hash、selection.json摘要/范围预算、解包上限与清单内容，在同一文件系统提交目录，最后标ready。目录已提交而登记缺失时按固定摘要对账，不得先ready后补文件。额外文件、重复条目、路径越界、解压膨胀、类型不符全部拒绝；身份不是上传方自称SHA，服务端实际读取计算。

传输与任务运行独立：暂停上传不取消别的任务；已接受运行命令不因传输连接断开被重放。只重传当前未确认段，已提交 input 命中缓存不再上传归档。staging 的 TTL/磁盘额度/清理错误可见，活动传输和被引用输入不能回收。

不同input_id的重复导入按全量归档处理；分段续传只恢复同一对象，不是本地改一点就增量同步。首版以云端workspace连续工作、本地低频导入为适用前提；高频大项目出现后再评估manifest差集/内容块上传，不预建第二套协议。传输走143c独立SSH连接，不能和停止/状态共享大字节队列。

## 5. 输入安装与远端复用

Coordinator 将验证后的归档只读挂载给新任务，Runner 在自身空 workspace staging 解包并核对清单后切换为就绪。单机无需再通过 OpenSandbox HTTP 上传一次同一归档；不得把整个 inputs 根目录或其他任务目录挂进去。安装中断保留阶段与错误，未完整校验的 workspace 不可运行。

同 input_id 可供多个容器安装，输入缓存不可写，任务工作区必须是真实独立副本，不能用指向源/其他任务可写内容的硬链接。允许文件系统提供的安全 copy-on-write 优化，首版不依赖快照能力。新容器启动时不临时 `pip install latest`，工具依赖由版本化模板预备。

首版仅实现Windows冻结上传。开发机已有项目导入后移到独立切片：届时仍须由隔离捕获作业生成同一I，不能将长期项目目录RW挂给任意任务。Git clone可作为用户主动准备来源的手段，不提供第三套同步协议或上传本机`.git`历史。已有任务更新到另一个本地输入首版不支持；显式新建任务，不以同步按钮覆盖活跃workspace。

## 6. 结果冻结与比较

capture 命令需要 task revision/generation、command_id。只有 Agent 与受管后台命令均已静止的边界允许冻结；忙时409并指出仍在运行的任务，用户可先显式停止。首版不承诺冻结任意未受管 daemon 正在写入的目录：有界扫描复核不稳定即失败，不交付混合时点内容。

Runner 捕获 O 并通过任务作用域传输交给 Coordinator；结果在容器外保存、校验、提交为 immutable 后才宣布 available。断线或重试复用同 result_id，不重新扫描成不同 O。S07.2按用户反馈改为运行时提供每个root_run专用产物目录：新建时不预填文件名，结束后收集实际生成的普通报告/交接文件，再供选择下载；代码清单排除整个产物根，报告不混进代码patch。格式/接口与验证边界见[143z](143z-s072-fixed-results.md)。

按143s的委派合同，输入就绪/云端持久受理后，由云端自主跟踪执行与静止边界并触发结果捕获，不依赖Windows在线补发capture。根本轮completed而受管后台命令仍活跃时，结果仍未就绪；捕获/必要保存失败不得用聊天终态冒充available。失败任务可保留明确标记的部分材料，不改称测试成功。

每个自主结果固定task_id、source_run_id、终局epoch/seq、input_id、generation和result_id；同一终局只建立一个捕获操作。终局确认时持久登记待捕获状态/阻止空闲释放，并与后续消息准入互斥：新run不能越过尚未固定的上一轮结果。Coordinator重启只核对原捕获，不重放Agent/测试。捕获失败可重试同一操作，或由用户明确放弃该轮文件结果再继续；放弃不改业务结论、不把后续工作区冒充原轮次O，缺失结果继续可见。S06.3沿既有artifact_operation_id/reclaim_blockers保护实际捕获，S07.2使用同一预留接完整结果；不预建无消费者的结果hook，也不以尚未实现的模块阻塞前面的最小任务验证。

待捕获与正在捕获分开：等待后台静止时只保留持久result_pending，不占正在执行的artifact_operation_id，因此仍可显式stop收束后台；真正静止后才预留捕获并扫描。现有控制准入会拒绝artifact_operation_id，不能在等待阶段提前占它而阻断所需停止。运行/控制终局和静止心跳不得意外清除result_pending，S07.2须同步映射/存储/消费者并补并发断言；只有捕获确认或所有者明确放弃后才解除对应保护。

任务交接说明写入本轮专用产物目录，作为真实普通文件随结果保存并按需下载，不建设云端长期记忆库。143w明确下载到本机目录、准备带材料引用的分析草稿、应用代码三种动作；用户确认发送草稿后才启动本机模型读取材料，不把报告字节伪装用户原话。前两者保留task/input/result归属和幂等状态，后者继续预检/确认。本机可在云端执行期间修改代码，取回不能自动覆盖并发改动。

O按导入时冻结的范围/排除策略扫描，不能采用任务改过的.gitignore悄悄改变受管集合；范围内新增代码纳入，依赖/缓存仍默认排除。mtime/size缓存只作扫描提示，不替代最终内容哈希/目录复核；首版不承诺仅凭元数据跳过未变文件。任务可伪报自己的结果，Coordinator的校验只证明收到的patch/清单/字节一致且符合范围，不证明实际测试成功或任务上报不可篡改。

```text
ResultReceipt {
  result_id, task_id, project_id, generation, captured_at,
  input_id, output_manifest_sha256,
  patch: {file_id, size, sha256, format: git-binary-v1},
  artifacts: [{file_id, relative_name, media_type, size, sha256}],
  status: capturing|verifying|available|failed
}
```

内部分别把原始字节写入私有 Git object/index，生成 I/O tree，用 batch 输入避免每文件一个进程。禁用系统/用户 Git 配置、hooks、filters、autocrlf 和外部 diff，保留真正字节；不因源仓库 `.gitattributes` 改写内容。`git diff --binary --full-index --no-renames` 产生 patch，不生成提交/bundle，不向原仓库发布对象/ref。tree_id 是比较缓存，input_id/O manifest 才是内容权威。

rename 可表达为 delete＋add；不承诺保留远端 Git 提交、rename 历史、空目录或文件时间。结果扫描复用输入的类型/路径安全规则；Windows 不可表达的远端文件仍可在云端审查，但本机交付必须明确拒绝并列冲突，不能静默丢文件。

## 7. 结果取回与本机交付

浏览器先查清单、分页差异和小产物预览，不下载整工作区。大文件按需 Range 下载，ETag/哈希绑定固定结果；HTML/日志预览按不可信内容处理，禁止脚本执行。下载报告不要求先把代码应用到本机。

连接器先核对可信I清单和固定R，预检patch全部路径/类型/链接父目录、总条目及大小。apply以闭合的I→O变化集合为单位：取得并验证这些路径的I原始字节，在私有staging用受控Git环境check/apply，核对输出hash/mode/删除状态；用I清单替换变化条目重算完整O身份，patch与清单差集必须完全一致。未知条目/额外路径/错hash拒绝，不将不可信patch直接交给用户checkout。

不为每次小改动重复物化整个I。已有可信文件缓存可只读取变化路径；只有tar.gz缓存时可能仍需顺序解压读取归档，不能宣称随机读取或零全量IO。无本机I缓存时首版仍下载并校验完整I归档；不临时增加任意远端文件读取入口。导出完整新目录需要完整O字节，可物化I一次并应用已验证变化；分支不要求重建完整工作树，按下述受管集合写私有Git tree。不同动作分别计时，不能以清单内存比较替代真实字节校验。

首版三个明确动作：

| 动作 | 本机副作用与条件 |
| --- | --- |
| 导出到新目录 | 默认安全动作；目标必须不存在，验证后的 O 分阶段安装；失败保留私有 staging 和明确状态 |
| 创建本地分支 | 仅 Git 来源且原 HEAD 与本机捕获回执存在；以原 HEAD 为父，将受管范围完整 O 覆盖到原树，未纳入范围保留；不 checkout、不改原工作区/index；分支名冲突拒绝 |
| 应用到当前目录 | 显式确认；逐一检查将修改/删除的路径当前内容仍等于 I，新增路径不存在，类型/父目录安全；无关未纳入文件可保留；冲突列表返回，不做猜测合并 |

“应用”替代 A 的远端历史 merge；这是明确的 breaking change，不保留旧 merge/bundle 客户端入口。原始 HEAD 仅用于创建分支的本地出处，不是输入内容的唯一身份。

分支不能把被排除/未选择的原跟踪文件意外删除。本机捕获回执额外记录所选范围、纳入路径及范围内相对原HEAD已删除的跟踪路径；分支受管集合为这些路径加O新增路径。由完整O覆盖该集合，故初始未提交改动也能带入分支，不只包含Agent后来修改的部分。回执只在连接器保存，不改变input_id；缺回执或原HEAD对象时分支动作不可用，仍可导出。apply则只处理I→O变化路径，允许无关文件继续有本地改动，应用后逐项核对新哈希。

分支在私有index/objects中以原HEAD树为基础：按受管集合合并原HEAD→I的本地改动与I→O的远端改动，未纳入条目保留，不checkout、不更改原index/工作区。受支持的Git text/eol/autocrlf规则来自捕获时冻结且用户授权的本地策略，只对写Git blob做明确的换行规范化，并记录O原始hash与仓库blob的对应；不执行任意clean/smudge/process filter、ident或自定义编码转换。LFS/filter/working-tree-encoding等超出支持集时分支动作明确不可用，仍可导出/apply原始字节。CRLF/LF往返、原本地未提交改动和非受管文件均须专项验证，不把纯换行变化当业务改动隐藏。

应用前建立本机操作日志，记录每路径期望旧/新哈希、备份和阶段；原子替换单文件，删除前再次核对。任一步 IO 失败标记 partial/unknown并保留日志供检查/显式恢复，不声称整棵目录原子，不自动回滚用户可能并发写入的文件。恢复命令仍核对现状，不覆盖第三方新改动。若不能建立所需备份/日志则在首次副作用前失败。

Windows 本地路径只存连接器，浏览器得到不透明 source_handle/destination_handle；远端 result 不得传任意本机绝对路径要求读写。浏览器只能选择用户已授权范围，路径重新校验后才执行。

## 8. 分叉、临时任务与清理边界

后续 Spec142 持久分叉捕获父任务**当前稳定 workspace**成新 I，加稳定聊天/checkpoint复制合同，建立独立 workspace/state；不得重新安装父原始 I 丢掉已修改代码。新任务不共享父可写挂载，删除父不破坏子。本文不实现分叉按钮、RAM 辅助会话或其审批策略。

缓存回收以输入/结果/任务引用为准，不靠“文件老了”推断无人使用。先标删除意图、核对无活动引用，成功清理后释放额度；失败保留可重试记录和诊断。用户导出文件不属于自动缓存回收范围。旧 A 缓存/输入/卷不纳入 B 回收器。

当前S07.2接口落地为task作用域`/api/cloud/v1/tasks/{task_id}/results/{result_id}/...`，替代上表早期示意的全局results路径；owner鉴权、精确文件槽位、来源与CAS见143z。浏览器附件下载与S08.1受控本机结果目录/台账是两个动作；只有后者为本机分析提供持久材料引用。
