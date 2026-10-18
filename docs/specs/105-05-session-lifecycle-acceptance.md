# 105-05：会话生命周期组合验收与地图收尾

日期：2026-10-18。状态：用户已批准并实施，验收通过。依赖105-01—04，完成后才进入104。

## 1. 范围与现有链

当前入口为AsyncApplication→Service._admit→AgentSession.admit_run安装ActiveRun→Actor._arun/aresume→原共享根/子图。根finish等待子driver并检查结算，关闭控制，必要保存，释放_run，最后通知。根待批是已结束run留下的State事实；子待批是仍存活父run拥有的future，两者不是同一寿命。

105-04已分离SessionState/ActiveRun和操作完成信号。本片不增加业务对象、DB结构、配置、依赖、公开签名或并发许可；验证其组合，而不是再造一套运行器。已有subagent_integration、subagent_sqlite_stop、manual_compaction、session_recovery_crash、restart_approval及active_run_lifecycle优先复用。

## 2. 文件及实现合同

| 文件 | 本片内容 |
|---|---|
| 新增tests/test_session_lifecycle_integration.py | 复用原持久化工厂、假异步模型、ToolSpec及子实例/审批观察helper；真实图与临时SQLite组合根待批→恢复→子待批→根保存，覆盖成功、保存失败、主执行/次保存双失败、保存期间取消和关闭；补根/子待批关闭重开及压缩后配置/档案恢复 |
| tests/test_application_cli.py | 原ClosingApp缺request_stop；用事件强制第二worker尚活跃时输入quit，先稳定复现，再补齐替身合同与停止次数断言。保留两条输入、仅一次steer及两份输出断言，记录并join测试线程，异常时释放事件。无生产兼容分支、紧sleep或conftest改动 |
| application/session_controls.py | 实搜发现_request_scope_stop仅被public request_stop转发一次，关闭已用stop_loaded_sessions且无其他消费者；将public request_stop直接路由Actor.request_scope_stop，删除失去作用的私有包装，公开签名与reason=user保持 |
| 105总Spec、085/086文件地图、README、handoff、project-plan、implementation-roadmap、future-work、104/106依赖说明 | 当前归属与验收同步；105-01—04保留历史时点，增加当前入口指引。104-01仅讲精确合同，不实施 |

现有Service控制/角色/审批mixin和Actor控制/执行/子实例/审批部分仍有真实类消费者，是同一对象的文件拆分，不能仅因名字是mixin就删除。扫描旧同步运行器、旧状态字段、旧装配/恢复IO函数及别名的实际消费者，发现冗余才删除。

## 3. 正常组合数据与阶段

示意：sid-a中根probe调用parent-call需要审批，批准后委派tester，子probe调用child-call再次需要审批。

```text
arun(sid-a) → root-run-1 → 根待批 → 保存interrupted → _run=None
aresume_tool_approval(sid-a,['approve']) → root-run-2
 → parent-call执行一次 → tester子图 → child-call待批
     父_run仍是run-2；只有一个driver和本批future；还没执行child-call
asubmit_subagent_approval(该批/该origin_run) → 同一driver恢复
 → child-call一次 → 子摘要/artifact返回根 → 根最终响应
 → 子结算并移除 → 控制关闭 → 根finish保存阻塞
     _run仍是run-2，busy=True，未发根run_finished
 → 保存完成 → _run=None → 根终局 → 操作完成
```

检查root-run-1与run-2的对象/ID不同，子审批不创建第三个根run、不再次Auto、不重复执行副作用。资源冻结位于本次记录，最终保存传Actor快照；根/子事件身份与原始工具结果/归档配对保持。

证据范围：本片新增审批组合使用Default，证明身份、恢复、保存和副作用次数；Auto不重审另由既有test_auto_permission_pipeline的test_two_asks_pause_entire_batch_and_resume_does_not_review_again、test_resume_uses_saved_ask_and_refreshes_only_subsequent_batch及全量回归覆盖，不将本片Default例称为新的Auto专项。

核对现行链后的精确补充：根resume尚未结束时State仍保留原pending，普通message重入按原校验优先级报SessionPendingApprovalError；再次resume报SessionBusyError，都不能安装新运行。子审批暂停会先发布该子执行片段的interrupted终局，随后同一driver恢复为新的子run；批准后为completed，停止后为stopped。根保存前的终局序列为根interrupted→子interrupted→子completed，最终再根completed/failed；不能把暂停片段当作没有终局事件。本片以这些已存在合同校准新增验收，不改变生产行为。

## 4. 失败、关闭和重开

