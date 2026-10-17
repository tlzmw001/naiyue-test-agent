# Spec085-05：会话活动工具、动态提示词与动态名称审批

日期：2026-10-16。状态：**已按授权实现并验证，实绩见第8节。** 接续[085-04](085-04-tool-registration.md)。本片使同一App中的工具能够在运行中登记、按会话启停，并让模型定义、提示词、预算与实际执行使用同一份工具集合；新Deny/Allow/Auto权限管线仍在后片。本片不实现子Agent或新的前端管理交互。

## 1. 当前事实与本片边界

04已实现ToolSpec、ToolRegistry、create_builtin_tools、App只读get_registered_tools；工具对象未复制。当前create_agent仍接启动时BaseTool列表，HITL的interrupt_on、ToolBatch的模式和compaction的tools都固定。因此仅调用registry.register并不足以实现运行中加工具，必须同时接下文消费者。

当前没有ToolSet/活动集合读取入口。project_prompt.py已有项目指令追加机制；session_controls.py已有SessionControlMixin；session_storage.py为schema v1；优先扩展这些真实职责，不再建SessionToolControls独立对象、AgentScope类或通用快照服务。

用户本轮确认取消命令工具专用启动开关：当前builtin代码包含六个文件工具（Find/Grep写在同一物理行）和条件加入的Bash，共七种已实现定义；PowerShell仍待双Shell片。05把create_builtin_tools改为不带host_bash参数并返回当前七项；删除CLI/Web的--host-bash、WebSettings.host_bash及其专用校验/展示分支。是否活动统一由会话工具选择控制；登记不代表启用，更不代表命令获批。该改动与本片活动集合一起接线，04现行可运行命令在实施前仍按真实代码说明。

本片保留现行ApprovalPolicy和工具正文授权复核。新权限片会用ToolSpec.permissions/PreparedToolCall整体替换；本片只把静态名称审批改为按当前批次原工具生成的局部官方HITL，不宣称已消除旧权限重复review或已经支持Auto。

## 2. 具体接口和数据

### 2.1 App与会话

- LocalApplication新增register_tool(spec)、get_active_tools(session_id)、set_active_tools(session_id,names)。AsyncApplication新增aregister_tool/aset_active_tools及同步只读get_active_tools，沿用所属事件循环/关闭检查。CLI异步facade委托同一实现；不新增CLI命令、HTTP管理接口或Python上传。
- register_tool校验ToolSpec及现行审批声明，再原子登记；失败保留旧表，不自动把新工具加入任何已存在会话。新会话使用App启动初始集合，避免新登记悄悄影响新会话的默认能力。
  两项定义校验集中在ToolRegistry.register发布前执行，App委托同一入口；启动和运行中登记共用，避免各应用入口重复维护审批声明校验。
- 注册集合与初始活动集合分开：build_*application增加initial_active_tools可选有序名称列表，省略时沿SDK原合同启用传入的初始定义；CLI/Web登记完整内置集合。默认选择建议维持现有体验：有工作区先启用六个文件工具、无工作区先为空，Bash由统一工具选择加入；不因删除旧开关自动启用Shell。用户选择已保存时恢复该选择。具体工具选择UI仍后排，本片后端SDK可通过同一set_active_tools选择任意子集，不新增另一种Bash开关或启用配置优先级。
- get/set复用SessionControlMixin和SessionService锁。set先校验完整有序名称列表，拒绝重复/未知；允许空列表。持久会话必要保存成功后才发布内存变更；失败抛出，旧内存值保留。允许活动run期间修改，下一次主模型请求才生效。
- resolve_active_tool_names(session_id, agent_path='root')是运行层注入读取函数。当前仅支持root，非根明确报错；后续086扩展同一参数，不增作用域对象。本片RunContext记录root事实，工具定义不保存会话可变状态。

App继续持有同一registry/compiled Agent。会话行增加active_tool_names有序列表，describe投影增加active_tools。登记、活动、批准是三个不同状态，模型猜到已登记但未活动名称也不能执行。

### 2.2 工具提示与本次请求集合

ToolSpec增加prompt_snippet=''、prompt_guidelines=()，在登记处验证类型；真实消费者为compose_tool_prompt。内置工具先用现有description作缺省摘要，不重写工具功能。共享指导按首次出现顺序去重，不往历史messages逐轮追加。

ToolSetMiddleware位于agent_runtime/tool_execution/toolset.py。prepare_tool_view取得tuple[ToolSpec,...]原引用，放入本次运行上下文供模型/预算/工具执行使用；图state只保存JSON名称，字段采用最终tool_execution结构中的tool_names，后续权限片继续向该结构增加批次/决定字段，不建另一个快照版本平台。

