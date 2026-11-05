# Spec142：会话分叉与辅助对话

日期：2026-11-01。状态：**方案稿，供用户审阅；未实施，未授权发布或迁移真实数据**。

**B阶段接线边界（2026-11-01）**：云端运行/输入/结果机制以[Spec143](143-cloud-task-b.md)及其配套合同为准。本组稿中的A阶段bundle、binding、环境捕获/下载入口属于旧接线参考，不作为B实现要求；分叉仍复制当前稳定工作区与聊天边界，形成独立任务，不恢复原始输入、不共享可写目录。Spec142功能仍独立待审阅/实施，不随B迁移自动加入。

本 Spec 将此前的“历史消息编辑、版本选择与会话分支”收束为两个可独立验收的功能：最后一条完整回复分叉为新会话，以及输入框加号菜单打开辅助对话。历史消息编辑、同一消息的版本切换、会话树不在本期范围。

配套合同：[数据、接口与生命周期](142a-conversation-fork-and-side-chat-contracts.md)、[前端交互与参考复用](142b-conversation-fork-and-side-chat-ui.md)、[实施分片与验收](142c-conversation-fork-and-side-chat-acceptance.md)。本文及配套文件中的新增接口、表、组件均为**拟实现**，不能作为现有能力使用。

## 1. 用户已确认的目标与本稿建议

| 项目 | 本期合同 | 决策来源 |
| --- | --- | --- |
| 分叉 | 最后一条完整回复下提供“分叉为新会话”；新会话持久保存，原会话可继续 | 用户已确认的方向 |
| 辅助对话入口 | 主输入框加号菜单，打开独立面板 | 用户已确认的方向 |
| 辅助对话上下文 | 打开时继承主会话的稳定上下文快照；面板初始不重复展示主聊天；实例内可多轮问答 | 本轮讨论收束 |
| 辅助对话保存 | 不保存为历史会话；关闭后丢弃，下次重新开始；折叠与关闭有不同语义 | 用户明确要求不持久化，生命周期细节为本稿细化 |
| 工具 | 辅助对话可使用有实际能力和权限的工具；继承历史不能自动触发任务 | 用户要求核对 ChatGPT/ZCode；本稿依据源码提出 |
| 辅助对话写操作 | Write/Edit、Shell、AITest及未知副作用 MCP 均在本实例内重新审批，不能继承父会话的放行记录 | 本稿安全合同，待审阅 |
| 持久分叉的沙箱文件 | 复制当前工作区到独立沙箱，带已有文件改动，之后两边互不影响 | **用户在云端核验后明确选择** |
| 辅助对话的执行环境 | 沿用独立工作区复制机制，按需准备；源忙时先提供上下文问答及不依赖工作区的工具，不等待/停止父任务 | 本稿细化建议，随整组方案审阅 |
| UI实现 | 参考 ChatGPT 的入口层级、ZCode 的紧凑布局，沿用现有 React/CSS Modules/主题和消息组件 | 用户要求参考，复用粒度为本稿建议 |

工具审批、辅助按需准备及具体复制范围要随本文一起审阅，不能把“整理 Spec”视作产品实现授权。持久分叉的环境方向已确认，下面已统一替换原初始输入候选。

## 2. 解决的问题、最小场景与范围

分叉场景：用户与 A 完成接口测试方案，想保留 A 并探索另一种认证策略。点击最后回复的分叉动作，得到普通会话 B，显示截至分叉点的聊天；创建时不调用模型，用户在 B 发送新问题后才运行。删除 A 不会删除 B 或令 B 无法续聊。

辅助场景：A 正在运行，用户想问“刚才说的幂等键为什么必要”。打开辅助对话 S，它能参考 A 上一轮已经完整保存的上下文；S 的问题、回答、工具事件、审批与停止均独立，A 不插入这些消息。关闭 S 后，再打开 S2，从当时的 A 重新取快照。

