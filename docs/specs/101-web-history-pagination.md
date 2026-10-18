# Spec101：长会话历史懒加载与操作准入解耦

日期：2026-10-17，2026-10-18更新。状态：101-01归档基础、101-02显式准备/轻量准入/只读分页已接线；101-01合同/证据见第11—12节，101-02合同/结果见第14—15节。101-03根/子分页、阅读锚点和补偿合并已接线，合同/结果见第17—18节；101-04组合验收已完成，最终结果/边界见第21节。整体采用“finish同步＋历史消费前补录”，各片按先讲解具体合同、确认后实现的节奏推进。前文当前链描述保留最初设计时点，以最新实施节为准。

用户要求逐项讨论、比较后记录，再实施。此前第1—10节保留设计边界；101-01只修改对应生产代码、测试和文档，不修改真实配置、用户库或发布；Spec100验收已收尾，保留其实现与原始证据。

## 1. 问题与当前调用链

长历史使Web会话快照超过默认1 MiB JSON预算后，GET会话返回409 view_too_large。start、compact、approve等操作先调用同一个session()，因此显示超限同时阻断继续运行、手动压缩与提交审批。

当前入口与消费者：

- `web/service.py:WebService.session`读取应用历史/当前状态，生成message_view，并在存在latest运行时读取DeliveredRun.view；最终结果同时包含messages和live.messages，JSON会重复编码消息。
- `web/run_delivery.py:DeliveredRun.view`也独立检查整份视图大小；_launch通过previous.view读取tool_states，不能只删除session末尾检查或只更换start入口。
- `application/async_application.py:aget_session_view`返回describe_session与aget_messages组合；持久只读历史由async_persistent/session_recovery提供。
- 前端api/useConversation读取完整会话或运行快照；Conversation按messages渲染，并在接近底部时自动滚到底。

证据：用户报告每轮约30KB、第17轮后HTTP会话持续409。本次独立服务层探针用真实WebService和合成历史验证：30条×30000字符可读，36条时session/start/compact/approve均409 view_too_large；无live、数据库、工具或模型调用。探针未重跑用户完整17轮HTTP场景，具体轮数不是固定阈值。

## 2. 用户确认的交互

1. 打开会话默认加载最近一段消息，初始定位最新内容，不下载全部历史再在前端隐藏。
2. 鼠标向上滚动，接近当前已加载内容的顶部时，自动请求更早一页，并按时间顺序前插。
3. 前插后保留当前阅读锚点及其屏幕偏移，不跳到新加载页顶或会话底部。
4. 有更早历史时显示加载状态；全部加载完显示历史起点，停止继续请求。
5. 加载失败保留已显示内容、阅读位置与草稿，支持重试，不循环自动请求。
6. 阅读旧消息时新消息不抢走位置；在底部阅读时保持原有跟随最新行为，并保留“回到最新”。
7. 自动或手动压缩仅替换模型上下文；用户仍可懒加载压缩前的原始聊天消息和工具结果，刷新与服务重启后保持。浏览旧消息不把它们自动放回模型上下文。

## 3. 方案方向与拟议接口

下面是工程建议，最终接口在实施前审阅确认，不把字段名或默认页大小当成用户已经批准的合同。

- 会话状态/操作准入与消息显示分开：准入读取存在性、工作区归属、busy、保存/恢复、人工核对与当前审批批次等权威状态，不为判断权限和可运行性生成完整JSON历史。
- 会话首屏返回有界最近消息及分页元数据；增设历史读取入口，建议GET `/api/sessions/{id}/messages?before=<cursor>`，返回messages、has_more、next_cursor及历史版本。精确接口可按既有入口职责调整，不保留两套完整历史装配链。
- 使用不透明游标，服务端验证所属会话、历史版本和位置。不能仅按会变化的当前数组offset翻页；不能接受客户端指定thread、文件路径或任意checkpoint作为授权。
- 单页同时受消息数量与编码字节预算限制；页内按原顺序返回。消息数量上限、默认页大小与单条正文详情协议留在本稿审阅范围，不在本轮新增配置。
- 工具调用与结果按原ID关联。页边界需以显示组为单位，或返回明确关联信息，避免把ToolMessage误配到相邻AIMessage；超大组不能使分页永远无法前进。
- 单条消息/工具结果超过页面预算时返回明确预览与受同一会话鉴权约束的详情读取能力，不静默截断为完整正文、不再次整体409。详情协议与多模态投影一并审阅。
- 清除会话响应中整份messages的重复嵌套；运行状态、SSE补偿与历史分页使用清晰的消息覆盖范围，不让状态查询再次要求交付全量正文。
- 新运行的projection种子和旧tool_states读取直接使用各自必要数据，不经过有界显示view作为执行门禁。显示窗口绝不作为Agent执行历史输入。

## 4. 正常与失败路径

正常：选择会话→认证/工作区检查→读取当前状态和最近一页→Conversation定位底部→上滑触发一次历史请求→验证游标→返回更早一页→按消息身份去重前插→恢复阅读锚点。浏览历史不调用模型、工具或改变checkpoint。

大历史下发送/压缩：消息或compact命令→独立的会话准入检查→原RunDelivery准入锁与request_id幂等→原应用执行/保存→实时事件更新最近窗口。显示总量不构成准入条件；模型上下文预算、审批和人工核对等原阻断保留。压缩仍可能因原有合法条件或供应商错误失败，不能宣称总能压缩成功。

失败：旧页网络失败仅显示重试状态；会话切换/删除后旧请求取消或按generation忽略，不把A历史插入B。聊天历史版本与模型上下文版本分开：压缩本身不删除聊天档案、不使聊天历史游标仅因上下文切换而失效；发生实际历史修订/删除导致游标基线不可用时，明确返回失效并重新同步，不能把显示历史当作执行上下文恢复。

实时消息、SSE重连及补偿快照必须按明确覆盖范围合并，区分消息新增、修订与上下文移除；不能用最近一页全量替换已加载列表，也不能把分页未包含误当成删除。上下文移除不删除聊天历史，摘要作为上下文说明而非替代原聊天。游标版本与事件sequence分别表达历史基线和实时事件进度，不混用。

## 5. 保存与压缩边界

懒加载只改变显示交付，不修改或裁剪真实checkpoint，不把显示页传成模型上下文，不凭前端缓存推断审批或保存事实。

用户已确认跨压缩保留可浏览原文的目标，不能仅给当前有效checkpoint分页。当前StreamingMessageProjection及前端事件合并已保留实时显示消息、不按上下文移除删除原文，但内存显示状态不等于跨重启历史档案。

推荐在现有工作区SQLite中保存独立聊天记录，checkpoint继续负责Agent执行状态/模型上下文。复用原数据库而非新增服务；按实际消息身份保存用户消息、完整AI/工具结果、必要修订及明确的未完成状态，不要求逐token或全事件永久落库。保存不能仅靠可降级的观察订阅者，须定义必要保存与运行收尾的合同，失败不静默丢记录、不重新执行工具补历史。表结构、提交顺序、故障补偿与schema迁移在实施前审阅，不因本稿直接写真实用户库。

归档来源采用官方已保存checkpoint：在finish或明确准备操作中增量整理到独立聊天表；分页查询直接查聊天表，不在每个历史GET中扫描旧checkpoint。需处理快照重复、修订、恢复分支与pending writes。旧会话原文补录独立核验可找回范围，缺失明确说明，不伪造摘要为原文。会话删除覆盖新增聊天记录，根子范围按已确认来源边界核验。

单次交付仍保留大小保护。bounded响应不自动证明后端只读取一页；实现评估须分别记录数据库/框架读取成本、序列化成本与浏览器渲染范围，不把全历史读入后切片称为数据库级分页。首版不为此预建新的通用消息平台或未经批准的存储迁移。

## 6. 影响范围与迁移检查

