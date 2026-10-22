# Spec125-05：独立后台任务管理入口

2026-10-21，承接主Spec第158行及文件地图05。用户已授权继续实施；06组合性能不在本片。

版本边界：05的独立面板已由[06统一辅助侧栏](125-06-background-acceptance.md)替代外框/关闭入口，当前顶部为单图标和子智能体／后台任务／上下文页签，输入区上下文入口保留直达。下面HTTP/CLI、作用域与刷新合同保持；05测试/截图为该片完成时点。

现有主链：Shell → BackgroundProcessManager → 同一个HostProcessRunner/ProcessCapture。应用已有异步list/output/stop；本片的CLI/Web仅调用它们，不建立新的任务拥有者、模型调用或SSE。

## 接口与边界

- 应用三个管理方法增加可选workspace_root关键字，直接下传manager。CLI不传，管理本应用所有会话；Web只传WebSettings的受信工作区。没有绑定工作区的Web返回空列表，单项操作404；请求不能指定工作区或任意文件路径。
- 列表保持limit=20、上限100和before游标。另返回整个筛选集合的active_count、terminal_revision和revision；分页不改变总数。revision是公开元数据的SHA256，只扫描内存，不读日志；terminal_revision取该范围已有终态版本最大值，不消费模型通知。列表只读不准备Agent，不改变审批/运行状态。
- GET /api/background-tasks，GET /api/background-tasks/{task_id}/output，POST /api/background-tasks/{task_id}/stop；支持可选session_id过滤。路由继承原Bearer/Host/Origin鉴权。业务入口检查任务存在及session/workspace归属；未知或越界统一404 task_not_found，非法游标422 task_cursor_invalid。关键失败记录安全错误类型，不向HTTP泄漏异常正文。
- 输出沿ProcessCapture的有界尾部；超过Web帧预算只缩短HTTP尾部并标记truncated，不改磁盘或捕获结果。元数据本身超过预算返回view_too_large。日志缺失返回output_available=false和保留的尾部/终态。归档路径仅展示相对引用，不提供文件读取API。
- stop等待已有worker收尾；重复终态stop幂等；cleanup_confirmed=false返回409 task_cleanup_unconfirmed，保留lost记录。意外读取/控制错误503 background_tasks_unavailable，客户端提供重试，不自动重发停止。
- CLI :tasks [before]有限分页；:task output ID读取输出；:task stop ID停止。通过原AsyncCliApplication循环桥接；在根任务执行期间仍可操作，普通:stop不替代它。CLI显示活动数、归属、状态、退出原因和日志引用，控制字符转义后输出。

## 前端

视觉：沿当前system字体、CSS Modules和theme tokens，440px右侧面板；窄屏覆盖工作区、保留关闭入口。内容：活动数 → 任务/会话/状态 → 选中任务的尾部和停止。交互：选择/刷新/停止为主，无装饰性动效；空数据仅显示空态，不注入示例数据。

App持有全局hook，切会话不重建。可见页面每3秒查第一页有限元数据（常量在uiSettings）；打开面板才查选中输出，更多页按需读取。刷新保留已加载页ID，通过逐页重新校验游标避免重复；新第一页到达时可重置更多页但保留选中任务和输出。隐藏页面/卸载/更换api取消读取及计时，恢复可见立即同步。每种请求不重叠，旧响应不能覆盖新选择；停止仅一次显式POST。

首次快照建立完成提示基线，此后terminal_revision变化显示“后台任务已结束或需核对”；提示不创建run，也不确认04的模型通知。初次查询/刷新失败明确显示未知/错误，不能把错误伪装为0。停止错误保留已有输出，刷新状态以供核对。

## 正常与失败链

正常：顶部后台入口 → hook → WebApi → 受鉴权路由 → AsyncApplication._owned_operation → manager.list/output/stop。后台服务跨根任务运行；用户继续聊天时管理请求独立；stop沿原runner回收后返回finished/cleanup_confirmed，UI更新状态。

失败：外国工作区ID → manager._find范围检查 → TaskQueryError → HTTP404，不读capture、不发停止。输出文件被手动清理 → capture.available=false → HTTP保留元数据和内存尾部 → UI显示日志不可用。清理未确认 → manager保留lost → HTTP409 → UI保留记录并显示需核对，不宣称停止成功。

## 验证

新增CLI/Web/前端专项测试，先确认缺少入口/字段的红灯，再实现。覆盖鉴权、工作区/会话边界、分页与全量计数、无模型调用、忙时独立管理、禁用Shell仍可停止、重复停止、缺失日志、清理与IO故障、帧预算；前端覆盖轮询/隐藏取消/旧响应/分页/完成提示/状态失败/停止、纯文本输出和切会话保持。全后端/前端、TS/Vite、500行/编译/依赖/diff、标准Mac ARM64 wheel/Twine/源外安装；浏览器检查桌面和375px的浅深色及实际临时任务状态。Windows/Linux原机与组合性能留06，不做完成声明。

## 实际结果

05已完成，实际主链、测试数量、构建安装和浏览器证据见[主Spec第16节](125-background-shell-tasks.md#16-125-05实际实现与验收2026-10-21)。全后端4184/前端406通过；06尚未开始。临时资源已关闭，0真实供应商请求，未提交或发布。
