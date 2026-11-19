# Spec143BP-S3：本机设置菜单与清理窗口

日期：2026-11-19。状态：界面和本机维护 API 已接通，真实浏览器与原生 Windows 验收归 S4。前置：S1/S2 后端合同。目的：用户能够在一个本机入口明确选择清理类别，并看懂实际删除、保留、跳过与失败结果。

## 1. 当前入口与状态归属

`main.tsx → ProjectWorkspace → 多个按 scope 保留的 App → Sidebar`。目前 Sidebar 底部是“本地工作台”；收起侧栏的 CSS 会把 footer 一起隐藏。

新清理窗口和操作状态由唯一 `ProjectWorkspace` 持有，不能放到每个 App/useConversation 中。切换项目只改变会话视图，不改变清理范围，不产生多个查询 timer 或重复 POST。`Sidebar` 通过 App 接收已确定的 `openLocalSettings` 回调，不接缓存路径、项目选择逻辑或删除业务实现。LegacyHistory 独立只读页面本片不改。

```text
任一当前 Sidebar 齿轮
  → ProjectWorkspace 中的菜单/窗口状态
  → useCacheCleanup（唯一请求、读取查询、结果）
  → WebApi 全局 service API
  → 本机后端
```

云端成果面板已有“清理未引用缓存”。两个按钮的清理范围和结果格式如下。

| 用户入口 | 作用范围 | 执行与回显 |
| --- | --- | --- |
| 左下角“本机设置 → 清理缓存” | 本机已登记项目与持久普通会话，用户勾选工具输出归档、云输入包缓存 | 向 `/api/v1/maintenance/cache-cleanup` 提交选中类别，用同一操作 ID 查询各类别的结果和失败明细 |
| 现有云端成果面板的“清理未引用缓存” | 当前项目的 `cloud-results/`，按本项目引用处理 `staging/` 与 `blobs/` | `CloudApi.cleanupReceiveCache(sid)` 向 `/api/client/v1/cloud-receive-cache/cleanup` 提交当前项目和会话查询参数，结果面板展示 `removed_files`、`freed_bytes` 或错误 |

例如，用户通过 Web 取回或项目 Agent 的 `cloud_result` 工具保存了一份报告。报告属于用户的项目文件。用户随后在齿轮中勾选“云输入包缓存”，后端只处理 S2 定义的输入缓存，报告、交付记录和取回材料都在此次操作范围之外。项目成果面板的独立清理仍由接收服务检查取回、交付及 Agent 下载是否进行中，占用时向该面板返回 `cloud_receive_busy`。[成果面板](../../frontend/src/components/CloudResultReceive.tsx)、[Agent 下载与状态查询](../../naiyue_test_agent/application/cloud_local/result_tool.py)、[项目接收缓存清理准入](../../naiyue_test_agent/application/cloud_local/receives.py)

## 2. 界面合同