预计涉及超过8个文件：Web service/app/schemas/serialization/run_delivery/settings的实际必要部分；应用async_application/async_persistent/session_recovery的读取与准入边界、session_storage及必要聊天记录存储适配；前端api/types/useConversation/Conversation及对应测试。具体文件与函数清单在接口审阅后收敛，新增模块前先搜索已有分页/合并能力，保持单文件不超过500行。

同一切片检查所有self.session消费者，包括发送、compact、approve/stop_approval、recover、模型/工具/模式选择及子历史入口；保留各自权限和状态门禁。删除只服务旧整份视图的内部转发，迁移当前测试和前端合同，不为旧测试保留阻断长会话的兼容路径。

## 7. 后续实现验收

- 先增加能复现长历史打开/发送/compact阻断的失败回归，再最小实现；用户报告的真实17轮形状和工具结果放大用确定性模型/临时SQLite复现。
- 无live、有latest、重启后历史与受限恢复均可有界读取；超限历史仍可触发合法发送/压缩和审批，非法状态继续拒绝。
- 首屏仅交付最近页，上滑多页不重复、不漏消息、顺序与工具配对正确；空会话/最后一页/超大单条/超大组均能结束或前进。
- 翻页期间流式新增、结果修订、SSE补偿、压缩历史版本变化、切会话与删除竞态均有确定性断言；旧响应不能污染新会话。
- 聊出超过显示上限的原历史→压缩→模型输入为摘要/近期内容→页面仍可分页读取原文→关闭重启后仍保持。自动/手动压缩均覆盖；模型输入和聊天档案分别断言，不以页面缓存代替保存证据。
- 聊天保存故障不静默丢失、不触发工具重放；消息修订不复制，审批恢复保留配对；schema迁移和会话删除覆盖新档案，旧历史补录缺失有明确结果。
- 页面前插保留阅读位置，顶部加载仅一项在途；失败可重试，底部跟随与回到最新仍可用。浏览器验证真实滚动，不仅检查DOM数量。
- 工作区/认证/游标篡改与跨会话拒绝；只读历史不执行模型/工具；审批批次、来源、准入锁、request_id幂等、保存失败和停止合同不弱化。
- 网络响应大小与读取/序列化成本分别测量；不以提高或去掉1MiB限制充当修复。
- 实现阶段执行目标测试、全量后端、前端测试与构建，并给出实际输出；本稿仅文档记录，无功能完成声明。

## 8. 同步时点比较与建议实施基线

本节是本轮讨论后的具体推荐，供后续逐片实施；不把讨论写成已完成代码。

| 比较项 | 前版：强调收尾时立即整理完整事实 | 推荐版：finish同步＋消费前补录 |
|---|---|---|
| 正常完成 | finish统一整理新增checkpoint | 相同，不随每次checkpoint触发写入 |
| 异常/停止 | 强调当前收尾同时整理pending writes | 能走完finish则复用同步；未走到或未成功完成时由下一次明确准备补录 |
| 崩溃 | 已提恢复补录，但未充分限定首屏时序 | 明确在历史交付前补录，不等下一轮模型调用完成 |
| 正在运行的显示 | 事件与显示投影 | 相同，实时显示不等待聊天表 |
| 冷打开/缓存已丢失/重连缺口 | 消费边界不够明确 | 明确准备历史覆盖范围，再以聊天页和有界live合并 |
| 消息去重与事务 | 消息ID/内容比较、进度与记录同事务 | 相同，复杂度不会因改时点而消失 |

推荐后版：与实际消费者对齐，避免为即时归档增加逐checkpoint监听或每个异常分支的独立处理。两版正常路径接近，不声称这是数量级减少代码；实质改进是补齐首次显示与恢复的一致性边界。checkpoint仍是运行事实，聊天表是可补录的查询记录；归档成功不能代替工具授权或执行恢复确认。

### 8.1 两个主要触发点

1. **finish**：一次应用arun/审批恢复/compact执行段退出后、已有执行资源settle之后，复用SessionPersistence.afinish必要保存。审批暂停也属于本段结束。正常路径复用已读取的最新状态；不每产生一个checkpoint就写表，也不逐token归档。
2. **明确打开/恢复或历史重新同步准备**：读取归档进度；有缺口时先补录已保存消息，再交付对应历史范围。覆盖ready冷装载、异常恢复、同进程失败后重新选中、交付缓存丢失及事件缺口，不能仅检查“是否发生进程重启”。普通历史分页GET保持只读，不隐式安装会话或写档案。

准备读取时若根run仍在运行，冻结一个已经保存的checkpoint边界，只保证该边界以前的历史；之后由原实时链交付。归档批次带来源边界，不能声称追平不断推进的全局最新状态。必要的同thread同步互斥与提交时进度复核由持久化拥有者负责，不持短状态锁跨IO，不让较旧准备覆盖较新归档。新模型/工具不等待显示准备；SSE实时范围与档案范围按明确身份合并。上滑所需范围已经归档时直接查询，不每页都重复补录。

“打开/准备”复用Spec102第6项的显式写操作方向；正常打开可以由前端自动执行，不新增用户手动确认。ready安全装载与中断恢复仍是不同动作，不能把ready直接塞入现有仅接受continue/restore_approval的recover POST。历史准备成功与会话可执行分别返回，不绕过原恢复/人工核对/审批门禁。

### 8.2 识别、写入和故障规则

- `naiyue_chat_sync`按可信session/thread/namespace保存归档checkpoint边界；`naiyue_chat_messages`按同一归属和message_id唯一，记录稳定显示顺序、内容及版本、来源。同ID同内容跳过、同ID修订更新原位置、新ID新增；内容相同但ID不同不得合并，压缩移除不删除旧聊天。
- 源读取只沿当前有效checkpoint父链整理，分批指定limit/before；当前框架aget_state_history会先消耗指定范围再yield，不能无limit读取全史后在外层break冒称增量。按旧到新整理；读取、反序列化及SQL成本分别测量。
- 历史快照用于保留本轮压缩前原文；最新aget_state在不指定checkpoint_id时可合入相应pending writes，作为同一批消息识别的补充。即使checkpoint ID未变化也需核对这份最新状态；不另建pending写入监听或归档状态机。运行中的固定边界读取与pending结果的可见性须用探针核验，未核验不得把较晚数据错误标成较早快照。
- 消息增量、归档进度和现有会话收尾元数据在本方SQLite事务内提交；官方checkpoint仍由原saver独立提交，不声称与聊天表跨连接原子提交。历史准备只更新档案/进度，不把活动run标ready。采集候选、序列化在短SQL事务外完成。
- 服务在finish前崩溃可以暂时没有档案增量，重新打开时补录；不要求为归档而再调用模型。不承诺找回未落盘的半截token。源缺失、无法解码或归档失败明确返回未就绪/缺口，已有记录仍可明确标为不完整地读取，不能悄悄当成完整首屏。
- 实际finish写入IO失败不等于“允许崩溃造成暂时滞后”：保留现有必要保存失败合同，不推进进度、不覆盖原运行异常、不误报ready。打开补录失败不自动批准/重放旧调用，也不阻止整个服务重启；受影响会话明确显示准备失败并允许重试。
- 根/子档案分别按thread保存并绑定主会话；来源映射从真实父调用派生，不接受客户端指定任意thread。原消息ID、工具call/result ID及已有来源标记保留，为后续分页和回复归属提供事实，不按run_id或相邻关系猜造回复块。

### 8.3 本轮依据及限制

已核对WebService.session/start/recover与前端useConversation：实时watch使用事件及运行投影，selectSession当前先GET并显示，再按条件自动recover；若改成只读落后的聊天表并等下轮finish，崩溃前已保存的一段会暂时缺失。因此需要消费前准备，且将GET中的原生命周期写副作用迁出。

前轮临时SQLite/离线模型探针：压缩前4消息，压缩后当前上下文3消息且有摘要，30个checkpoint版本仍包含原4消息。既有test_recovery_pending_writes实际1 passed in1.14s，验证已落盘工具结果无需下一次模型请求即可恢复，原任务记录不被改写。这些是归档来源可行性的证据，不是新聊天表/分页/准备接口的实现验收。

