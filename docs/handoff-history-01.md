# Naiyue Test Agent：开发学习交接

更新日期：2026-09-29。当前阶段：真实最小工具循环、按会话选模及首个假模型结构化折扣用例设计均已做分级验证；真实业务模型与 HTTP 执行尚未接线。最新状态见文末，前文保留阶段历史。

## 1. 用户与项目目标

用户网名 naiyue，有三年测试开发经验，正在准备大厂测试开发及 AI 测试相关面试。项目正式目录/仓库名是 `naiyue-test-agent`，展示名是 Naiyue Test Agent。

项目会用于简历与面试，也希望在真实公司测试工作中使用；以生产级工程要求为目标，持续从真实问题中完善。实现需有架构深度、可观测性、评测、异常处理、权限边界和实际证据，不能只做堆叠术语的演示。

AI 助手负责主要工程代码，同时担任导师。学习目标：用户能独立阅读调用链、编写关键代码、增加工具、修改上下文策略、接入模型、定位故障并解释实验；能应对面试连续追问。不能以 AI 写完项目代替用户掌握，也不承诺面试结果。

## 2. 已确认与未确定

已确认：Python + LangChain create_agent + 自定义 middleware；类似 Pi 的标准模型—工具循环；四层架构；多模型配置与选择；RAG、记忆、工具和评测模块化；边学习边开发。

首批业务为 HTTP/RPC 接口测试：基于知识库设计用例，结合源码和用例生成自动化测试。原 AIAutoTest 内已有待测示例、文档和源码可供核对，具体业务模块尚未指定。

开发环境已确定为 Python 3.12 + venv + pip，具体已安装版本见第 10 节。未确定：应用包名、首个真实模型配置、具体工具契约、存储、沙箱实现和部署方案。进入相关模块时再讨论，不因此重新选择总体框架。

首期预算约 15–30 小时，含学习。完整 RAG、多种文档、记忆、MCP、安全沙箱、本地推理与 SFT/偏好优化属于分期目标；未实现、未接线或未验证的能力明确标识。

## 3. 当前证据

| 项目 | 状态 |
|---|---|
| 总体选型、模块边界 | 用户已确认，设计文档已保存 |
| 独立项目与本地 Git | 本次初始化，默认分支 main；无提交、无远程仓库 |
| AgentX 调研 | 2026-09-23 静态源码记录，不是运行验证 |
| 应用、依赖与环境 | 尚未创建应用或安装依赖；没有模型凭证配置 |
| 真实模型、工具循环、RAG 与评测 | 尚未在新项目运行验证 |
| 用户对新框架的独立实现能力 | 尚未在本项目考察，不能标记为已掌握 |

本次只检查初始化文件、内部链接、Git 状态和忽略规则；尚无应用构建/测试命令。

## 4. 下一个单元

主题：最小 Agent 链路与基础轨迹。

先用一个真实测试任务的简化例子讲清：模型消息是什么、工具描述怎样传给模型、tool call/result 怎样配对、模型为何继续下一轮、每轮实际上下文如何记录。

然后核对当前环境，讨论这个单元的模型选择、工具契约、目录与依赖方式，写阶段 Spec 后实现。模型凭证只通过受控配置提供，不请求用户在聊天中粘贴密钥。

候选验证目标：

- 实际模型请求工具，工具结果进入下一轮输入并产生最终回答。
- 自定义上下文能在实际模型输入中查证。
- 工具失败有可关联的错误事件，并按明确策略反馈或终止。
- 模型/工具开始、完成、错误记录关联到同一次运行。
- 一个独立评分器能根据轨迹判断指定行为，而不是根据 Agent 自述打分。

以上是待细化的单元目标，不是已经批准全部接口和文件布局的实现 Spec。

## 5. 教学与记录

每天 1–2 小时按一个小目标推进：讲解 → 局部设计 → 实现 → 运行验证 → 用户复述/独立练习。

讲解以真实代码和数据流为中心，逐步引入框架抽象；通过增加工具、修改行为、复写核心片段或解释失败轨迹检查掌握情况。记录区分“讲解过”“提示后理解”“独立完成”和“能应对追问”。

下一会话不应一次生成整个项目，也不应直接跳过阶段设计安装整套依赖；复杂功能遵循 Spec 与验证规则。确定本次范围后连续实现，不逐步重复询问许可。

## 6. 新会话首条消息

> 请先阅读 README.md、AGENTS.md、docs/handoff.md 和 docs/project-plan.md，继续 Naiyue Test Agent 项目的开发学习。
>
> 我有三年测试开发经验，这个项目要用于大厂面试，同时以能在真实工作中使用的生产级工程要求为目标，需要有架构、实现和评测深度。请用真实实现和验证证据说明能力及限制，不编造规模或业务收益。
>
> 我们已选定 Python + LangChain create_agent + 自定义 middleware，采用模型接入、Agent 运行、测试 Agent 策略、应用入口四层架构，支持多模型配置；RAG、记忆、工具和评测独立。沿用已确认路线，不重新选框架。
>
> 代码主要由你实现，但你必须带我学会 Agent：能独立读懂调用链、写出和修改关键代码、定位问题，解释机制与取舍，并应对面试追问。每次先讲清具体机制，再讨论局部设计、实现和验证，最后让我复述或独立做小练习；不要把你写完代码当成我已经掌握。
>
> 首期每天 1–2 小时、约半个月，学习包含在预算里，后续持续完善。今天从“最小 Agent 链路与基础轨迹”开始，先讲消息、工具调用与结果回填，再确定环境和阶段 Spec；按 AGENTS.md 推进，不一次性生成整个项目。

## 7. 参考与文档归属

原平台 `/Users/zmw/AIAutoTest`、Pi `/Users/zmw/naiyue-personal-workspace/pi`、AgentX `/Users/zmw/AgentX` 仅作为参考来源；读取前核对当前状态，不能将旧调研记录当作当前运行事实。

新项目工作记录在本目录维护。旧会话保留面试准备与前期讨论；新会话以文件中的最新决策、进度与学习证据衔接。

## 8. 首个单元启动记录（2026-09-28）

- 已阅读 README、协作规则、本交接与项目计划，沿用 Naiyue Test Agent 项目名及已确认选型。
- 仓库检查：`git status --short` 显示初始化文件尚未跟踪；`git log -1 --oneline` 返回 main 尚无提交。文件清单尚无应用代码、依赖清单或测试。
- 环境检查：`command -v python3` 返回 `/usr/bin/python3`；`python3 --version` 返回 `Python 3.9.6`；`uv --version` 返回 `command not found: uv`。这些结论限定为本次终端环境，未证明整台机器没有其他 Python 环境。
- 已核对 LangChain 官方安装及消息文档：当前要求 Python 3.10+；工具请求通过 AIMessage 表达，结果通过带对应 tool_call_id 的 ToolMessage 回填。尚未安装或验证项目依赖版本。
- 本轮讲解以“读取接口契约后提出测试点”为教学示意，覆盖工具描述、请求、实际执行、结果回填及下一轮模型输入；示意消息不是真实业务资料或运行证据。
- 候选最小范围：单个只读契约工具、create_agent 循环、模型/工具边界轨迹、独立规则评分器；环境方式、真实模型、契约来源与错误策略仍待讨论，尚未冻结 Spec。
- 学习证据：已提供首轮机制讲解；用户尚未复述或独立练习，不能标记为理解、独立实现或可应对追问。
- 验证边界：本轮仅做文档与环境检查；没有应用构建、单元测试、真实模型调用或业务效果验证。
- 唯一下一步：检查用户对工具执行和结果回填的理解，再确认环境与模型信息，收敛阶段 Spec。

## 9. Python 3.12 安装（2026-09-28）

- 用户要求检查 Python 3.12，不存在则安装。常见安装位置未发现 3.12，`brew info python@3.12` 明确返回 Not installed；Homebrew 原有 3.9 与 3.14。
- 已执行 `HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 brew install python@3.12`，退出码 0。安装 Python 3.12.14；Homebrew 同时升级了 ca-certificates、openssl@3、sqlite、xz 依赖。
- 验证：`/opt/homebrew/bin/python3.12 --version` 返回 `Python 3.12.14`；`/opt/homebrew/bin/python3.12 -m pip --version` 返回 `pip 26.2.1`，对应 Python 3.12。
- 最小运行检查：Python 版本断言、ssl/sqlite3/venv 导入及内存 SQLite 查询通过，输出 `Python 3.12 + ssl + sqlite3 + venv: PASS`。
- 当前终端默认 `python3 --version` 仍为 3.9.6；使用 `/opt/homebrew/bin/python3.12` 可明确选择新版本。未修改 shell 配置、项目环境配置，未创建项目虚拟环境或安装应用依赖。
- 本次验证仅证明解释器和上述基础模块可运行，不代表 Agent 应用构建、测试或模型集成通过。学习理解检查及阶段 Spec 仍待继续。

## 10. Agent 核心依赖安装（2026-09-28）

- 用户明确要求安装 Agent 依赖。采用已安装的 Python 3.12.14，在项目根目录创建 `.venv`，使用 venv + pip；未新增环境管理器。
- 安装命令：`/opt/homebrew/bin/python3.12 -m venv .venv`，随后 `.venv/bin/python -m pip install langchain pytest`，均成功，组合命令退出码 0。
- 直接依赖：langchain 1.4.2、pytest 9.1.1；关键传递依赖：langchain-core 1.6.5、langgraph 1.2.12。完整版本快照已保存到 requirements.txt，适用本次 Python 3.12 / macOS arm64 环境。
- `.venv/bin/python -m pip check` 实际输出 `No broken requirements found.`；`.venv/bin/python -m pytest --version` 输出 `pytest 9.1.1`。
- create_agent、AgentMiddleware、wrap_model_call、wrap_tool_call、AIMessage/HumanMessage/ToolMessage、tool 均可导入；解释器断言确认版本为 3.12 且位于独立虚拟环境。README 保存可复用导入检查命令。
- 未安装模型提供方适配包，尚未确定模型；未修改 .env 或 shell 配置，未执行真实模型请求。LangSmith 为传递依赖，不能据安装推断项目已配置轨迹采集。
- 当前无应用代码、构建目标或测试用例；pytest 版本检查不是测试套件通过，接口导入也不是模型—工具循环验证。
- 用户理解状态仍为已讲解、待复述；本轮环境安装不增加掌握程度证据。
- 唯一下一步：确认首个真实模型的提供方与模型名，据此补适配包并收敛最小链路 Spec。

## 11. HeiyuCode / gpt-5.5 接入准备（2026-09-28）

