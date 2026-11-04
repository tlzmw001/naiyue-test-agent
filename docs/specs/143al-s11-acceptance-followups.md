# 143AL S11原机验收补修

范围已获用户授权：后台停止确认、恢复后的准备状态、任务标题、删除清场预算与续接、断线发送回执核对；补恢复文档，审查未实测合同。保留既有工作树、真实配置与数据，不提交/上传/部署。

## 顺序与合同

1. 后台停止：现有任务快照只有background_commands标志，没有可靠进程清单；复用居中dialog明确停止该任务所有后台命令/服务，取消不发送，确认固定原binding revision/run。状态改变需重新确认。不编造命令/端口清单；本轮停止保持现有行为。
2. 准备状态：沿本机已验证云端快照更新链追踪历史failed，成功环境恢复使本机关联准备状态收敛；失败/删除/未核对不能伪装ready。历史创建回执保持原事实。
3. 标题：首条有效运行消息的空白折叠短摘要随任务预留原子保存；已有标题不覆盖，不调用模型。未发送任务仍显示真实创建时间。
4. 删除：资源快速扫描和落盘删除预算分离，保留条目/深度限制、路径安全与fsync；超预算保留原操作并可reconcile，不删除墓碑/跳过物理核验。具体默认值与测试依据在实现处补充。
5. 断线发送：复现服务端受理而响应/观察断线，原command ID查询结算；不能自动换ID重发或误删后续草稿。读取故障保留可观察错误。

## 验证与边界

每片先失败回归→最小实现→相关回归，最后全后端/前端/构建及UI运行检查。未实测性能只审查计时范围、预算、取消、重试/恢复、隔离和对应测试；不将静态审查或模拟耗时当作Windows/公司环境p95、吞吐或业务通过。原始报告尚未提供，不编造其身份/采样。

## 实现补充

- 停止dialog固定binding revision与last_run；状态变化禁用确认。接口无后台明细，因此明确说明全部后台命令/跨轮服务影响，不新增不可靠进程查询。
- cloud_local._cache在经checked_task验证、非倒退且ready/backend_ready、runner ready或waiting_model的快照上，将历史failed/unknown关联恢复prepared；不改原创建操作。
- RunnerRuns.reserve将空标题设为首条预留消息折叠空白后的前80字符，与原任务/命令同事务；已有标题保留，列表与详情消费同字段。
- cleanup_timeout_seconds独立于scan_timeout_seconds，默认30秒，可配至3600秒；默认是保守操作预算，不是原机性能承诺。未放宽安全检查，不减少fsync；原ID续接回归涵盖中途清理超限。
- 操作列表仅对cloud_connection_failed降级为重新读取本机持久回执并记录安全日志；单次列表遇到一次断线后其余读本机，避免逐操作重复超时。身份/权限/损坏等错误继续传播，不伪造新受理或成功。前端原ID受理收敛后清旧重试意图，保留后续草稿。
- 没有新增锁、改变锁作用域或嵌套；标题沿CT1→registry短事务，关联恢复沿已有项目DB事务。

## 回归发现的观察竞争

首次全量真实SSE回归触发snapshot_busy：snapshot连续变化耗尽3次尝试，_resync把异常抛入已开始响应，导致SSE连接被拆。补确定性失败测试后，_resync仅对snapshot_busy在原租约deadline内等待状态wake或既有heartbeat再取快照；保留游标，成功才发布snapshot，取消与其他错误仍传播并释放lease。无新配置/锁；没有持锁跨等待。真实HTTP/WS/SSE及故障回归一起核对。

## 未实测项目的代码审查（2026-11-04）

下表是源码与确定性测试证据，不是原机性能通过。照片未提供原始JSON，本片不修改原机采样报告。

| 项目 | 审查证据 | 判定与剩余证据 |
| --- | --- | --- |
| 新任务ready p95≤8s | scripts/cloud_workflow/scenario.py在create返回后计started_at，再start/wait；143G要求create接受→ready、20样本 | 当前ready_seconds口径不满足该门禁，两个任务不能当20样本p95。保留原字段兼容本轮报告，不伪改历史值；后续实测需按门禁入口采样 |
| 纯热环境≤1s | cloud_runner/kernel.py复用sessions/agent，accept只登记本轮journal/result再调既有sessions.arun；输入安装属于bootstrap | 结构上避免每轮重新导入/装配，不证明≤1s。需原机独立入口→可执行采样，不能用ACK或round_total替代 |
| SSH吞吐及续传≥70% | input_upload.py固定传输ID、offset/hash；receive_transfer.py固定Range/If-Range、fsync后登记本机offset，末尾核对SHA | 协议有续传与字节校验，不能证明链路吞吐比。必须同链路raw SSH基线 |
| 200MiB传输下控制增量≤500ms | ProfileConnections.get按profile_id复用ConnectedProfile；ManualCloudTasks.connection被接收与控制共用；ssh_argv虽ControlMaster=no，仍是一个-L进程 | 不满足“已有独立传输TCP或限速”的结构保证。没有在本片扩成多隧道功能；此项保留架构差距与原机压测待办，不能review签成达标 |
| 50工具序列 | kernel.runner_tools→SessionService/create_agent的真实工具与循环已有集成覆盖；现workflow仅固定少量工具流程 | 没有50工具A/B性能证据，功能测试不能代替该对照样本 |
| 模型代理增量 | model_relay.ModelLease.prepare/chunks流式转发、有字节/截止/取消限制；test_model_streaming验证首块不等尾部及取消关闭 | 流式正确性有测试，首token额外时延没有实测；测试协议块不能称token |
| 二进制小差异本机交付 | result_snapshot/result_scan全内容SHA、Git二进制patch、O清单；delivery_apply字节预检与应用，test_delivery_apply含NUL二进制 | 内容正确性可回归；warm仍扫描/哈希O，不是按变化字节恒定成本。不证明大二进制差异性能 |
| 409文案/删除失败提示 | cloudPresentation映射task_revision_conflict为状态变化，CloudTasks测试覆盖rejected保留草稿与alert，删除返回false不关闭确认 | 本机回归覆盖；原机本轮未触发仍记未实测，不能用未出现当修复证据 |
| 独立新根restore | 更新手册补allowed_host_paths检查；后端白名单不会随Coordinator.data_dir自动变动 | 文档修正，不自动改变真实权限；原机管理员核对新根后再启用业务 |

当前不引入远程发布、自动改配置、多隧道或新的性能基准框架。以上未通过数字门禁不能被本轮review替代。

## 最终本机证据

6377后端全部通过（零失败/错误/跳过），599前端通过，TS/Vite与四目标标准wheel构建/Twine通过；514个Python源逐包一致，Mac新venv源码目录外安装烟测通过。新增14后端/4前端测试；1370文件冻结后未变。浏览器生产组件配模拟API确认取消零副作用、确认一次派发。详见`build/s11-followups-20261104/verification.json`与report.md。本片不上传/部署，不声明原机数值门禁通过。