- 删除“本地工作台”文字块，替换齿轮按钮，名称/tooltip 为“本机设置”；点击菜单，首项“清理缓存”。不增加空设置页、不加入其他功能。
- 采用现有 `SessionMenu/ThemeMenu` 的菜单组件模式与现有依赖；不新引入 UI 库。菜单之外点击/Escape 关闭、键盘箭头移动、Enter 激活、关闭返回原按钮。
- 侧栏折叠时齿轮仍显示；移动端位于导航抽屉左下角，抽屉关闭后按原导航入口打开即可到达。不得同时显示一个遮挡 Composer 的浮动齿轮。
- 窗口使用原生 dialog 及项目已有焦点约束方式。标题“清理缓存”，范围说明“本机已登记项目与普通会话”。清理不随当前项目变化。
- 两类别初始均不勾选；标题使用“工具输出归档”“云输入包缓存”，展示后端实际保留天数。暂不可用类别 disabled 且明确说明，不使用伪造大小。
- 说明：归档清理会移除较早的详细工具输出；会话正文仍保留。输入缓存清理保留正在使用及恢复所需数据，原项目文件保持。说明应直接表达用户会遇到的行为，不展示锁、receipt、GC 等内部名词。
- 首版打开窗口只 GET 策略，无候选文件扫描，无“预计释放”假数字。未选择时“清理所选”disabled。点击此按钮是明确删除入口，不再增加第二层逐文件确认。
- 请求开始后冻结此次类别，显示“正在清理”；允许关闭窗口和切换项目，不能再次发送 POST。重新打开显示原请求状态。
- 完成后显示实际删除文件数、逻辑文件字节数，类别统计及“已保留/已跳过/失败”区别；部分成功标题不能写“全部清理完成”。
- 明细按项目/会话与类别分组，显示文件或残留包路径、步骤、原因、系统错误码与处理建议，可展开；纯文本渲染，路径和异常不使用 innerHTML。
- 失败处理提示匹配原因；WinError 5 不直接断言“杀毒软件占用”。不提供强制删除、自动提权、关闭进程或改权限按钮。
- 再次清理是用户新点击，生成新 ID 并重新扫描；不要自动重试失败文件。用户可保留当前类别选择，但完成结果不可混入下一次。

## 3. API、请求与恢复

新增 `cacheCleanupTypes.ts` 与 `cacheCleanupApi.ts`。在 `ProjectWorkspace` 的根 `WebApi` 上组合维护客户端，使用现有 `response(path, init, true)` 发送受认证的服务级请求。普通对话入口即使还没有会话也能操作。请求 URL 和 body 均与当前项目、会话、云任务绑定 ID 无关。[WebApi 的服务级请求与实例头](../../frontend/src/api.ts)、[工作区与各项目客户端](../../frontend/src/ProjectWorkspace.tsx)

- `policies(signal?)` 和 `execute(requestId, categories)` 分别 GET、POST `/api/v1/maintenance/cache-cleanup`。POST body 只含 `request_id` 与按合同排序的 `categories`，沿用传输层的 `X-Request-ID` 和 `X-Service-Instance`，前者与 body 中的 ID 相同。
- `lookup(requestId, signal?)` 只 GET `/api/v1/maintenance/cache-cleanup/{request_id}`。云端成果面板继续使用带 `local_project_id` 和 `local_session_id` 的客户端路由。两个入口各自显示本次操作结果。[公共 HTTP 合同](143bp-00-contracts.md)、[成果面板 API](../../frontend/src/cloudApi.ts)

`main.tsx` 将已经解析的 `connection.instanceId` 随根 `WebApi` 传入 `ProjectWorkspace`。唯一的 `useCacheCleanup` 用此 ID 核对本页保存的操作，持有 `loading_policy/idle/submitting/checking/finished/unknown` 状态及本次类别、request_id、结果或读取错误。纳秒和字节数字字符串按 string/BigInt 处理，不能先转 Number 再比较。

正常序列：

1. 打开窗口 GET policies；失败保留空策略并给重读按钮，不可执行。
2. 用户点击：固定类别、生成 UUID，在当前浏览器 sessionStorage 保存该服务实例的最后操作标识与类别，随后发送唯一 POST。只存 ID/类别/实例，不存凭证和用户路径。
3. POST 正常结束：展示服务端结果或准入错误；不把 HTTP 200 自动等同 completed。
4. POST 网络失败/断线：状态 checking，只 GET 原 ID；沿现有 `sessionSyncPollMs` 间隔查询，查询失败显示可重查提示。不自动 POST 重发，不生成新 ID。
5. GET running：继续只读查询；finished：展示原 http_status/result/error；404：显示“无法确认原操作结果”，不表示没有删除。
6. 页面刷新或窗口重开：同实例找到最后标识则仅 GET 查询；服务实例改变则标记 unknown，不向新实例重放旧操作。用户明确再次执行才能发新请求。

