# Spec126-03：单开关查找子实例与多次委派展示

状态：2026-10-22已完成本机验收。用户已确认[主Spec](126-subagent-continuation.md)第三片及“一个子智能体开关，配套两个模型工具”。不实施04专用交付worker、不新增直聊或侧栏结构。

后续状态：[04](126-04-subagent-acceptance.md)已完成共享安装验收与耗时测量；本篇末尾的下一片方案保留03收束时点。

正常链：用户选subagent→会话仅保存该选择→ToolSetMiddleware.prepare_tool_view派生已注册的subagent_list→同一请求视图服务模型定义、预算和执行门禁→只读工具校验permit/root→现有RunServices.alist_subagents→当前AgentSession核对session/run/workspace→读取可信登记及legacy引用→有界元数据分页。调用本身不执行子模型、不恢复Future；真正续跑仍沿01/02准入。禁用subagent后新请求不暴露两工具，旧请求继续沿原批次快照语义，不撤销已执行副作用。

失败链：未知/其他会话before、非法limit、非根/过期运行或许可不符→安全错误或机制异常，不能读外部会话；固定元数据超过预算显式报错，不能返回空页无限循环。SQL/文件失败保留原必要/观察异常合同。角色改变/不可用、模型不可用、busy/未确认/legacy/retired均产生候选不可续跑原因，不为列表扫描子正文。候选提示不等于下一调用许可。

预算：tools.subagent新增max_list_items=20、max_list_bytes=8192、task_preview_chars=160，全部严格正整数，完整UTF-8 JSON含分页元数据计入字节；完整项分页，首项可缩短task预览。before为本会话实际存在的完整ID，稳定倒序；登记使用created_at+ID，迁移前legacy没有时间戳，按档案首次出现的倒序排在新登记之后，不伪造时间。

接线及文件：tools/subagent.py新增schema/工厂，builtin和导出注册；settings及示例接配置。用户选择规范化为subagent，不给list独立开关；SessionService/AgentSession入口、持久恢复校验与Web控制选项一致。新的application/subagent_queries.py负责查询候选/分页，复用现有查询和登记；它与历史引用解析、执行准入职责不同，不把这些塞进已接多职责的session_subagents。既有RunObservation.bind/RunServices/context投影能力，子入口不投影；根普通与审批运行均传入同一当前会话能力。

展示：Python observe_agent、TS applyAgentEvent在同path新run_started(mode=message)重新设置当前草稿、工具态和父批；approval_resume保留当前委派。不同旧agent_run_id的迟到事件不覆盖新执行。CLI按source区分新委派与审批恢复。Web列表/详情增加resume_available/resume_reason，原侧栏显示原因；同实例详情保留持久全部委派历史，当前task/父批不被旧列表覆盖。详情/列表读取沿Abort/失效令牌，旧run晚到不得覆盖当前来源。停止与审批沿当前run绑定，保留原业务校验。

停止接线补充：同一父run可以有同path多次委派，侧栏停止携带现有source.agent_run_id，审批停止携带approval_batch_id；ControlInput及request_stop追加可选字段，业务request_scope_stop在同一锁内核对当前实例/当前审批后才改控制器。新Web始终提供所观察到的绑定；旧调用省略时保持原“停止当前目标”语义，CLI显式路径停止沿此行为，不制造breaking接口。原审批提交仍按完整batch身份及决策集验证。上下文next_request预览和一致性复查也使用同一个派生函数，避免模型定义/预算/预览漏接。

500行约束收尾：useConversation原已500行，新增停止来源绑定后502行；搜索确认不存在独立子任务控制模块。新增frontend/src/subagentControls.ts，只搬出本次子任务停止目标检查与control发送，实际消费者为useConversation.stopAgent；通知与会话刷新仍由原hook持有。不为压行改写其他会话逻辑，不新增控制框架或状态。

先写失败测试，再最小接入。重点覆盖单开关真实模型工具定义/执行、旧会话重开、禁用/子角色/过期/跨会话、配置和完整响应预算/游标、同父多次委派及复用call ID、新委派/审批恢复/旧终局、旧详情晚到、原UI当前停止/审批。结束跑全后端、全前端、构建和标准包验证，补真实浏览器行为观察；真实供应商和其他原机证据分别记录。所有操作限临时库/测试配置，不改真实环境/用户库/依赖，不提交/push/发布。

## 实际主链与迁移检查