## 9. 分片与代码职责

| 切片 | 明确交付 | 实施边界 |
|---|---|---|
| 101-01 | 聊天归档与进度表、增量整理、finish/恢复收尾接线、迁移/删除/故障验证 | 新增application/chat_history.py的collect_chat_updates；扩展SessionStorage、SessionPersistence、session_recovery现有职责。独立async整理函数通过现有执行链调用，不新造生成器驱动或Runtime回调。暂不将聊天表替换为Web唯一历史源 |
| 101-02 | 显式打开/历史准备、轻量准入、只读有界分页与详情、live覆盖范围 | 与Spec102第6项合片；完成消费前补录后再切换Web读取。所有self.session操作消费者同片迁移，运行投影种子和tool_states不再依赖完整view。页/组/大消息与HTTP合同在本片细案审阅时确定 |
| 101-03 | 前端首屏/上滑/锚点/重试、打开准备与SSE补偿合并 | 依赖101-02已稳定的准备/分页/覆盖合同，保留原Composer公共交互；子智能体按同来源范围读取 |
| 101-04 | 长历史＋自动/手动压缩＋重启/崩溃/审批组合验收、旧链清理 | 从已保存原文到页面可读和模型上下文分别断言，不重放工具，不保留被替代全量视图入口 |

101-01新增表沿现有工作区库schema6事务升版，保留当前4/5→6路径再升级；旧全局1/2/3仍不在支持范围。迁移本身不启动模型、不同步扫描所有会话checkpoint，不默认删除旧数据。该片包含迁移、去重/修订、跨压缩原文、根子归属、pending、归档事务失败、重开补录与会话删除测试，预计代码与测试合计超过8文件；公开分页读取及前端切换属于101-02/03，第一片不宣称已修复完整Web长会话问题。

## 10. 其他Spec的依赖与并行边界

| 事项 | 与本Spec的关系 |
|---|---|
| Spec102第6项轻量准入/显式装载/GET只读 | 101-02的直接共同交付；先准备再交付历史。不能只实现分页却保留GET安装/恢复及全历史准入 |
| Spec102第1/2/3/4项状态、共享阶段、原生async、SessionPort | 不作为101全量前置；101归档进度归持久表，不塞入运行SessionState。新增整理直接async，后续生命周期迁移复用同一入口；归档不经可降级事件或SessionPort零散回调绕入Runtime |
| Spec102第8/9项设置异常辅助、DeliveredRun拆文件 | 随101-02涉及的实际文件对齐；整类迁移可协调完成，不为历史功能强行先做全部重构。DeliveredRun继续是可淘汰显示缓存，不拥有档案 |
| Spec102第7项跨会话并发 | 仍待用户决定，101不升级全局执行额度。归档作用域和锁不能预设进程唯一当前会话，方便之后按会话协调 |
| Spec102第5/10项清理与middleware说明 | 独立后续切片，保留现有身份/顺序/取消与预算消费者，不因归档调整框架循环 |
| Spec103 U1回复块 | 消费101的消息身份/修订/来源与分页覆盖；稳定请求归属还须确认实际生产字段。101-01不据run_id猜造归属，不能单凭基础表完成就宣称U1已可实施 |
| Spec103 U7重连 | 消费101-02/03准备与补偿协议；缺口时恢复显示，不重发模型/工具/审批执行POST |
| Spec103已完成U5/Composer | 独立，保持原props与控制API；本轮不修改其组件/样式/测试，不覆盖另一会话成果 |
| Spec092恢复 | 保留既有状态分类与必要多次更新。恢复改写前保留原始已保存消息，恢复说明再按原身份归档；档案补录不等于恢复可执行 |
| checkpoint修剪、M6缓存、持久usage/诊断 | 独立设计，不作为本片前置；当前不清理旧checkpoint。以后修剪必须先保证待保留聊天已归档，诊断TTL不套用聊天记录 |

## 11. 101-01实施合同

本片已接线。新增`application/chat_history.py`的不可变`ChatUpdate`（一个可信thread的候选增量及预期进度版本）和原生async `collect_chat_updates(agent, storage, session_id, *, snapshot=None, previous=())`。它读取SessionStorage中的可信根thread及已登记子范围，从真实父调用派生新子thread；根/子使用独立顺序与进度，namespace首版为现行空根命名空间。调用方不能通过HTTP传入thread。父、子执行均已settle后才采集。

`SessionStorage.chat_heads`只取进度。采集器以有限页（每页64个）沿最新快照的parent_config链回溯，遇到已归档边界停止，倒序整理新增快照并补最新aget_state可见的pending结果；忽略不在父链上的分支。边界丢失、消息缺ID或不可编码明确失败，不把源缺口当成归档完成。即使边界ID相同也核对最新消息。消息用官方message_to_dict表示，稳定JSON保留content、artifact、usage、call ID及additional_kwargs；不经前端显示投影。摘要另标context_summary，不替换原文。

schema7：`naiyue_chat_sync`以(session_id,thread_id,checkpoint_ns)为主键，保存agent_path、checkpoint_id和revision；`naiyue_chat_messages`在上述范围加message_id为主键，保存sequence、revision、body_json、body_hash、kind及source_checkpoint_id，范围内sequence唯一。进度revision用于提交前复核采集基线；同ID同正文跳过，同ID修订保留sequence并推进消息revision，不同ID不按内容合并。source_checkpoint_id保留这版正文在有效父链上最早可确认的来源；后续重复快照不覆盖，正文修订才更新。消息revision是归档提交观察到的修订计数，首次补录保存最终正文，不假造此前从未提交过的修订账本。

扩展现有`SessionStorage.finish_run(..., chat_updates=())`，只增加内部可选参数；候选消息、进度与原finish元数据在一个短SQLite事务提交。IO和序列化在事务外。schema4/5先执行原6迁移再到7；现行6直接加空表，不启动全库回填。delete事务覆盖新增两表。不会修改官方checkpoint/writes的结构。

`SessionPersistence._finish_steps`在原ready检查之后、finish_run之前调用collect；失败/停止/暂停也采集已保存事实，仍保持原“最后安全checkpoint”元数据语义。采集或事务失败沿用必要保存异常及受影响会话阻断，原运行异常优先。`session_recovery.recovery_steps`先采集改写前事实、保存在本次候选中，保留三次官方更新，随后采集新恢复说明，与finish元数据一同提交；恢复审批分支同样保存档案。恢复中途崩溃未提交候选时，下次从保留的官方父链重建，不自动执行工具。

正常例：user h1 → AI a1/read c1 → Tool t1 → AI a2，finish提交4条消息及进度；下一段压缩只留下摘要s1及近期内容，新增s1但h1/a1/t1/a2仍在档案。失败例：工具A的结果已在pending writes、B失败；finish保存A，恢复时追加B的result_unconfirmed消息；如果SQLite拒写，两表和收尾元数据一起回滚，模型/工具不重放。

验证包含：真实临时SQLite正式应用收尾、审批、自动/手动压缩与重开、根/子归属、pending writes/恢复、去重/修订/同文异ID、有限页/父链/缺口、schema4/5/6迁移、删除及事务故障/原异常优先。101-02再实现历史准备与分页公开接口；本片不迁移Web GET、不改变aget_messages的当前上下文语义、不修改Composer。

## 12. 101-01实施结果与验证

生产代码只新增chat_history.py，修改session_storage/session_persistence/session_recovery；不新增Runtime中间件、事件观察回调、依赖或配置。原IOCall/arun_steps只负责编排这条已存在的应用链，collect本身原生async，后续102可直接复用。finish前仍先settle子驱动者、清理运行资源；ready的原安全checkpoint检查保持，失败/暂停的最后安全checkpoint元数据不被归档进度冒充。

