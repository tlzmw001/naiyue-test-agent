# Spec141 受限沙箱探针

这是独立诊断原型，**不是正式后端、不是沙箱修复包**。默认只读；不创建、停止或删除容器，不安装服务，不修改工作区或配置，不调用模型。会在显式选择的已有容器中启动临时 Python 桥接进程，用有限内存数据测吞吐。

需要本机 Python 3.11+、原生 OpenSSH，以及容器内 Python 3.11+ 和现有 envd 协议 2。SSH 继续使用已有 Host 别名、跳板配置和 known_hosts，采用非交互认证，不接收密码。保留 PROGRAMDATA。

## Windows 公司链路

1. 解压 `sandbox-probe-spec141.zip` 到一个新目录，在该目录打开 PowerShell。
2. 使用你此前已经验证成功的 SSH 别名，选择当前 **naiyue 项目自己的运行中容器**。不要选择其他业务容器。
3. 运行，替换下面两个占位值：

```powershell
python .\probe.py --ssh-target "你的SSH别名" --container "你的nyt容器名" --output .\probe-report.json
```

如果原探针用了特定的 `ssh.exe`，继续加 `--ssh-exe "该ssh.exe的完整路径"`。可选 `--read-path /workspace/AGENTS.md` 测一个**确定存在**的文件；不确定时先省略。已有报告不会被覆盖，重测换输出文件名。

默认测 20 次连接、每项 200 次往返、8 路小查询，以及上下行各 8 MiB 的内存数据。预期几十秒到数分钟，实际取决于链路；单请求有超时，遇错立即退出，不自动重试或继续写入。正常 `status=measured` 只表示完成采样，**不是生产验收通过**。

分享 `probe-report.json` 即可。报告只保留计数、耗时、吞吐、受限版本号、固定错误码/errno；不保存 SSH 别名、IP、项目路径、文件正文、环境变量或 stderr。失败时不要改大超时后当作通过。

报告解释：

- `connect_hello_close`：建连接、真实 envd hello、正常关闭的总时间，20 次的 p95 只用于预筛。
- `envd_rtt`：保持连接后真实 envd 请求的往返；`pipe_rtt` 只测桥接，不可替代前者。
- `resource_info_parallel_8`：8 个普通查询的并发测量；控制请求另有预留名额。
- `memory_upload/download`：区分原始 payload 与编码后的 wire 字节，含校验/编解码开销，不代表磁盘落盘吞吐。
- `budget_screen`：按 Spec141 的条件预算预筛；未测字段明确标为未测。公司完整验收始终需要另外进行。
- `probe_cleanup_unconfirmed`：无法确认子进程清理，不能当作正常结束，保留报告进一步诊断。

## 本机专属实验

`--context` 可选择本机 Docker context，与 `--ssh-target` 互斥。

`--exercise-owned` 会写文件、运行固定命令、断线后查询原操作并停止该操作，还会运行 200 MiB 输出与权限测试。只有完整容器身份匹配且带 `naiyue.probe=spec141` 标签的专属一次性实验容器可用。**公司已有会话不要添加该标签或启用这个选项。** 本工具不会创建或删除实验容器，资源由实验协调器负责。

`fixtures.py` 为本仓库本机实验辅助代码，另需项目依赖；生成 671 个文件及 20 个 Skill，复用项目 GitRepository/SnapshotBuilder/InputReceiver 合同，不读取真实项目。它不在默认只读命令中执行。

## 当前证据与限制

- 本机 Linux ARM64 的正常通道、文件/命令、原 ID 查询、停止已跑通。
- 真实工作负载同 UID 可访问 journal/socket/envd 环境，安全门禁失败。
- 200 MiB 输出并发 IO 重复实验出现 `probe_envd_io_failed` / errno 107；连续三次失败后停止试改，根因待定位，不能宣称并发稳定。
- 背压、响应乱序、错误身份、控制名额、超时、子进程管道清理有确定性回归；这不替代公司 Windows 两跳链路验收。
- 首建时间、批量 Skills、UID 隔离、取消/销毁 UI 均没有产品级完成声明。

本轮完整记录见 `docs/specs/141-persistent-sandbox-performance.md`。不发布、不替换现有远端镜像。