- 用户指定使用 https://www.heiyucode.com 测试，模型先用 gpt-5.5。
- 已通过浏览器读取站点官方文档 https://docs.heiyucode.com/docs/#codex-setup：示例模型为 gpt-5.5，base_url 为 https://www.heiyucode.com，wire_api 为 responses，要求 Codex 对应分组的 API Key。这是站点宣称的接入契约，不是后端模型身份或可用性证明；未修改 Codex 配置。
- 无密钥路由探测：向 https://www.heiyucode.com/responses POST 空 JSON，实际返回 HTTP 401、application/json。仅证明路由可达且要求认证；未完成模型请求。
- 安装命令：`.venv/bin/python -m pip install -c requirements.txt langchain-openai`，退出码 0。新增 langchain-openai 1.6.6、openai 3.19.2 及所需传递依赖，保留原有固定版本。
- 当前项目未发现 .env，相关密钥环境变量未设置。已请用户将该站点 API Key 在本地保存为项目 .env 中的 OPENAI_API_KEY，或只提供已有密钥文件的路径；不在聊天、日志或文档中记录密钥。
- ChatOpenAI 拟使用 use_responses_api=True，正式带鉴权兼容性仍需真实调用确认。未创建业务实现或扩展第一阶段范围。
- 唯一下一步：用户提供本地凭证位置后，先执行受限的最小模型请求，再验证工具调用与结果回填；分别记录各级证据。

## 12. 真实模型与工具循环验证（2026-09-28）

- 用户确认密钥已写入。实际发现 `.env` 为目录，凭证位于 `.env/passwd.md`，内容为 OPENAI_API_KEY 赋值；直接读取使用，未修改用户文件。`git check-ignore .env/passwd.md` 命中忽略规则。
- 最小请求：ChatOpenAI，Responses API，streaming=True、store=False、timeout=45、max_retries=0；输入仅为连接检查。实际返回 CONNECTION_OK，响应元数据 model_name=gpt-5.5，input_tokens=28、output_tokens=7、total_tokens=35，耗时 12.38 秒。
- 新增独立诊断脚本 scripts/probe_tool_loop.py，不属于正式业务模块。模型与 URL 通过命令参数传入；凭证文件只读。脚本不发送业务资料，关闭本次进程的托管 tracing，不打印密钥。
- 真实验证命令：`.venv/bin/python scripts/probe_tool_loop.py --key-file .env/passwd.md --base-url https://www.heiyucode.com --model gpt-5.5`，退出码 0。
- 结果：status=PASS；2 次模型调用、1 次工具执行；工具请求及结果 ID 匹配；第二次模型调用的 middleware 输入消息类型为 human / ai / tool，其中 ToolMessage 含本次工具的实际随机值；最终模型回答精确匹配随机值。
- 轨迹顺序：model_start → model_end → tool_start → tool_end → model_start → model_end。脚本断言检查必要行为，并通过两次模型调用上限限制循环；它不是独立业务评分器或持久轨迹系统。
- 两次模型调用报告 token 用量分别 91 与 125，合计 216，耗时 8.94 秒；加上最小请求本轮合计 251 tokens，计费金额未核验。两次响应元数据均为 gpt-5.5，不作为网关后端身份的独立证明。
- `.venv/bin/python -m py_compile scripts/probe_tool_loop.py` 退出码 0；`.venv/bin/python -m pip check` 输出 No broken requirements found.。这是语法/依赖检查和一次真实诊断，尚无应用构建目标或单元测试套件，不能报告全量业务测试通过。
- 未验证：工具失败恢复、超时故障演练、正式可观测性、业务测试点质量、RAG、权限与沙箱。用户理解仍为已讲解待复述，尚无独立实现证据。
- 唯一下一步：结合真实脚本检查用户对第二轮消息与 tool_call_id 的理解，然后收敛正式阶段 Spec。

## 13. 诊断脚本讲解记录（2026-09-28）

- 用户先询问 `line.strip().removeprefix('export ').partition('=')` 的含义，已用单行赋值示例讲解三段拆分与缺少等号时的结果；未收到复述证据。
- 随后要求讲解 `scripts/probe_tool_loop.py` 核心代码。本次按凭证读取 → 随机诊断工具 → 模型/工具 middleware → create_agent 调用 → 消息与结果断言的执行顺序讲解，以第 12 节的实际运行轨迹为例。
- 学习状态：这些机制已讲解；尚未观察用户独立复述、改动或排查代码，不能标记为独立掌握。
- 唯一下一步：请用户解释 `handler(request)` 与第二次模型输入中 ToolMessage 的作用，再按理解情况推进阶段 Spec。

## 14. 协作规则更新（2026-09-28）

- 用户要求将生产级目标、真实项目式的最小 MVP/选型/优化/评测、功能驱动 TDD，以及自写核心代码不跳行的教学方式写入 AGENTS.md；已更新，并在项目设计记录同步原则。
- 已建立 `.agents/skills/rndflow` 到个人工作区原 skill 的符号链接；SKILL.md 和两个 references 均可解析。没有复制或修改原 skill；该本机链接依赖相邻工作区的存在。
- 已阅读 project-learning skill，将其“先建地图、按调用链、逐段检查理解”作为教学参考；逐行讲解的强度以用户本次明确要求为准。
- 本次仅修改规则与文档，执行文件链接与文本检查；没有改业务代码、没有运行模型或业务测试。用户掌握程度未因规则更新而改变。
- 唯一下一步：先完成当前 `probe_tool_loop.py` 核心路径的逐行学习，再按新 TDD 规则收敛第一个业务功能 Spec。

## 15. 教学方式校准与当前理解证据（2026-09-28）

- 用户明确指出自己具备测试工程师所需的 Python 基础；后续跳过基础语法，核心代码与 Agent 框架机制保持深入讲解。首次系统做 Agent 项目，先厘清要做什么和怎么做，不急于写业务代码。
- 对 `probe_tool_loop.py` 的实际互动表明：只解释列表推导式而不展示 `messages` / `msg.tool_calls` 结构，会造成理解缺口；改为用 HumanMessage、AIMessage、ToolMessage 的具体对象示例及真实本地构造输出后，再解释筛选与变量填充。
- 用户自述已理解参数输入和最终验证链路，并在解释 `('user', text)` 到 HumanMessage 的框架转换后表示理解。该证据为“引导后自述理解”，不是独立编写、调试或面试追问能力验证。
- 已把基本 Python 略过、复杂语法等价展开、真实数据形状先行、变量生产者与逐步数据流、框架底层转换/状态/执行边界的核验要求写入 AGENTS.md，并同步总体计划。未改业务代码、未重复运行模型。
- 唯一下一步：继续围绕已跑通的最小 Agent 链路学习具体框架机制；待用户与助手共同明确第一项业务功能、边界和验收标准后写阶段 Spec，再按 TDD 开发。

## 16. Pi Agent 与 AgentX 设计参照（2026-09-28）

- 用户要求后续 Agent 开发和架构讨论多参考 Pi Agent、AgentX 的成功开源实践，结合本项目给出设计建议。已写入 AGENTS.md 和总体计划。
- 本次只确认两个本地参考仓库仍存在，当前 HEAD 分别为 Pi `a69bef789`、AgentX `3fcfa0e`；未重新审阅其具体调用链。既有 `docs/agentx_architecture_reference.md` 为 2026-09-23 静态记录，具体方案讨论时须按当前源码复核。
- 建议格式：当前功能问题 → 两个项目中相关做法及源码证据 → 可迁移原则与差异 → 对本项目的推荐和验证方法。保持已确认的 Python + `create_agent` 路线，除非出现新证据需要重新评估。
- 本次只改协作规则和文档，没有改业务代码或运行应用测试。用户学习状态无新增独立能力证据。
- 唯一下一步：继续当前 Agent 消息、工具与 middleware 机制学习；进入具体设计时按上述方式对照 Pi/AgentX 后确定最小功能 Spec。

## 17. Agent 生命周期与多模型会话定向调研（2026-09-29）

- 用户要求明确 Pi Agent、AgentX 的参考理由，并核查“共享 Agent + 动态 middleware”与“新会话新 Agent”何者常见、各有什么影响。已补充 `AGENTS.md`；完整源码依据、方案比较和候选建议见 `docs/model-session-lifecycle-research.md`。
- 调研发现还存在“每次请求构造 Agent”这一不同维度：Pi 新会话替换运行时；AgentX 流式请求按次装配；LangChain 官方示例复用编译图并用 `thread_id` 分离状态，也支持 middleware 动态选模；DeepAgents 接受共享图或会话工厂。样本不支持“行业多数”断言。
- 当前建议是固定会话模型时先在入口解析配置，按请求构造 Agent，用稳定 `thread_id` 和 checkpointer 延续会话；动态 middleware 留给真正的按轮选模或 fallback。此为待用户共同讨论的方案，不是已确认设计或已实现功能；未修改 `docs/project-plan.md`。
- 验证：阅读两个本地仓库当前源码及 LangChain 官方文档；调研文档给出可重复的假模型 A/B/A 交错验证命令，实际退出码 0，消息数为 2、2、4；三份文档结构检查通过。未运行真实多模型 API、性能基准、跨进程恢复或业务测试，学习状态无新增独立实现证据。
- 唯一下一步：围绕“会话固定模型是否允许中途切换、会话需否跨进程/重启恢复”确认 MVP 边界，再写阶段 Spec 和确定性测试。

## 18. 分层、模型切换和入口形态讨论（2026-09-29）

- 用户确认同一会话允许换模型，并要求对照 Pi/AgentX 理清 Pi-AI、Agent 循环、会话和工具的对应职责，遵守上层依赖下层的项目分层，同时比较本地 Web 与可执行应用。候选静态依赖图、运行时序图和取舍见 `docs/agent-runtime-architecture-draft.md`。
- 框架已经提供通用模型接口、模型—工具循环、工具结果回填与 checkpointer 机制；项目仍需会话归属、选模记录、工具业务实现与权限、切换规则及验证。源码/官方文档依据列在草案，不把来源做法当成项目已实现。
- 用户进一步说明希望参考 Pi 的 turn 边界。已核对 Pi 的 `turn_end → prepareNextTurn` 与会话模型状态刷新，把草案修正为“本轮模型与工具完成后，下一次模型调用前生效”，不要求整条用户请求先结束；Pi 核心事件名是 agent/turn，trace 是另一个观测概念。
- 已用项目虚拟环境的假模型/工具及 `wrap_model_call` 做无网络机制验证：同一次 `invoke` 两次模型调用依次选择 A、B；消息序列 human/ai/tool/ai，最终 B-reply，命令退出码 0，输出 `turn-boundary-model-switch: PASS`。这不证明应用层并发、持久化、跨供应商或实际用户切换实现。
- 建议首期先以 CLI 验证核心，再考虑本地 Web 作为展示入口；桌面打包延后。此为候选建议，入口形态和忙碌会话中的切换提交规则尚待决定。未改业务代码、依赖或配置；用户本轮提出了 Pi 机制类比，仍未有独立复写或追问能力证据。
- 唯一下一步：明确忙碌会话中的切换提交/取消语义和首期重启恢复要求，再把草案收敛为阶段 Spec；入口选型可随后定稿。