当前正常调用链：`SessionService._afinish_run` → `SessionPersistence.afinish/_finish_steps` → `collect_chat_updates` → `SessionStorage.chat_heads`＋官方有限页`aget_state_history`/最新`aget_state` → 返回每thread的ChatUpdate → `SessionStorage.finish_run/_write_chat_updates`同事务提交 → 原释放busy/终局。例：h1、a1(read c1)、t1(c1)、a2分别有稳定ID，档案sequence为1/2/3/4；重复快照不追加，同ID修订只更新原行。

恢复链：`recovery_steps`校验分类/权限 → collect保存改写前候选 → 原cleared/paired/finished三次更新 → collect(previous=原候选)合入新增恢复说明 → finish_run一次事务提交 → 安装会话。已落盘的A工具结果保持原artifact及ID，B缺结果仅增加result_unconfirmed消息，不重放旧工具。中途进程退出未提交候选时，下次从官方保留父链重建；档案成功不等于审批授权。

先运行新5项回归，得到5 failed（缺少表/模块），随后实现转绿。扩展真实saver多页补录发现同正文来源被覆盖，新增回归后修正候选去重，完整行比较保留；测试默认循环同一AI对象会复用ID，分页夹具改为12个独立响应/ID，未放宽断言。schema4/5夹具同步移除新表后再模拟旧列，保留原身份、顺序、思考档位断言；未修改conftest或共享test helper。

最终命令与实际输出（2026-10-17，本机临时SQLite/离线模型，无供应商请求）：

```text
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
2245 passed in 91.83s (0:01:31)
cd frontend && npm test -- --run
20 files, 138 passed (2.48s)
cd frontend && npm run build
TypeScript/Vite success; 207 modules transformed
```

日志：`/tmp/naiyue-101-01-backend-final.log`、`/tmp/naiyue-101-01-frontend-current.log`、`/tmp/naiyue-101-01-build-final.log`。归档/恢复专项36 passed in14.97s（`/tmp/naiyue-101-01-archive-final-targeted.log`），含真实多页checkpoint补录、分支排除/缺口、pending、审批、根子、崩溃边界及事务回滚。新18项后端测试，另增强原自动压缩/恢复/子重启/迁移断言。前端先119项通过，收尾遇到另一会话ToolCall新增断言暂时1 failed/137 passed；对应会话修正其DOM多行断言后，本轮再跑当前工作树138项全部通过，本片未改该区域。compileall、pip check、369份Python AST/≤500行、局部凭证形状和diff检查通过；架构/安全独立复核无确证遗留发现。

实际读取成本：归档器每次官方历史读取limit64，已同步边界停止（同ID边界只读最新合并状态）；第一次补录仍需遍历当前有效父链，返回的完整快照要解码，采集候选在内存保存到短事务提交。未声称首次补录或当前checkpoint读取是常量成本。Web尚未改为档案查询，1MiB显示阻断尚未由本片闭合；消费前准备与正在运行时的固定覆盖边界交给101-02，滚动交给101-03，完整浏览器组合交给101-04。未修改用户库/真实配置，未提交/push，未新增用户掌握或Windows/Linux原机证据。

## 13. 101-02实施前的讲解范围（历史，现已接线）

与102第6项共同交付：明确的准备写操作负责会话安全装载及档案追平；普通GET只读取状态/分页，发送/压缩/批准/模式等操作只用必要状态作准入。复用collect_chat_updates和_write_chat_updates，不另建归档器；新增档案单独提交入口不会调用finish_run把活动run标ready。准备与finish的同scope协调/版本复核在此片完成，固定已保存边界，后续较新事实由live交付。

拟扩展SessionPersistence/AsyncPersistentApplication的prepare入口，SessionStorage的分页/正文查询，WebService的显式prepare、轻量状态与历史读取，以及RunDelivery中projection种子/原tool_states不经过整份view的接法。普通aget_messages仍指当前模型上下文；聊天分页只查独立表。HTTP准备/分页/大消息详情的字段、游标及预算在本片细案中明确；主子归属复用可信范围，不让客户端指定thread。

为保证切片可用，必要的前端api/选中会话接线与后端协议一起调整；自动上滑、前插锚点、完整SSE补偿合并仍留101-03，不改Composer输入交互。正常例为打开旧会话→准备已保存历史→有界最近页，再发送时只查状态；失败例为大历史仍能合法compact/批准，恢复/人工核对仍按原门禁，档案准备失败只明确影响该会话而不重放模型/工具。上述细案已在第14节具体化并按授权接线。

## 14. 101-02实施合同（本轮授权）

本片与102第6项合片，接口职责如下：

操作准入按可信session_id直接读取单行并核对服务端workspace_root；列表在SessionStorage用参数化workspace_root IS ?及必要列投影，排序保持pinned/sort_order/created_at/session_id。CLI/探针仍使用有真实消费者的alist_sessions全范围入口，不以Web过滤替换其合同。

- `POST /api/sessions/{id}/prepare`：显式装载安全ready会话、补录已保存消息，返回含根历史coverage的有界SessionView；不执行模型/工具，不代替continue/restore_approval。普通GET不装载、不写库。未准备的冷会话可以读取已有档案，但history_ready=false，不能把旧档案当完整覆盖；前端选中时消费POST返回值，不重复GET。失败会话准备仍可显示历史和原恢复分类，恢复仍走原POST。
- `GET /api/sessions/{id}`：轻量状态＋有界最近页＋小型live控制信息；messages仅在外层出现，live声明同一消息来源，浏览器本地接回delta基线。操作准入使用独立状态方法，发送/compact/审批/停止审批/模型/推理/工具/模式/子历史不调用整份session显示链。
- `GET /api/sessions/{id}/messages?before=<cursor>&agent_id=<optional>`：SQL范围分页，返回messages、has_more、next_cursor、coverage（checkpoint锚点/同步版本/当前修订版本/上界）。agent_id只由已保存真实父调用或本run可信来源解析；不接thread。游标HMAC绑定会话、agent、修订版本、固定序号上界及下一页位置。新增不使旧页失效，同ID修订使旧游标明确失效；压缩新增摘要不删历史、不使旧页失效。
- `GET /api/sessions/{id}/messages/{sequence}/content?agent_id=...&revision=...&offset=...`：受同一归属校验，返回原始官方消息JSON的有界字符片段、next_offset及complete；修订变化拒绝混合正文。不是重新读文件/执行工具。单条过大返回有标记的预览/详情身份，分页至少消费一条，不因超大消息或工具组永远停住。

WebSettings代码默认：history_page_messages=50，history_page_bytes=128KiB，history_message_bytes=16KiB，history_preview_chars=2048，history_detail_chars=8192；同时受现有max_frame_bytes约束。没有改真实配置文件。SQL先取有限行及有界正文；过大行不把完整body_json传入Python，SQLite内部JSON字段提取仍可能读取该行，首次prepare仍可能遍历未归档父链，不声称所有成本为常量。内容块不能伪装为纯文本完整正文；工具call参数未完整交付标arguments_truncated，调用元数据也未完整交付标tool_calls_truncated，不把部分调用列表当整批。

schema8从7事务增加sync.history_version/stopped和messages.message_type/parent_message_id。history_version仅在既有消息修订时增长，sync.revision继续用于并发提交基线；工具结果保存真实父AI消息ID，跨页不靠相邻或重复call ID猜配。7迁移只从现有档案提取类型/调用关系，不扫描checkpoint或模型；4/5/6继续原路径。归档候选从实际snapshot生成父调用归属和stopped。当前没有单条历史删除/分叉编辑API，不预建其平台。

聊天分页只交付kind=message，压缩摘要继续独立归档为context_summary，并以has_compaction提示压缩事实；摘要不额外混入原始聊天列表，刷新前后使用同一来源。冷审批恢复没有旧投影时，AsyncPersistentApplication.aget_approval_seed按当前可信pending.batch_id，经SessionStorage.read_approval_seed读取单个批次的AI身份和所有调用ID/名称；不取历史正文或参数。RunDelivery直接种入身份数据，不经显示view。identity_only种子只留服务端投影，不进入公开messages、覆盖档案原消息或让浏览器以空正文替换原调用；投影未知父归属的None不得覆盖已确认档案关系。执行中和完成后均验证配对。预算截断的draft标content_unavailable，客户端继续维护序号但不把后续delta伪接成完整正文，完整快照校准后解除标记。

