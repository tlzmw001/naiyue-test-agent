# Windows 大项目导入与通用服务隧道

适用：0.5.9起提供143AO通用入口。0.5.8使用固定两个入口，升级见第8节；0.5.7不含服务隧道。已有部署先看[两端升级与点击指南](cloud-143an-upgrade.md)。管理员配置开发机一次，用户平时在页面改地址。本手册中的地址、用户名和端口都是示例，必须替换为实际分配值。

## 1. 工作方式

每台Windows分配自己的network_id和1–64个开发机TCP端口；端口不绑定HTTP/gRPC类型。共享SSH账号和Coordinator可用；network_id负责选对路由，不提供对恶意共享账号的安全隔离。任务仍从本机项目/会话关联查询。容器池由现有任务创建和资源管理负责，无需用户SSH进容器。

```
本机Agent控制请求 → 本机受管SSH -L → 开发机Coordinator
任务容器 → 个人桥接端口 → 受管SSH -R → 本人Windows → 待测服务
```

同一入口切换会影响所有绑定该network_id的任务，包括其他本机项目里的任务。每个入口对应一个当前目标；多个HTTP/gRPC/其他TCP目标可以同时启用。若要同时访问新旧服务，应使用不同入口。HTTPS和TLS gRPC字节透传，保留原证书验证。

## 2. 管理员一次配置

1. 使用现有容器专用桥地址，确认真实容器能够访问开发机此地址。例中是172.18.0.1，不要照抄到其他Docker网络。Coordinator继续只在127.0.0.1监听Owner API。
2. 在Coordinator配置增加`networks`（完整可解析例子见[cloud.example.json](examples/full/cloud.example.json)）：

```json
"networks": {
  "alice": {"bind_host":"172.18.0.1","ports":{"orders":21001,"users":21002,"rpc":21003,"cache":21004}},
  "bob": {"bind_host":"172.18.0.1","ports":{"service1":21005,"service2":21006}}
}
```

3. SSH服务器须允许指定地址的远端转发。以下是需要由管理员审查后合并到sshd配置的示例，不能覆盖整份现有配置：

```text
Match User nyt-shared
    AllowTcpForwarding yes
    GatewayPorts clientspecified
    PermitListen 172.18.0.1:21001 172.18.0.1:21002 172.18.0.1:21003 172.18.0.1:21004 172.18.0.1:21005 172.18.0.1:21006
    PermitOpen 127.0.0.1:18701
```

`PermitListen`限制反向监听位置；`PermitOpen`允许本地控制转发访问Owner端口。18701替换成现有Coordinator Owner端口。保留其他必要SSH用途时由管理员合并允许项。管理员先`sshd -t`检查，再按公司流程重载sshd；本程序不修改/重载SSH服务、不改防火墙。不要用GatewayPorts yes把隧道暴露在所有网卡；bridge端口也应按公司网络策略仅供容器网络访问。推荐每台电脑使用独立SSH密钥，即使用户名相同，便于单独撤销。

4. 给每台Windows发放：SSH主机别名所需信息、Owner连接配置、该用户network_id、模板ID。不要重新生成既有Coordinator instance_id。
5. 更新Coordinator和Runner模板到包含本变更的相同版本。已存在任务不会自动改变network_id；需要网络的旧任务应新建绑定任务。

## 3. 用户在Windows首次设置

确认Windows OpenSSH客户端可用：

```powershell
ssh -V
```

编辑本人`%USERPROFILE%\.ssh\config`（用户自行操作），例如：