## 19. 本地 Web 与持久会话分期（2026-09-29）

- 用户确认首期要有可展示界面，为未来 SaaS 保留应用服务边界；当前不做多用户或账号登录。已将本地 Web 作为首期入口方向，CLI 先验证核心，更新 README、总体计划和架构草案。现有 AIAutoTest 本地 Console 只作参考，不自动复用。
- 用户明确持久会话是最终目标，并要求评估首期必要性。当前建议首个 Agent 技术切片使用同进程内存会话元数据 + `InMemorySaver` 验证模型切换、消息和工具回填；核心 MVP 优先 HTTP/RPC 业务链路、轨迹/评测与本地 Web。跨重启恢复紧随其后为独立切片，届时同时持久化会话元数据和 Agent checkpoint 并做重启测试，不能只保留会话列表。
- 最早固定的契约是外部 `session_id` 与内部 `thread_id` 分离、会话读写经过应用服务、模型切换记录版本/事件；现在不创建数据库表、仓储接口或占位适配器。当前仅有内存 checkpointer 依赖，SQLite/Postgres 插件未安装；未进行持久会话或 SaaS 运行验证。
- 本地 Web 无账号登录仍需回环监听和最小 API 访问控制；未来对外 SaaS 部署前必须补身份、会话授权、租户隔离。此轮只更新设计文档，未改业务代码、依赖、环境配置或用户学习能力记录。
- 唯一下一步：确定忙碌会话中的切换提交/取消规则和首个 HTTP/RPC 测试场景，收敛第一阶段 Spec；持久化放在后续独立 Spec。

## 20. 首个运行层小切片：模型调用边界选模（2026-09-29）

- 用户同意开始，但要求每次只做一个小目标、完整文档与边学边练。本次选择 Agent 运行层中的 `wrap_model_call` 选模边界；阶段 Spec 为 `docs/specs/001-model-selection-boundary.md`，讲解笔记为 `docs/lessons/lesson-1.md`。
- 先写测试并实际得到红灯：`.venv/bin/python -m pytest -q tests/test_agent_runtime.py` 退出码 2，因 `naiyue_test_agent` 模块尚不存在而收集失败。随后只新增 `naiyue_test_agent/agent_runtime.py` 和包入口：每次模型调用从运行 context 读取可信 `session_id`，注入的解析函数返回模型 ID/对象，记录选择，`handler(request.override(model=...))` 继续框架循环。缺失 ID 失败关闭，解析失败不吞掉。
- 确定性接线测试绿灯：同命令输出 `3 passed in 0.16s`，覆盖一次 `invoke` 的 A→工具→B、s1/s2 隔离、缺失会话标识。正式应用会话服务、Web、数据库、真实多供应商与持久轨迹均未接线；本单元只验证运行层机制。
- 全量回归：`.venv/bin/python -m pytest -q` 输出 `3 passed in 0.13s`；`.venv/bin/python -m py_compile naiyue_test_agent/agent_runtime.py tests/test_agent_runtime.py scripts/probe_tool_loop.py` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。文档/源码结构检查输出 `artifact-check: PASS`。此时项目尚无单独构建目标。
- 本节核心代码已写入 lesson 逐段/逐行讲解。用户尚未复述或独立练习；学习状态只记“已讲解”，其余理解/独立实现/面试追问仍待验证。
- 唯一下一步：在本节理解检查后，选择首个 HTTP/RPC 业务场景，再设计应用会话服务与真实工具的下一小切片。

## 21. 分层命名与首期实施路线（2026-09-29）

- 用户针对当前 `agent_runtime.py` 提问：`ModelRequest[RunContext]` 属于泛型类型标注，`request.runtime.context` 是运行 context，`handler(request.override(model=model))` 在新请求上继续框架模型调用。已按实际对象形状、执行顺序和 A→工具→B 数据流讲解；用户明确表示最后一行已理解。仅记录**引导后自述理解**，没有独立改动或面试追问证据。
- 用户要求设计不同架构层的命名并按计划开始。已对照本机 Pi 的 `pi-ai`/`pi-agent-core`、AgentX 与项目现有代码，统一逻辑四层的暂定名称：`model_access`、`agent_runtime`、`test_agent`、`application`。`create_agent` 仍拥有基本循环，本项目当前只有运行边界 middleware，不声称实现了 Pi 式 Agent core。
- 新增 `docs/implementation-roadmap.md`，说明职责、依赖方向、首期单场景闭环的步骤和分级验证；README 增加入口，project-plan 记录总体决定，运行架构草案标明五节点只是四层的组件展开。未创建空包、未修改生产代码、依赖、环境文件或外部待测系统。
- 本次验证：`.venv/bin/python -m pytest -q` 输出 `3 passed in 0.17s`；本地文档链接检查输出 `local-link-check: PASS`。文档修改没有新增功能，因此没有 TDD 红灯或业务运行证据；项目仍无独立构建目标。
- 首个业务场景候选已在 AIAutoTest 源码中核对：只读 `GET /api/v1/coupons/{user_id}` 与会发券的 `POST /api/v1/recommend`。具体选择仍待用户回复；推荐先用只读查询接口。选择后先核对契约、源码、环境与固定验收样本，再写下一阶段 Spec，不把历史文档直接当成真值。
- 唯一下一步：确定首个业务场景并完成该场景的输入、预期与失败边界 Spec；之后按 TDD 接通最小应用链路。

## 22. 用户选定 discount_system 与首个业务契约（2026-09-29）

- 用户在选择首个场景时明确改用更简单的 `discount_system`；停止后续 coupon_system 设计。已只读核对 `/Users/zmw/discount_system` 的公开 API 文档、`app.py`、`models.py`、`policy.py`、`store.py`，并看过原 AIAutoTest 的相应用例资产；没有修改两个参考项目。
- 新增 `docs/specs/002-discount-policy-first-scenario.md`：首个具体任务为根据公开 API 设计“黑名单、活动、空库存同时命中时黑名单优先”的 HTTP 用例；明确请求、预期、来源边界、成功评估的内存存储副作用以及后续 GET/DELETE 的验证与清理。公开文档可给 Agent，SUT 源码和历史用例只供验收核对，避免把答案直接作为输入。
- SUT 本机自有测试命令 `PYTHONPATH=src python3 -m pytest -q tests/test_api.py tests/test_policy.py` 实际输出 `11 passed in 0.16s`；另用 `TestClient(create_app())` 对三条件冲突样本做进程内 POST→GET→DELETE→再次 GET 探测，输出 `priority-contract-probe: PASS`、状态 `[200, 200, 200, 404]`、原因 `USER_BLOCKED`。它们不代表新 Agent 业务链路已实现或网络服务已启动。新项目本轮最终 `.venv/bin/python -m pytest -q` 输出 `3 passed in 0.16s`，文档链接/格式检查输出 `document-check: PASS`。更新了 README、项目总体计划与实施路线的场景指向。无新业务代码、模型请求、环境文件修改或 SUT 运行服务。
- 唯一下一步：为“从公开文档设计一条结构化折扣优先级用例”画清输入/输出与校验边界，写下一小单元技术 Spec，随后先写失败测试再实现；HTTP 执行与独立评测单独接续。

## 23. 假模型结构化用例设计小单元（2026-09-29）

