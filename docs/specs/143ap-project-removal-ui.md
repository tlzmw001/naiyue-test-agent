# 143AP 项目列表显式移除

状态：用户批准的范围已实现，本机全量验证完成（2026-11-04）。此前143AO及4份Spec142保持，已本地提交1473afd，未push或发布、不改真实配置。

## 当前链与范围

项目登记在 projects.sqlite；ProjectService.list → _view 查询目录availability，不会因missing自动丢登记。service/projects DELETE → ProjectAPI.remove → HostManager.remove → ProjectService.remove → operations.resume 删除注册行并返回data_retained=True；项目数据库、归档、源码保留。HostManager要求项目已关闭，ProjectHost._close拒绝运行/恢复/审批/后台工作；storage.deletion_guard保护活动会话及云端关联。前端ProjectWorkspace/ProjectNavigation只有选择，无移除入口。

新增侧栏项目行“移除项目”按钮与居中确认弹窗，展示名称/完整路径及保留数据说明。普通对话无移除入口，missing项目可移除；不自动隐藏缺失目录，不增加清库/删除源码/远程删除/历史恢复功能。

## 接线与状态

ProjectNavigation → ProjectRemoval → WebApi.removeProject(id, requestId) → 原DELETE接口 → ScopeServices.remove_project → 现有close/remove。复用G1串行化整个Web关闭与移除，以owner-loop集合标记当前移除的scope，get/_open拒绝该scope新请求；不同scope不受影响。关闭只处理空闲host，project_not_open可继续；其余失败可观察并保留登记。关闭成功后清理缓存delivery，再调用原remove，finally释放标记。CLI同时重新打开时原HostManager仍拒绝移除，不越过保护。无新线程锁、无DB事务跨await。

失败后UI保留项目和草稿。列表额外保留deleting记录，避免最终检查失败后项目消失且无恢复入口；pending/migrating创建记录继续沿原合同不展示。原请求ID保存在弹窗；核对读取projectOperations并严格匹配action=remove、project_id和result.data_retained。done才更新列表；未知结果原ID重试。已确认拒绝且未生成操作记录时下一次尝试使用新ID，避免HTTP失败回执缓存。存在未完成remove记录则通过显式reconcile原ID续接；ProjectService.reconcile的白名单扩展remove，仍使用同一resume/deletion_guard，不做全库恢复。

成功清除本项目visited/client/expanded并刷新目录；当前项目切回chats，其余项目选择/草稿不变。已移除ID记录用于过滤迟到的列表响应，新增同ID登记时清除此过滤。首次打开页面若选中ID已不存在，回到普通对话，不持续显示已移除项目。确认请求未完成时禁止重复提交/关闭；取消确认不发送命令。

## 数据与接口

DELETE请求/响应不改签名，增加空闲host自动关闭行为；domain remove仍保留必须关闭语义。WebApi增加removeProject，RegistrationOperation增加可选action/result投影供核对。UI文案区分project_in_use、project_cloud_tasks_retained、未知结果与普通IO失败。项目scope校验及认证继续使用原入口。

正常：已打开但空闲项目 → 确认 → 关闭 → 持久删除登记 → 成功回执 → 切换/更新列表，源码/历史仍在。
失败：活动run → close拒绝 → 0登记变化；保留云端关联 → remove拒绝 → 记录仍在；删除回执丢失 → 读取原操作done → 仅更新页面，不再发副作用。

## 文件与验证

涉及10余文件：service/projects、scopes、application/projects/service；frontend api/projectTypes/ProjectWorkspace/ProjectNavigation及CSS、新ProjectRemoval组件；对应后端/前端测试；锁表、Spec/交接、README。新弹窗单独文件避免Workspace混入IO和恢复逻辑，样式复用ProjectPicker。

先失败测试验证：空闲已打开项目/缺失目录移除且数据保留、活动运行/云关联拒绝、同ID幂等、并发打开拒绝/标记释放、指定remove恢复；UI取消、确认、未知回执核对、失败保留、选中/非选中移除、迟到列表、刷新后失效scope。全量后端/前端及正式构建。实际Windows/多浏览器并发由原机验收，不用本机测试代替。

## 实施复核

独立安全/架构复核发现两项并先红后绿修复：非canonical UUID（chats/temp等）在任何scope关闭副作用前拒绝；host已实际关闭但清理抛错时仍退役旧WebService缓存，保留主异常/登记，允许重新打开。移除最终检查失败的deleting记录在列表中保留，显式原ID恢复。前端新Ref按string标注以容纳服务端操作ID，TS构建错误已修复。测试云关联夹具直接使用现有storage API，避免跨tests/cloud模块的sys.path依赖；未修改测试基础设施或放宽断言。

## 验证结果

后端6478通过（512.24秒，0失败/错误/跳过）、前端75文件627通过；新增11后端/12前端回归。TS/Vite、标准Mac ARM64 wheel/Twine/源码外新venv安装、522 Python/4 Web逐字节一致性通过。架构与安全复核关闭；真实浏览器扩展未连接，布局原生验证及Windows验收仍待原机。证据见[报告](../../build/project-removal-20261104/report.md)和[JSON](../../build/project-removal-20261104/verification.json)。
