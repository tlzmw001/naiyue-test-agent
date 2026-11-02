# Spec143X：S06.4 云端任务候选侧栏

状态：2026-11-02已按143d/143s/143f完成本机实施与验证。前置B底座至S06.3b已本地提交57a40bf；本片不提交/推送/部署，不修改真实profiles/settings。

后续控件对齐与推理强度已按[143y](143y-s064-controls-and-thinking.md)补齐；下文第6节为首次侧栏验收时点，当前累计验证以143y为准。

## 1. 实施前数据流与本片范围

主界面为React+CSS Modules；ComposerControls的加号菜单、ActivitySidebar、Conversation、ToolCall/ReasoningContent已真实使用。B本机API已具备profile、来源捕获、准备、持久绑定、独立草稿、消息/控制/环境、删除/回执；Coordinator已有snapshot/SSE、档案分页/详情/线程和usage。缺少浏览器到本机的云端SSE桥、档案详情/线程代理和前端真实接线。

复用当前API/producer/会话边界和消息组件，补候选UI：加号打开→列表新增/选择→详情观察/专属输入→停止/释放/核对/删除。不做自然语言委派、云端长期记忆、完整结果固定/接收/分析草稿或代码应用，不展示这些未完成动作。先前ZCode/Codex/OpenHands的比较沿143d的证据边界，当前交互以用户指定的侧栏手动流程为准。

正常链：已注册/信任的本地项目会话→加号打开（无副作用）→选择已配置profile/模板与Git当前状态或明确相对路径→用户点击导入准备→cloud-sources、固定command_id cloud-tasks→原准备回执/列表→用户首条消息→云端run受理→独立SSE观察。用户可继续主聊天；关闭侧栏/本机不停止远端。

失败链：创建/消息的HTTP失败保留原操作ID和冻结请求，先读原回执；显式retry复用原命令，不因轮询/刷新造新任务。profile缺失、非本地项目/未选会话、Git来源失败、额度/连接/模型配置不足均显示真实原因。SSE断线显示观察缺口、有限退避后提供重新连接，正式档案保留；工具错误不自行把任务判失败，执行终局仍取owner元数据/回执。

## 2. 候选入口与视觉边界

默认构建入口保持正式A行为；`vite build --mode cloud-candidate`（开发同mode）在入口向ProjectWorkspace/App传显式候选能力，默认prop=false。不是产品设置里的A/B切换器；S09再正式切入口/清A。普通构建没有云端菜单/请求；候选可在当前认证的本机服务上运行同一API。

沿当前字体、surface/text/danger/focus/radius token和CSS Modules；辅助侧栏增加云端任务tab，保留子智能体/后台任务/上下文。桌面沿440px侧栏，窄屏覆盖面板；4tab可横向滚动，正文/操作/底部输入无水平溢出。面板/菜单Esc、tab键、列表/返回/关闭焦点可用，危险删除使用有标题的确认对话框。

视觉主张：当前灰阶工具界面、紧凑行列表、清楚的选中/错误状态。内容顺序：任务身份与真实状态→过程/错误→目标明确的输入与动作。交互：沿原hover/focus/侧栏开关，不引入新动效/字体/组件库或装饰卡片。

## 3. 前端数据与交互