公开输入模型仍是04保存的input_schema；大模型看到的tools由同一集合的spec.tool提供。SDK重试、溢出恢复、当前工具批次固定这份集合；活动切换只在下一次主模型请求读到。

## 3. 正常调用链与恢复

```text
可信应用代码：register_tool(new_spec)
 → 校验定义/现行审批 → 原子登记（现有会话尚未启用）
set_active_tools(session_A, ['read', 'new_tool'])
 → 校验 → 必要保存 → 更新A的活动名称，B保持

A下一次主模型请求：
ToolSet.before_agent（首个集合先于主动压缩准备）
 → compaction使用同一tools/prompt计算预算
 → ToolSet.before_model（首轮消费已准备集合，后续循环重新读取活动名称）
 → 选模后ToolSet.wrap_model_call override(tools=原对象, system_message=组合prompt)
 → 原预算/观察/溢出恢复/provider
 → 模型返回tool_calls
 → BatchApproval用本轮保存名称构造局部HITL
 → 需要时整批暂停，任何工具都未执行
 → 用户批准/拒绝 → 原审批grant创建与恢复
 → ToolBatch按本批Spec模式创建计划
 → ToolSet.wrap_tool_call核对本轮名称，override(tool=原对象)
 → 原调度/观察/ToolError/BaseTool/实际IO
 → 同调用ID结果 → 下一次模型请求读取新活动集合
```

before_agent/before_model的真实先后必须用实际create_agent轨迹测试。当前主动压缩在before_agent，不能提前给它空工具，或仍让它捕获启动集合。ToolSet准备首份集合后，before_model不再次读取另一份值；后续模型循环才刷新。恢复入口从state保存的本批名称还原旧集合，不让当前活动名称覆盖尚未执行的调用；无替换/卸载意味着同名仍取得同一对象。

例子：模型已看到Read和Edit并提出两个调用，此时把Edit停用。这两个已提出的调用仍按原权限及串行规则完成；下一次模型请求不再看到Edit。审批暂停期间切换也遵循这一边界，不能把新集合套到旧批准上。

## 4. 固定审批节点如何处理动态名字

保留一个BatchApprovalMiddleware固定图节点，每次根据本批保存名称读取原ApprovalPolicy，创建局部ObservedHumanInTheLoopMiddleware(interrupt_on=本批映射)并调用它。局部映射不写回共享middleware，不改框架暂停/恢复payload；原观察和停止边界继续用ObservedHITL。

SessionService不再持启动审批字典；_pending_tool_approval和grant_factory读取本批名称对应的现行声明。新增名称也能暂停，不重建Agent。审批被拒的ToolMessage仍使用官方协议，ToolBatch只调度剩余调用；补“拒绝了唯一sequential工具，剩余parallel仍能完成”的回归，sync缺失plan检查不能再把已拒绝调用当待执行项。

这只是名称动态化。当前when、应用review及工具正文的复核仍保留；后片保存PreparedToolCall/最终决定后再消除重复完整权限判断。本片不会调用审批模型，也不改变本会话不再询问的未实施状态。

## 5. 修改/新增/删除位置

以下路径相对naiyue_test_agent。

| 文件 | 函数与真实职责 |
|---|---|
| tools/registry.py | ToolSpec新增两项提示字段并校验；register仍校验原对象，不复制 |
| tools/builtin.py、application/cli.py、web/app.py、web/settings.py、web/__main__.py | 内置七工具统一登记，移除host_bash工厂参数/CLI和Web专用开关/校验/提示；工作区只影响初始活动选择，工具仍检查执行上下文 |
| application/session_controls.py | 扩展现有SessionControlMixin：get/set/resolve_active_tool_names及可信登记协调；复用当前锁/必要保存模式 |
| application/session_service.py | 持registry/启动默认名称，创建会话和describe带活动列表；_pending_tool_approval/恢复从批次定义读现行policy；不超过500行 |
| application/__init__.py、async_application.py、persistent.py、async_persistent.py | 注入同一registry和会话读取；公开控制委托，不各写业务算法；异步IO用现有run_blocking/settle |
| application/cli_async.py | facade转发登记/查询/启停，无新交互语法 |
| agent_runtime/tool_execution/toolset.py（新） | ToolSetMiddleware.prepare_tool_view/compose_tool_prompt和四个model/tool wrapper；拒绝未知/未活动名时复用现有dispatch观察，返回同ID error |
| agent_runtime/__init__.py | create_agent(tools=[])，固定ToolSet wrapper保证空集合也有ToolNode；state增加可保存工具名称，注入resolve_active_tool_names；集中登记hook顺序 |
| agent_runtime/project_prompt.py | 唯一项目prompt追加职责迁入compose_tool_prompt；消费者迁移后删旧模块/工厂，不重复追加 |
| agent_runtime/context_compaction.py | 通过本次集合/prompt读取入口构造_arguments，不再捕获启动tools；摘要触发时机及算法保持 |
| tool_execution/approval.py、batch.py | 局部HITL映射；批次模式读本批Spec，不修改共享self.modes；原sync/async排序/控制算法保持 |
| agent_runtime/invocation_context.py、RunContext | 显式携带root及当次工具集合，恢复由保存名称还原，不保存工具对象到DB |
| application/session_storage.py、session_persistence.py | 活动名称保存/恢复及v1→v2事务迁移；现有checkpoint、安全状态和故障保护保持 |
| web/service.py、frontend/src/types.ts及现有SessionView fixtures | 增加active_tools投影/类型并迁移实际fixture；无新管理按钮/快捷键/路由 |