- 第二次根元数据finish失败：原始写异常作为SessionPersistenceError的cause，blocked及running未确认状态保留，Actor占用在必要收尾后释放，根终局failed，子已completed；不可伪报ready。
- 根模型原异常与次保存失败同时发生：原异常身份优先，次保存可观察；敏感异常正文不进日志。错误不是普通成功ToolMessage。
- 保存期间取消：等待已经开始的保存与终局结算，再将原取消交回调用方，不提前释放busy或关资源。保存已成功的run保留既有completed语义。
- 保存期间关闭：关闭新入口，等待该操作完成后关catalog；工厂随后退出官方saver并关storage。根已到finalize不再执行额外模型/工具。
- 根前置审批关闭并重开：通过原restore_approval和本地再校验恢复State.pending；没有旧ActiveRun/队列/临时Allow，阅读历史和恢复卡片不调用模型/工具，批准后才执行一次。
- 子内部待批关闭：停止和唤醒该future、结算driver及父run，再关闭；重开保留历史与停止事实，不复活子future或自动重跑委派。
- 压缩后重开：实际模型上下文只保留摘要及约定最近轮，独立聊天档案仍保留原文；配置、thread、活动工具/模式与已存结果保持，模型调用计数读取时不增长。

同步使用asyncio.Event/threading.Event和明确阶段通知；超时仅是测试防挂保护，不是产品期限。观察回调只记录，断言在测试主体进行，避免观察隔离吞掉检查失败。

## 5. 验证与证据

CLI先固定重现旧替身缺接口的红灯，然后补齐真实CLI调用的替身合同。本片其他新增项是对已实现能力的组合验收：先运行当前实现以建立证据，不把本就通过的验收声称TDD红灯；若发现生产缺陷，单独先复现、同步Spec后最小修复。

定向新用例及原根/子/停止/恢复/压缩测试→完整`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`；frontend运行`npm test -- --run`及`npm run build`；compileall、pip check、AST/500行、Runtime反向应用依赖、旧入口消费者、文档目标/标题锚点及git diff --check。按本片前基线生成差异，保留已有脏树，不提交push。

这证明本机真实图、临时SQLite和离线模型下的合同，不声称Windows/Linux实机或真实供应商通过。104/106未实施项不得标完成；验收后再讲104-01新增配置/错误/准入及按会话持久化协调。


## 6. 实际实施与验收（2026-10-18）

新增test_session_lifecycle_integration.py共8项参数实例，均由真实应用/根子图/官方saver和临时SQLite执行；普通调用和摘要仍是离线异步假模型，不调用供应商。5项结果覆盖根审批恢复后子待批、子完成后根必要保存的成功/保存失败/原模型与次保存双失败/保存中取消/保存中关闭；另3项覆盖根待批冷恢复、子待批关闭再打开历史、压缩配置与独立档案重开。

CLI旧替身在强制第二worker活跃的quit路径稳定复现AttributeError后，补齐真实CLI会调用的request_stop；增加停止次数和线程join断言，原输入/Steer/两份输出断言保持。没有修改conftest/共享测试helper，也没有为替身添加生产兼容分支。

新增验收首轮有两条预期需按已有合同校准：根resume期间原pending保留的异常优先级，以及子暂停片段先interrupted再恢复completed/stopped的终局序列。已核对源码及实际事件并同步第3节，改为更精确的重入/身份/序列断言；没有改动生产审批或事件语义。新增组合不是未实现功能，不冒称TDD红灯；CLI缺接口回归有明确红绿证据。

唯一生产改动为session_controls.py删除_request_scope_stop包装，public request_stop直接路由所属Actor；原签名、sid/run/path校验和默认user原因保持。其余mixin均有真实类消费者，保留为同一对象的文件分区；无新增生产模块、依赖、配置或DB迁移。

实际验证：

| 命令/范围 | 结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | **2464 passed in 100.59s (0:01:40)**，日志`/tmp/naiyue-10505-backend.log` |
| 新组合+CLI+根/子/停止/恢复/压缩/资源/异步入口专项 | **284 passed in23.02s**；新组合独立8 passed；CLI强制分支红灯后1 passed |
| frontend目录`npm test -- --run` | **26文件、186 passed**，`/tmp/naiyue-10505-frontend.log` |
| frontend目录`npm run build` | **TypeScript零错误、Vite210模块成功**，`/tmp/naiyue-10505-build.log` |
| compileall、pip check、Python AST/500行、Runtime反向application依赖、旧内部入口消费者、diff | 396份Python检查通过；反向依赖和旧入口实际消费者均0 |
| 变更文档的本地目标/标题锚点 | **726目标、76锚点通过** |
| 本片差异的架构/安全复核 | 无确认遗留问题；独立复验分别24 passed in1.69s、9 passed in1.28s |

本片相对开始时基线修改1个生产文件、2个测试文件（含1个新增）、16份docs及README。当前调用链及文件地图已同步；105五片全部完成。历史Spec/研究图明确保留原时点，当前入口统一指向本片。未修改.env/真实配置、用户数据库、前端行为或供应商预算，未提交push，原有脏树保留；不新增用户掌握声明。

下一片[104-01](104-01-application-concurrency.md)为待审方案，尚未实施；默认10已确认，需先确认精确准入和按会话持久化合同。105完成不代表浏览器已开放并发，也不代表106的订阅/429/观察优化已实现。