SessionPersistence以会话async锁协调prepare/finish/recover/delete的归档采集与提交；不持线程短锁跨IO，不锁模型/工具执行。prepare冻结每thread当时aget_state返回的已保存快照（含当时可见pending），归档后只声明该边界；随后产生的消息由live交付，finish再次追平。等待归档提交可能延后finish，不能把活动run标ready。新增SessionStorage.save_chat_updates只提交档案，不改run元数据。冷会话准备得到的恢复说明只作该持久化拥有者的轻量缓存，GET不重读完整checkpoint；真正恢复重新核对最新checkpoint/权限。

新增应用查询模块chat_queries.py安放有界SQL查询/子记录索引，SessionStorage保留连接、锁、事务所有权；新增Web历史投影模块chat_history.py负责游标/预算/预览/正文wire，不把Web规则引入应用归档。DeliveredRun按现有102第9项拆至delivered_run.py，run_delivery保留调度/幂等/控制。运行view输出有界窗口及明确window覆盖，工具状态只携窗口/活动调用必要字段，projection种子不再依赖全量历史；原观察缓存仍非永久档案，不在本片实现M6全缓存预算。

前端只接prepare顺序、近期页/跨页工具归属及必要类型，保持Composer合同；自动上滑/锚点/全部补偿合并留101-03。测试先复现大历史阻断、GET隐式装载、超大消息和游标缺失，再覆盖SQL分页/修订/跨会话/子归属、prepare与finish/删除并发、pending固定范围、保存失败、原审批/幂等/恢复/停止；当前Web测试从旧GET装载迁移到显式POST，保留行为断言。未批准全Spec102原生async改造或跨会话执行并发。

## 15. 101-02当前实现、调用链与清理

新增生产模块仅三个：`application/chat_queries.py`安放现有SessionStorage连接上的查询/迁移，`web/chat_history.py`负责分页wire及签名游标，`web/delivered_run.py`接原DeliveredRun类。没有新增运行中间件、依赖或真实配置文件；原`application/chat_history.py`只补父归属与子停止事实。schema8的历史修订版本用于游标；原sync.revision继续做采集提交复核，不能与事件sequence、消息revision混用。

| 入口/对象 | 本片真实调用及数据边界 |
|---|---|
| `useConversation.selectSession` / `WebApi.prepare` | POST prepare，消费返回的有界SessionView；不多发一次GET，不自动重发用户消息 |
| `WebService.prepare` | 单行元数据/工作区核对→`AsyncPersistentApplication.aprepare_session`→返回近期档案/live窗口；原092自动恢复POST仍由前端按分类单独调用 |
| `SessionPersistence.aprepare` | 会话归档锁→必要时原arestore安全装载→冻结当前aget_state→collect_chat_updates→save_chat_updates→缓存轻量恢复分类。只提交档案，不修改活动run状态 |
| `WebService.session/messages` | `_session_status`→单行状态、当前批准批次→`aget_chat_page`→`SessionStorage.read_chat_page`→有界SQL/wire；GET不查执行checkpoint、不安装会话、不提交写入 |
| `WebService.message_content` | 同归属验证→`aget_chat_message/read_chat_message`→指定revision/sequence的原JSON字符块；不读源文件、不执行工具 |
| `WebService.start/compact/approve` | 独立状态→原RunDelivery回执/准入锁→原应用执行/保存。批准在锁内复核当前batch，不用显示历史充当执行许可 |
| `DeliveredRun.seed_data/view` | 必要种子与公开window分开；状态和正文预算不参与执行准入。冷审批单批身份种子只服务端保留，不覆盖原AI参数 |
| `SessionStorage.list_workspace_sessions` | 参数化工作区条件、必要列及原手工排序；按ID操作不先列全部会话。旧全范围alist_sessions保留CLI/探针等实际消费者 |

正常数据例：档案h1(sequence1)、a1/read c1(2)、t1(3,parent=a1)、a2(4)。limit=1先交a2及签名cursor，上翻交t1时明确parent=a1，不把同call ID的其他轮AI当父节点。新h2/a3追加到5/6后，旧cursor仍以upper=4翻页；修订a1使history_version增长，旧cursor与旧详情revision明确409，不能混合新旧正文。超过16KiB的消息只交标记预览与detail身份，原始JSON仍留档案，可由字符offset分块重组；超大组按消息推进，不要求整组塞进一页。

并发/失败例：模型暂停在等待响应时prepare冻结已保存h1，保存中模型仍可前进；finish等同会话归档锁后提交更新进度，prepare不把run标ready。准备取消仍等待已经开始的必要提交settle；提交失败清除prepared标记、记录安全异常类型、旧档案与进度保持，显式重试，不执行模型/工具补历史。删除遇当前prepare明确拒绝，不互相等待；其他会话创建仍可用。原保存故障阻断、审批幂等、停止与三次恢复更新继续复用。

原消费者迁移：Web冷GET自动装载测试/两个TCP探针改显式POST prepare，再核对只读GET，保留原身份/顺序/不重放/权限断言；schema迁移断言跟到8；手动压缩重开改查原始聊天，另断言模型aget_messages仍只有摘要/近期内容。两个仅用SimpleNamespace模拟旧Web设置的夹具改用真实WebSettings默认值，未改conftest、共享helper或依赖。DeliveredRun测试导入新真实模块；删除已无生产/脚本消费者的aget_session_view全量包装，GET回归直接禁止aget_state和aget_messages。原aget_session_history仍服务CLI/恢复查询，不为Web保留并行全历史链。

审查发现的两处冷恢复配对缺口均有失败回归再修复：空种子导致Tool.parent=None；随后内部身份种子误进入公开/run并可能覆盖前端原调用。现在单批元数据种入后只服务投影，公开/run不携identity_only，执行中工具父ID/状态batch及完成后档案原args都有断言。另有有界draft续接验证：维护delta_index，标记未完整，不拼接为假正文，完整snapshot才解除标记。架构与安全独立复核已闭合上述发现。

2026-10-18最终验证（当前工作树，临时真实SQLite/离线模型，无供应商请求）：

```text
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
2259 passed in 93.60s (0:01:33)
cd frontend && npm test -- --run
23 files, 163 passed (3.18s)
cd frontend && npm run build
TypeScript/Vite success; 209 modules transformed
.venv/bin/python -m scripts.probe_web
web_probe=PASS mode=offline tcp=True sse_delta=True disconnect_keeps_run=True stop=True retained=True two_processes=True history_used=True duplicate_safe=True
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m scripts.probe_web_tools
web_tools_probe=PASS mode=offline tcp=True approval=correlated mixed_decisions=True resume_run=new duplicate_safe=True tool_results=paired two_processes=True history_used=True
```

新14项后端/4项前端回归；14项分页/准备/归属专项4.82s，工作区查询/恢复/管理专项23 passed in4.02s。初始3项缺口红灯后实现转绿；冷审批配对、公开身份种子、工作区全表读取及超大批次调用截断标记均先复现再修复。全量首轮2255通过/1项旧压缩显示合同失败：保留原聊天等值断言，修正查询仅交原聊天，并迁移重开断言为原文＋另验模型摘要，而非削减检查。前端新增用例的选择器和缺失matcher按当前项目夹具修正，未引测试依赖或放宽产品行为断言。并行Spec103当前UI结果包含在163项与本次构建中，未覆盖其Composer/Sidebar/ToolCall实现。

