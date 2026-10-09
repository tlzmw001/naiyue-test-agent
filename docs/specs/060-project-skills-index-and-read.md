# Spec 060：项目双来源 Skill 索引与通用 Read 懒加载

状态：2026-10-07，已按 TDD 接线，确定性回归及一次有界真实模型双来源探针通过。

## 目标与已确认选择

沿用现有 `read`，不新增专用 Skill 加载工具。支持每个会话工作区的 `.agents/skills/` 和 `.claude/skills/`；两组索引同时进入提示词，来源不能丢失或把同名条目合成一个。`.agents` 有可用 Skill 时作为默认；否则 `.claude` 有可用 Skill 时作为默认。用户可以通过自然语言明确要求另一来源。默认选择是模型收到的提示词约定，不宣称具有确定性的自然语言意图判断器。

闭环为 `应用发现/选择 → 本 run 提示词索引 → 模型 read 正文/引用 → 同 ID ToolMessage → 下次模型输入`。来源选择与只读资源范围由应用层负责；runtime 不 import Skill 目录扫描代码，不根据具名 Skill 写规则。读取失败遵守现有安全错误回填，不将预期文件错误升级为整次 Agent 失败。

## 当前数据流与需改动点

- `SessionService.run/resume_tool_approval` 目前读取项目指令，冻结 `run_project_instructions`；运行层在模型请求中附加该字符串，压缩预算也使用它。本片把两个来源的 Skill 索引与项目指令组成同一份本 run 提示词快照，使普通请求、摘要后请求与溢出恢复的预算包含索引。
- `create_session_read_tool` 当前只从可信 ToolRuntime.context 取得工作区与审批 grant；`.agents`/`.claude` 被通用隐藏路径规则拒绝。需要把应用已发现的 Skill 资源目录作为一个只读范围传给 Read，不允许模型通过工具参数设置该范围。
- 现有 `invoke_agent_run*`、`resume_agent_run*` 公开签名保持。新增资源上下文入口供应用普通运行和审批恢复使用，并扩展共享私有 `_invoke_agent_run` 的内部参数。旧调用默认没有资源范围，行为不变。不能仅在 before_agent 安装范围，因为审批恢复可能从图中间继续而不重跑 before_agent。
- 应用文件当前 467 行、运行装配 464 行。发现/解析/提示词组装放独立模块，新增调用入口放独立小模块；不把全部逻辑堆进现有大文件。

## 发现与元数据契约

1. 从可信会话工作区根目录发现上述两个来源，扫描来源下一级的 Skill 目录中的 `SKILL.md`。不递归扫描整个工作区；其他来源由后续显式共享来源切片处理。没有工作区或没有来源目录时返回空目录结果，不生成空 Skill 提示词块。
2. 目录和 `SKILL.md` 规范化后须仍在所选工作区，且不能落在 `.env*`、`.ssh`、`.git` 等敏感路径。仅豁免两套来源前缀 `.agents/skills`、`.claude/skills` 的隐藏祖先，指向其他隐藏目标的元数据不注册，避免显示 Read 无法加载的条目。工作区内合法链接允许；跨工作区链接在诊断中标记并排除，不自动注册外部只读权限。现有 `.agents/skills/rndflow` 是指向其他项目的链接，首片不得据此宣称该 Skill 已向运行 Agent 开放，也不改动该链接或复制其文件。
3. 每个 `SKILL.md` 按现有 64 KiB 文件读取预算有界读取、严格 UTF-8（允许 BOM），用已声明的 `PyYAML==6.0.3` 的 `safe_load` 解析 YAML frontmatter。不新增或升级依赖。必需 `name`、`description` 均为非空字符串；name 最多 64 字符，采用小写字母/数字及单个连字符分隔的标识规则 `[a-z0-9]+(?:-[a-z0-9]+)*`，description 最多 1024 字符。其他字段不变成权限授权；`disable-model-invocation: true` 的条目不进入模型可用索引，本片不增加显式 `/skill:` 命令。
4. 一个条目的内部数据为 `source_id`（agents/claude）、`name`、`description`、`skill_file`（规范路径）、`resource_root`（规范 Skill 目录）。身份是 `(source_id, name)`。跨来源同名保留两条；同来源同名按排序后的原路径先遇到者生效，记录赢家/被遮蔽条目的诊断。解析失败、文件不可用、超限或权限拒绝只影响该条目，不伪造名称/描述或占位正文。
5. 来源目录不存在属于正常空结果；读取/列目录失败和无效条目须有脱敏诊断。日志不含正文、描述、原始 YAML 或未经处理的异常文字；可记录来源、固定原因、计数和安全异常类型。

## 索引与生命周期契约