sessionStorage 读写失败须 console 诊断并可见说明本页刷新后无法恢复查询；当前页内存状态仍可继续使用，不向服务提交第二次命令。查询取消只结束浏览器读取，不取消服务端删除。组件卸载清理 timer/listener，避免未处理 Promise rejection。

策略在新一轮操作前重新读取；执行期间后端按准入时配置固定。若后端响应 available=false/unsupported，保留用户可读错误并要求重新读策略，不能静默删另一个类别。

## 4. 文件清单

| 文件 | 修改 |
| --- | --- |
| `frontend/src/main.tsx` | 将当前服务实例 ID 交给根工作区，用于恢复查询 |
| `frontend/src/ProjectWorkspace.tsx` | 唯一管理状态、窗口挂载与入口回调 |
| `frontend/src/App.tsx` | 传递入口回调，不重复创建清理 hook |
| `frontend/src/components/Sidebar.tsx`、`Sidebar.module.css` | 替换 footer、折叠/移动布局 |
| `frontend/src/components/Icons.tsx` | 先查现有 gear/settings；已有即复用，缺少才加图形 |
| `frontend/src/components/LocalSettingsMenu.tsx` | 单项菜单，使用已有菜单 primitive |
| `frontend/src/components/CacheCleanupDialog.tsx`、`CacheCleanupDialog.module.css` | 类别、状态、结果与错误明细 |
| `frontend/src/useCacheCleanup.ts` | 单一请求状态、查询与本页恢复 |
| `frontend/src/cacheCleanupApi.ts`、`cacheCleanupTypes.ts`、`api.ts` | 全局 API 组合与类型 |

所有新增文件保持小于 500 行。结果明细如果使 dialog 超限，再按已出现的独立职责拆分；不预先构建通用本机设置平台。

## 5. TDD 与验收

计划新增 `frontend/src/cache-cleanup-api.test.ts`、`cache-cleanup.test.tsx`、`components/LocalSettingsMenu.test.tsx`，扩展 Sidebar/ProjectWorkspace 的入口行为测试，并在已有 `cloud-api.test.ts` 核对成果清理路由与参数。使用真实交互和精确请求断言。

- 无项目/无会话可打开，当前模型不可用不影响入口；策略未加载不可删。
- 展开/折叠/窄屏、键盘、Escape、焦点返回、菜单与搜索弹层不争抢。
- 初始未选；取消窗口、切换类别、GET 不发 POST；只提交勾选类别。
- 从普通对话和项目齿轮分别执行，断言准确的服务路径、请求头、body 白名单及零次成果清理请求。在现有成果面板显式点击“清理未引用缓存”，断言只访问带原项目和会话参数的客户端路由，显示原计数或错误。
- 项目切换不改变范围，不创建第二个 hook/任务；隐藏 App 不多挂窗口。
- 快速双击仅一次 POST；运行时关闭再开仍为原请求。
- HTTP 200 partial 展示失败，空结果不是异常；error path 按纯文本显示。
- 断线只 GET；刷新同实例查旧 ID；不同实例/404 明确 unknown；没有自动 POST。
- 部分删除计数、路径、errno/winerror、建议可见；BigInt 字节字符串精度保留。
- sessionStorage 异常有诊断且不制造重复操作；所有等待/失败状态可退出，不永久占满屏幕。

```bash
npm --prefix frontend test -- --run src/cache-cleanup-api.test.ts src/cache-cleanup.test.tsx src/components/LocalSettingsMenu.test.tsx src/components/Sidebar.test.tsx src/ProjectWorkspace.test.tsx
npm --prefix frontend test -- --run src/cloud-api.test.ts src/components/CloudResultReceive.test.tsx src/components/CloudResults.test.tsx src/components/CloudFilesDelivery.test.tsx
npm --prefix frontend run build
```

正式浏览器验收通过真实本机认证服务和隔离缓存目录，检查 1440px、折叠导航、375px，执行至少一次真实删除和一次真实/确定性注入失败。不使用用户现有项目缓存作演示；运行证据留 S4。