日志：`/tmp/naiyue-101-02-backend-final-current.log`、`/tmp/naiyue-101-02-frontend-verified.log`、`/tmp/naiyue-101-02-build-verified.log`、`/tmp/naiyue-101-02-specific-final.log`、`/tmp/naiyue-101-02-tcp-web.log`、`/tmp/naiyue-101-02-tcp-tools.log`。compileall/pip check/diff通过，376份Python AST与426份源文件≤500行、本轮文档562个本地链接及局部凭证形状检查通过。架构/安全复核无确证遗留发现。未改真实配置/用户库，未提交/push；无新增浏览器滚动验收、Windows/Linux原机或供应商实网证据，不新增用户学习掌握声明。

## 16. 101-03实施前准备范围（历史，现已接线）

本片已提供分页来源，前端目前仍只展示最近窗口；下一片完成根/子同来源的浏览行为。复用api.messages、history coverage及events.mergeMessages，不改变Composer或重新设计执行协议。`useConversation.ts`目前467行，不能继续直接堆入全部滚动与分页逻辑；拟新增由根/子两个真实消费者使用的`useMessageHistory`，持有分页游标/加载错误/作用域代次和单在途请求，必要合并算法沿现有events模块扩展。`useConversation`接根首屏/重连，`useSubagents`接选中子范围；不是另造消息仓库或缓存平台。

`Conversation`新增可选历史状态/loadOlder入口，接近顶部加载一页；请求前记录首个可见消息ID与视口内偏移，DOM前插后恢复同一锚点，真实浏览器验证。加载失败保留当前内容/位置，显示重试，不自动循环；全部读完显示起点。主页面App和SubagentSidebar都接同一组件，审批仍在主会话。切会话/子范围/删除递增代次，旧响应不插入新范围；阅读旧页时live追加不抢滚动，底部跟随/回到最新保持。

SSE缺口由prepare补录已保存范围、分页与live window按ID/revision合并，不把窗口中缺少的旧消息当删除；活动草稿与序号仍有自己的基线。原自动继续/恢复审批继续独立POST，不重发执行/审批请求。大消息先明确预览状态及详情身份，按需读取的具体展示与分页细案一起确认，不默认在首屏下载完整超大正文。101-04再做长历史＋自动/手动压缩＋重启/崩溃/审批的完整浏览器组合验收与最终旧链检查。

## 17. 101-03实施合同（2026-10-18用户授权）

本片新增前端useMessageHistory.ts，由根useConversation与子useSubagents两个真实消费者使用。hook拥有消息列表、当前(session_id,agent_id)范围、页游标/coverage、加载/错误及作用域代次；open安装首屏，merge接实时消息，synchronize合并最新已保存窗口并按需补缺口，loadOlder只允许同范围一个请求。reset/cancel使旧请求失效；真实fetch使用AbortSignal，同时用代次保护不能取消的迟到响应。根/子不分别复制分页状态机。不改Composer、模型上下文/执行协议或配置/DB schema。

正常链：selectSession→prepare→history.open(近期messages,history)→Conversation记录可见消息ID/偏移→loadOlder→api.messages(cursor,agent)→服务端SQL页→按ID/revision前插→布局阶段恢复锚点。只有实际上滑/点击加载触发旧页，不因列表变短或重新渲染连续下载全部历史。失败保留内容/位置/草稿，显式重试；无更多页显示起点。切换/删除/卸载取消在途请求；晚到的旧会话/旧子响应无副作用。

重连链：SSE缺口或显式重新同步→prepare补录已保存事实→取得新run window/事件基线→history.synchronize。普通新增只补最新窗口与已加载末端之间的缺页；历史版本改变时补到已加载最早序号，更新该已加载范围而非冒充旧正文仍为新版本。各次查询保持固定upper/version，途中再次修订则明确失败、保留旧显示并供重试，不循环重发执行/审批POST。同ID以最新revision和完整正文优先，实时终稿不被旧草稿/截断预览覆盖；前插页不覆盖较新事件，工具父消息身份/归档sequence/detail保留。无范围重叠时不把单纯最近窗口当全量替换。

应用接线：useConversation迁移messages/setMessages到hook，普通运行展示merge，选择安装open，控制状态refresh与消息同步保持各自职责；原watch的缺口分支补prepare及最新基线，结束刷新合并归档事实。useSubagents在范围切换时open并独立分页，详情刷新及实时更新仅合并，不重新清空已加载页；主/子Conversation复用可选history交互。大消息本片只显示content/arguments/tool_calls截断提示及已保存详情身份，不实现尚未讨论的完整正文弹层/下载器。

最小后端补齐：WebService.session/subagent将档案与live再裁成最终window时，web.chat_history根据实际交付的最早档案sequence重算has_more/next_cursor；若全被live挤出，则从原upper+1继续读取，不能跳过未交付档案。合并保留可信sequence/revision/detail与父ID，档案GET合同和大小预算不放宽。新增回归先证明游标跳页，再修同形根/子调用方。

UI沿用当前应用壳与App.module.css，仅增加轻量状态文本和按钮；Conversation采用布局阶段恢复首个可见消息ID/屏幕偏移，原生滚动锚定关闭以免双重补偿，找不到原DOM锚点时以保留消息/高度差回退。浏览旧消息时新内容不抢位置；接近底部仍跟随并保留回到最新。测试覆盖单在途、失败/重试、空页/起点、切范围/删除竞态、重连缺页/修订/旧预览、跨页工具关系和根子接线，再以真实临时Web/SQLite及桌面/375px浏览器验证滚动与网络请求，无供应商请求。

浏览器补充边界：若当前正在阅读的独立工具结果在更早页到达后归入其父调用卡片，保留同一结果ID锚点并仅在这次新卡片挂载时展开结果，避免折叠打断阅读；用户后续手动折叠继续有效，其他已完成卡片保持默认折叠。

## 18. 101-03实际接线与验证

新增生产文件仅`frontend/src/useMessageHistory.ts`（根/子两个消费者）。`open/reset/cancel`管理范围及代次，`merge`接实时行，`loadOlder`读取一页，`synchronize`串行补齐窗口与已加载末端之间的缺页/修订。根`useConversation`保留控制状态与run/SSE所有权，子`useSubagents`保留选中与详情所有权；原分散的messages状态迁到hook，没有增加第二份消息仓库。`WebApi.messages`扩展可选AbortSignal；`events.mergeMessages`按ID、revision、完整性及delta进度合并，并保留归档身份。`App/SubagentSidebar`只传历史控制，`Conversation`处理上滑/布局锚点/重试/起点，`ToolCall`补预览提示及跨页当前阅读结果首次展开。未改Composer、配置、DB schema或执行上下文。

正常例：首屏仅近期页→上滑记录`(message_id,offset)`→hook单请求→签名cursor GET→SQL有界页→ID/revision前插→布局阶段恢复同一正文位置；到起点不再请求。子范围使用同一hook和Conversation，但每个选中范围独立安装，不混入根消息。失败例：旧页503只记录安全错误并保留列表、位置和草稿，用户重试；切换/删除/卸载时取消并递增代次，迟到响应不能回写。根/子详情刷新合并已加载页，先到的子实时消息不会被晚到首屏替换。旧snapshot不能把终稿退成draft；同步排队用while等待当前请求，避免多个等待者同时启动。

确认并修复的后端缺口：档案页再与live裁窗后，旧cursor会跳过被live挤出的档案行；`web.chat_history.history_window`根据最终交付的真实sequence重算cursor，`WebService.session/subagent`共用。根真实流式暂停、子真实Read批次暂停两个回归证明补页可取回被挤出的行，没有扩大响应预算或把显示数据交回执行。

测试变化：新增`message-history.test.tsx`11项、`history-pagination.test.tsx`4项、`history-scroll.test.tsx`5项及`test_history_window_cursor.py`2项。缺少hook、根子接线、阅读锚点、cursor跳页、旧draft覆盖/不实保存提示、同步排队和工具跨页折叠均先失败再修复；子cursor同形覆盖为后补绿灯，不伪称全部TDD。原session-controls两次重读都分别延迟，保留“先前GET不能覆盖新工具/模式选择”断言并增加第二次GET pending时继续修改的检查；没有放宽断言、改conftest或新增依赖。