- 输出分组索引包括 source_id、默认来源、每项 name/description/location；XML 内容及属性正确转义。名称相同也保留来源与路径，不能把另一套条目因同名永久丢弃。
- 提示词说明：未指定来源时使用默认组；用户明确指定来源时使用该组，指定条目不存在应说明找不到，而不悄悄替换。读取正文后按 Skill 目录解析相对引用，使用相应路径调用通用工具。Skill 是任务指令，不能扩大工具或文件权限。
- 普通 run 与审批恢复 run 均重新发现、冻结本 run 索引；同一 run 的各次模型请求保持该索引。正文/引用在 read 调用时读取当前文件版本，不能把索引快照宣称全部文件快照。下一 run 更新名称/描述/默认来源。已读取正文作为普通 ToolMessage 纳入状态、计数、摘要与 checkpoint；移出上下文后仍可再次读取，不保存永久“已经加载过所以不用再读”的状态。
- 发现会读取本地文件以解析 frontmatter，但初始模型上下文只包含索引，不包含全部 Skill 正文。懒加载指正文和引用按需进入模型上下文，不宣称发现阶段完全不读取文件内容。

## 只读资源与调用接口

- 发现结果附带有效 Skill 的规范 `resource_root` 元组。应用只注册上述已确认来源中、仍位于当前工作区且通过校验的目录。索引可见性与读取能力必须同时接线，不能显示一个当前策略无法加载的有效条目。
- 新运行上下文字段 `read_resource_roots: tuple[Path, ...]` 由应用传递，不加入 Read 的模型可见参数 schema。它只允许读取相应 Skill 目录中的普通 UTF-8 文件，包括正文与引用文本；隐藏来源目录的祖先得到有限豁免，资源内部的敏感/隐藏目录和 `.pem/.key` 仍拒绝。实际读取重新解析路径并核对规范目标范围，链接变化不能逃出授权范围。
- 普通工作区文件、工作区外只读审批与一次性 grant 保留现有行为。资源范围不影响 write/edit/bash/ls/find/grep，也不自动执行 `scripts/` 中的代码。模型仍可用 read 读取脚本文本，执行时需要现有 Bash 审批。
- `read(path, offset, limit)`、`classify_read_target(root, path)`、`create_read_tool`、`create_session_read_tool` 的公开签名不变。资源判断作为内部执行策略；会话 Read 的审批谓词和执行重验使用同一资源范围。绑定到固定工作区的旧 Read 默认不具有 Skill 资源权限。
- 新 `agent_runtime/resource_invocation.py` 提供 `invoke_agent_run_with_resources`、`resume_agent_run_with_resources`，接受现有运行身份、workspace_root、消息/审批决定与 grants，以及资源目录元组，均转交同一私有运行入口。原公开入口保留并默认空资源范围。私有入口把可信资源范围放入 Runtime.context，仍只调用一次既有 agent.invoke，不创建新循环。普通运行和中断恢复必须都传入本次范围。
- `application/skills.py` 提供发现结果与本 run 资源准备函数，将项目指令/双来源索引与读取范围作为一组结果返回。应用使用现有每 run 冻结字符串和局部资源元组，不把目录扫描、审批策略或模型选择移入 runtime。没有新增 DB 映射、远程配置或模型凭证读取。

## 影响范围

产品文件：新增 `application/skills.py`、`agent_runtime/resource_invocation.py`；扩展 `application/__init__.py`、`agent_runtime/__init__.py` 和 `agent_runtime/tools/read.py`。预计 5 个产品文件；新发现与新入口各自隔离，所有文件保持低于 500 行。现有公开 API 与工具名称/schema 保持兼容。

新增聚焦测试文件 `tests/test_project_skills.py`、`tests/test_application_skills.py`，按实际接线补现有 Read/审批/提示词断言；不为测试通过而 skip 或放宽现有权限断言。还涉及 Spec、README、handoff、future-work、project-plan 等记录，因此总体超过 8 个文件，但只有一个“Skill 索引到 Read”的完整功能目标，没有新增外部服务。

真实验收保存为 `scripts/probe_skill_loading.py`：父进程拥有并清理临时工作区、限制子进程总耗时；子进程用现有模型配置分别验证默认和点名备用来源，不输出凭证/正文。聚焦 oracle 测试 `tests/test_skill_loading_probe.py` 检查工具实际路径、结果 ID、错误状态及缺失读取，避免把模型猜中标记误判为读取通过。测试的共享文件格式 helper 放 `tests/skill_helpers.py`，不修改 conftest。

本片不建立数据库、Skill 安装服务、HTTP 下载、专用加载工具、完整可信工作区模式或 `/skill:` 命令。来源路径暂限用户确认的项目双目录，用户级/共享外部来源待独立配置切片。`.env`、真实模型配置、依赖锁定、旧参考项目及《关键知识点》均不修改。

## TDD 与验收

