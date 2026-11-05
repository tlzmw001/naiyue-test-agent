# Spec142C：实施分片、影响文件与验收

状态：2026-11-01 方案稿，未实施。本轮只整理文档，没有新增测试通过证据或功能完成声明。持久分叉复制当前工作区已由用户选定；文件范围、辅助按需准备和工具审批细节随[主Spec](142-conversation-fork-and-side-chat.md)审阅后再实施。

实施顺序更新：先完成[Spec143F](143f-implementation-slices.md)的B核心闭环，再按实际任务/内容输入接口修订本稿接线。旧bundle/binding测试入口不作为B验收目标；当前工作区已有改动、后续互不影响等行为断言必须保留。

## 1. 四个串行切片

每片从行为失败测试开始，确认失败原因正确，再最小实现并回归；独立可验收，不以空模块/未接线API充数。前一片构建或测试红灯时先解决，不继续后片编辑。

| 切片 | 交付内容 | 完成证据 |
| --- | --- | --- |
| 142-01 稳定快照与持久分叉 | schema21、稳定锚点/retention、值复制/身份映射、源准备预留、当前工作区捕获/私有输入、SessionAPI.fork、原子发布/幂等/崩溃对账、普通恢复 | 正式SQLite/checkpointer中创建B；A文件/聊天不变；B重开可续聊；创建0模型/Agent工具调用，无目标Docker创建；故障无半成品 |
| 142-02 分叉Web与动作 | 认证路由/能力字段、最后回复动作、侧栏/来源导航、分页/图片/缺失产物标注 | 旧/跨scope请求拒绝；双击只一份；1440/375可操作；旧重生成合同不变 |
| 142-03 辅助RAM与工具生命周期 | 新side创建入口、RAM seed与边界、独立权限/审批、lease/关闭、预算、环境输入与临时资源拥有权 | 多轮/工具/审批/并发父任务；关闭确认停止且无聊天落盘；崩溃仅对账自己的资源 |
| 142-04 辅助UI与组合验收 | 加号入口、SideChatPane、折叠/关闭/导航、流/草稿/图片隔离、主题/窄屏、标准安装验收 | 完整前后端行为、正式服务与浏览器操作；全量/构建/安装通过；证据明确真实与夹具 |

142-01必须连同文件捕获验收，不以纯文字分叉冒充已确认范围。142-03涉及临时沙箱销毁规则，必须以审阅后的142A第7节为准；不改为共享父沙箱，也不临时豁免身份检查。

## 2. 影响文件与真实消费者

预计超过8个文件，覆盖服务、应用、存储、执行和前端。下列新增文件是实施目标，当前不存在；实现时可按真实行数再拆分，但不能再建一套并行runtime。