浏览器证据为实际构建、临时真实Web/SQLite、离线模型与真实Read，未请求供应商。1440×1000根首屏10条，单页前插到20条时同一消息偏移64→63.8125px；503保留20条与草稿，显式重试到30条；新消息完成增加2条时旧阅读scrollTop2303及锚点偏移均不变。逐页到历史起点，原记录保留。子首屏10条SQL记录（配对后6个外层消息节点），父调用跨页到达后当前Tool正文仍展开且同ID偏移99.3984→99.4844px。375×812根前插10→20条，偏移64→63.96875px；body宽375，主/子消息面板scrollWidth等于clientWidth；回到最新后scrollTop5971+client506=scrollHeight6477。控制台warn/error为空。工具组折叠会改变外层DOM节点数，不能把该数当档案条数。查询记录每次limit≤10、records≤10；首屏没有全历史下载。浏览器临时测试故障注入只作用于本次临时服务。

截图：`/tmp/naiyue-10103-root-desktop-final.png`、`/tmp/naiyue-10103-child-desktop.png`、`/tmp/naiyue-10103-root-mobile.png`、`/tmp/naiyue-10103-child-mobile.png`。初次Playwright扩展连接超时后使用IAB验收，没有改用户浏览器配置。临时进程/标签及viewport覆盖在收尾清理；证据文件保留。普通分页验证不等于101-04的压缩/崩溃/审批浏览器组合完成。

前端最终命令`cd frontend && npm test -- --run`：26文件183 passed（3.40s）；`npm run build`：TypeScript无错误，Vite210模块成功。日志`/tmp/naiyue-101-03-frontend-final.log`、`/tmp/naiyue-101-03-build-final.log`。Python compileall、pip check、377份AST/443份源文件≤500行及diff检查通过。架构/安全专项只读审查无确证遗留发现；跨页工具锚点补丁再次架构复核无发现。后端最终命令`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short`：2261 passed in95.12s（`/tmp/naiyue-101-03-backend-final.log`）。未改真实配置、用户库或供应商调用，未提交/push；无新增Windows/Linux原机或用户学习掌握证据。浏览器数值摘要另存`/tmp/naiyue-10103-browser-evidence.json`。

## 19. 101-04实施前准备范围（历史，现已验收）

本片是101整体组合验收及旧链核对，优先扩展已有`tests/test_web_history_paging.py`、`test_manual_compaction.py`、`test_web_recovery.py`、`test_session_recovery_crash.py`及前端history分页/滚动测试，不新增生产类或另一套保存/恢复机制。必要独立TCP组合探针沿`scripts/probe_web.py`现有临时SQLite/离线模型模式编写，不能替换正式模型、配置或用户会话库。只有组合测试证实缺陷才补最小生产修改，并先同步本Spec。

场景矩阵：长历史超过旧1MiB阈值仍可发送/合法compact/审批；自动与手动压缩前后的原聊天可分页、根子身份不乱，模型输入仍只用有效checkpoint；关闭服务重开后懒加载原文；前置审批重启后只恢复卡片并等待新决定；工具执行后、结果未完整保存的进程崩溃沿092恢复，真实已保存结果与未确认说明分别展示，不重复副作用；SSE缺口/切会话/旧页失败与新消息并存不丢消息或草稿。用模型/工具调用计数和临时文件副作用计数证明只读翻页、刷新及补录不执行工具，不只断言HTTP200。

正常调用链：用户滚动→useMessageHistory/api.messages→WebService.messages→aget_chat_page/read_chat_page→Conversation；压缩单独走原POST compact→原Runtime/必要保存→独立聊天归档，显示只合并不删旧聊天。失败调用链：进程退出→新持久App→选择POST prepare→原恢复分类/显式恢复POST→档案补录→近期页及历史分页；旧工具不因显示查询重新开始。审批仍统一主会话，子侧栏只显示消息/进展。

验收从数据库/应用原文、HTTP分页、实际浏览器三层分别确认，并跑全量后端/前端及构建、当前旧全量view/重复兼容消费者检查。浏览器桌面/375px至少覆盖压缩前原文、重启历史、审批卡片与不重放。大消息仍明确预览，原JSON分块详情API继续独立验证；完整正文弹层/下载交互尚未批准，本片不默认加入。全Spec102、103后续UI、M6缓存、usage/诊断、checkpoint修剪和Windows原机验收不混入101-04。


## 20. 101-04实施合同（2026-10-18用户授权）

按第19节及本轮详细方案实施组合验收，不新增生产类、schema、公开API或真实配置；只在测试证实缺口时同步Spec并做最小修复。首先分别确认模型checkpoint、永久聊天档案和前端已加载列表；不能用仍在浏览器/服务内存的消息证明持久恢复。离线模型只替代供应商，正式应用/图/SQLite/工具/HTTP保持真实。

验收矩阵：①18轮各用户/AI约30000 ASCII字符产生超过旧1MiB正文，分页核对ID/顺序/预览身份、有界响应、普通GET零写入，继续运行可用；②手动压缩和小预算自动压缩分别触发真实入口，模型上下文变为摘要/保留轮，原档案分页不删、旧游标保持固定上界，重启后继续可读，后续普通模型输入不重新装入已压缩原文；③根前置审批在重启后先prepare、再原restore_approval，恢复卡片/翻页不调用模型工具，用户批准/拒绝才按决定处理且回执不重复；④真实文件提交后进程os._exit(91)，原092恢复保留真实结果、只补缺失的result_unconfirmed，HTTP/分页不重复副作用；⑤子内部审批/提交后崩溃仍结束旧根委派、保留子档案，不复活进程内Future或旧审批；⑥前端压缩/恢复/实时刷新与已加载旧页共存，原草稿/工具身份/阅读机制保持。

测试文件按现有模式扩展test_web_history_paging、test_manual_compaction、test_web_recovery、test_session_recovery_crash、test_subagent_restart、前端history-pagination；实际滚动沿已通过history-scroll复用。三处以上HTTP分页检查共用test_web_history_paging.read_pages，明确检查响应、游标推进、序列/唯一性，不修改conftest。新增scripts/probe_web_history.py沿原probe_web的_launch/_stop/_until启动临时真实TCP服务，_catalog离线模型、_worker实际Web、_parent请求/压缩/关停重开检查、main有界退出；接tests/test_web_tcp_probe。正常TCP组合验证、真实硬退出测试和实际构建浏览器分别报告，不能互相冒充。

调用链保持：/compact→WebService/RunDelivery→acompact_context→原Runtime/checkpoint→SessionPersistence.afinish→collect_chat_updates→finish_run；上滑→useMessageHistory/api.messages→WebService.messages→aget_chat_page/read_chat_page→Conversation；重启→POST prepare冻结/补录已保存事实→前端按恢复分类调用原recover→三次状态更新/必要保存→档案/分页。prepare/recover允许写状态，但不得执行旧工具；普通GET不写库。模型调用、工具计数、临时副作用账本在每个阶段分别核对，不能仅断言HTTP200。崩溃前从未保存的内容不承诺恢复。

实施顺序：先追加组合测试并确认当前行为（补证初次绿灯如实记录）；缺陷先红后最小修复；再TCP/真实进程及桌面/375px浏览器；最后全量后端/前端/构建与旧全量入口消费者检查。若发现不一致，不放宽断言或为测试保留兼容执行链。完整正文展开/下载、嵌套任务跨重启续跑、全Spec102/103、缓存/usage/诊断/checkpoint修剪和Windows原机仍独立。本轮无供应商调用、真实用户库/配置修改或Git提交push。

101-04组合测试复现：selectSession首次open后紧接自动recover，applySession闭包持有此前render生成的history.sessionId标量，仍为undefined，将恢复窗口误作新范围open，丢失原首屏中不在恢复窗口的行。最小修复将hook已公开的sessionId改为读取scope.current的getter，接口名称/类型保持，消息仍通过synchronize合并；不另增状态/缓存或修改恢复协议。先失败回归保留在history-pagination。