1. 先写失败测试验证两套分组/默认和备用、同名保留、agents 空/无效时 claude 默认、无 Skill 不加清单、同来源冲突与坏文件诊断、XML 转义和跨工作区链接排除。
2. Read 红灯覆盖隐藏 Skill 正文/引用读取、原参数与同 ID ToolMessage、严格 UTF-8/分页及敏感资源拒绝；符号链接改向工作区外不被资源许可绕过。旧隐藏目录拒绝、外部审批和 grant 测试继续通过。
3. 完整应用 + 真实 create_agent + 假模型：初始请求含两组索引但不含正文；分别请求 agents/claude 的绝对路径，下一次请求含对应实际正文；模型显式选择备用来源的能力与提示词内容分开断言，不把脚本化假模型当成自主选择质量。
4. 两会话工作区索引与资源范围隔离；同 run 索引固定、下一 run 更新；进入 HITL 再恢复后仍能读取当前有效 Skill；读取失效只产生说明性错误 ToolMessage；索引进入正常/压缩/溢出恢复预算。原工具/模型响应门禁、消息队列、终局与状态保存均回归。
5. 全量 pytest、compileall、pip check。确定性链路通过后，用临时工作区中的真实完整 Skill 做有界真实模型探针，记录默认来源与用户点名备用来源时实际调用路径、正文是否进入后续调用；一次成功不等于所有模型都严格遵循来源约定。不修改真实 `.env`，不为了探针执行未经批准的脚本。

实现前范围修正先同步本文；验收结果按存在、真实实现、应用接线及模型效果分别记录。

## 实施与验证证据（2026-10-07）

- 基线 `.venv/bin/python -m pytest -q` 为 `515 passed in 15.54s`。新增应用测试红灯为 `6 failed, 6 passed in 0.72s`：没有索引、资源读取/隔离/恢复/预算未接线；发现测试先因缺少 skills 模块在收集阶段退出 2。实现后大部分通过，预算样本最近用户消息过短使现有切点为 0 正确跳过，调整合成 keep=5 以实际覆盖完整旧轮次预算，未放宽断言。
- 额外红绿反例：Skill 元数据链接指向其他隐藏文件时，原发现模块仍广告不可读条目，增加规范隐藏路径检查后转绿；可选资源目录在发现后消失时，原入口在 IO 重验处抛异常终止，改为入口仅检查应用快照的类型/绝对路径/工作区范围，Read 执行时重新核对 filesystem 并回填错误 ToolMessage；跨工作区与隐藏目标诊断原来只记 generic skill_unavailable，补固定安全原因后两个反例转绿。未把原始异常文本写入诊断。
- 确定性专项 `.venv/bin/python -m pytest -q tests/test_project_skills.py tests/test_application_skills.py tests/test_skill_loading_probe.py --tb=short` 为 `36 passed in 0.87s`。新增发现/资源入口是真实实现，应用普通运行与审批恢复均使用它，Read schema 未增加模型可设置的权限字段；未注册会话 Read 时不广告无法加载的 Skill。正文、引用、分页、编码错误、权限错误、同 ID 结果、快照/刷新、隔离与摘要/溢出预算均经真实 create_agent 验证。
- 收尾反例均先红后绿：collision 返回结果保留赢家/被遮蔽位置供本地诊断，但日志只收固定来源/原因/异常类型；合法工作区内链接导致已注册目录重叠时，宽目录的隐藏相对路径限制不能遮蔽窄目录的明确读取范围，Read 继续检查其他注册目录，未匹配仍回到原拒绝/审批策略。敏感目标全局拒绝保持，原权限断言未放宽。
- 最终 `.venv/bin/python -m pytest -q` 为 `551 passed in 15.36s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。产品与测试单文件均小于 500 行，原公开 API、工具名称与参数 schema 保持兼容，无依赖/真实配置变更。
- 真实命令 `LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m scripts.probe_skill_loading --config .env/models.json --total-timeout-seconds 120` 退出 0，输出 `phase=default_verified source=agents read_calls=2`、`phase=explicit_verified source=claude read_calls=2`、`status=PASS default_source=agents explicit_source=claude body_and_reference_reads=verified tool_messages=paired`。使用配置默认网关模型与父进程拥有的临时工作区；两套同名 Skill 均为完整受控任务指令。真实调用分别读取正文与引用，最终标记与实际引用结果一致，未批准任何额外操作。未独立验证网关远端模型身份，不把一个受控任务扩展为任意模型/Skill 的来源服从率或业务效果。
- 限制：只发现确认的两个项目来源下一级 Skill 目录；跨工作区共享链接（包括现有 rndflow）诊断排除，未开放用户级或远端来源。Skill 文件解析限 64 KiB；正文/引用按需读取当前版本，索引冻结不是文件快照。只有 registered read 支持的普通文本资源获得范围，其他工具不因此扩权。来源选择依赖模型按提示词约定行动；没有确定性自然语言分类器。路径解析与打开之间的 TOCTOU 竞态仍未解决，不宣称 OS 沙箱或 race-free 文件隔离。学习状态只记用户确认范围，新增代码尚未逐段学习/独立实现。