- 用户确认继续。按 `docs/specs/003-discount-case-design.md` 只做公开文档→结构化测试用例→基本校验，不执行 HTTP、不读取待测源码或既有用例作为 Agent 输入。新增 `naiyue_test_agent/test_agent/discount_case_design.py` 和包入口；Pydantic schema 描述公开请求/响应字段，`build_discount_case_agent` 提供 `ToolStrategy(DiscountCase, handle_errors=False)` 等测试策略；既有 `agent_runtime.py` 新增 `build_runtime_agent` 持有 `create_agent` 装配入口和选模 middleware，保持下层不 import 测试策略。`design_discount_priority_case` 调用后检查引用原文与请求/预期 ID 一致，并返回用例及内存消息。
- TDD 首次红灯：`.venv/bin/python -m pytest -q tests/test_discount_case_design.py` 因 `naiyue_test_agent.test_agent` 不存在而收集失败，退出码 2。首次实现后 4 个目标测试通过；新增 ID 不一致测试实际红灯 `Failed: DID NOT RAISE ValueError`，只在设计结果检查处补一项比较后，目标测试 `6 passed in 0.13s`。测试覆盖正常结构化输出、空文档、缺少必填字段、引用不存在、ID 不一致及模型错误原样上抛。
- 装配归位后的最终完整回归 `.venv/bin/python -m pytest -q` 输出 `9 passed in 0.14s`；`py_compile` 覆盖新旧项目代码与测试退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`；文档链接/格式检查输出 `document-check: PASS`。额外用 SUT 真实公开文档文本和假模型固定输出做只读探测，输出 `full-public-contract-probe: PASS`、两段引用均存在。测试假模型的实际消息顺序是 `HumanMessage → AIMessage(tool_calls=[DiscountCase]) → ToolMessage`，结构化结果为 `DiscountCase`；这是输出工具，不是 HTTP 执行。
- 新增 `docs/lessons/lesson-2.md` 记录代码地图、对象形状、执行顺序、异常路径及理解检查。用户尚未看过这份新代码并复述；新单元只记“已实现/假模型验证”，学习状态为待讲解，不能记作独立实现或面试就绪。原来对 `ModelRequest`/`handler` 的自述理解不自动外推到 `ToolStrategy`。
- 未完成：真实 HeiyuCode 模型生成质量、模型配置/会话服务、受控 HTTP 执行、失败轨迹持久化和独立评测。结构/引用校验不判断业务预期是否正确；模型自行提供的 request_id 不应原样用于真实执行。
- 唯一下一步：沿 Lesson 2 用一次实际假模型对象逐段讲清 schema 工具调用、`structured_response` 与 `ToolMessage` 的关系，检查用户理解；随后为真实模型受限验证及 HTTP 执行确定下一小单元边界。

## 24. 连续会话与策略切片的边界澄清（2026-09-29）

- 用户使用 VS Code 阅读当前测试策略后自述“基本看懂”，并指出最终产品应在同一会话连续接受自然语言请求，按任务设计场景、取得用例、生成和执行自动化用例。该自述只记为对当前代码的引导后理解；没有独立修改、调试或面试追问证据。
- 当前 `build_discount_case_agent` 在装配时固定折扣场景提示词和 `ToolStrategy(DiscountCase)`，仅验证单一用例设计能力；它不是最终会话 Agent。用户目标已同步 `docs/project-plan.md`。本节原先提出把“设计用例/生成 pytest”当作业务工具的候选方向已被用户纠正，见下一节。
- 本次只核对现有代码、Pi/AgentX 本地源码和设计记录，并更新文档；没有修改生产代码、环境配置或待测项目，也没有产生连续会话的验证证据。
- 唯一下一步：按用户纠正后的通用工具方向，先核对 Agent 循环与工具职责，再确定下一小切片。

## 25. 通用工具与循环优先的架构纠正（2026-09-29）

- 用户明确希望模型使用 `read`、`write` 等通用工具：设计用例时将 Markdown 写入工作区，生成 pytest 时同样由模型通过写工具落盘；模板先由提示词提供，格式化工具可后续按证据增加。用户还提到 `base`，具体是否指 `bash`/命令执行在工具 Spec 中确认。不能把“设计用例”和“生成 pytest”预设为必须调用的两个业务工具。
- 对照本地 Pi 源码：`packages/agent/src/agent-loop.ts` 处理模型、工具结果与下一轮，`packages/coding-agent/src/core/tools/` 提供 read/write/bash 等具体工具。对照本机 LangChain 1.4.2：`create_agent` 已编排模型→工具→模型；`before_model` 位于工具返回后的下一轮入口，`after_model` 位于工具执行之前，不与 Pi 的 `turn_end` 一一对应。运行中插话或压缩不能仅凭 hook 名称宣称等价，需独立样本验证。
- 当前项目：运行层已装配 `create_agent`、按模型调用边界选模；诊断脚本证明一次真实模型→探针工具→模型，假模型测试证明 A→工具→B。正式测试策略只有固定的 `DiscountCase` 结构化输出实验，`tools=[]`；没有通用文件工具、会话 checkpointer、运行中消息队列或上下文压缩。下一候选切片先写 Spec，以通用工具接线和多次工具往返为验收，不重写框架循环；具体工具先后、安全边界与消息时点仍需确定。
- 本轮只更新设计和交接文档，不修改应用代码、环境文件或外部 SUT。学习证据仍为用户自述基本理解现有策略代码，不能标为独立实现或掌握新循环语义。
- 唯一下一步：和用户确认第一个通用工具切片的最小范围，然后写阶段 Spec、先测后实现，并用真实调用轨迹讲清每一轮。

## 26. `create_agent` 循环与模型接入现状核对（2026-09-29）

- 用户要求深入了解当前安装版本的框架循环，并澄清“框架已有多供应商能力”和“项目尚无多供应商配置模块”是否矛盾。本轮核对 LangChain 1.4.2 的 `factory.py`、LangGraph `ToolNode`、官方文档、项目 `agent_runtime.py`、诊断脚本与测试；具体代码地图、数据流、异常/特例和理解检查已存 `docs/lessons/lesson-3.md`。
- 只读假模型探针实际输出图节点 `__start__/model/tools/__end__`，消息为 human→ai(tool_call)→tool→ai；`.venv/bin/python -m pytest -q tests/test_agent_runtime.py` 输出 `3 passed in 0.12s`。未调用真实付费模型、未修改环境配置。
- 框架已提供统一模型接口和供应商适配机制；项目目前只在诊断脚本创建一个 `ChatOpenAI` 网关模型，运行层的解析函数由测试注入。没有项目级多模型配置加载/注册、凭证引用、正式会话选型或真实第二供应商验证。当前环境只有 `langchain_openai` 集成包，不应将其报告成完整多供应商产品能力。
- 本次只新增学习笔记和交接记录，未实现新功能。用户对此课的复述、独立实现和面试追问能力待验证。
- 唯一下一步：先请用户用 `AIMessage(tool_calls)` 到第二次模型输入的具体对象复述循环，并区分框架适配能力与项目接线；之后确定是否先做最薄模型配置切片，再接通通用文件工具。

## 27. 模型接入与凭证存储调研（2026-09-29）

- 用户确定下一模型接入切片一次覆盖 OpenAI、Claude、本地模型，并要求对照 Pi/AgentX 决定是否现在引入数据库及如何保存 API key。源码、官方文档、适用边界和建议见 [模型接入与凭证存储调研](model-access-storage-research.md)。
- 建议先做三类模型的配置、构造与现有选模 middleware 接线；当前不为模型配置单独引入数据库，也不把密钥明文存 SQLite。将来本地 Web 需要保存非敏感模型元数据时可选 SQLite，密钥持久化单独设计。此项仍是设计建议，未作为功能完成。
- 实际检查：本项目只有 `langchain-openai`；`langchain-anthropic`、`langchain-ollama` 未安装，本机未找到 Ollama 命令。没有安装依赖、读取/修改 `.env`、运行 Claude/本地模型或写应用代码。用户对本次存储/接入设计的理解与独立实现尚未验证。
- 文档更新后回归：`.venv/bin/python -m pytest -q` 输出 `9 passed in 0.19s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。这些结果只说明现有项目未回归，不证明新增模型接入。
- 唯一下一步：根据调研建议确定模型接入阶段 Spec 的字段、凭证来源和三类后端验收边界，再先写失败测试、安装必需适配包并逐步实现。

## 28. 本地模型配置格式设计（2026-09-29）

- 用户选定本地阶段先在 `.env` 目录中配置模型并从文件读取；服务端存储以后另选。设计稿 [Spec 004](specs/004-local-model-config-format.md) 约定 `.env/models.json`，分离 `providers` 与 `models`，覆盖 OpenAI、OpenAI 兼容网关、Claude 与 Ollama；文件在应用启动时校验成内存快照，不做热重载。
- 只做格式与数据流设计，未创建或读取 `.env/models.json`，未读取/修改现有 `.env/passwd.md`，未安装依赖或写加载器。实际 key 不进入文档；用户对此格式及调用链的理解尚未验证。
- 设计验证：从 Spec 004 提取 JSON 示例并用 `json.loads` 解析，检查版本、默认模型引用、四种 `kind` 和模型到供应商引用，输出 `Spec 004 JSON example: PASS`；`git check-ignore -v .env/models.json` 输出 `.gitignore:2:.env`；现有 `.venv/bin/python -m pytest -q` 输出 `9 passed in 0.20s`。这些检查不证明加载器或真实模型已可用。
- 唯一下一步：按 Spec 004 先写配置加载与失败路径测试，再实现文件读取、模型构造及现有选模 middleware 接线；真实供应商调用分级验收。

## 29. 本地模型接入实现与验证（2026-09-29）

- 按 [Spec 004](specs/004-local-model-config-format.md) 实现 `naiyue_test_agent/model_access.py`：严格读取 `.env/models.json`、检查私有文件权限和字段、构造 OpenAI/OpenAI 兼容网关/Claude/Ollama 的 LangChain 模型对象；异常不回显 API key。`agent_runtime.py` 增加 `build_configured_runtime_agent`，加载一次快照并将会话选模回调接到原 middleware；原公开函数签名不变。
- 新增 `tests/test_model_access.py`。TDD 红灯：缺少 `build_configured_runtime_agent` 导致测试收集失败；实现后正常构造、缺失/非法配置、权限、重复键、错误不泄密和假模型 A→工具→B 接线均通过。新增 `langchain-anthropic==1.7.5`、`langchain-ollama==1.1.0`；所需的 `langchain-core` 从 1.6.5 升至 1.6.6，完整环境快照已同步 `requirements.txt`。
- 已从原 `.env/passwd.md` 只读迁移现有网关凭证到新建 `.env/models.json`，原文件未改动、未删除；新文件权限为 `0600` 且 `git check-ignore -v .env/models.json` 确认被 `.gitignore:2:.env` 忽略。实际新配置只含 HeiyuCode/gpt-5.5，未填造 OpenAI 官方、Claude 或本地模型。
- 验证：`.venv/bin/python -m pytest -q` 输出 `21 passed in 0.63s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`；依赖快照与 `pip freeze` 比对输出 `requirements snapshot: PASS`。
- 可重复真实验证：`.venv/bin/python -m scripts.probe_configured_tool_loop --model-id heiyu-gpt-5.5` 输出 `status=PASS model_calls=2 tool_executions=1 final_matches_tool_result=true`。该探针只用合成随机值，模型调用上限 2 次；Claude、Ollama 和 OpenAI 官方仅有构造与确定性接线证据，尚无真实服务/密钥/模型工具能力验证。没有测试业务用例质量、持久会话、Web 或 SaaS 权限。
- 学习状态：本次新增代码尚未与用户逐行讲解；不能记录为“已讲解”“引导后理解”“可独立实现”或“可应对追问”。下一课堂先从 `load_model_catalog` 到 `ModelCatalog.get` 再到 `handler(request.override(model=...))` 用一个具体 `model_id` 追踪数据流，之后让用户复述错误路径。
- 唯一下一步：按上述代码地图分段逐行讲解本次模型配置加载与选模接线，完成一个由用户独立修改的最小配置验证练习；之后再设计通用工具切片。

## 30. 模型配置读取课堂第 1 段（2026-09-30）

- 用户同意继续学习上一轮新增的模型接入代码。本轮按 [Lesson 4](lessons/lesson-4.md) 的路线，只讲 `build_configured_runtime_agent` 调用 `load_model_catalog` 后的文件类型、权限、JSON 解析、重复键与异常路径；供应商校验、构造、`ModelCatalog.get` 和 middleware 留待逐段讲，不将路线表误记为已讲解。
- 脱敏只读观察：`.env/models.json` 是 `0o600` 普通文件；顶层键为 `default_model_id/models/providers/schema_version`；唯一配置链为 `heiyu-gpt-5.5 → heiyu → openai_compatible → gpt-5.5`。未展示或修改密钥；重复键最小示例实际输出 `duplicate-key example: ModelConfigError Configuration has a duplicate JSON key`。
- 学习证据：本段“已讲解”；用户正确判断 `0644` 权限会在读取内容前停止，顶层字段校验讲解后自述“这里我懂了”。这是引导后对局部机制的理解反馈，不是独立实现或可应对追问的证据。`object_pairs_hook` 已用示意 JSON 展示回调顺序，用户未回答该检查题。没有修改生产代码或运行付费模型；本轮无新功能测试结果。
- 后续已讲解 `model_access.py:116-137` 的供应商按 `kind` 分支校验；用户反馈“这块懂了”。这仍是讲解后自述理解，独立实现与追问能力待验证。
- 随后已讲解 `model_access.py:139-152` 的模型引用与 `built_models` 映射，用户反馈“理解了”。这仍是讲解后自述理解；`_build_model` 内部尚待讲解。
- 随后已讲解 `_build_model` 的供应商构造和异常路径、`ModelCatalog.get` 的按 ID 查询。用户对 `dict[str, BaseChatModel]` 的基类实例标注提出问题，解释后反馈“理解了”；仅记录此局部机制的自述理解，独立实现和追问能力仍待验证。
- 已讲解 `build_configured_runtime_agent` 的一次加载、闭包延迟调用与 `(model_id, BaseChatModel)` 返回数据形状；用户反馈“理解了”。这仍是讲解后自述理解，不是独立实现或真实多会话证据。
- 已讲解 `build_runtime_agent` 的 `create_agent` 装配参数，并核对本机 LangChain 实现返回编译图；用户反馈“理解了”。这仍是讲解后自述理解，不是会话服务或多供应商实测证据。
- 已讲解 `make_model_selection_middleware` 从运行 context 取会话 ID、resolver 选模、记录选择及 `handler(request.override(model=...))` 继续调用，并说明缺失 ID 失败路径；用户反馈“懂了”。这属于讲解后自述理解，未证明独立实现或面试追问能力。
- 唯一下一步：逐变量讲解 `tests/test_agent_runtime.py` 的 A→工具→B 验证，再比较真实配置探针的证据边界。