浏览器进一步复现大正文但无工具调用的AI预览仍显示“工具调用信息未完整”：record_view在未读取完整body_json时无条件标arguments_truncated。新增原大正文HTTP回归先证明错误标记，再仅按实际calls非空标记参数截断；真实工具参数/调用列表截断合同及大小预算不变。

同形检查与独立复核确认bounded_message也会对live终稿中的空tool_calls误报。补真实Web近期live裁窗断言先红，再按实际非空调用判断；归档和live两条入口同时闭合，不改已有非空工具参数截断语义。


## 21. 101-04实际结果与Spec101收尾（2026-10-18）

四片主线已完成：独立聊天归档、显式准备/轻量准入/有界分页、根子上滑/阅读锚点/补偿合并、长历史/压缩/重启/崩溃/审批组合验收。完整正文弹层/下载、原机平台、供应商扩面及长期保留策略仍按前述边界独立，不将本机离线证据提升为生产/实网保证。

本片新增生产模块/公开API/schema/依赖均为零。新增`scripts/probe_web_history.py`提供可重复的临时真实TCP/两进程组合；HTTP分页检查器放既有test_web_history_paging供三个用例组使用，检查响应/游标推进/ID唯一性/顺序，不改conftest。扩展五份后端测试、原TCP测试和前端history-pagination，共新增后端5项/前端3项，增强既有17项真实退出/子恢复及大正文用例。原行为组合首次绿灯如实记补证；生产缺口均保留先红后绿回归。

| 场景 | 当前证据与断言 |
|---|---|
| 大历史/只读GET/继续运行 | 18轮用户与AI各约30000字符超过旧1MiB，按3条页遍历36条原ID且无重复/缺失，GET前后SQLite total_changes不变，继续第19次正常调用可用；超大正文JSON按详情片段重组等值 |
| 手动与自动压缩 | 参数化两场景通过正式HTTP/图/必要保存，8条原聊天跨2条页保留；旧cursor固定upper可读，关闭重开保持原文；后续model.seen含摘要而不含USER_0/ANSWER_0，摘要与普通调用分别计数 |
| 根审批重启 | approve/reject两场景，8条原聊天跨页；prepare/restore/GET不调用模型工具、不写GET，批准实际Read一次、拒绝零次，重复回执不重放；结果父ID/原call ID准确 |
| 真实硬退出 | 14个恢复边界＋文件提交后退出1项，原三次更新及真实结果保持，补缺结果只一次并可分页；提交账本write一次，恢复/翻页不调用旧工具 |
| 子审批/提交后退出 | 2项保留根/子档案、原子thread来源及真实子调用，结束旧根委派并补result_unconfirmed；不复活子Future/审批，不调用旧根/子模型或工具 |
| 前端组合 | 新增手动/自动压缩完成合并两项及冷会话自动恢复一项；旧页/草稿/父ID保持，没有运行或审批POST重试 |

两处缺口与最小修复：①useMessageHistory.sessionId原为render时标量，首次open后紧接recover的旧闭包误判为新范围并再次open丢行，现用getter读取当前scope，保留同名/同类型接口；②无调用长AI的预览误报参数截断，record_view归档与bounded_message live两处均按非空调用标记，真实调用截断仍保留。第一处前端回归先失败再转绿；第二处分别用归档超大JSON和真实Web近期live裁窗回归先失败，随后同形检查与独立复核闭合。未重写执行/审批/恢复协议，没有用调大预算或放宽断言绕过问题。

最终当前工作树验证：

```text
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
2266 passed in 100.33s (0:01:40)
cd frontend && npm test -- --run
26 files, 186 passed (3.34s)
cd frontend && npm run build
TypeScript success; Vite 210 modules transformed
.venv/bin/python -m scripts.probe_web_history
web_history_probe=PASS mode=offline tcp=True large_history=True pages=complete compact=preserved two_processes=True model_context=summary read_replay=False model_calls=20
```

日志：`/tmp/naiyue-101-04-backend-verified.log`、`/tmp/naiyue-101-04-frontend-final.log`、`/tmp/naiyue-101-04-build-final.log`、`/tmp/naiyue-101-04-tcp-final.log`。历史/压缩15项4.16s、根恢复6项1.45s、真实退出17项13.28s；修复后前端27专项、归档/调用/窗口13及live同形12项均绿。硬退出专项第一次因新增应用查询测试漏传body_bytes/preview_chars而17失败，补齐测试调用合同后全绿，属于验收代码错误而非生产缺陷；不把它计为产品红灯。

实际浏览器使用生产构建、临时Web/真实SQLite/真实Read及离线模型，1440×1000与375×812均验。手动压缩已加载12条保持12条/草稿不变；自动压缩12→14条且保留全部原ID；独立服务重开首屏6条，翻到最早BIG_00及压缩前委派，39外层消息节点与工具卡配对，后续新请求到41且不抢顶部。超大正文只显示明确预览，归档与live不再误报无工具参数。新进程查看已压缩根与子历史没有模型调用，子档案12条仍分页可读；模型下一请求has_summary=true/has_old_big=false。

根审批新进程恢复卡片及查看期间总模型计数仍36/新Read为0，手动批准后Read账本仅1次；崩溃文件恢复场景新模型0/新Read0/write账本仍1；子审批硬退出恢复新模型0/新Read0/无审批卡片，根委派未确认说明与2条子历史可读。根、子窗口宽度均无横向溢出，浏览器warn/error为空。初期离线夹具缺finish_reason及本机无PowerShell产生诊断，后续普通响应已补fixture元数据；不声称本机有Windows解释器或无服务端诊断。上述模型调用数为临时夹具计数，不是供应商请求。

截图：`/tmp/naiyue-10104-reopened-history-desktop.png`、`/tmp/naiyue-10104-reopened-history-mobile.png`、`/tmp/naiyue-10104-root-approval-desktop.png`、`/tmp/naiyue-10104-root-approval-mobile.png`、`/tmp/naiyue-10104-crash-recovered-desktop.png`、`/tmp/naiyue-10104-crash-recovered-mobile.png`、`/tmp/naiyue-10104-child-crash-desktop.png`、`/tmp/naiyue-10104-child-crash-mobile.png`、`/tmp/naiyue-10104-reopened-child-desktop.png`、`/tmp/naiyue-10104-reopened-child-mobile.png`。数值摘要`/tmp/naiyue-10104-browser-evidence.json`，测试脚本与临时fixture日志留/tmp；临时进程、标签和viewport覆盖收尾清理，证据保留。

旧链核对：生产/脚本/测试无aget_session_view消费者；Web没有aget_messages/aget_state/aget_session_history全量执行历史查询，只有GET路由消费session有界显示，start/compact/approve继续独立轻量准入。RunDelivery以previous.seed_data取必要投影种子，执行最终result进入原观察投影，不从显示页恢复模型上下文。CLI/恢复用途的aget_messages/aget_session_history与官方checkpoint历史采集保留，各有真实消费者；没有为本片测试恢复同步应用链或留下新的兼容包装。现有session dict/私有穿透和IOCall由Spec102独立处理，不在验收中顺便重构。

compileall、pip check、378份Python AST/444份源文件≤500行、diff及593个当前文档本地链接检查通过。check Deep架构/安全独立复核完成，前端getter无遗漏消费者；架构复核发现的live同形标记已先红后绿闭合，无确证遗留发现。无真实配置/用户库/依赖修改，保留既有100/101/103工作树，未提交/push；无新增Windows/Linux原机、供应商实网或用户学习掌握证据。

下一步建议先讲解Spec102剩余项的分片，第一片围绕SessionState及状态拥有者/跨对象行为入口；第6项轻量准入和第9项DeliveredRun拆文件已随101-02完成，不重复实施。第7跨会话并发仍待决定。Spec103剩余UI、完整正文展示、M6缓存/usage/诊断、checkpoint修剪、PyPI及平台原机验收分别列于future-work，不自动开工。