- 用户选择入口、默认启动和持久重开保存canonical选择；根请求、手动压缩及下一请求上下文预览共用subagent_request_tools。实际请求和执行tool_names都包含两个工具，用户选择仍只有subagent；子角色不获得list。
- list从当前工具批次核对完整permission_binding及规范化limit/before，再投影当前根run能力。会话owner在读取前后重验归属；候选计算只读登记/角色/模型元数据，不扫描子正文。调用不启动子driver，不调用子模型。正常和异常返回均沿原工具回填/机制错误合同。
- Python/TS当前投影在新message委派时更新父batch并清草稿/工具态；approval_resume保留原委派。旧agent_run_id终局被拒绝。详情读取在同path、同running状态但执行ID变化时也失效；持久多次报告仍由已有子档案/分页读取。
- App两个包装、Conversation/ToolCall/侧栏、useConversation、api.control、ControlInput、RunDelivery、request_stop到request_scope_stop逐层保留观察到的子执行/审批批次；业务同锁核对后才请求停止。旧执行绑定与旧审批批次不能停止当前同path委派。
- 冷审批恢复最初漏用canonical用户选择检查已保存请求工具，首轮全量回归实际失败；改为同一当前派生集合，仍逐call重算完整subject、模式与Allow/Ask/Deny，MCP和临时session_allow旧恢复限制均保留。
- 删除原“一次parent_batch固定”“当前详情只按状态失效”和模型定义/预览硬用canonical集合的分支；直接迁移原消费者，没有为旧测试保留平行路径。原16项builtin目录与旧15项精确数组同步，用户可选仍15项；release_storage_smoke主动降级移除registry后完整比较legacy不可继续元数据，历史/usage/执行次数与文件副作用检查均保留。
- run_services/bind/context只扩展已有机制，无新全局状态或查询缓存；子实例登记、必要保存、权限与审批恢复仍沿01/02主链。

## 验证命令与实际结果

证据目录：build/spec126-03-r8u0fov3。新增19个后端、3个前端用例；不把预览测试误用字段产生的KeyError当产品红灯，测试后补的该项也不称为先红后绿。初始工厂/配置缺失、父batch重置和过期停止均观察到预期红灯，之后转绿；发现接线缺口时原失败与修后结果均保留。

- `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests --tb=short`：**4205 passed in 290.20s**，backend-final.log。首轮13 failed/4192 passed的目录/冷审批/旧格式消费者结果在backend-before-migrations.log；迁移定向125 passed in8.58s；修后全量重新执行，不以定向代替全量。
- `npm --prefix frontend test -- --run`：**50文件415 passed**，frontend-final.log。最后职责拆分后重跑该全量。
- `npm --prefix frontend run build`：TypeScript/Vite成功，frontend-build-delivered.log。仍有原有大于500kB chunk提示，无构建错误。
- Python AST/compileall、182份产品+41份脚本/123份前端源≤500行、pip check、git diff --check通过。useConversation原500行，本片一度502，子任务停止逻辑按职责移入11行subagentControls后主hook为500行；真实App停止/审批回归保持。
- `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec126-03-r8u0fov3/dist-finalized`：标准wheel成功；Twine、182份产品Python/前端产物/README源字节核对通过。wheel SHA-256为501a2ef9b220df7428298316f40b0b07b2de658bba84c6c2769eadd92d0470ff。
- `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec126-03-r8u0fov3/dist-finalized --output build/spec126-03-r8u0fov3/install-finalized.json`：源外干净安装通过，原生工具/CLI-Web/SQLite/配置/备份还原/历史审批与既有MCP、后台任务门禁均passed；恢复阶段模型/Write/Auto均0，真实供应商0。专用子实例续跑worker尚未接入，既有安装成功不替代04。
- 架构/安全独立只读复核先发现上下文预览1/实际2，以及App包装吞掉执行ID；修复并补现有链回归后关闭。冷审批的逐条授权与最后前端拆分复核无新增高置信发现。

真实浏览器：Playwright扩展连接不可用后改用in-app browser，不改配置。临时SQLite/正式Web/固定模型验证一个subagent开关且无list开关→同ID两次委派→侧栏两个报告与候选提示→list工具成功；浏览器warn/error为0。截图ui-continuation.png、结论ui-result.json。初始fixture未启用工具的调用保留为失败历史，实际后续在UI启用后成功，不把失败行伪造成成功。临时页签/服务已关闭并核对端口回收。0次真实供应商请求，未改真实配置/用户库/依赖/版本，不提交/push/发布，保留并行研究与学习成果。

## 唯一下一片126-04

接入专用release_subagent_smoke共享worker，正式图/真文件/SQLite验证新建→同ID第二次委派→正常关闭重开→第三次委派，模型轮数每次独立、旧历史与文件次数一致；源内和源外安装复用同一检查器，检查器故意篡改ID/次数必须拒绝。测量纯元数据列表与固定模型新建/继续路径耗时，保留原始样本，不把模型网络延迟算作本地开销；核对旧消费者并完成四目标wheel/本机干净安装及完整浏览器组合。此次只说明04，不实施。其他原机、真实供应商业务质量和用户掌握仍分别验证。