- CloudApi绑定WebApi.scopeId，通过既有Bearer/X-Service-Instance请求`/api/client/v1`；cloudTypes独立描述现有DTO，WebApi仅提供受控client前缀与工厂，不外泄远端连接配置/token。非UUID普通对话显示需本地项目会话，不伪造project_id。
- 打开菜单只打开面板。列表读取持久binding，分页、刷新时更新真实任务，运行优先；切换会话按project/session key重置选中。关闭/后台隐藏停止观察读取，不发stop/delete。
- 新增表单明确profile/模板与来源，Git默认保留未提交状态；手工路径逐行、相对项目根。导入和新建分开保存source_handle/冻结原请求；准备不自动发送提示词。重复点击/重试复用原ID；刷新从绑定继续。
- 详情顶部返回、task/instance与准备/执行/保存状态。根/子线程使用owner档案目录切换，正文/思考与工具输出复用Conversation；长正文/思考从固定message/thread/revision分段读取。历史分页保留固定cursor，上屏实况只覆盖相同身份/连续水位，正式确认替换临时草稿，不按文字猜配对。
- SSE只更新观察，不结算命令。snapshot建立全量视图，update为完整替换live视图；epoch/seq缺口或resync重新读snapshot。错误/断线保留已有档案并标明，task_deleting终止观察。各异步请求有AbortController与project/session/binding/thread身份防迟到回包。
- 任务草稿与主聊天完全分离。浏览器按project/session/binding缓存未发送文本；server草稿使用独立revision CAS，显式保存/发送前保存。任务切换/关闭不串草稿；旧发送ACK仅清匹配版本。草稿冲突保留本页文本并提供重新读取，不自动覆盖他端更新。
- 运行中可编辑，待结束/保存确认或先停止后再发；模型选择来自当前任务公开model_ids。每次发送固定text/model/draft_revision与command_id；202本机受理不等于云端接管，接管以原远端receipt accepted/running/succeeded确认。查看过程不写主聊天/记忆。
- 停止本轮、释放环境、显式核对/恢复、删除使用既有API。unknown只查询原操作或用户显式retry/recover，不能新建run代替恢复。删除先真实preview，正常未静止提示先停止；unknown需确认丢弃未保存内容，固定原预览digest/gen；成功后移出retained列表，失败仍保留。
- 空闲Runner断线且没有可恢复checkpoint时，提供“核对并释放环境”：明确确认可能丢失未保存现场，固定当前binding revision/generation，使用既有acknowledge_unknown释放合同；不删除工作区/历史。确认过程中状态变化则拒绝旧CAS，不能以新现场重基后重试；释放确认后可启动原环境。
- 若原消息/控制仍为reserved/unknown/waiting_runner，先核对原操作，UI与既有后端均不越过pending屏障。本片不承诺将无法核对的执行自动放弃后重建；该组合仍需S09实机故障验收核定，不能为了入口可用放宽未知副作用保护。

## 4. 必要本机代理补齐

新增绑定下GET `/stream`、`/history/threads`、`/history/messages/{message_id}`，沿同一project/session/instance检查和只读连接借用。浏览器只传binding与有界查询，不传远端URL/task路径/凭证。

CloudClient增加异步观察流方法，通过已核验loopback连接与同一固定认证头直达owner stream；单条长连接，不为每个chunk建连接。重定向禁止、代理环境关闭；逐帧预算和UTF-8/JSON边界遵循原SSE合同，错误只暴露安全码。manager持有有限observer集合、固定binding借用和project生命周期；本机关闭先取消/排空观察再释放SSH/HTTP，不发远端停止。

新增max_observers（默认8）、stream_seconds（默认1800）、stream_send_timeout_seconds（默认15）在CloudTaskSettings公开模型中；不改真实配置。流关闭/取消排空上下游和连接引用；慢客户端超时是观察故障，不改变远端成功/主异常。shutdown不能等待自然30分钟到期。新增observer预留/关闭顺序同片更新lock-hierarchy。

## 5. 文件与验收

主要变更：frontend cloudTypes/CloudApi、观察/草稿hook、CloudTasks/创建/详情/命令组件、Composer/ActivitySidebar/App/候选main；local stream/detail代理、CloudClient与设置。超500行前按真实职责拆分，无新依赖。已有消息/工具组件持续复用；没有实际消费者的新包装/占位按钮不保留。

先失败测试：加号只打开、无自动创建；真实API路径/认证/固定ID；列表分页/空白/错误；来源创建重试；主/云输入隔离与旧回包；根子/工具失败/长内容分页；运行禁发/停止/释放/unknown/删除确认；SSE取消/缺口/断连与manager关闭排空。真实浏览器在桌面及375px验证布局、键盘和一次确定性任务过程/修复，使用临时本机服务/受控后端与模型，不访问公司/供应商或真实Docker。