## 31. 模型接入课堂收尾与通用工具开发讨论（2026-09-30）

- 已沿 `tests/test_agent_runtime.py` 的假模型 A→工具→B 逐变量讲解 `selected`、`selections`、消息顺序、工具调用 ID 与最终响应断言；单项命令 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_switches_model_after_tool_result_in_same_run` 实际输出 `1 passed in 0.82s`。用户在讲解后明确要求停止课堂、转向开发讨论；不再继续课程检查或独立练习。尚无用户独立实现、面试追问或真实跨供应商切换证据。
- 本轮只读核对 Pi 的 `packages/agent/src/agent-loop.ts` 与 `packages/coding-agent/src/core/tools/read.ts`、`write.ts`、`index.ts`：循环执行工具，具体文件工具在编码 Agent 层定义并装配。核对 AgentX 的 `AgentMessageHandler.provideTools`、`AbstractMessageHandler.buildStreamingAgent`、`BuiltInToolRegistry.createToolsForAgent`：按 Agent 组装工具定义/执行器后交给 LangChain4j 的 `AiServices`。核对 LangChain 官方 Python Tools 文档：`@tool` 定义参数 schema，`ToolRuntime.context` 可向工具提供本次运行的上下文且不暴露为模型参数。以上是设计参照，不证明本项目文件工具已实现。
- 当前开发候选：先讨论受限只读 `read` 工具，以 `discount_system` 的公开契约为首个目标，验证可信工作区根、路径越界拒绝、内容大小边界、可观察错误以及模型→真实文件工具→模型的结果回填；随后再单独设计 `write` 产物边界。用户先前提到的 `base` 是否指 `bash` 命令工具尚待澄清。未写 Spec、运行代码、配置或新增依赖。
- 唯一下一步：与用户确认第一个工具切片的目标和 `base` 含义，收敛范围后写阶段 Spec，再按 TDD 实现。

## 32. 自底向上推进与模型接入层复核（2026-09-30）

- 用户纠正当前优先级：暂停工具制作讨论，按模型接入 → Agent 运行 → 测试策略 → 应用入口自底向上推进；每层一次只做一个小切片，全程 TDD。已同步 `AGENTS.md`、`docs/project-plan.md` 和实施路线。本节取代上节“工具切片”为当前下一步，不改写上节的历史记录。
- 当前 `model_access.py` 只校验本地配置并构造 OpenAI、OpenAI-compatible、Anthropic、Ollama 的 `ChatModel`；没有显式传思考等级、供应商提示词缓存参数、超时或重试预算。运行层 middleware 在 `handler` 前记录模型选择，该记录表示尝试选模，不能证明请求成功；诊断脚本的 `model_calls=2` 是选模次数，不是底层网络尝试次数。
- Pi 源码显示其上层 thinking/cache 选项由供应商适配器分别翻译；AgentX 的流式错误回调会记录失败和耗时。两者是设计参照，不证明本项目已实现相同能力；AgentX 将原始异常文字回传客户端的做法不能直接用于本项目的敏感信息边界。
- 错误处理评价：配置读取/字段/私有权限等失败已 fail-fast，且当前面向用户的错误一般不带原始凭证；运行时缺少明确的超时、有限重试、错误分类和安全诊断记录。`_build_model` 统一包装构造异常但会丢失细节；探针失败时只输出异常类型。当前还不能评价为满意的生产级错误处理。
- 本轮只做源码、已安装适配器和官方接口文档的只读核对；未修改应用代码、环境配置或进行新网关请求。验证命令 `.venv/bin/python -m pytest -q` 输出 `21 passed in 0.86s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0。这只证明既有代码未回归，不证明思考等级、缓存或运行时错误能力。
- 学习证据：用户之前已确认理解基础模型接入代码；本节三个新问题正在讨论，尚未形成独立实现或面试追问证据。
- 唯一下一步：先与用户确定模型层首个 TDD 切片及思考等级的生效范围（模型默认值还是每次调用可选）；建议先用一个真实故障场景定义模型请求的错误可观测契约，再进入最小实现。工具切片待模型层必要契约验证后再议。

## 33. Pi 式 thinking 降级与错误传播方案讨论（2026-09-30）

- 用户提出项目级 thinking level，并希望按 Pi 语义：请求等级不受支持时先向上找，再向下找；同时倾向于模型层捕获错误、记录事件，再将失败交给上层影响 Agent 循环。这是用户的设计建议，不记录成已批准的完整接口或已实现功能。
- 核对本地 Pi `a69bef789`：`packages/ai/src/models.ts:900-932` 的 `getSupportedThinkingLevels`/`clampThinkingLevel` 正是该搜索顺序；`packages/ai/src/api/openai-responses.ts:181-192` 把请求异常转为 error stream event，`packages/agent/src/agent-loop.ts:317-358` 接收最终消息，`:196-200` 遇 `stopReason=error/aborted` 发 `turn_end`、`agent_end` 并停止。Pi 的供应商请求重试与上层 Agent 会话重试是不同层次。
- 本项目当前 `model_access.py` 只构造模型，请求由 `agent_runtime.py` 的 `handler(request.override(model=...))` 触发。用不改仓库的假模型探针验证：`wrap_model_call` 捕获一次合成 `TimeoutError` 后记录 `('model_failed', 'TimeoutError')` 并重新抛出，`agent.invoke` 向调用方抛出相同异常；没有自动生成正常 AI 消息。该探针只说明当前框架边界的异常传播，不证明取消、流式部分输出或真实供应商错误的处理。
- 建议候选：模型接入层维护每个模型支持的等级及供应商参数映射，公开 requested/effective/native 的可观察结果；运行层在模型调用边界记录脱敏失败事件并传播失败，由上层决定任务状态。不要把错误文本包装成普通 `AIMessage`：本机 LangChain 的 `ModelRetryMiddleware` 默认 `on_failure='continue'` 会这么做，可能让失败被视为一次正常模型响应。若后来启用框架重试，要显式选择可重试错误、次数和终局策略，同时避免与 SDK 内部重试叠加。
- 尚未写 Spec、测试或产品代码；未改 `.env`。唯一下一步：与用户确认 thinking 等级集合和升档成本边界，并据此收敛一个模型层小切片；错误契约先保留上述候选，实施前用失败用例定义事件与上层结果。

## 34. thinking 等级与模型失败类型草案（2026-09-30）

- 用户确认可按上一节的方向开发，但要求编码前先确定项目 thinking level 和模型层失败类型。本轮只做设计讨论，设计基线见 `docs/project-plan.md` 第 16 节；已确定 Pi 式七档显式等级、未指定时沿用模型默认，以及八类失败 category，不将设计基线视为已实现功能。
- Pi 本地源码 `packages/ai/src/types.ts:83-85` 和 `models.ts:900-932` 支持七个有序显式等级及向上再向下选择。用户已选择本项目额外保留“未指定/沿用模型默认”这一非等级选项，与显式 `off` 区分。
- 对照 OpenAI 官方错误文档、Claude API 错误文档和 Ollama 错误说明：不同供应商错误码不能只按 HTTP 状态归类；OpenAI 的 429 可表示短期限流或额度/消费上限，Claude 流式错误可能发生在 HTTP 200 之后。故草案采用稳定 category + 可扩展 reason；把重试决定留给上层策略，默认不重试未知/不能安全重放的失败。
- 本轮未改产品代码、测试、依赖或环境配置；不声称分类、降级或重试能力已接线。八类失败 category 是设计基线，具体供应商映射和事件字段仍待阶段 Spec 与测试。唯一下一步：先写模型层第一个小切片 Spec，按 TDD 验证等级解析和供应商参数映射；错误事件与重试作为后续独立切片。

## 35. 模型接入包与默认 thinking 切片（2026-09-30）

- 用户同意按已讨论建议开始，并提出按层归类现有代码。命名采用已有逻辑层名 `model_access`：把 `naiyue_test_agent/model_access.py` 移为同名包的 `__init__.py`，新增 `thinking.py`；`agent_runtime.py`、脚本和现有测试的公开导入路径保持不变。未预建错误或缓存模块。
- 先写 [Spec 005](specs/005-model-access-thinking.md)，随后新增测试观察红灯：`pytest -q tests/test_model_access.py` 收集时报 `ModuleNotFoundError: No module named 'naiyue_test_agent.model_access.thinking'; 'naiyue_test_agent.model_access' is not a package`。实现后定向命令 `.venv/bin/python -m pytest -q tests/test_model_access.py` 输出 `23 passed in 0.88s`，最终全量 `.venv/bin/python -m pytest -q` 输出 `32 passed in 0.94s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0，`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。
- 可选 `thinking` 配置在模型条目中声明支持等级和默认等级；缺省或 `model_default` 不传参数。Pi 式上找再下找、本机四类适配器构造参数、OpenAI 请求载荷字段，以及现有假模型 Agent 接线均有确定性测试。没有改真实 `.env`，没有发送新网关请求；实际 HeiyuCode/Claude/Ollama 接受并执行参数的能力仍未知。回退元数据可从 `ModelCatalog.thinking_selection(model_id)` 读取，但完整运行轨迹尚未接线。
- 学习证据：本轮代码由助手编写，用户尚未逐段学习、复述、独立实现或回答面试追问，不能记录成已掌握。
- 唯一下一步：沿 Spec 005 的配置示例逐段讲清 `thinking.py` 与 `ModelCatalog` 的实际数据流；随后单独设计并测试模型请求失败事件及终局异常传播。若用户继续讨论架构而非课堂，先处理其最新问题。

## 36. thinking 等级回退课堂第 1 段（2026-09-30）

- 本段只讲 `thinking.py` 的 `resolve_thinking_level`：项目七档顺序、`model_default` 与显式 `off` 的区别、精确命中、向上再向下搜索及异常路径。供应商参数映射与 `ModelCatalog` 接线留到下一段。
- 确定性本地调用实际输出：`high` + `('off', 'low', 'xhigh')` → `xhigh`；`high` + `('off', 'low')` → `low`；`model_default` + `('off', 'low')` → `model_default`。未读取或修改 `.env`，未调用真实模型；此结果只验证解析函数，不证明供应商接受所选等级。
- 学习状态：已讲解；用户随后反馈“懂了”，可记录为讲解后自述理解，但没有回答小检查题，不记录为独立实现或可应对面试追问。本段没有新增功能或代码测试。
- 唯一下一步：先检查用户对“先向上、再向下”的理解，再用同一示例逐行追 `native_thinking_settings` 和 `ModelCatalog` 的配置到模型构造链路。

