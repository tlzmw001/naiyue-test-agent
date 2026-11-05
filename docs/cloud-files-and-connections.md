# 云端文件与统一连接配置（Spec143AQ，0.5.9）

这份说明对应0.5.9发布实现。0.5.8及更早标签仍使用各自的配置和界面。本说明不修改真实配置、实例身份、现有任务或部署。

## 用户操作

1. 主输入框加号 → 云端任务 → 新增任务。只选择连接、模板和服务网络；不选择Git基线或上传范围，创建不调用模型。
2. 打开任务对话，加号 → 添加文件或添加文件夹。中心窗口浏览当前项目目录，勾选普通文件或整个目录，点击“上传所选项”。支持多选、隐藏文件、`.env`、`.git`和压缩包；目录递归展开，链接、junction和特殊文件明确拒绝。归档保持原文件，不自动解包。
3. 上传仅暂存，运行中的Agent不会看到尚未提交的附件。勾选附件、填写用途和验收要求，空闲时点击“发送到云端”。Agent收到本批 `.nyt-input/<attachment_id>/` 路径，重复使用原附件不会覆盖Agent修改后的内容。
4. 本轮运行时仍可上传、写下一条提示词，点击“本轮结束后发送”。每任务一条待发送项；上一轮正常完成、结果保存、附件就绪后才发送。停止、失败、unknown或恢复阻塞会暂停，不因WebSocket断线猜测完成。关闭浏览器不取消服务中的任务；停止本机服务期间不工作，重开来源项目后核对。服务崩溃中断的上传点击“重试上传”，复用原ID和已确认偏移。
5. 提示Agent把需要交付的文件或压缩包放入 `task_output_directory` 返回的本轮目录。程序只收集此目录，不扫描整个项目、不生成diff、不替用户合并代码。
6. “结果与产物”打开中心窗口。可逐个“浏览器下载”，或勾选文件后选择已有目录/新建目录并保存。相同内容可复用；不同内容同名必须明确确认覆盖，确认后目标再次变化则停止并要求重新核对。
7. 勾选文件 → “交给主Agent分析”。程序先接收、校验选中材料，把材料引用和提示词放入主会话输入框；用户再点击发送。主Agent通过材料工具读取，不自动执行下载文件或应用代码。

浏览器下载链接只在本机、当前浏览器的短期授权内使用。URL不含Owner或本机服务token；授权采用限定文件路径的HttpOnly Cookie，默认5分钟，重启本机服务后失效。已开始的下载不会因为链接到期被截断。主Agent使用材料引用/已认证API，不把复制一个URL当作授权。浏览器的保存位置遵循浏览器设置；需要明确目录和覆盖控制时使用“保存到本地”。

## 配置只保留各自的一份

| 机器 | 手工配置 | 职责 |
| --- | --- | --- |
| Windows | `settings.toml` | 本机Agent和传输预算，引用connections文件 |
| Windows | `models.json` | 本机模型配置 |
| Windows | `connections.json` | 所有开发机地址、SSH、凭证、实例、profile/network和可选远程更新设置 |
| Linux | `cloud.json` | Coordinator、后端、网络端口、模板、传输预算 |
| Linux | `models.json` | 云端模型配置 |
| Linux | `update.json`（使用更新/备份时） | 更新器配置，引用cloud.json；无需另配connection.json |

完整样例见 [connections.example.json](examples/full/connections.example.json)、[settings.full.toml](examples/full/settings.full.toml)、[cloud.example.json](examples/full/cloud.example.json)、[update.example.json](examples/full/update.example.json)，字段解释见[配置参考](examples/full/cloud-configuration-reference.md)。这些文件使用占位身份和凭证，不可原样部署。页面生成的 `connections.networks.json` 是程序状态，不是额外手工配置。

Windows示意：

```toml
[cloud_tasks]
connections_file = "C:/private/connections.json"
# 从旧profiles迁移且要沿用页面中的网络目标时，指向原来的状态文件：
# network_state_file = "C:/private/profiles.networks.json"
```

同一connections条目只写一次connection和ssh，profiles可列多个network。沿用已有profile_id、network_id和instance_id；不要重新生成实例ID。`connections_file`和旧`profiles_file`不可同时配置。旧0.5.8文件只在读取边界兼容，执行复用同一主链；不自动覆盖或删除旧文件。