失败场景：页面看到的最后回复已经被重生成替换，服务端收到旧分叉请求。服务端在源会话锁内核对回复、版本和快照，返回冲突；原会话与侧栏均不变，前端保留草稿并刷新可用动作。不得从当前最新会话“猜一个相近分叉点”。

本期包含持久分叉的来源记录、复制与恢复、辅助对话的内存宿主、工具和关闭收尾、根/子消息及图片边界、两种入口的桌面和窄屏 UI、幂等及故障验收。

本期不包含历史任意点分叉、编辑旧消息、回复版本轮播、分支树/合并、自动回填主会话、辅助对话转正或导出、辅助对话内再分叉/再开辅助对话、任务 worktree、复制运行中的任务、文件回滚或新的后台 Agent 平台。现有替换式重生成保留原合同。

## 3. 参考项目核验与采用边界

核验日期为 2026-11-01。源码事实、产品文档、本项目设计分别列明；本轮没有完成参考产品的原生 UI 操作验收。

| 来源 | 实际证据 | 对本项目的采用 |
| --- | --- | --- |
| ChatGPT 桌面文档 | `/side` 是不中断主聊天的临时辅助对话；不公开其私有后端实现 | 借鉴入口与临时交流语义，不声称复制了闭源实现 |
| OpenAI Codex TUI 源码 | `side_fork_config` 复制当前配置并设 `ephemeral=true`；边界提示允许按权限使用工具，禁止把继承历史当新指令；默认轻量检查，明确新请求后才修改工作区 | 采用快照、指令边界与独立生命周期；提示词之外另做真实权限检查 |
| Codex App Server 文档 | `thread/fork` 创建新 thread；指定完整 turn 截断；`ephemeral=true` 为内存分叉且不进入持久列表 | 采用两个保存等级；不接入它的 Rust runtime 或协议 |
| ZCode 稳定回复分叉 | UI→`forkAssistant`→稳定候选校验→`forkStableConversation`→原子复制；复制消息/工具调用身份，注册新会话，沿用父 workspace | 采用独立身份、稳定边界与原子发布；本项目沙箱归属不能照搬 workspace 共享 |
| ZCode 辅助对话 | `createSelectionSideConversation` 复制上下文，继承部分运行选择；历史标为 model-only，添加 side boundary，正常注册 workspace app；边界明确只按辅助对话中的新请求修改工作区 | 采用隐藏继承历史、复用消息/工具 UI 和新指令边界；不照搬其持久存储 |
| Pi | `AgentSessionRuntime.fork`→`SessionManager.createBranchedSession` 复制到新 JSONL，再重建上下文；`/tree` 则切换同文件 leaf | 采用“独立会话”的解释；本期不建设 `/tree` 的树形版本系统 |
| AgentX | `setupContextAndHistory`→activeMessages→`initMemory/buildHistoryMessage` 按有效消息组装请求；本轮未核实到同等分叉入口 | 仅用来校验“显示档案与模型上下文不是同一份投影”，不宣称它实现了本功能 |

ZCode 的辅助会话经 `commitForkBundle` 持久化，并且 `selection_side_chat` 被纳入项目记忆类型判断。侧栏不列出它，不等于不持久化；关闭标签的 UI 函数本身也不证明删除后端数据。本项目按用户要求采用内存会话，不复制这一行为。