## 37. thinking 供应商参数课堂第 2 段（2026-09-30）

- 沿 `high → xhigh` 的示例讲 `native_thinking_settings` 的项目等级到 OpenAI 适配器参数映射，以及 `load_model_catalog` 构造 `ThinkingSelection`、传给 `_build_model` 的数据流；本段只展开 OpenAI 分支，其他供应商和运行时请求行为另行讲解。
- 本地纯函数调用实际输出：`openai/xhigh → {'reasoning_effort': 'xhigh'}`、`openai/off → {'reasoning_effort': 'none'}`、`openai/model_default → {}`。定向测试命令 `.venv/bin/python -m pytest -q tests/test_model_access.py::test_thinking_configuration_reaches_each_provider_adapter` 输出 `1 passed in 0.65s`。该测试含适配器对象及本地请求载荷检查，不是线上供应商执行证明。
- 学习状态：本段已讲解；用户理解反馈待确认，不标记独立实现或面试追问能力。未修改生产代码、`.env` 或发送真实模型请求。
- 唯一下一步：确认用户对 `selection.native_settings` 经 `**thinking_settings` 进入 `ChatOpenAI` 的数据流理解，再讲其他供应商分支或进入下一模型层切片。

## 38. 模型列表发现与配置边界讨论（2026-09-30）

- 用户追问可用模型是否只能靠配置、能否向供应商查询；助手最初按“模型 ID 列表”回答。用户随后澄清：真正的问题是单个模型可用的推理级别。本节关于模型 ID 发现的资料仅作背景，不代表回答了原问题。
- 本项目当前 `load_model_catalog` 只读取 `.env/models.json` 的 `models` 并构造模型；`ModelCatalog.get` 只从该内存快照取已配置 ID，不调用远端模型列表 API。
- 核对官方资料：OpenAI 与 Claude 均提供 `GET /v1/models`；Ollama 提供本地 `GET /api/tags`。Claude Models API 可返回部分能力元数据；OpenAI 模型列表对象主要是基础 ID/归属等信息。OpenAI-compatible 网关是否支持模型发现及返回哪些能力须逐网关核验。模型列表、当前凭证可用性、thinking 等级能力是不同问题，不能从“列出 ID”推断均可成功调用或支持项目七档。
- 本轮仅讨论，未更改代码、配置或调用真实模型/列表接口；模型发现尚未实现。学习状态：用户理解反馈待确认。
- 唯一下一步：若用户决定把自动发现纳入模型接入层，先收敛单个供应商的发现接口、失败回退及验证标准；否则继续当前已确认的模型层教学与错误事件切片。

## 39. 推理级别能否自动发现（2026-09-30）

- 用户澄清原问题为“单个模型支持哪些推理级别能否向模型/供应商获取”。当前 `thinking.supported_levels` 是本地声明；`validate_supported_levels` 只检验项目七档中的合法、非空、唯一值，`native_thinking_settings` 只检查/翻译适配器参数，并没有向模型查询实际支持档位。
- 官方资料显示不同供应商的元数据能力不同：Claude Models API 的 `capabilities` 可含 `effort` 的分档支持信息；OpenAI Models API 对象目前只有 ID、创建时间、归属等基本字段，不提供统一的 reasoning 等级字段；Ollama `/api/show` 展示模型能力信息，但其通用接口文档没有承诺逐模型的完整 thinking 等级清单。兼容网关另需单独核验。向聊天模型提问“你支持什么等级”不能作为权威能力证据；请求探针只能验证端点当时接受/拒绝某参数，成功响应也不证明实际推理预算。
- 本轮只做本地代码与官方资料核对，未改生产代码、`.env`，未发模型请求或能力探针。发现现有 Ollama 映射对所有项目显式等级直接透传，而 Ollama 官方通用 API 文档只列布尔或 `low/medium/high/max`；逐模型支持及 `minimal/xhigh` 的可用性没有验证，后续应先用针对性测试/Spec 修正，不能宣称配置声明等于实际能力。
- 唯一下一步：在继续 thinking 功能前，先决定能力来源与未验证档位的处理规则，再按 TDD 修正任何已证实的供应商映射缺口；错误事件切片后移。

## 40. Ollama thinking 映射回归修正（2026-09-30）

- 用户表示可继续开发。本单元只处理上一节发现的确定性边界：Ollama 通用 API 文档的 `think` 字符串等级为 `low/medium/high/max`，但本项目此前将 `minimal/xhigh` 也直接映射到 `ChatOllama(reasoning=...)`。不修改 `.env`，不接触真实服务。
- 按 TDD 先新增配置加载回归测试：`minimal` 与 `xhigh` 应在构造前抛 `ModelConfigError`，`max` 应正常进入适配器。红灯命令 `.venv/bin/python -m pytest -q tests/test_model_access.py::test_ollama_rejects_undocumented_thinking_levels tests/test_model_access.py::test_ollama_max_thinking_level_reaches_adapter` 输出 `2 failed, 1 passed in 0.93s`；失败均为 `DID NOT RAISE ModelConfigError`。
- 根因为 `native_thinking_settings` 的 Ollama 分支对所有非 `off` 等级无条件透传。最小修复只让 `off` 映射 `False`、`low/medium/high/max` 映射原字符串，并拒绝其他值。相同定向命令转绿：`3 passed in 0.65s`；全量 `.venv/bin/python -m pytest -q` 输出 `35 passed in 0.76s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。Spec 005 的 Ollama 映射文字已同步。
- 证据边界：L1/L2 代码与测试存在，L3 配置加载会调用映射函数并拒绝不受通用适配器支持的等级；未调用 Ollama 服务，逐模型能力和推理预算仍未验证。用户尚未学习或复述本次改动，不能记录为掌握。
- 唯一下一步：基于第 16 节错误分类设计写模型请求失败事件与终局异常传播的阶段 Spec，先定清事件字段、脱敏和异常边界，再按 TDD 实现；缓存及动态等级继续单独排期。

## 41. 同步模型调用失败事件与异常传播（2026-09-30）

- 用户要求继续开发。依据现有 `agent_runtime.py` 与第 16 节分类基线，先写 [Spec 006](specs/006-model-call-failure-event.md)：只在已完成选模后、`handler(request.override(model=model))` 抛异常时记录一条脱敏 `model_call_failed` 日志事件，并裸 `raise` 原异常；不改变公开函数签名，不改 `.env`，不增加重试、备用模型或失败 `AIMessage`。
- TDD 红灯：新增 `TimeoutError` 与 `RuntimeError` 假模型用例，经项目 `build_runtime_agent` 实际装配调用 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_model_failure_records_safe_event_and_propagates_same_exception`，结果 `2 failed in 0.60s`；两例都已传播相同异常对象，但断言发现事件列表为空。
- 最小实现：`model_access/errors.py` 将内建 `TimeoutError` 映射为 `transient/timeout`，其他 `Exception` 暂映射为 `unknown/unclassified`；选模 middleware 在 handler 异常路径写不含原始异常正文的标准库日志记录，并原样抛出。测试还验证正常模型响应、缺少会话 ID 和选模记录器失败不会生成模型调用失败事件。
- 验证：`.venv/bin/python -m pytest -q tests/test_agent_runtime.py` 输出 `6 passed in 0.60s`；全量 `.venv/bin/python -m pytest -q` 输出 `38 passed in 0.77s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。事件经现有 builder→middleware→handler 接线产生，非孤立分类函数验证。
- 证据边界：仅同步假模型的 handler 失败；没有真实供应商错误码归类、取消、异步/流式、持久轨迹、应用入口安全展示或自动重试。用户尚未学习/复述本次核心代码，不标记为独立实现或面试追问能力。
- 唯一下一步：与用户沿一次假模型超时追 `handler` 异常、分类、日志记录及裸 `raise` 的数据流；之后选择一个有真实 SDK 异常样本的分类小切片，或单独设计缓存控制，不同时堆两项。

## 42. 后续工作清单与失败切片课堂（2026-09-30）

- 用户要求把以后开发或补充的点放在独立文档。新增 [后续开发与补充清单](future-work.md)，按模型/运行边界、核心业务闭环、长期扩展和学习证据列出未实现或未验证项及完成判据；已在 README 和实施路线接线，并把每单元维护清单写入 AGENTS.md。清单不把长期项目目标写成首期承诺。
- 本轮课堂沿 `tests/test_agent_runtime.py` 的一次假模型 `TimeoutError` 讲 `agent.invoke` → `build_runtime_agent` → 选模 middleware → `handler` 异常 → `classify_model_failure` → 脱敏日志 → 裸 `raise`，逐项说明事件字典、测试变量和异常对象身份。用户正确回答“`record_selection` 抛错不会产生 `model_call_failed`”，理由是它位于 `try` 前；只记录这一局部问题的引导后理解，不推断已能独立实现整个错误路径或应对面试追问。本轮未改功能代码、`.env` 或调用模型。
- 验证：检查新增清单及相关入口文档的相对链接输出 `relative links: PASS`；定向失败路径测试输出 `2 passed in 0.79s`，全量 `.venv/bin/python -m pytest -q` 输出 `38 passed in 0.70s`，`compileall` 退出码 0。文档链接检查不算新增功能验证。
- 唯一下一步：若继续课堂，检查用户能否独立追踪 `handler` 抛错到裸 `raise` 的路径；若继续开发，选一个真实 SDK 失败类型做分类回归。以用户最新优先级为准。

## 43. 提示词缓存下一切片候选（2026-09-30）

- 用户将下一单元转向缓存控制讨论。当前项目没有缓存策略配置或命中统计；现有同步模型调用边界位于 `agent_runtime.py` 的 `handler(request.override(model=model))`。本机 `langchain-openai` 已将 OpenAI 响应中的 `cached_tokens` 映射到 `AIMessage.usage_metadata.input_token_details.cache_read`，但尚未验证 HeiyuCode 网关是否返回该字段。
- 建议下一切片先做“缓存使用量观测与当前网关能力验证”：在成功的模型调用边界记录脱敏的模型 ID、输入 token、缓存读取 token（区分 0 与缺失），用假响应覆盖有值/零/缺失，再做有界的真实网关对照实验。此建议尚未写 Spec、测试或实现；不修改 `.env/models.json`。
- OpenAI 官方文档显示，`gpt-5.5` 的前缀缓存默认自动启用，可用稳定的 `prompt_cache_key` 优化路由；它不支持 GPT-5.6 以后的显式断点，`prompt_cache_retention` 只支持 `24h`。这些是 OpenAI 官方端点的协议说明，不证明兼容网关已转发同样参数或统计。Pi 按供应商能力装配缓存参数并记录读取/写入量；AgentX 已检查的会话调用路径未发现可直接迁移的供应商提示词缓存控制。
- 唯一下一步：确认这项候选的具体成功事件字段和验证标准，写小切片 Spec；随后按 TDD 实现观测。确认网关能力后，再单独讨论供应商专用控制参数与留存策略。

## 44. 缓存用量观测与 HeiyuCode 实测（2026-09-30）

- 用户确认实施上一节小切片，并追问是否必须传缓存策略才能命中。已依据 OpenAI 官方提示词缓存文档澄清：支持的模型可自动缓存相同前缀，策略参数不是开启缓存的必要条件；但官方协议不自动证明兼容网关行为。本切片未传缓存控制参数，也未修改 `.env`。
- 先写 [Spec 007](specs/007-prompt-cache-usage-observation.md) 和四种成功响应测试；红灯命令 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_successful_model_call_records_reported_cache_usage` 输出 `4 failed in 0.90s`，失败点均为 `model_usage` 事件缺失。最小实现只在现有 `agent_runtime.py` 同步 `handler` 成功路径记录 `model_call_usage`，读取 `AIMessage.usage_metadata` 中的输入与缓存读取 token，保留零与缺失的区别；失败路径仍原样传播，无成功事件。
- 定向测试 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py` 输出 `10 passed in 0.83s`；全量 `.venv/bin/python -m pytest -q` 输出 `42 passed in 0.90s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。确定性测试经 `build_runtime_agent` 实际接线，且验证最终回答不变、日志不含输入/回答正文。
- 新增受限诊断 `scripts/probe_prompt_cache.py`，沿本地模型配置向 HeiyuCode 发送两次仅含合成折扣规则的相同长前缀请求，不输出正文或凭证。命令 `.venv/bin/python -m scripts.probe_prompt_cache --model-id heiyu-gpt-5.5` 退出码 0，输出：第一次 `input_tokens=7399 cache_read_tokens=None`，第二次 `input_tokens=7399 cache_read_tokens=6912`，`status=PASS requests=2`。这证明网关经当前适配器在第二次报告了正的缓存读取量；首次缺失不能解释成零，也不证明后端模型身份、官方 OpenAI 计费、成本节约额度或稳定命中率。
- 学习证据：用户已通过提问区分“传策略”和“供应商自动缓存”，本轮实现由助手完成，尚未逐行讲解、复述或独立改动，不标记为独立实现或可应对追问。
- 唯一下一步：沿一次真实响应逐行讲 `usage_metadata` → middleware `model_call_usage` 的数据流；之后单独设计供应商专用缓存控制及其配置/留存边界。