```text
Host nyt-dev
    HostName development.example.invalid
    User nyt-shared
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

使用公司实际地址和管理员登记的公钥。首次手动连接`ssh nyt-dev`，核对管理员提供的主机指纹，再完成known_hosts登记。使用加密密钥时先由用户配置ssh-agent并解锁；程序使用BatchMode，不会弹出密码询问，也不会自动接受陌生指纹。验证自动登录：

```powershell
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes nyt-dev exit
```

本机profiles示例（路径和模板ID替换为实际值）：

```json
{
  "profiles": [{
    "profile_id":"development",
    "connection_file":"C:/nyt/private/connection.json",
    "template_ids":["python-tests"],
    "default_template":"python-tests",
    "tunnel":"owned",
    "ssh_host":"nyt-dev",
    "network_id":"alice",
    "reconnect_seconds":5
  }]
}
```

connection.json沿用现有endpoint、owner_authority、owner_token、instance_id配置；若显式设置max_response_bytes=4194304，改为67108864（64MiB），以容纳大文件清单，参见[完整参考](examples/full/cloud-configuration-reference.md)。tunnel=owned时不要再手动创建占用同一本机端口的`ssh -L`；程序负责控制连接和每个启用入口的独立反向进程。共用SSH账号不代表每个人使用同一个network_id或相同开发机业务端口。

在settings.toml中的`cloud_tasks.profiles_file`指向此profiles文件。随后正常启动：

```powershell
nyt --settings C:/nyt/private/settings.toml
```

## 4. 页面操作

云端任务列表 → **服务连接** → 选择云端连接：

- 每行可以填写名称；入口ID和监听端口由管理员分配，不可在页面改动。
- 每个入口均可使用任一受支持地址，不按入口ID限制协议。HTTP输入完整`http://域名:端口`或`https://域名:端口/基础路径`。
- gRPC输入`grpc://域名:端口`；使用TLS时填写`grpcs://域名:端口`。
- 其他TCP服务填`tcp://主机:端口`，TLS-over-TCP填`tls://主机:端口`；不支持UDP/QUIC，不代表Agent已有相应业务客户端。
- “测试端口”只验证Windows到目标的TCP连接；不冒充业务API、TLS或gRPC握手成功。地址不含用户名、密码、token、查询串或fragment；请求凭证通过现有测试项目自己的授权配置提供。
- “应用配置”由云端检查关联任务静止性，取得切换预留，再切换SSH并提交。各入口独立切换，只重建修改的入口，不关闭其他入口或控制连接。
- 有运行中测试、后台命令或状态待核对时会拒绝；先处理相应任务，不会自动杀后台服务。
- 断线后自动重连；关闭网页保持连接。主动“断开”持久保持，不会被后台重连重新启用。
- 切换失败会保留原操作并重试；可以“取消切换”恢复之前的配置。回执丢失时先核对成功事实，不重复切换。等待状态不会按固定时间自动当作成功。
- 配置保存在profiles旁边的`profiles.networks.json`（随原文件名变化），不要手动删改未完成操作。原profiles/connection/settings不被页面覆盖。

停止全部本机服务和受管连接：`nyt service stop`。再次`nyt`启动后恢复已启用入口；已主动断开的入口保持断开。Windows不睡眠有利于长期连接，但VPN断开、Wi-Fi切换或电脑离线时待测服务仍会不可达；云端任务可继续存在，不承诺服务调用永不失败。

SSH状态以服务端remote-forward成功回执和进程存活检查为基础。使用Windows OpenSSH；其他同名ssh客户端如果不提供兼容回执，状态会报未确认，不会误报成功。故障查看`nyt service logs`；日志记录安全错误码，原SSH诊断不写入产品日志以避免暴露密钥路径等信息。

## 5. 容器如何知道访问地址

新建任务时，后台把所选profile的network_id持久写进云端任务；不需要用户每轮粘贴端口。每轮执行前，Coordinator把当前入口、目标原地址、TLS server_name和配置revision放入命令上下文。Runner用本轮配置替代之前的路由说明，因此已有容器下轮会看到新目标。Agent需要据此构造请求，程序不劫持容器全局DNS或全部网络流量。

HTTPS测试需保留原URL/Host/SNI。例如任务上下文给出`api.example.invalid:443`通过`172.18.0.1:21001`访问，可在容器按实际值使用：

```bash
curl --connect-to api.example.invalid:443:172.18.0.1:21001 https://api.example.invalid/health
```

私有CA应按既有公司模板/项目配置正确加载，不能添加`-k`绕过验证。gRPC客户端连接入口IP:port，并设置原域名的TLS server name/authority；不同语言SDK的设置入口不同，应按测试项目实际客户端配置。明文gRPC则直接连接入口，必要时仍设置服务所需authority。一个HTTP目标跳转到另一个域名不会自动给第二域名建隧道；这类多域名场景需另行确认，不把它当通用代理。

## 6. 大项目导入