| 位置 | 修改/拟新增 | 责任与接线 |
| --- | --- | --- |
| `application/conversation/context_snapshot.py`（新） | 稳定导出值/校验/身份映射 | SessionAPI.fork与临时side创建两个消费者 |
| `application/conversation/session_fork.py`（新） | fork操作/seed/发布/对账 | SessionAPI.fork，普通会话恢复入口读取新thread |
| `application/sessions/api.py` | 新增fork入口 | WebService调用；不改旧公开方法签名 |
| `application/sessions/session_persistence.py`、`storage/unit_of_work.py` | 完整成功保存/压缩/替换时维护锚点；删除独立来源 | 普通afinish/prepare/delete，不能只有新入口维护 |
| `application/sessions/session_state.py`、`session_service.py`及真实准入/控制入口 | 源准备operation标记、RAM side_context、拒绝源写入/删除并正确释放 | start/compact/regenerate/delete/environment_action；不能只在新按钮上禁用 |
| `storage/conversation.py`、`conversation_sql/`、`sessions.py` | 完整档案复制、水位/来源/操作读写 | 既有仓储边界；不让Web执行SQL |
| `storage/migrations.py`、`backup_schema.py` | schema21及新表验证 | 升级、备份、恢复、旧库正式重开 |
| `storage/checkpoint_retention.py` | 保护稳定锚点与未发布目标的必要状态 | 必要保存后retention，导出/替换/删除竞态 |
| `application/projects/host_manager.py`、`host_assembly.py` | 为side RAM host传已验证项目/执行事实和独立禁用能力 | TemporaryAPI/临时scope装配；原无父临时API保持 |
| `application/conversation/side_chat.py`（新） | 导入side seed/隐藏继承消息/边界 | TemporaryAPI的新增side创建；不管理持久fork发布 |
| `service/temporary.py`、`scopes.py` | lease初始化/关闭/预算/迟到响应；scope隔离 | 新side端点与既有临时端点 |
| `service/side_chat.py`（新，若temporary将超过500行） | 有父side创建请求/认证装配 | service路由挂载，复用TemporaryScopes而非第二套lease系统 |
| `web/routes.py`、`schemas.py`、`service.py`、`service/app.py` | 新端点/动作能力/错误映射 | 既有认证、中间件与WebService |
| `application/sessions/run_resources.py`、现有权限执行入口 | side可用工具及写操作每次审批、进程归属 | 真实tool执行链显式校验；手工伪造调用仍拒绝 |
| `application/environments/workspace_fork.py`（新）、`inputs.py`、`results.py`、`storage/sandbox_operations.py`及`execution/git_results.py` | 从现有capture/download/verify阶段得到私有快照，转目标InputSnapshot；未发布input pin与retire协同；不publish用户仓库ref | fork/side两个消费者；原结果取回仍显式publish，目标prepare校验ready输入并复用 |
| `application/sessions/execution_binding.py` | 独立新sid/setup，不共享父binding | 正式准备/status/恢复；原严格校验保持 |
| `application/environments/`、`storage/registry_database.py`、临时owner仓储（新） | 临时执行所有权、正常/重启收尾 | Application启动/TemporaryScopes关闭/EnvironmentAPI；无模型回放 |
| `settings.py`及设置测试 | 两组复制/RAM设置模型与输入校验 | 设置加载器→宿主/导出消费者；不改真实配置 |
| `frontend/src/types.ts`、`api.ts` | 新动作/来源/side创建接口与temp WebApi | 主动作和useSideChat；不覆盖主browserConnection |
| `frontend/src/useSideChat.ts`（新） | 实例/lease/close/generation | App和SideChatPane，消息循环继续用useConversation |
| `frontend/src/components/SideChatPane.tsx/.module.css`（新） | 面板与复用渲染/审批/输入 | 现有右侧面板槽，折叠保活 |
| `frontend/src/components/Conversation.tsx`、`ComposerControls.tsx` | 最后回复动作、加号入口 | 以服务端能力为准，原有动作保持 |
| `frontend/src/App.tsx`、面板CSS及必要控制类型 | 分叉导航/来源行/侧栏槽 | 不把side消息塞进主chat状态，不建设新导航框架 |
| `tests/test_conversation_fork.py`、`test_side_chat.py`、`test_web_conversation_fork.py`、`test_side_chat_execution.py`（新） | 下列行为/故障与正式跨层验收 | 真实仓储/checkpointer/装配；必要外部IO用确定性替身 |
| `frontend/src`现有测试及`side-chat.test.tsx`（新） | 入口/异步/键盘/隔离/关闭行为 | Vitest+现有前端测试设施 |
| `scripts/verify_wheel_install.py`及必要正式安装fixture | 安装后的fork/side最小链 | 复用现有构建/安装入口，不写平行发行工具 |
| `docs/lock-hierarchy.md`、handoff/future-work/project-plan | 实施时同步实际锁/当前状态/验收链接 | 不预写不存在的锁或功能完成记录 |

单文件500行上限：当前useConversation接近上限，side生命周期必须另放；输入服务、WebService或仓储若扩展会超限，按独立职责拆分并迁移消费者，不能保留一套旧实现加一套新转发。没有重复需求证据时不额外抽通用类。

## 3. 确定性验收矩阵