## 45. 缓存统计数据流课堂（2026-09-30）

- 按用户“继续”的要求，开始讲解已完成切片的核心路径。以网关第二次报告的 `input_tokens=7399`、`cache_read_tokens=6912` 为实际统计，区分探针直接 `model.invoke` 的运行证据与 middleware 中 `ModelResponse.result → AIMessage.usage_metadata → model_call_usage` 的本地接线测试；展示对象形状时仅作为说明，不伪称已捕获该次网关响应的完整对象。
- 重点讲 `handler(request.override(model=model))` 的返回、`next(..., None)` 从消息列表选 AIMessage、缺失用量与明确零值的分支、`logger.info(extra=...)` 的结构化事件，以及 `return response` 如何保持 Agent 原回答。对照 `tests/test_agent_runtime.py` 的正数/零/缺失样本和真实网关输出。
- 本轮只读核对当前代码、LangChain `ModelResponse` 和 `langchain-openai` 的 usage 映射；没有改功能代码、配置或再次请求模型。学习状态暂为“已讲解”，待用户反馈；不能记录为独立实现或面试追问能力。
- 唯一下一步：确认用户能否解释 `cache_read_tokens=0` 与 `None` 的区别；理解后再设计 Pi 式供应商缓存控制切片。

## 46. 缓存策略语义调研（2026-09-30）

- 用户正确回答：供应商没有返回 `input_token_details` 时，本项目记录的 `cache_read_tokens` 是 `None`。这是该局部数据分支的引导后理解；用户尚未回答是否能据此断定未命中，也没有独立实现证据。
- 核对本地 Pi `openai-responses.ts`：策略类型为 `none/short/long`；默认 `short`。对 OpenAI Responses，`short` 会在有会话 ID 时传稳定 `prompt_cache_key`；`long` 另传 `prompt_cache_retention=24h`；`none` 不传这两项，且仅在适配器声明支持显式缓存模式时才用 `prompt_cache_options.mode=explicit` 关闭隐式断点。故对不支持显式断点的 GPT-5.5，Pi 式 `none` 不能被解释为“保证供应商不缓存”。Claude 适配器另用 `cache_control` 与可选 `1h` TTL，不能把不同供应商的短/长等同为同一留存或价格承诺。
- 当前 OpenAI 官方文档显示 GPT-5.5 自动缓存、可用稳定 `prompt_cache_key` 优化路由、只支持 `24h` 留存参数且不支持显式断点；本地 `ChatOpenAI` 可在单次调用传这些参数。HeiyuCode 已在不传缓存参数时报告第二次 `cache_read_tokens=6912`，但尚未验证它会接受或遵守 `prompt_cache_key`、`prompt_cache_retention`。AgentX 已检查的会话调用路径未发现可直接迁移的供应商提示词缓存参数。
- 候选最小控制契约：先保留 `provider_default`（不传缓存参数），未来只为已验证的供应商加入有稳定作用域的路由 key；长期留存及“禁用缓存”分别按具体模型能力和数据策略设计，不能直接照搬 Pi 的名称并作跨供应商保证。本轮仅调研与设计，没有写 Spec、代码、配置或再次调用网关。
- 唯一下一步：先确定稳定 key 的作用域（建议可信会话级、未来再纳入租户隔离）以及是否要在本期做其真实网关参数探针；明确后单独写控制切片 Spec 与 TDD 测试。

## 47. `prompt_cache_key` 会话级分组解释与建议修正（2026-09-30）

- 用户询问“稳定 key 按会话划分”具体是什么意思。概念上，逻辑会话 A 的各次模型请求传同一不含提示词的 opaque key，逻辑会话 B 传另一个；key 只是供应商的缓存路由/统计分组提示，不存聊天历史，不是应用鉴权或严格数据隔离。当前 `RunContext` 只有可信 `session_id`，还没有持久会话服务或缓存 key 生成/存储逻辑，故这只是候选设计。
- 复核 OpenAI 当前官方指南：对 GPT-5.5，稳定 key 有助于相关请求路由，但不保证命中；同一用户/工作区的相关请求可共享 key，只有确实需要逐会话单独统计时才给每会话单独 key。过早按会话拆分可能降低多个会话复用相同系统提示词的机会。故上一节“建议会话级”应降为可选方案，不作为本项目已确认决策；本地单用户且无逐会话账单需求时，暂维持不传 key 的 `provider_default` 更合适。未来 SaaS 的租户权限隔离须由应用边界实施，不能寄托于 prompt cache key。
- 本轮只做概念解释和官方文档复核，没有新增代码、配置、网关调用或测试。用户理解反馈待确认；唯一下一步是确认有无逐会话统计/隔离的产品需求，再决定是否做显式 key 控制切片。

## 48. OpenAI SDK 认证失败分类切片（2026-09-30）

- 用户确认暂停主动缓存控制，继续底层模型错误分类。选择一种可重现错误：OpenAI SDK `AuthenticationError`。按 [Spec 008](specs/008-openai-authentication-failure.md) 只映射为 `access_denied/authentication_failed`，保持其他现有分类、运行层事件结构和原异常透传；未修改 `.env` 或新增依赖。
- TDD 红灯：新增 SDK 原生异常经项目 `build_runtime_agent` 的集成测试，`.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_openai_authentication_failure_is_classified_without_leaking_response` 输出 `1 failed in 0.78s`；失败点是事件仍为 `unknown/unclassified`，同时已观察原异常对象透传且合成敏感正文没有进入日志。最小实现只在 `model_access/errors.py` 增加该类型映射。
- 定向测试 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py` 输出 `11 passed in 0.55s`；全量 `.venv/bin/python -m pytest -q` 输出 `43 passed in 0.75s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。功能在现有 middleware 中接线，日志仅含固定分类与模型 ID，无失败正文或成功用量事件。
- 一次真实 HeiyuCode 鉴权探针使用自动生成的无效诊断凭证、15 秒 SDK 超时和零重试，不使用有效凭证发请求。命令 `.venv/bin/python -m scripts.probe_openai_auth_failure --model-id heiyu-gpt-5.5` 退出码 0，输出 `status=PASS error_type=AuthenticationError http_status=401 category=access_denied reason=authentication_failed`。它只证明当前网关当次无效凭证响应与 SDK 映射；未通过真实网关触发 Agent middleware，其接线证据来自确定性集成测试。
- 学习证据：本轮代码由助手实现，用户尚未复述、独立改动或处理此错误路径，不标记为独立实现/面试追问能力。唯一下一步：用这次 401 的具体数据流讲解 SDK 异常怎样进入 `classify_model_failure`、middleware 事件与上层异常通道；之后再选下一种真实 SDK 错误切片。

## 49. 其他 SDK 错误能否提前实现（2026-09-30）

- 用户询问是否必须等真实运行发现错误才能补分类。核对本机 `openai` 3.19.2 的异常层级及 HTTP 状态映射：`APITimeoutError`/`APIConnectionError`、`PermissionDeniedError`、`BadRequestError`、`NotFoundError`、`UnprocessableEntityError`、`RateLimitError`、`InternalServerError` 等均有明确 SDK 类型，适合用原生异常对象或本地 mock transport 做确定性 TDD；不必故意让真实网关过载或消耗额度。
- 可提前实现的只是稳定粗分类和可重复本地接线验证。429 的临时限流与额度耗尽、400 的具体拒绝原因、兼容网关错误体格式、流式输出后的失败及能否安全重试仍需各供应商的实际响应或受控故障证据。不能将 `RateLimitError` 直接等同“稍后重试”或把所有 5xx 一律自动重放。
- 建议下一小切片先覆盖 OpenAI SDK `APITimeoutError` 与 `APIConnectionError`，因为当前只匹配 Python 内建 `TimeoutError`，这两类 SDK 异常仍落入 `unknown`。先定义事件 reason 和本地故障注入，再按 TDD 实现；其他类型按类别逐片推进。本轮仅调研与设计，无功能代码、配置或模型请求。

## 50. Anthropic 与 Ollama 基础错误分类（2026-09-30）

