# Spec143BC 云端任务本机关联移除

## 问题与范围

旧任务的 profile 已从本机配置移除时，刷新和删除预览都返回 cloud_profile_not_found，尚未访问远端。正常远端删除继续以严格核验的成功回执结算。增加独立的本机终极恢复入口，让用户显式放弃管理旧任务。

## 接口与数据

- 新增本机认证接口 POST /api/client/v1/cloud-tasks/{binding_id}/forget，查询参数 local_project_id。请求包含 command_id、local_session_id、expected_binding_revision、acknowledge_unmanaged=true。
- 校验项目、会话、binding 身份和 revision，无需 profile、SSH 或远端任务可用。一次本机事务标记 retained=false，保留 removal 审计信息，清空云端草稿、附件和待发送消息，终止旧操作后续重试。保留主聊天、已下载文件和已接收材料。
- removal 是用户放弃管理的证明。deletion 字段仍只接收真实远端删除回执。重复请求返回同一移除结果。
- 沿用 LC0 准入锁，只做本机状态和 DB 核对。存在本机正在派发、上传、接收、写文件或待发消息 producer 时拒绝并提示先处理本机操作，远端运行状态不阻止显式移除。旧元数据响应不能恢复已移除关联。
- 操作和附件重试的本机意图读取、flight 登记同样经过 LC0，防止移除检查后才启动旧 producer；取消 HTTP waiter 先排空独立 admit。网络派发、上传和已取消创建操作的远端核对在锁外执行。

## 界面

- 「移除本机关联」放在「删除任务」旁，独立于 metadataFresh 和远端连接状态。确认窗说明该操作放弃对原任务的管理，远端仍存在时可能继续运行，主聊天和下载文件保留。
- 成功后立即从当前列表移除并返回列表，迟到的旧列表响应不能重新展示。失败时保留确认窗显示原因；丢失回执可重复核对同一请求。
- 任务读取实际返回 cloud_profile_not_found 后显示具体原因，暂停该 profile 的远端详情读取，不再每轮重复失败请求；恢复同名配置后可重新刷新。不能仅因配置目录未列出 profile 而判定任务不可用，其他网络问题保留现有状态核对。
- 尚无 cloud 快照的旧关联也提供相同恢复入口。

## 验证

后端验证缺 profile、正常 profile、重复请求、错误 revision、跨会话、普通鉴权、局部 producer 活动、晚到缓存和旧操作不可重放；前端验证按钮相邻、状态不新鲜时可用、确认与取消、成功消失、迟到列表及失败重试。前端全量测试、生产构建和相关后端回归通过后交付，Windows 原机验收由用户执行。

### 本机验证记录（2026-11-07）

- 云端后端 `pytest -q tests/cloud`：仅叠加本片的隔离快照 1208 passed，100.01 秒，使用项目已有 native assets。
- 前端 `npm test -- --run`、`npm run build`：隔离快照 662 passed，TS/Vite 构建退出 0。正常结果取回/主 Agent 分析现有回归保持。
- Chromium 1200px/375px：使用真实组件与模拟缺 profile 响应，验证按钮相邻可用、确认窗完整可见、取消/Escape 返回焦点、成功消失及旧列表响应不能复活任务。未代替 Windows 和公司链路验收。
- 当前工作区 `npx vitest run src/components/CloudTaskForget.test.tsx src/components/CloudTasks.test.tsx`：34 passed；`npm run build`：退出 0，保留现有 chunk 大小警告。
- 工作区同时包含其他菜单改造，整合测试为 661 passed / 3 failed，失败均在 `send-scroll.test.tsx`；未修改该组测试或菜单代码，本片通过不等于全部工作区通过。尚未提交或发布。
- 2026-11-08 整合复核复现操作／附件重试绕过 LC0 的并发窗口；事件同步回归先得到 2 failed，再将意图读取与 flight 登记纳入原准入锁。补充 waiter 取消后共 4 项确定性回归；`pytest -q tests/cloud/test_local_task_forget.py tests/cloud/test_local_attachments.py tests/cloud/test_manual_task_commands.py tests/cloud/test_manual_task_preparation.py tests/cloud/test_manual_task_restart.py` 得到 47 passed（2.00 秒），专项 compileall 和 diff 检查退出 0；完整整合测试及构建另行验证。
