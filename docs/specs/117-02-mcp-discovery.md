# MCP-02：用户配置与只读工具发现

状态：2026-10-20，用户批准后已实现并完成本机验收。沿用 Spec117 与 00 的 SDK 2.3.0 决策。

## 范围与当前链

当前 `nyt` 分发 Web/chat，`load_settings` 读取用户 settings.toml；01 已有
`JsonInputContract`，但没有产品 MCP 连接。02 只增加 `nyt mcp list`，不改图装配、
ToolSpec 注册、Agent 调用、真实配置、数据库、版本或发布状态。

正常：`cli.main → mcp_client.cli.main → load_settings → discover_server`，按启用的
server 顺序，在同一任务内创建 SDK Client、握手、低层 session.list_tools 分页、
01 schema 校验、生成目录和 SHA256 指纹、退出客户端，最后输出安全名称与检查结果。
空配置/禁用配置不导入 SDK、不访问网络/创建进程。未配置 allowlist 可以查看候选；
指定而缺失的工具导致失败。失败：配置校验失败在连接前停止；连接/发现/校验失败先
退出拥有的上下文，再记录固定错误类别并返回非零，不输出第三方异常正文。

## 接口与边界

- `MCPSettings.servers: dict[str, MCPServerSettings]` 接入 AppSettings/load_settings。
  字段及默认值沿用 Spec117 第4节；`env` 采用 `{"NAME": "${SOURCE_NAME}"}` 的
  明确引用语法，只支持引用，不接受内嵌凭证字面量。未实现的 idle_timeout_seconds
  仅接受0。stdio/HTTP字段互斥，URL拒绝userinfo/fragment/控制字符；HTTP明文禁止
  任意 headers_env（避免猜测自定义认证头），非loopback HTTP另须显式opt-in。
- `discover_server(server_id, settings, network) → DiscoveredCatalog`：临时客户端，
  返回原名、计划注册名、描述、input/output schema、allowlist状态和稳定指纹。
  不返回活客户端，不安装工具。所有工具先校验，失败不交付半份目录。
- tools/list累计 max_tools，单工具 input+output schema合计 max_schema_bytes；
  最多 max_tools+1 页（允许终止空页），重复cursor/重复工具名/非法名称失败。
  输出schema复用01的本地方言/引用校验，赋予独立文档资源范围以保留本地引用根；输出runtime/config字段是数据，允许存在。
- 连接/协商/发现整体 connect_timeout_seconds；read_timeout_seconds 0映射None。
  客户端上下文由同一任务进入/退出。取消继续传播，关闭失败不得伪报成功。
- stdio 用 SDK公开 stdio_client 和参数，不经Shell；仅SDK最小默认环境及显式引用，
  stderr管道持续排空，只保留有界尾部用于计数诊断，正文不写日志/终端。
- HTTP用httpx2，复用 selected_proxy；保留TLS验证与SDK同源重定向。
  JSON读取设置独立 max_response_bytes 默认1048576、SSE使用相同上限；拒绝压缩，
  预算在解析前检查。schema/count预算仍是解析后目录限制，stdio没有SDK公开的解析前行上限。
- SDK/HTTP库日志在本次连接上下文内脱敏为固定诊断，不改全局级别，不输出URL、
  session ID、协议内容、header或子进程stderr。该上下文有明确连接消费者，异步任务
  继承它；不会影响同进程其他非MCP网络日志。
- `nyt mcp list [--settings PATH]` 默认用户配置位置；显式路径便于确定性检查。
  输出JSON，固定字段/安全名称，不输出描述、schema、URL、argv或环境变量。
  配置错误exit1；任一启用服务失败exit1；空目录/全部禁用exit0。无tools/call/模型请求。

## 影响与验证

新增 mcp_client 包（配置、连接、目录、CLI），扩展 settings.py/cli.py 和两份依赖
清单；新增配置/目录/真实传输测试及测试fixture（仅测试用），README/Spec117/handoff
与真实待办同步。先确认新行为失败，再最小实现；已有01行为保持。

确定性覆盖：配置/空配置零IO、字段校验、敏感值保护、分页/预算/schema/指纹、真实
stdio及loopback HTTP发现、认证失败、超时与取消、进程清理、tools/call为0。回归
使用项目原生工具PATH，执行全后端、前端测试/构建、pip check/compileall/diff检查、
现有 release builder 与源码外干净安装；不把本机结果当成五平台或真实业务服务证据。

## 后续

03建立会话/实例资源owner与并发借用，不在02提前创建连接池、TTL或工具执行器。


## 本机验收（2026-10-20）

配置25项、目录14项和真实连接13项先观察缺失入口的红灯，再实现；CLI错误码/显式
文件缺失与输出schema引用根分别复现后转绿。新测试共65项，未伪称所有补充检查都
先红灯。HTTP fixture最初错误地要求所有协议都发DELETE：核对SDK源码后改为分别
检查现代协议无session ID/无DELETE、旧握手协议有DELETE，并对所有分支断言本地
HTTP transport实际关闭；这是纠正测试协议假设，未放宽资源释放检查。

| 验证命令 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_mcp_settings.py tests/test_mcp_discovery.py tests/test_mcp_connections.py tests/test_mcp_cli.py tests/test_mcp_transport_policy.py -q` | 65 passed in10.35s |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q` | 3129 passed in138.30s |
| `cd frontend && npm test -- --run` | 44文件、347 passed，4.31s |
| `cd frontend && npm run build` | TypeScript/Vite成功，既有chunk>500kB提示保持 |
| `.venv/bin/python -m compileall -q naiyue_test_agent` / `pip check` / `git diff --check` | exit0；No broken requirements found；150份生产Python AST/500行检查通过 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp02-20261020/wheel` | exit0，280文件；150份生产源码逐字节与归档一致，8个新增SDK pins均在METADATA |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp02-20261020/wheel --output build/mcp02-20261020/install.json` | exit0；源码外新venv原生工具/前端API/Find-Grep/审批恢复/SQLite重开/CLI全通过 |
| `build/mcp02-20261020/verify_mcp_install.py`（临时安装探针，命令见下） | 源码外另一干净venv中，默认空list、真实stdio分页发现2工具、子进程退出、敏感标记未输出；tools/call=0，provider=0 |

专项安装探针由 `.venv/bin/python build/mcp02-20261020/verify_mcp_install.py build/mcp02-20261020/wheel/naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl tests/mcp_discovery_fixture.py build/mcp02-20261020/mcp-install.json` 运行，exit0。首次仅最后的安装路径断言把macOS的/var与/private/var视为不同位置；改用resolve/is_relative_to后重跑通过，产品代码未改。

本轮wheel SHA256为`ca47b2e0694c569240117d471a9f7c0e7fe818c176d4a5d1f7839babc7d6e5ee`；
归档、标准安装、MCP专项报告分别在build/mcp02-20261020/archive.json、install.json、
mcp-install.json。控制夹具覆盖stdio、现代/legacy HTTP、JSON/SSE、认证错误、
schema拒绝、超时/取消、HTTP解析前预算、代理/TLS参数、跨源重定向阻断与日志脱敏；
全部无业务tools/call、无供应商请求。未做真实远程认证服务、五平台或内存硬隔离证明。
既有依赖pins不变，02共新增8个包（含官方SDK及其剩余传递依赖）；01已有5项校验依赖保持。
真实配置、数据库、学习文件未改；未commit/push/tag/发布。唯一下一片03先说明方案
待用户确认，默认TTL仍为0。