完整后端/前端、默认及候选构建、标准wheel/Twine/源外安装按最终冻结代码验证，记录与57a40bf原6741/568测试身份比较。更新handoff/143f，唯一下一片S07.2完整固定结果与自主捕获。

## 6. 已实现与验证（2026-11-02）

正常主链：`ComposerControls.openCloud → App/ActivitySidebar → CloudTasks → CloudTaskCreate/CloudTaskDetail → WebApi.cloudTasks/CloudApi → service/cloud_tasks + cloud_observation → ManualCloudTasks/observe → 固定owner连接`。创建只准备，消息先保存独立草稿、冻结command_id/text/model/revision，再读原回执；已受理后不以观察连接结算执行。原Conversation读取工具结果/长正文，独立根子选择按thread_id合并档案目录与实况，目录/历史可分页。列表按持久绑定分页，当前页真实元数据刷新失败时标记过期并禁止控制；不根据缓存编造停止或失败。

失败主链：明确binding/draft等预准入拒绝保留草稿并解除发送锁；丢ACK保留原ID/冻结请求，用户核对原操作。晚到snapshot与任务/线程切换由AbortSignal及身份挡住；SSE缺口退避后重新建立snapshot，断线仍显示已保存档案。manager关闭先取消reader，显式排空上游async iterator后归还SSH/HTTP借用；发送主异常和清理次异常分别记录。已补额度/慢发送预算与LC4关闭顺序，未注册观察写记忆或远端stop副作用。

先红后绿证据在`build/spec143-s064-20261102/red-*.log`：缺少代理/入口、路由遮蔽、状态过期、分页/详情取消、明确拒绝发送、迟到profile、未归档子线程、确认释放、返回焦点、双异常与yield后清理等。测试环境未提供HTMLDialogElement原生方法，仅在该确认测试局部模拟open/close；实际浏览器另验原生dialog，不修改全局测试基础设施。

验证命令与实际结果：

- `PATH="$PWD/build/spec143-s05-20261102/test-assets/bin:$PATH" .venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/spec143-s064-20261102/backend-final-0.xml`：3507 passed；同命令index 1：3241 passed，合计6748、无失败/跳过。原6741保留；两项旧参数化UUID按源字节核对后仅归一收集名称，详见verify_evidence.py。
- `npm --prefix frontend test -- --run`：71文件、583 passed，原568保留；新增15项。不删旧测试/断言。
- `npm --prefix frontend run build` 及 `npm --prefix frontend run build -- --mode cloud-candidate`：均退出0；Vite仍有既有chunk体积建议，无构建错误。普通构建不展示云端菜单，不发云端请求。
- `node build/spec143-s064-20261102/browser_probe.mjs`：真实临时本机服务＋受控owner传输，1440px/375px闭环通过，两轮消息/工具错误展开/修复/草稿隔离/关闭重开/原生删除取消与焦点，JS errors=[]，375px无页面溢出。此证据不冒充真实Runner/Docker、供应商或Windows/公司业务验收。
- 四目标`python -m scripts.build_release --target <target> --frontend-assets build/spec143-s064-20261102/frontend-assets-final --output ...`：Mac ARM64/Linux x64/Linux ARM64/Windows x64均通过；Twine四包通过，499个生产Python源逐包字节一致。
- `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec143-s064-20261102/wheels/macos-arm64 --output build/spec143-s064-20261102/install-macos.json`：源外干净安装/产品smoke通过，provider_requests=0；pip check、compileall及diff check通过。

完整证据：[verification](../../build/spec143-s064-20261102/verification.json)、[review](../../build/spec143-s064-20261102/review.md)、[桌面错误过程](../../build/spec143-s064-20261102/desktop-task-error.png)、[375px](../../build/spec143-s064-20261102/mobile-task.png)。旧无消费者的临时探针/包装未接入产品；尚有正式消费者的A按S09退出，不提前删除。未改真实配置/依赖/用户数据，未部署或再次提交。本片无新增用户掌握记录。唯一下一片为S07.2，不自动跨片。
