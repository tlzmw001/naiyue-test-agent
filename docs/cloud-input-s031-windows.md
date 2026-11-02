# S03.1 Windows 最小复验

本轮本机证据是Mac捕获/客户端测试和Linux ARM64真实服务，不代表Windows原机通过。此页只给复验步骤；先在获准目标机按143k/143l准备当前版本Coordinator、含input_install模块的模板、专用非root账号和网络白名单。不要将原S00控制容器当成已经部署了B产品，也不要使用A的数据根。

## 1. 安装本轮Windows候选

使用Python3.12的新环境，避免覆盖正在使用的A环境。源码侧本轮候选位于`build/spec143-s031-20261101/final-dist-windows-x86_64/`，不是已发布PyPI版本。复制前按同目录验证汇总里的SHA核对wheel；Windowsx64包不能用Mac/Linux包替代。

```powershell
py -3.12 -m venv .venv-cloud-s031
$CloudPython = (Resolve-Path .venv-cloud-s031\Scripts\python.exe).Path
$CloudWheel = (Get-Item .\naiyue_test_agent-0.5.6-py3-none-win_amd64.whl).FullName
& $CloudPython -m pip install $CloudWheel
& .venv-cloud-s031\Scripts\nyt.exe cloud input --help
```

## 2. 专用传输连接

沿用已验证SSH Host别名；准备单独的前台SSH进程承载文件传输，和后续控制/SSE连接分开，不复用ControlMaster。端口取实际配置，不需要对公网开放。

```powershell
$CloudSshHost = Read-Host '已配置的SSH Host别名'
$CloudLocalPort = [int](Read-Host 'Windows本地空闲端口')
$CloudOwnerPort = [int](Read-Host 'Coordinator的owner loopback端口')
ssh -N -T -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes -L "127.0.0.1:${CloudLocalPort}:127.0.0.1:${CloudOwnerPort}" $CloudSshHost
```

保持此窗口运行，在另一终端继续。连接配置用私有JSON文件显式提供，字段为：endpoint（Windows转发端口的http://127.0.0.1:端口）、owner_authority（开发机实际127.0.0.1:owner端口）、owner_token、instance_id、timeout_seconds（JSON浮点数）。instance_id由部署侧受信cloud-root.json取得。owner_authority可能与本地端口不同，不能用本地转发端口替代它。不要把token/配置内容贴到报告或对话；Windows文件ACL应只允许当前账户读取。

## 3. 选择并捕获

选择一个新建的小测试目录，放入一条可运行pytest、一份proto和一个普通二进制文件；先不要使用有公司凭证的完整工程。创建selection.json，内容包含schema=1、paths（相对文件路径的数组，使用/）、executables（所选可执行脚本子集，通常为空）。这是明确白名单；本片不自动汇总Git ignore/filters。不要选.git/.venv/node_modules/.env或密钥文件；原字节保留，CRLF的可执行shebang脚本会拒绝，普通CRLF文本不转换。

```powershell
& .venv-cloud-s031\Scripts\nyt.exe cloud input capture --source $CloudSourceDirectory --selection $CloudSelectionFile --output $CloudNewCaptureDirectory
& .venv-cloud-s031\Scripts\nyt.exe cloud input upload $CloudNewCaptureDirectory --settings $CloudPrivateConnectionFile
& .venv-cloud-s031\Scripts\nyt.exe cloud input upload $CloudNewCaptureDirectory --settings $CloudPrivateConnectionFile
```

上面的路径变量先填成实际绝对路径；capture输出必须是尚不存在、位于source之外的新目录。记录第一次的input_id/uploaded_bytes，第二次应同input_id、cache_hit=true、uploaded_bytes=0。capture目录内的receipt、归档、selection和上传命令日志需要保留，重跑使用同一目录；不要删日志伪造新的重试身份。

## 4. 服务侧接线与断线

由受信所有者API创建项目，再POST tasks（command_id/project_id/input_id/template_id/title），然后用S02 task commands的start。GET task应input_ready=true、environment_phase=backend_ready，不能称Agent ready。用受控观察器在/workspace/project运行pytest；确认两个任务引用同I仍有独立可写目录，一边改文件后stop/start仍保留，另一边内容不变。禁止给Coordinator挂Docker socket来方便观察。

上传中关闭本次SSH进程，再建立独立连接并用同一capture目录重跑。S03.1只支持一次完整PUT：未完整确认时从0重传整份，已提交输入零上传，不承诺分段续传。创建/上传/提交ACK丢失沿用原command_id；GET transfer查询实际offset/state。多段续传、200MiB规模、完整Git来源及进度UX在S03.2。

复验记录只带版本/SHA/架构、input_id、字节数、状态码、pytest摘要、断线重试结果和资源收束，不带token、源内容或内网地址。Windows休眠/实际公司RPC/模型供应商出站不属于这次小目录复验；原S00三段结果保持原证据边界。