| 编号 | 行为/故障输入 | 必须断言 |
| --- | --- | --- |
| F01 | A完整保存→forkB | A正文/版本/费用/现有任务不变，仅有必要捕获操作事实；B独立sid/thread/锚点；0模型/Agent工具调用和目标Docker创建 |
| F02 | B发送新输入后重开库续聊 | 请求含正确有效前缀和新输入，无旧工具重放，A继续正常 |
| F03 | 压缩历史、长分页、规范化图片、多工具调用 | 完整档案与模型消息各自正确；图片可认证读取；id映射/顺序/配对完整 |
| F04 | 人工修改旧source revision/reply/token | 409且无目标会话/新来源，不能自动选新尾部 |
| F05 | running/approval/save_failed/子Agent/临时scope | 分叉拒绝；无权限越界和必要数据删除 |
| F06 | 同request_id并发/重试/响应丢失/服务重开 | 仅一个B；同结果；不同fingerprint拒绝；B删后旧请求不重建 |
| F07 | 导出、seed、publish各阶段故障/强杀 | 源不变；无可运行半成品；孤儿仅按操作身份对账；主异常保留 |
| F07C | 捕获中取消、HTTP abort、发布与取消竞态、取消后重试 | 专用取消端点核验归属；abort不冒充取消；取消封口并只停自己捕获；已发布B保留，旧request_id不重建 |
| F08 | retention、压缩、替换式重生成、删除与导出交错 | 活锚点不被剪；准备预留拒绝新的源写/删；失败/取消释放；绕过预留的版本变化不能错误发布 |
| F09 | 删除A或B、source无权访问 | B不随A级联；A不随B删除；来源导航不授予读权限 |
| F10 | schema20迁移/备份/恢复/损坏锚点 | 正式恢复保持；旧库无来源默认；无法证明锚点时禁用，零回放 |
| F11 | 历史附件/子实例/任务id/旧usage | 复制正文可读，未复制产物标明；不能接管父任务，费用不重复累计 |
| S01 | idle A→S多轮→折叠/恢复 | 初始UI空白但请求含快照；折叠不重seed、不丢草稿/流；A无side消息 |
| S02 | A running/approval→打开S并发送 | 只含前一完整稳定轮；不含当前输入/半工具/审批；文件未捕获时无files/processes，不回退Host，不停止/等待A；独立联网能力可用 |
| S03 | 无稳定点、取快照冲突、创建取消后迟到ready | 明确拒绝；不静默降空上下文；迟到实例精确关闭 |
| S04 | S/A各有read/write/Shell审批 | 各自run归属；S写操作必须新审批，父session_allow无效；deny保持 |
| S05 | S伪造子Agent/memory/history/父任务/父图片身份 | 服务端拒绝；工具schema与能力面一致；不靠模型自觉 |
| S06 | 关闭S时运行/待审批/背景进程/MCP调用 | 封口→停止/拒绝→排空→清理；只清自己的资源，不覆盖主异常 |
| S07 | 关闭后再开、刷新/lease断开、服务重启 | 全新sid/thread/context；旧实例不恢复/不重放；旧流/ACK不能污染新实例 |
| S08 | RAM/实例/运行/帧/沙箱额度耗尽 | 明确错误与草稿保留，无静默截断；不抢停A；预留失败与关闭均正确释放 |
| S09 | query所有持久会话/聊天/checkpoint/标题/概览/记忆表 | 没有S正文或可恢复聊天；允许的操作收尾记录单独核验且无提示词/聊天正文 |
| E01 | 源当前工作区有tracked修改/删除、新允许文件、二进制及mode变化 | 新输入含已确认范围内的全部当前内容；新base为捕获commit；独立binding/session；B后续修改不影响A |
| E02 | 删除父会话/retire父输入与未发布目标输入 | B/S已捕获输入仍可用；不依赖父目录；目标creating pin保护，发布接管/失败解除/跨库崩溃不提前丢pin |
| E03 | 源输入损坏/模板不可用/容量满 | 真实错误；无默认环境/Host回退；不修改父环境 |
| E04 | S关闭成功、SSH断连/未知结果/错误owner | 仅自己临时资源被清理；未知先查询、收尾未确认可见；错误owner拒绝删除 |
| E05 | 控制服务强杀后重启 | 临时执行owner可对账；无聊天恢复、无新Shell/MCP提交、无父容器stop/destroy |
| E06 | 捕获/下载/verify前后核对父工作区及本地用户仓库 | HEAD/index/工作文件不变，无用户result ref新增；旧结果取回仍publish；断点只续同receipt |
| E07 | 忽略/敏感/不支持路径、外链、文件变化、候选变化、非descendant历史 | 按现有策略拒绝或明确列出排除，不能声称全部复制；校验失败不publishB，源不受损 |
| E08 | 源忙→S纯上下文运行→源闲→S下一轮准备→源再次修改 | 首轮无文件能力；第二轮捕获时间明确、绑定私有环境；以后不自动跟父；安装重试不重新捕获 |
| U01 | 最后回复动作/双击/打开失败 | 唯一B、草稿保留、无假条目，按钮与来源语义正确 |
| U02 | 主/辅助同时流式、长文、图片加载、审批 | 内容/停止/输入/审批/响应generation无串线 |
| U03 | 主题/1440与375/键盘/触屏/输入法 | 无横向溢出；焦点可回；菜单与关闭/折叠可区分 |
| U04 | 切换主会话/项目/面板、未发草稿、清理失败 | 面板切换只折叠；会话/项目切换按关闭；失败可重试且不误绑定新源 |

