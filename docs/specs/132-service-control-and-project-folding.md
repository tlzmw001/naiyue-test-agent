# Spec 132：共享服务退出、日志查看与项目栏收起

## 当前链路与问题

- `nyt` 经 `web.__main__ → ensure_service` 连接或拉起独立服务，打印链接后退出；服务的 stdout/stderr 追加到 `data_dir/service/service.log`。因此启动终端的 Ctrl+C 不再控制服务，也看不到服务端日志。
- 服务已有认证的 `POST /api/v1/shutdown`，通过 `ServiceServer.shutdown → ScopeServices.shutdown` 停止运行、结算保存并释放锁；CLI 尚无入口。
- `ProjectNavigation` 的箭头按钮切换展开，项目名称按钮总是调用 `expand(id, true); select(id)`；`select` 又把目标项目设为展开，所以再次点名称无法收起。

## 已确认的目标与接口

保留可跨网页和终端复用的后台服务，不改 `nyt` 默认启动方式。

1. 新增 `nyt service stop [--config PATH] [--settings PATH]`。只连接同一用户、data_dir、版本和配置路径的现有服务，复用认证关闭接口；无服务时明确提示，不拉起新服务。收到关闭请求后只报告“已接受、正在收尾”，不把 HTTP 接受误报为进程已退出；关闭失败报告安全错误码。停服会影响此服务下所有客户端和未完成任务。
2. 新增 `nyt service logs [--settings PATH] [--follow]`。从该 settings 的 `storage.data_dir/service/service.log` 查看末尾最多 100 行（读取窗口有界）；`--follow` 持续显示新增日志，Ctrl+C 只退出日志查看。文件不存在明确提示；不用发现文件中的访问凭证。保留服务原有安全日志格式，不额外记录请求正文、凭证或模型内容。
3. 左侧项目名称：点击非当前项目仍进入并展开；点击当前已展开项目则收起，保持当前项目和当前会话；点击已收起的当前项目则展开。箭头继续只切换展开，不改变项目选择。键盘通过原按钮有相同行为。

## 正常与失败链

- 正常停服：CLI 解析路径 → `ServiceOptions` 定位原服务 → `ServiceClient.connect` 验证锁、发现记录和握手身份 → `POST /api/v1/shutdown` → 服务设 `should_exit` → 关闭任务/保存/资源 → 释放锁。CLI 只报告被服务确认接受的事实。
- 失败停服：锁无持有者、版本/配置路径不符、握手或请求失败 → 不创建服务、不按 PID 杀进程；输出安全错误码，保留原服务状态供用户核对。
- 日志：CLI 只读打开既有常规文件 → 输出其末尾并可持续读取。文件缺失/不安全或读取失败均显式报告，不创建日志文件。
- 项目栏：项目名称按钮由 `scopeId` 与 `open` 决定是进入还是收起；只改前端展开状态。原 `select` 仍负责跨项目切换和已存会话状态。

## 影响与验证

- 后端：`cli/__init__.py` 分发；新增单一 `cli/service.py` 承担命令；`config_paths.load_storage_settings` 增加可选配置路径，同时保留无参用法，使日志和退出即使 settings 的无关部分损坏也能定位原 data_dir。复用 `service/client.py`、`service/ownership.py` 与现有关闭路由，不新增服务协议或配置。
- 前端：`components/ProjectNavigation.tsx` 与 `ProjectWorkspace.test.tsx`；不改 API、存储和权限。
- 文档：README 说明退出、日志位置和命令；本 Spec 与 handoff 收尾。
- 先让定向测试在当前行为下失败，再做最小实现；验证不存在服务不被拉起、真实本机服务正常关闭与锁释放、身份不匹配不关闭服务、日志尾部/跟随退出、项目切换与再次点击收起。最后运行后端全量、前端全量和构建。

## 本机验收（2026-10-27）

- 红灯：新 CLI 测试 4 项因 `service` 命令不存在失败；新项目栏测试因再次点击后仍展开而失败。实现后定向后端 9 项和前端 14 项通过。
- 完整后端：`PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests` → `5606 passed in 575.33s`，退出码 0。源码开发环境原 PATH 缺 `fd`，使用仓库已有 Mac ARM64 `fd 10.5.0`；未修改产品依赖或测试断言。
- 前端 `npm --prefix frontend test -- --run` → 68 文件、552 项通过；`npm --prefix frontend run build`、`compileall`、`pip check`、`git diff --check` 均退出 0。Vite 保留原有大 chunk 提示，无构建错误。
- 临时真实子进程验证 `logs --follow` 读取已有和新增日志行，Ctrl+C 仅使查看进程以 130 退出。未操作用户正在运行的服务或真实配置/数据；未做浏览器手工点击和真实模型请求。