ZCode 的按钮、工具条、消息渲染与桌面壳采用同一 UI 包；整体复制会带入大体量组件、内部协议及 Radix/Tailwind 等依赖。具体可复用范围和许可证处理见 [142B 第 7 节](142b-conversation-fork-and-side-chat-ui.md#7-zcode-可借鉴与可复用范围)。

一手来源：

- [ChatGPT 桌面斜杠命令](https://learn.chatgpt.com/docs/reference/slash-commands)、[Codex 辅助对话说明](https://learn.chatgpt.com/docs/developer-commands)、[App Server 分叉/ephemeral 合同](https://learn.chatgpt.com/docs/app-server)。
- [Codex side.rs，67f0994](https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/tui/src/app/side.rs)：边界文本 28–60，配置 638–651，关闭路径 `discard_side_thread`。
- [ZCode session-fork.ts，a60ba6a](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/apps/zcode-cli/packages/core/src/runtime/methods/session-fork.ts)：`buildForkedSessionInput`、`commitAtomicConversationFork`、`createSelectionSideConversation`、`forkStableConversation`。
- [ZCode 协议宿主注册](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/apps/zcode-cli/packages/bootstrap/src/zcode-protocol/server-operations.ts)：`registerForkedSessionRuntime`→`materializeSessionRecord`→`createRecord`→`createWorkspaceZCodeApp`，工具名单在正常 runtime 配置中处理。
- [ZCode 项目记忆类型](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/apps/zcode-cli/packages/core/src/runtime/helpers/project-memory.ts)、[ZCode UI SessionPane](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/packages/ui/src/v4/SessionPane.tsx)。

本机补充源码基线：Pi `a69bef789`，`packages/coding-agent/src/core/agent-session-runtime.ts`、`session-manager.ts`；AgentX `3fcfa0e`，`ConversationAppService.setupContextAndHistory`、`AbstractMessageHandler.buildHistoryMessage`。它们不是本项目运行依赖。

### 3.1 用户追加的云端任务核验

用户在本稿整理期间追加要求：先看参考产品的云端任务如何处理代码环境，再决定本项目沙箱继承。完成下面核验后，用户明确选择**复制当前工作区到独立沙箱**；前面的独立初始输入候选已撤回，不进入实施。

| 具体操作 | 公开材料能证明的事实 | 不能从中推出的结论 |
| --- | --- | --- |
| Codex Cloud新任务 | 从发布后的准备文件系统启动，每个任务有独立workspace | 不能证明分叉已有任务也只从初始仓库开始 |
| Codex Cloud继续已有任务 | 保留该任务自己的已保存文件、未提交修改和已安装工具；重新发布模板不覆盖已有任务现场 | 不能证明fork会共享该任务容器或完整复制其现场 |
| IDE委派到Cloud | 官方示例说明新云端聊天带已有聊天上下文及local source changes | 不能证明会复制数据库、后台进程、所有未跟踪文件或整个容器 |
| ChatGPT桌面`/fork` | 当前命令说明写的是本地聊天/本地worktree | 本轮未找到足以证明云端fork/side文件继承细节的官方合同；不据此断言云端必然支持或不支持 |
| ZCode远程开发 | 官方介绍SSH主机/已有Docker环境；源码后端还区分WSL/server。手机Remote Control操作已有会话 | 不是已核实的、与Codex Cloud等同的“托管任务克隆环境”产品合同 |
| ZCode当前稳定回复分叉 | V4 `forkAssistant`走conversation-only复制，child沿用父workspace/directory，不停止父任务、不回滚workspace | 不创建独立容器快照；不能代表所有历史fork入口。旧`forkSession`另有checkpoint rewind语义 |

来源：[Cloud环境的状态复用](https://learn.chatgpt.com/docs/environments/cloud-environments)、[IDE到Cloud的上下文/源码变更交接](https://learn.chatgpt.com/docs/prompting)、[桌面fork命令范围](https://learn.chatgpt.com/docs/reference/slash-commands)、[ZCode远程开发介绍](https://zcode.z.ai/cn/docs/ADE-tools)、[ZCode V4分叉处理器](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/apps/zcode-cli/packages/bootstrap/src/zcode-protocol-v4/commands/handlers/fork-edit-retry.ts)、[ZCode远程后端选择](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/packages/server/src/remote/create-backend.ts)。

本项目由此区分四件事：新任务从模板开始、同任务继续现场、聊天历史分叉、代码环境分叉。采用用户选定的当前工作区复制，使新分叉可以带着已有工作探索新方案；这属于本项目明确的产品合同，不冒称已复刻闭源云fork实现。

## 4. 本项目当前主链

```text
Composer → useConversation → api → 已认证的 scoped WebService
  → SessionAPI / AgentSession → ActiveRun → create_agent 图
  → official AsyncSqliteSaver（图状态）
  → SessionPersistence.afinish → RunUnitOfWork.finish_run
       → 会话元数据 + 完整聊天档案 + 保存状态
  → history.page/message → Conversation / ToolCall

现有重新生成：session_regeneration.regenerate
  → 从源 checkpoint 建新 thread → 激活替换
  → 删除当前会话旧尾部、更新 conversation_revision

现有临时 scope：POST temporary → lease
  → TemporaryAPI.open → assemble_host(database_path=None)
  → RAM ProjectStore + RAM AsyncSqliteSaver + 独立 SessionService
  → lease断开 / DELETE → seal → stop → drain → close
```

当前不能直接当作分叉的地方：

1. `conversation_revision` 用于防过期，并非分支 ID；`parent_message_id` 是工具结果归属，并非聊天树。
2. 当前重生成会替换原会话尾部；不能把该入口改名后复用为“保留原会话”的分叉。
3. 完整聊天档案包含已被上下文压缩掉的原文；模型恢复应从正式 checkpoint 取有效消息，不能把 UI 分页预览重新拼成上下文。
4. checkpoint 会修剪；任意旧回复不保证还有可执行状态。本期只承诺受保护的最新完整稳定点。
5. RAM Host 已有独立关闭路径，并且没有持久记忆作业；但目前没有从父会话导入快照、隐藏继承消息或绑定项目沙箱的辅助入口。
6. 沙箱绑定校验要求 project/session/input/binding 一致；不能只换 session_id 并共享父记录，也不能从失败沙箱回退 Host。
7. 元数据和 checkpointer 使用不同数据库连接；不得在元数据事务/短锁中 await saver，假装它们天然原子。

当前数据库 schema 为 20；依赖为 LangChain 1.4.2、LangGraph 1.2.12、checkpoint-sqlite 3.1.1。实现前核对漂移，沿用官方状态读写和现有装配，不新增框架。

## 5. 拟采用的数据流与职责

```text
主会话 A 的完整保存点
  │ S1 内核验身份/稳定锚点，物化有效消息与指定档案
  └─ immutable export（值复制，不携带活对象/凭证）
       ├─ 持久分叉 B
       │    准备新 thread → 原子发布元数据/档案/来源/操作结果
       │    → 侧栏新条目 → 普通会话运行/恢复
       └─ RAM 辅助 S
            临时 scope/lease → RAM seed → side boundary
            → 仅显示新消息 → 独立运行/审批/停止
            → 关闭收尾 → 丢弃；不写回 A
```

导出只服务这两个明确消费者，放在 conversation 模块；不新建通用继承总线、跨会话 resolver 或另一套 Agent 循环。分叉与辅助对话共用稳定快照校验/值复制；发布、保存、关闭和权限分别由各自现有拥有者负责，不能强行合并整条链。

| 责任 | 拟归属 | 必要操作/失败合同 |
| --- | --- | --- |
| 稳定点 | `RunUnitOfWork.finish_run` + 会话仓储 | 成功保存同事务更新稳定锚点；失败不得发布新的可分叉状态 |
| 导出 | `application/conversation/context_snapshot.py` | 身份/版本/状态/预算校验；完整消息配对与值复制失败直接拒绝 |
| 持久分叉 | `SessionAPI.fork`→`session_fork.py` | 准备与发布分开；发布失败无侧栏半成品；重试不多建 |
| 辅助实例 | `TemporaryAPI` + `TemporaryScopes` | RAM 宿主及 lease 拥有会话；关闭只处理自己的任务和资源 |
| 工具与审批 | 现有 RunScope、能力、权限与工具执行链 | 新 sid/run、全新审批；安全检查不依赖 side prompt |
| 环境 | 现有 EnvironmentAPI/ResultService/InputService | 捕获并校验当前工作区，转为目标私有输入和独立绑定；操作日志负责不确定结果 |
| UI | 现有会话渲染 + 小型 SideChatPane | 按 scope/sid 绑定请求、流、草稿及审批，禁止串流 |

## 6. 运行和失败合同

### 6.1 持久分叉

只有持久根会话、最后完整成功回复、没有运行/审批/未完成跟进/保存故障时开放。沙箱还要求没有运行或结果未知的文件写入任务，能取得源准备预留和当前工作区快照；忙碌时拒绝，不停止父任务。摘要不是 assistant 回复；不能在子 Agent 面板、辅助对话、历史页旧消息上触发。

服务端先用短状态预留阻止源会话新运行/重生成/压缩/删除及环境写操作，再在 S1 锁内校验动作令牌和稳定锚点、复制上下文/档案；释放S1后捕获并校验工作区。重新生成消息/回复/tool-call 身份并保持引用配对，创建新 sid/thread。新会话不携带 run、队列、未决工具、审批、session_allow、子实例/任务控制权、MCP 活连接或父会话累计费用。预留不是跨IO持锁，所有出口均释放。

新工作区快照和目标私有Git输入必须校验ready，再将新 thread 写入 checkpointer，以短事务一次发布会话、档案、来源和操作成功结果。目标 id 已在操作记录中预留；崩溃只对账和清理本次未发布数据，不能执行模型/工具以“恢复创建”。已经发布但响应丢失时，同 request_id 返回同 B。

完成后打开 B 并聚焦空输入框，A 草稿保留。B 可改名、置顶、删除、重开，来源只是导航信息；删除 A 不级联删除 B。创建阶段 0 模型/工具请求；后续运行走普通主链。

### 6.2 辅助对话

主会话有稳定完整保存点即可打开；主会话正在运行时也可打开，但只带上一次完整保存的上下文，排除本轮输入、部分回答、工具结果和审批。首次运行未形成稳定点时禁用并解释原因。服务端不读取最新 live graph 来猜截断边界。

辅助实例有独立 sid/thread/run/control/事件流和 RAM 保存；继承消息只供模型参考。用户新问题才是任务入口。模型/工具选择取打开时快照，之后只在 S 内变化；父模型变化不更新 S，S 选择也不影响 A。

默认允许真实能力支持的读取、检索和联网；修改与未知副作用调用强制本实例重新审批，拒绝规则仍优先。辅助首期禁用子 Agent 委派/控制、跨会话历史查询、记忆工具、环境管理和worktree创建。文件/Shell/AITest使用自己的沙箱：源闲时可捕获，源忙时先不注册这些依赖执行环境的工具，提示“主任务运行中，辅助工作区尚未复制”；后续用户再次发送时可重试准备。不能静默把读父文件请求变成已完成，也不自动等父任务或中断它。后台任务不得脱离辅助实例存活。

折叠保留实例和草稿；关闭封口、取消/拒绝待审批、停止自己的运行/进程、排空投递、关闭 MCP/存储、清理自己的临时产物/沙箱，然后丢弃。清理不确定时显示“收尾未确认”，不能声称已清空，也不能停止/删除 A 的资源。正常刷新/lease断开视为关闭；重启不恢复对话。正在收尾时不开第二个同源实例。

### 6.3 执行环境与保存边界

Host 项目：沿用同一已核验的项目根，进程和审批仍属于新会话。两个会话可以观察同一文件系统，辅助对话明确批准的文件修改会保留；关闭不会回滚文件。它们的文字上下文快照独立，不代表外部工作区快照。

Sandbox项目：从源沙箱的**当前工作区**捕获已提交文件、修改/删除及允许的未跟踪文件，生成独立校验输入；新会话使用新binding/input/container/卷和相同冻结模板选择。复用现有result capture/chunk/verify与Git输入安装，不复制父容器、后台进程、工作区外依赖或凭证配置。源HEAD/index/文件不被改写，不向用户仓库发布结果ref。具体文件范围、排除与一致性见142A第7节。

持久分叉先完成文件捕获再发布B，但不创建目标Docker容器；首次发送才沿原准备流水线安装B。辅助打开只复制文字上下文，首次发送尝试捕获文件，之后冻结该文件快照、不持续跟父同步；源忙时仅保留有能力的上下文/联网调用，文件能力明确不可用。文字快照和文件快照的时点分别显示，不暗示两者相同。目标安装失败保留同一输入供正常重试，不重新读取父最新文件。

辅助沙箱在关闭时仅精确清理本实例创建的资源；分叉沙箱按普通持久会话管理。沙箱容量、输入丢失、模板不可用、输入校验或清理失败均保留现有错误，不回退执行环境。关于替代方案与待确认点见 [142A 第 7 节](142a-conversation-fork-and-side-chat-contracts.md#7-执行环境及临时资源)。

“不持久化辅助对话”承诺不将聊天正文、模型 checkpoint、历史列表、摘要、记忆提取结果落入项目/个人会话库。工具执行为了可确认的提交/取消可能产生操作记录和临时输出；它们不作为聊天恢复入口，按精确资源归属清理。用户主动写入项目的文件和外部服务的副作用不会因关闭自动撤销。不能承诺进程崩溃后远端已经运行的任务瞬间消失。

## 7. 成本、风险与发布边界

预计涉及超过 8 个文件、前端/服务/应用/存储/执行五处边界，拆为四个串行可验收切片，详见 [142C](142c-conversation-fork-and-side-chat-acceptance.md)。没有新语言、新服务、新账号、UI依赖或模型供应商；复用用户已有模型、可选联网/MCP配置和沙箱模板，本轮不读取或修改真实凭证。

最脆弱的假设是“源环境在准备预留期间不存在绕过应用的外部写入”。应用会拒绝自身未完成/未知任务并阻止新写任务，用现有文件/HEAD/身份核验拒绝观察到的并发变化；不承诺宿主管理员从Docker外部修改时仍得到事务级磁盘快照。不满足时失败保留源，不静默退回初始输入或共享binding。

最小实施选项是先交付持久分叉，再交付辅助对话；不为了快速上线删掉来源校验、工具审批、关闭收尾或不持久化合同。与共享环境方案相比，独立沙箱多占容器额度，并有工作区导出/下载/校验和准备延迟；收益是无需豁免envd会话所有权，关闭与父任务隔离有清晰证据。

10 倍会话长度时按显式复制预算拒绝超限，不静默截断或替用户压缩；10 倍并发时复用全局运行和沙箱额度，另对辅助 RAM 总量准入。联网/模型/MCP不可用按当前错误返回，不影响父会话；观察投递失败记录诊断，必要保存失败不能降级成成功。

项目库计划 schema20→21，包含来源、操作和稳定锚点；注册库新增精确临时执行资源归属。使用现有备份/迁移流程，旧记录默认无来源。关闭功能入口可以停止新创建，不删除已有分叉；旧二进制回退需核对 schema 支持，必要时经用户明确批准离线恢复备份，不自动降库或抹去新会话。

## 8. 本轮产物和唯一下一步

本轮仅完成 Spec 设计及静态文档核验，没有新增生产模块、接口、表、测试或 UI；也没有新增学习掌握证据。文档检查结果在交付回复中列出，不能代替后续实现的 TDD、构建、安装或真实环境验证。

下一步是审阅本组Spec，重点检查文件复制范围、辅助按需环境及工具审批细节。批准后才按142C四片实施。Spec141F公司原机候选验收仍独立未完成，本Spec不将其标成通过，也不改动其候选或环境。