用事件/屏障控制并发，不用紧sleep。异常测试同时检查异常身份、必要状态、事件配对与资源；测试检查器失败不能被观察异常隔离吞掉，末尾显式断言。只mock外部供应商并不免除正式SQLite/装配/权限链验收。

## 4. 正常与故障的跨层证据

分叉正常：真实已安装包→认证POST fork→WebService→SessionAPI.fork→正式AsyncSqliteSaver→UnitOfWork发布→分页/图片查询→重开B发送假模型一轮。观测snapshot来源、映射后的消息、B首个provider请求、A/B各自状态与库行数。

分叉失败：停在seed完成/commit之前强制终止控制端，重启后相同request_id对账。证明目标未发布或准确恢复同一个已提交B；没有任何旧工具再次执行，原始失败原因不被清理异常覆盖。

辅助正常：正式服务→创建/lease→RAM Host→当前模型/工具主链→Read和需要审批的写调用→关闭。父会话持续运行且能独立停止/完成；S自身事件完整，持久项目/个人库没有S消息、checkpoint或记忆产物。

辅助故障：工具已提交后断连并关闭，查询原操作状态/控制端重启。只对账和收尾自己的临时owner；不把不确定任务当未提交重试，不报告“已清理”直到证据成立，不从临时记录恢复聊天。

Sandbox正常：专用fixture项目/环境中，父初始输入及后续新增/修改/删除/mode分别放可核验内容，新会话取得父当前允许工作区内容及全新身份。核对源HEAD/index/文件未改、本地用户仓库无新ref、目标私有bundle可独立安装；B继续修改不影响A。关闭辅助后父任务/父卷/持久分叉环境可用。仅操作专用新资源，不碰公司原容器。

## 5. 验证命令与结果记录

以下是**实施后**门禁命令，不是本轮已运行结果。新测试文件只在对应切片创建后才能执行。

定向后端：

```bash
.venv/bin/python -m pytest -q tests/test_conversation_fork.py tests/test_web_conversation_fork.py tests/test_side_chat.py tests/test_side_chat_execution.py
.venv/bin/python -m pytest -q tests/test_last_regeneration.py tests/test_checkpoint_retention.py tests/test_service_temporary.py tests/test_temporary_application_storage.py tests/test_temporary_subagent_scope.py tests/test_web_regeneration.py
```

前端（在frontend目录执行）：

```bash
npm test -- --run
npm run build
```

提交前全后端，沿用已有分片入口，必须两个都完成并核对收集节点/JUnit，不把某片退出0当全量通过：

```bash
.venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/spec142/backend-0.xml
.venv/bin/python -m scripts.ci_shards --index 1 --count 2 --output build/spec142/backend-1.xml
```

标准本机平台构建（选择未使用的输出目录）：

```bash
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec142/wheels
.venv/bin/python -m twine check build/spec142/wheels/*.whl
```

随后沿 `scripts/verify_wheel_install.py` 的现有平台入口做干净安装及正式fork/side探针，记录最终命令、安装包hash、源码外工作目录和结果。Windows/Linux运行、真实模型/MCP和公司两跳SSH分别记录，不以本机确定性测试或QEMU代替。没有发布授权则止于可审阅制品，不自动commit/push/tag/PyPI/Release或部署。

每片在handoff顶部记录实际完成范围、唯一入口/调用链、命令/结果、已知限制与下一步；最终证据索引写入 `build/spec142/verification.json`，只列真实存在的文件。测试数量届时从实际收集/结果获取，不能预填当前历史6142/568作为本功能通过数。

## 6. 实施收尾检查

L1：声明的模块、API、schema、UI和测试真实存在；L2：没有空实现/占位，seed与发布/关闭均真实执行；L3：入口已挂载并由正常生产调用链消费，设置实际load、稳定锚点由普通finish维护、关闭由lease及显式按钮触发、重启收尾已接启动。

检查所有旧与新消费者：生产代码、脚本、测试、序列化、DB映射、备份恢复、图片/产物路由、分片收集、打包notice、当前文档。旧重生成继续保留其实际消费者；新功能替代的内部包装在证实无消费者后清理，不为旧测试保留另一套实现。

本Spec的“已整理”仅指文档；功能完成必须同时给出实际验证命令和运行结果、构建0错误、对应测试全绿、真实接线和UI证据。单元协议正确、真实模型连通、实际业务质量是不同证据，不互相替代。