SSH别名和密钥认证仍由Windows OpenSSH配置。使用 `ssh.tunnel="owned"` 时本机服务管理保活/重连；不要同时手工启动占用同一端口的转发。页面“服务连接”先选HTTP/gRPC/TCP和TLS，再填域名或`host:port`；粘贴完整URL可自动识别。HTTP路径保留，gRPC/TCP不接受路径。底层仍是TCP转发，不解释业务协议；HTTPS/gRPC保留原Host、证书域名和authority。

CLI可复用同一connections文件，多profile明确指定：

```powershell
nyt cloud project list --settings C:/private/connections.json --profile development
nyt cloud release discover --remote C:/private/connections.json --profile development
```

CLI复用已有正向隧道，本机服务或外部隧道需要在线。Linux更新器从cloud.json读取地址/凭证，从已存在的cloud-root.json读取实例；不会创建新实例。旧显式connection_file存在时校验地址/身份一致。

## 预算与版本边界

| 项 | 新默认值 | 说明 |
| --- | --- | --- |
| 单文件、每批输入 | 20GiB | 即21474836480字节；不是浏览器内存载入 |
| 每批文件数 | 100000 | 选目录会展开计数 |
| 归档大小 | 22GiB | 为封装留空间 |
| 输入缓存 | 64GiB | 包括冻结副本和归档，累计超过会拒绝 |
| 单产物、单轮产物总量 | 20GiB | 每轮最多1000个普通文件 |
| 云端产物缓存 | 64GiB | 累计容量约束 |
| 本机材料/保存预算 | 128GiB | 覆盖缓存、临时副本和覆盖备份的保守预留 |
| 传输分段 | 8MiB | 原ID续传，提交前校验完整摘要 |
| 解包保留空间 | 256MiB | 按未压缩清单预检，不能只看压缩包大小 |

上述是配置及有界流能力，真实20GiB传输耗时、峰值磁盘和公司链路仍需实测。Coordinator按归档加展开量保守准入，重复引用附件也做该软预算检查。共享已提交输入包遵循原保留合同，不自动按任务删除；输入预算耗尽应调整预算或按离线维护处理，不得删除仍被任务引用的包。本机“清理未引用缓存”针对未引用结果材料，不清除正在使用的附件。

Windows本机、Linux Coordinator和Runner模板须一起升级到含本Spec的同源构建。新模板预检回执具有 `file_transfer_version=1`；旧模板会明确提示更新，不靠手改回执冒充兼容。新空任务不保证旧程序可读，禁止旧版混写。新任务主链不依赖Git；旧任务已有冻结结果仍能读取，旧结果接口及CLI消费者不在本轮销毁。

保持原启动方式：Linux `nyt cloud serve --settings /实际私密目录/cloud.json`；Windows `nyt --settings C:/private/settings.toml --config C:/private/models.json --workspace C:/实际项目目录`。安装新包前先正常停止对应旧服务；更新Runner不等于重启Coordinator。具体维护、备份及模板构建沿用[更新手册](cloud-updates.md)，不要在运行中替换venv文件。

## Windows与公司环境验收

1. 单一connections文件启动；多connection和同connection多network分别可选；凭证不出现在页面或链接。
2. HTTP完整URL、HTTPS、gRPC裸地址、TLS和至少三个通用端口；切换地址后原任务收到当前路由。
3. 非Git项目无文件创建并发送文本；旧模板应明确拒绝新附件模式。
4. 加号窗口选择普通、隐藏、中文、空格路径、压缩包、整个目录；junction/符号链接明确失败；边界附近的大文件用真实磁盘验证。
5. 运行中上传并排队；正常结束只发送一次；失败/停止/断线未知不自动发；刷新浏览器和重启服务后分别核对状态。
6. 重复引用原附件保留Agent改动；两个任务同时读取同一包；中断续传后文件摘要一致。
7. output包含任意普通文件/归档；环境释放后仍能下载。浏览器下载、指定目录、新建目录、同内容、同名异内容覆盖和确认后再次变化均核对。
8. 只勾选一个产物交给主Agent，检查材料归属和内容；不出现代码应用/分支预检入口于新任务。
9. 已安装附件经过备份恢复后可再次引用，Agent改动保留；未完成安装回执应明确拒绝恢复而不是猜测。

自动化测试、Mac打包证明和Windows/公司实测分别记录，不互相替代。