- 用户要求先实现非 OpenAI 能稳定识别的错误。先核对本机 Anthropic/Ollama SDK 的异常类型与 Ollama 的 `status_code`，写 [Spec 009](specs/009-non-openai-model-errors.md)，再按 TDD 只扩展 `model_access/errors.py` 的粗分类。Anthropic 依据具体异常类；Ollama 依据 `ResponseError.status_code`；共用的内建连接错误与 HTTPX 超时也分类。429 仅为 `limit_unspecified`，5xx 仅为 `server_error`，不添加自动重试。Anthropic 400、Ollama `-1` 和其他不明确状态保留 `unknown`。
- 红灯：定向命令 `.venv/bin/python -m pytest -q tests/test_model_access.py::test_non_openai_sdk_failures_have_safe_coarse_classification tests/test_agent_runtime.py::test_non_openai_failure_reaches_agent_event_without_leaking_body` 输出 `17 failed, 3 passed in 0.78s`，预期新分类仍落入 `unknown`。最小实现后同一命令 `20 passed in 0.53s`。又补 Anthropic 401 与 Ollama 404 的本机 SDK HTTP mock 测试，定向 `2 passed in 0.52s`；这只证明 SDK 转换和分类，不是供应商实网请求。
- 全量 `.venv/bin/python -m pytest -q` 输出 `65 passed in 0.72s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。`tests/test_agent_runtime.py` 经 `build_runtime_agent` 验证失败事件固定字段、异常对象原样传播、无成功用量事件和敏感正文不入日志。未改 `.env`、配置或依赖。
- 证据边界：未接 Anthropic 账号，也未启动 Ollama 服务；真实服务错误体、异步/流式中途失败和是否可重试仍未验证。代码由助手实现，用户尚未逐行学习或独立编写本切片，不标记为可独立实现或应对面试追问。
- 唯一下一步：先用 Anthropic 异常对象和 Ollama `ResponseError.status_code` 的具体例子，逐段讲清分类顺序、Agent 事件和原异常传播；然后再选 OpenAI 其他 SDK 错误或真实服务故障验证的小切片。

## 51. 模型接入层收口边界与下一切片候选（2026-09-30）

- 用户确认无需继续逐行讲错误分类，询问模型接入层是否完成及与 Pi `pi-ai` 的差距。核对当前模型构造、运行层、Pi 模型调用路径与 LangChain 标准模型接口后，结论是：本项目已具备首版多供应商接入骨架，但只有 HeiyuCode 真实工具循环；不能将整个模型接入层标记为生产就绪。Pi `pi-ai` 的模型目录、鉴权、流式事件、成本和跨供应商上下文转换等能力，部分由 LangChain 提供接口，部分属于本项目后续运行/应用层，不逐项复制。
- 用户表示目前没有本地模型，Ollama 真实调用验收暂缓，保留为未验证项；不因为缺少服务而把对象构造或 HTTP mock 称为本地模型可用。
- 下一切片候选：先对当前模型构造显式配置单次请求超时与 SDK 重试次数，建议初版将 SDK 重试设为 0、记录终局失败并继续原异常传播；随后补 OpenAI SDK 超时/连接失败的分类与 Agent 接线测试。当前 `ChatOpenAI`/`ChatAnthropic` 未显式传 timeout/retries；本机 SDK 默认各有 2 次重试，Ollama 客户端默认 timeout 为 None。SDK 的 HTTP 超时不是整个 Agent turn 的严格墙钟截止，后者在运行层另行设计。
- 本轮为设计讨论，没有写 Spec、实现、测试或修改 `.env`。在确定配置字段、数值和验收标准前不进入代码；任何 `.env/models.json` 修改须按项目规则先取得用户同意。唯一下一步：待用户确认请求超时与 SDK 重试配置契约，再写阶段 Spec 并按 TDD 实施。

## 52. 模型请求超时与 SDK 重试配置（2026-09-30）

- 用户确定默认重试次数为 2（可重试故障最多 3 次请求）、默认 HTTP 超时为 60 秒，配置可覆盖，缺失字段各自使用默认值；并确认本期先实现在线模型重试，Ollama 只设超时、重试后续再做。按 [Spec 010](specs/010-model-request-policy.md) 扩展 `models.<id>.request` 可选对象，接到 OpenAI/兼容网关、Anthropic 的 `timeout`/`max_retries`，接到 Ollama 的 HTTP 客户端 timeout；Ollama 显式重试字段报配置错误，不静默忽略。补 OpenAI SDK `APITimeoutError` 与 `APIConnectionError` 的保守分类；无 Agent 层第二套重试。
- TDD 基线 `.venv/bin/python -m pytest -q` 输出 `65 passed in 1.15s`。新增定向测试红灯 `13 failed, 2 passed in 1.98s`，预期失败为缺少默认传参、新字段未识别、OpenAI SDK 错误仍为 `unknown`。最小实现后同组定向测试 `15 passed in 1.93s`。新增 OpenAI 与 Anthropic 的本机 HTTP mock，各自验证 503 在默认重试 2 次时发出 3 次尝试，显式配置 0 时只发 1 次。Anthropic mock 初次注入位置不符合适配器构造方式，修正测试注入位置后 `2 passed in 1.94s`。
- 最后补充配置驱动 Agent 装配的默认传参断言后，全量 `.venv/bin/python -m pytest -q` 输出 `82 passed in 3.42s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。单文件代码均未超过 500 行。运行层集成测试验证终局 `model_call_failed` 与原异常对象透传；不把 SDK 重试每次尝试伪称为当前日志已有逐次轨迹。
- 证据边界：HTTP timeout 不保证整个 Agent turn 的严格墙钟截止；真实网关对超时/503 的响应和计费、服务端是否实际处理过超时请求、Ollama 重试、异步/流式部分输出均未验证。未改真实 `.env/models.json`、依赖或公开 API。代码由助手完成，用户确认设计选择，但尚未独立实现或调试本切片。
- 唯一下一步：依用户节奏，先核对这个请求策略切片的行为和配置示例；之后转入 Agent 运行层的连续会话与模型切换设计。任务总预算及 Ollama 重试留在后续清单，不阻挡下一层。

## 53. Agent 运行层同进程连续消息（2026-09-30）

- 用户要求继续，按已有架构草案选择最小运行层切片：[Spec 011](specs/011-in-memory-agent-continuation.md)。`agent_runtime.py` 两个装配函数现在可接收调用方持有的可选 checkpointer，并传给 `create_agent`；省略时既有无检查点调用行为保持不变。不创建会话服务、数据库或 UI，不修改 `.env`。
- 入口与数据流：调用方在进程内创建 `InMemorySaver`，装配时传入；第一次 `invoke` 提供 `context.session_id=s1` 和 `configurable.thread_id=thread-s1`，第二次仅提交新用户消息并复用该 `thread_id`。框架加载上次检查点，模型 middleware 从当前 `session_id` 选择 B；B 的模型输入含第一次用户消息、A 的回答和第二次用户消息。另一个 `thread_id` 的输入只见自己的历史。配置驱动的装配路径也有相同的检查点透传测试。
- TDD 证据：实施前全量 `82 passed in 4.07s`；两个新测试先因不接受 `checkpointer` 参数得到 `2 failed in 0.67s`；实现后根据本机框架实际输入补正空 `SystemMessage` 的测试形状，定向 `2 passed in 0.53s`。最终全量 `.venv/bin/python -m pytest -q` 为 `84 passed in 3.57s`，`compileall` 退出码 0，`pip check` 输出 `No broken requirements found.`。该测试修正不涉及放宽历史/隔离/选模断言。
- 证据边界：假模型、内存检查点与单进程顺序调用，不证明重启恢复、同会话并发、真实供应商跨模型历史兼容或安全的会话归属。当前运行层接受调用方传入的 `thread_id` 作为框架配置；实际应用入口必须先校验会话并映射内部 ID，不能让外部直接索引检查点。已有一次 `invoke` 内工具回合切换测试保持通过。
- 学习状态：本切片的代码由助手编写，用户尚未复述或独立改动；只记录功能已由确定性测试验证，不记录为已独立实现或可应对面试追问。
- 唯一下一步：先用本切片真实测试里的 `session_id`、`thread_id`、两次 `invoke` 走一遍数据流并检查理解；随后设计最小应用会话服务的可信映射和切换提交/并发规则，再进入该服务的 TDD。

## 54. 连续消息切片的代码讲解（2026-09-30）

- 用户询问该切片是否还要继续开发，并要求先讲解代码。本轮只讲已经验证的 `checkpointer` 透传、`session_id` 选模与 `thread_id` 历史索引，以及假模型测试里的两次调用/第三个隔离线程；没有新增功能或修改环境配置。
- 复查本机安装源码：`langchain.agents.factory.create_agent` 把 `checkpointer` 传给 `graph.compile`；`InMemorySaver` 的 `get_tuple`、`put` 从 `configurable.thread_id` 取键。定向复核 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_checkpointer_continues_messages_and_switches_model_between_invokes tests/test_model_access.py::test_configured_agent_forwards_checkpointer_for_continuous_messages` 输出 `2 passed in 0.65s`。这证明本机版本与项目测试的接线，不代表持久化或权限边界。
- 学习状态：代码机制在本轮已讲解；用户的理解反馈、独立实现和面试追问能力均待观察，不能提前升级。唯一下一步：先确认用户能区分相同 `session_id`、不同 `thread_id` 时的选模与历史结果；然后再设计应用会话服务。

## 55. Checkpoint 与 checkpointer 机制追踪（2026-09-30）

- 用户追问本切片是否只是接入 checkpoint，以及框架功能和实现。本轮只核对当前安装的 LangChain/LangGraph 源码并做无文件修改的假模型探针，不新增产品功能。`create_agent` 将 `checkpointer` 传入 `graph.compile`；AgentState 的 `messages` 使用 `add_messages` reducer；`InMemorySaver` 按 `thread_id / checkpoint_ns / checkpoint_id` 保存检查点，独立保存版本化 channel blobs 和 pending writes。图运行循环调用 `get_tuple`、`put`、`put_writes`。
- 本地假模型探针中，同一 `thread_id=demo` 的第一次 `get_tuple` 返回空，第一次简单无工具 `invoke` 观察到 3 次 `put`；第二次 `get_tuple` 命中已有检查点，再观察到 3 次 `put`。返回消息由 `['Q1', 'A1']` 变成 `['Q1', 'A1', 'Q2', 'A2']`，共列出 6 个检查点。这些次数是当前安装版本和该简单图的观测值，不是所有 Agent 运行的固定契约。
- 证据边界：checkpoint 是框架执行状态快照，checkpointer 是保存/读取它的组件；本轮探针没有证明跨进程恢复、任务副作用安全重放、并发控制或授权。教学状态仅记“已讲解”，用户理解反馈待确认。唯一下一步：待用户消化 checkpoint 机制后，再设计应用会话服务的可信 ID 映射和并发/切换规则。