低层build_runtime_agent显式接resolve_active_tool_names；无应用的测试注入固定有序名称，不默认读取全注册表以掩盖缺少会话来源。总Spec的最终权限上下文读取等到权限片有真实消费者时加入。

## 6. 持久化和失败分支

本片schema v2只增加active_tool_names_json；permission_mode等到权限片有实际消费者再增，不提前保存无效设置。v1旧行用NULL区分“尚未初始化”，恢复时采用该App启动默认集合并保存；JSON []表示用户确实禁用全部工具。未知名称、非法JSON、checkpoint不一致明确恢复失败，不静默扩大集合或删除旧库。现有限制仍是仅恢复ready会话，不开放跨进程pending审批恢复。

活动名称的必要保存复用现有IO步骤/锁/异常合同；描述会话与Web投影同步。模型配置仍为启动快照；修改Python源码/设置后仍须重启，运行中登记只是调用现有进程的可信代码API。

失败例子：模型提出本轮未活动的registered_tool，ToolSet返回同ID错误、正文零执行，必要事件完整且不回显原参数。切换集合时保存失败，set抛出并保留旧内存集合。审批恢复发现保存名称不存在，明确失败，不能用当前默认集合替代。

## 7. 框架边界与验收

本机已读当前ToolNode源码：_inject_tool_args对动态request.tool有按需推断回退，验证异常分支却仍用_injected_args.get(name)。不修改框架缓存或升级依赖；必须新增动态工具参数错误回归，检查模型反馈/日志不含runtime、workspace_root或注入状态。继续使用当前安全ToolError反馈及作者准备的具体参数提示；若实际探针表明需额外处理，先更新本片合同，不能偷偷复制ToolNode或增加BaseTool子类。

先红灯后实现，至少覆盖：

1. 从空集合创建同一图，登记后只启用A；A可调用、B不变；登记不自动启用；重名失败无半发布。内置集合固定七项，移除host-bash后Bash仍由活动集合选择，启用不绕过审批；无工作区也可登记但未选工具不向模型暴露，解释器不在登记时启动。
2. 请求中切换：本次SDK重试/溢出重试/批次保持原集合，下一次请求schema/prompt/预算一致变化；同名原对象identity保持。
3. 新动态名称及含ToolRuntime工具可以执行、校验错误不泄露注入字段；未知/未活动调用零执行且事件/消息配对完整。
4. 新动态审批工具整批零提前执行，批准/拒绝/停止与恢复保持；暂停后改活动集合仍恢复旧批；拒绝唯一serial不阻塞其余调用。
5. 多会话并发集合不污染；sync/async/持久SDK接线，v1临时库迁移/空集合/恢复缺失名称/必要保存故障保持原合同。
6. 项目指令/Skills与工具prompt只组合一次；主动摘要/溢出恢复使用同一工具集合；真实hook轨迹证明顺序。
7. 删除静态模式捕获、静态HITL映射及旧project_prompt入口；保留现行权限声明直至下一片替换；全量后端/前端、构建、compileall、依赖及文件行数检查。

后续新权限管线再接ToolSpec.permissions、参数/目标准备、Deny/Allow、模式/Auto和保存许可，不把动态工具交付当作整个Spec085完成。

## 8. 实施、代码入口和验证（2026-10-16）

实现沿第5节接线，实际只新增一个生产模块`agent_runtime/tool_execution/toolset.py`，删除已无消费者的`agent_runtime/project_prompt.py`；其余扩展已有模块。WebService已有会话投影展开，直接消费新增active_tools，故无需重复写一份HTTP投影算法。CLI/Web移除专用Shell开关并登记七工具；前端只更新类型/fixture，没有管理交互。

具体对象及消费者：

