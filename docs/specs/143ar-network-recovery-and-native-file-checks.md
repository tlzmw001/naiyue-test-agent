# Spec143AR：连接状态恢复与 Windows 原生文件烟测

2026-11-05 用户批准两项已记录待办，按顺序实施；不改版本、真实配置、远端Release或任务重试策略。原有三份文档修改保留。

## 1. 已确认问题和范围

1. ServiceNetworks._maintain 的连接检查失败只更新 connecting，connected 留存。CloudNetworks 轮询失败也保留旧状态。两处都会误导用户。
2. release_network_smoke 现有安装门禁覆盖旧输入模式与原生进程，未覆盖 schema3 显式隐藏附件及 input_id=None 的产物保存。

正常链：服务启动维护器 → ProfileConnections.get 校验管理连接 → _connect 确认反向隧道 → 页面读取状态。失败链：get/request 抛 CloudError → 记录错误、继续维护；需要将原 connected/connecting 降为 reconnecting（待确认），不得断言反向通道一定断开。已禁用、已明确错误及其他profile的状态保留。仍存活的隧道恢复时重新探测，成功后恢复connected，不因管理检查失败直接重启。进程确实退出仍走既有重建。

页面轮询失败仅降级旧 connected/connecting 状态，保留已编辑目标和端口；显示“连接待确认，正在恢复”。成功读取清除读取错误，不清除独立的应用/测试错误，不覆盖用户草稿。没有状态或禁用项不虚构连接成功。用户点击操作与恢复轮询保持现有副作用合同。

## 2. 原生烟测

扩展既有 scripts/release_network_smoke.py，不新增依赖/测试平台。network_scenario调用新增场景，并由verify_network_report强制检查新增结果字段；release_smoke与verify_wheel_install现有消费者沿用。Windows安装job调用同一脚本，在源码外安装的wheel上运行。

- 附件：真实普通文件/中文空格目录/隐藏文件/.git目录/不解压的归档字节 → ordinary目录浏览、select_files(schema3)、capture_cached、validate_package解包 → 清单身份、文件集合及字节一致。
- 产物：真实ReceiveFiles临时材料缓存、input_id=None的产物与接收记录 → deliver_files准备/执行 → 指定已有目录保存；不同内容必须确认覆盖，创建新目录和重复保存内容一致。材料及操作元数据使用烟测局部记录，文件读取、校验、目录保护、写入、替换与日志落盘走生产函数；不宣称涵盖HTTP/真实云端鉴权。
- 保留旧schema1/Git与Windows进程/CRLF回执检查，仍有历史接口消费者。

## 3. 影响与验证

影响：application/cloud_local/networks.py、CloudNetworks.tsx及其测试、tests/cloud/test_service_network_supervisor.py、scripts/release_network_smoke.py、tests/test_release_network_smoke.py、本Spec及交接/待办。

先写故障/恢复和烟测字段缺失的失败测试，再最小实现。定向后端/前端、完整后端/前端与构建、标准Mac wheel和源码外安装门禁。本机结果不宣称Windows实机或公司SSH/VPN/20GiB已验；新增Win32分支在下次正式Windows安装CI运行后才有原生结果。无锁新增/作用域变化，无新公开接口签名或DB字段，status字典新增显示值reconnecting。

- [x] 连接失效与恢复（后端+页面）
- [x] 原生文件烟测及门禁接线
- [x] 完整验证与记录


## 4. 实施证据（2026-11-05）

- 红灯：旧维护器保留connected、恢复时重启健康进程，新增两项回归失败；恢复探测失败两种错误也先失败。旧烟测缺两项新检查且接受缺失报告，两项回归失败。页面断线仍显示建立，新增行为回归失败。
- 绿灯：网络恢复/烟测15项通过；独立网络回归26项通过、文件回归32项通过（有重叠，不累加）。前端完整76文件634项通过，TS/Vite构建通过，仅既有chunk大小提示。
- 源码外标准Mac wheel安装通过，新环境报告包含attachment_roundtrip=passed/output_files_save=passed；Twine通过，535 Python与4 Web文件逐字节匹配。
- 架构/原生文件专项只读复核无追加缺陷。恢复探测失败保留reconnecting、具体原因落日志；进程退出继续旧重建逻辑。
- 完整后端结果及日志见最终报告 `build/spec143ar/report.md`。本轮无Windows原生结果，下一次Windows安装CI会强制执行新增检查。

最终完整后端：**6547 passed in 515.83s**；前端：**76文件634项通过**。构建/Twine/源码外安装通过；源码在完整测试和打包后未变化。本机报告见[验证报告](../../build/spec143ar/report.md)。未提交、push或发布。