创建任务时自动探测Git。非Git项目使用文件/目录选择；不会初始化Git。Git项目可取消“导入全部Git项目文件”，选择目录或文件，仍保留来源回执以支持代码取回到新分支。当前项目必须是仓库根目录才能使用Git来源。

目录通过列表“打开”浏览，通过复选框选择整个目录；支持多目录，也可取消目录里的个别文件。若整个子目录已取消，要先回上一级重新选中它，再调整其中的文件。手动填写每行一个项目相对文件/目录，支持Windows分隔符；不支持绝对路径、..、glob。安全排除仍生效，不能选择.env、私钥、.git、依赖缓存等被禁止项。符号链接不会作为目录递归追踪，越界链接在捕获时拒绝。

先“预览导入范围”，查看文件数/未压缩大小/上限，再“导入并准备任务”。编辑选择后须重新预览；捕获时会再校验实际文件，不会凭旧预览上传新增加的其他目录。

默认输入总量2GiB、单文件2GiB、归档2.5GiB、32768文件、元数据16MiB、本机缓存16GiB。结果工作区总量3GiB、65536文件；补丁仍64MiB。已有配置如显式写旧128MiB，需要参照settings.full.toml同时调整本机和Coordinator。空间峰值远大于单个源码总量，需为本机冻结副本、归档、云端输入、容器工作区、Git对象和结果留空间。

## 7. Windows最终验收

1. 非Git目录：创建窗口禁用Git来源；选目录、取消文件、预览、创建成功。越界路径拒绝；失败时云端没有新任务。
2. Git大项目：选择src/tests等子范围，确认云端只含该范围；修改后取回“新分支”仍可用。选择超过预算时显示明确原因。
3. alice/bob两台Windows：各配不同network_id，共用SSH用户名；分别启动两个测试服务，任务只访问各自响应内容，不能仅凭端口连接成功判通过。
4. HTTP→HTTPS、gRPC→TLS gRPC：正确Host/authority/证书时成功；错误证书仍应失败。
5. 空闲时切换HTTP目标，旧容器下一轮访问新目标，gRPC保持；运行中或保留后台服务时切换被拒绝。
6. 切换时断网、目标不可达、远端端口冲突：不得显示成功，不得自动解除待核对；恢复后原操作收敛，或取消恢复原配置。
7. 关闭网页继续可用；重启本机服务恢复已启用入口；主动断开后重启仍保持断开。Windows无新增可见终端窗口。
8. 重复读写3轮、重连和换目标，检查service.log安全错误码；生产地址、密钥及token不进入代码/提交/报告。

自动化测试和本机2GiB探针不替代以上Windows、公司SSH、容器网络与TLS/gRPC原机证据。

## 8. 从0.5.8升级通用入口

先处理完或取消页面内未完成切换，停止关联运行和后台服务，正常关闭Windows本机服务；按现有维护流程更新Coordinator与本机到包含143AO的版本（本说明不代表已经发布新版本）。0.5.8页面不支持新的入口列表响应，两端应同步升级后使用。

原cloud.json中的http_port/grpc_port仍可读取，自动规范成ports中的http/grpc；原profiles.networks.json中的端点快照同样只在读取时规范化，下次保存写新结构。入口ID、旧目标、禁用状态、revision和原pending command_id保持，不自动清理数据。新旧配置字段不能同时填写。

扩容时保留原入口ID/端口和bind_host，例如ports中保留http、grpc再增加cache。管理员同步允许新端口并重启Coordinator，本机重新打开“服务连接”可看到新增入口；只有填写目标并应用后才启动新隧道。原入口不因新增入口而重新绑定。新增入口仍需管理员配置，不在页面随机分配端口。

删除或改变已有端口/桥地址会触发identity_changed，不能直接手改侧车文件绕过。确需重排时，先在原配置下完成/取消pending并断开入口，然后为新分配使用新的network_id和本机profile，再创建绑定新组的任务；旧任务保留原网络身份，按既有任务流程收尾。

Windows只配置自己的profile/network_id；多个profile的owned连接必须使用不同本机endpoint监听端口，远端Owner可以相同。管理员保证不同电脑不重复分配network_id；误配同一ID仍会争用入口，本轮不增加占用租约或服务端租户鉴权。