- `ToolRegistry.register → _validate_spec`统一验证原BaseTool、公开模型、prompt元数据和现行审批声明，锁内原子发布；LocalApp.register_tool/AsyncApp.aregister_tool经SessionControlMixin委托。没有复制工具或为旧裸工具保留包装入口。
- `SessionControlMixin.set_active_tools`先用registry.select校验有序列表，在独立工具选择锁下必要保存，成功后才在会话锁内发布tuple名称。SessionService创建/describe和SessionPersistence恢复同步带活动列表；新会话继续使用固定启动初始集合。
- `ToolSet.prepare_tool_view`在context放tuple原Spec，返回`{'tool_execution': {'tool_names': ['read', ...]}}`供checkpoint保存；`batch_tool_specs`在审批恢复的新context中按已保存名称取回原对象。模型与工具wrapper分别override请求，不修改共享工具表。
- `ToolSet.model_inputs`返回`(list[BaseTool], str提示词)`，主动压缩的`_arguments`和模型wrapper共用；溢出恢复沿request.override保留原tools/system_message，重试不重新读取会话选择。真实hook轨迹已断言：首次selection → compaction → before_model → model；下一循环before_model → selection → model。
- `BatchApproval._local`按本批Spec构造ObservedHITL；官方暂停、批准/拒绝payload和观察保持。低层无checkpointer时，新动态审批定义同样明确报错、零工具执行；App四入口均提供saver。`ToolBatch._modes`改为每批读取，不再持self.modes；缺plan检查只看尚未有ToolMessage的调用。
- `SessionStorage`v1→v2迁移显式BEGIN，将ALTER及版本更新放在同一事务；只增加active_tool_names_json。NULL按App初始集合初始化，[]保留空集合。保存未知名/非法JSON明确失败；finish_run只更新原终局列，不覆盖活动列表。

可沿以下两个例子核验当前代码，不需要重学原运行/事件链：

```text
register_tool(new_spec) → ToolRegistry校验/发布
set_active_tools(A, ['new_tool']) → 必要保存 → A.active_tool_names
A.run → 既有SessionService/RunObservation/资源准备
 → ToolSet首次准备 → 既有主动压缩 → before_model消费首次集合
 → 既有选模 → ToolSet组合prompt和原tools → 既有预算/恢复/模型观察
 → AIMessage.tool_calls → BatchApproval局部官方HITL（整批暂停）
 → 原resume入口创建一次性grant → 重入同一审批节点 → ToolBatch准备计划
 → ToolSet从已保存集合找到原tool → 原调度/观察/ToolError/执行
 → 同ID ToolMessage → 下次before_model再读取A当前选择

失败：本轮集合没有模型提出的名称
 → ToolSet不进入工具正文，复用observe_tool_dispatch产生开始/未执行结束事件
 → 同ID status=error ToolMessage，不回显原参数 → 模型继续
保存失败：set_active_tools抛出SessionPersistenceError，内存旧选择保留
```

验证新增3个文件：`tests/test_dynamic_tools.py`、`test_dynamic_tool_context.py`、`test_dynamic_tool_persistence.py`，共32例。最初18项动态用例和7项持久用例先红灯，分别因新接口/字段缺失与schema仍为v1；其后无checkpointer动态审批用例也先确认未报错的红灯，再补相同保护。其余补充同源预算、真实hook顺序、溢出重试固定集合、并发会话隔离与坏审批声明的检查，不把后补测试称为先红灯。

旧调用方迁到显式活动名称读取；测试原审批/顺序/事件/异常身份断言保留。CLI/Web Bash案例改由同一后端选择API启用；旧开关仅留“已移除应拒绝”测试。SQLite直接造会话fixture补活动字段。跨进程恢复探针登记保存的定义，再显式停用以验证历史不重跑；原单次模型调用预算保留。少数低层工具循环探针的图步数上限6→12，以容纳新增固定before/after节点；模型调用次数限制未增加。未修改conftest或测试全局配置。

最终命令和实绩：

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -q` | **1218 passed in39.14s** |
| `npm --prefix frontend test -- --run` | **9文件，27 passed** |
| `npm --prefix frontend run build` | TypeScript检查与Vite构建退出0 |
| `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` | 退出0 |
| `.venv/bin/python -m pip check` | No broken requirements found. |
| `git diff --check`、Python解析/500行检查 | 退出0；无Python文件超过500行 |

动态ToolRuntime参数错误返回公开value字段错误，模型消息及日志正文不含runtime/workspace_root/_run_control；没有修改框架注入缓存或升级依赖。生产/脚本无旧host_bash、project_prompt、启动审批字典或self.modes消费者。验证使用假模型、临时SQLite/文件与现有本地子进程探针；没有真实模型、Windows、真实配置/会话库或完整新权限的证据。无配置/依赖改写，无提交/push。功能验证不代表用户已独立掌握。
