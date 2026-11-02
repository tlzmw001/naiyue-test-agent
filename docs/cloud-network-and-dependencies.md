# B任务的网络与Python依赖

适用S04.2a，合同见[143p](specs/143p-s042a-task-capabilities.md)。本机普通模式仍保留原审批；B默认任务工具NoAsk并预授权，显式deny继续生效。本文件是部署/使用说明，不表示已在公司机器应用配置。

## 网络由开发机部署配置

首版同一Coordinator/OpenSandbox实例的任务共享一套专用bridge和宿主网络策略。没有逐任务切换网络按钮，不由Coordinator取得Docker socket或修改防火墙。

- 受控外部网络：依开发机现有策略下载依赖，经指定中转入口→SSH反向隧道→Windows→待测服务；不意味着任意外网/内网都允许。
- 关闭外部网络：拒绝公共包源、业务中转、通用代理和其他外部IPv4/IPv6路径；保留当前任务的控制/历史/结果入口，以及S05受限模型入口。云端Agent仍能运行。不要配置Docker network=none。

OpenSandbox配置在开发机的显式server.toml，`docker.network_mode`为已准备的专用bridge名称，`docker.publish_host`为loopback。命名网络不传不支持的networkPolicy。真正出站限制在宿主INPUT/转发规则和已配置代理，保留ESTABLISHED/RELATED返回流量；不照抄iptables规则到未知Docker防火墙后端，也不以ICC=false证明全部隔离。

Windows SSH反向转发先落到开发机loopback固定端口，再由仅绑定专用bridge的入口转发。禁外网策略必须同时阻止任务访问这个入口；关闭公网下载而仍可访问Windows待测服务，不算这里的关闭外部网络。Windows睡眠使业务依赖不可达，不自动重放结果未知的测试请求。

## 显式工具环境

下面只展示云端服务settings JSON的一段，地址为说明占位值，按开发机实际配置填写；不修改Windows全局环境或把整套Coordinator环境复制进去：

```json
{
  "tool_environment": {
    "values": {
      "HTTPS_PROXY": "http://proxy.example.invalid:8080",
      "PIP_INDEX_URL": "https://packages.example.invalid/simple",
      "NO_PROXY": "localhost,127.0.0.1"
    }
  }
}
```

无需代理的部署可保留默认空对象；公共PyPI索引及文件下载地址都须按实际策略可达。私有包源/测试账号凭证只按既有私密配置通道提供，不能把含凭证URL贴入日志/命令记录。证书文件必须在任务容器内存在，宿主文件路径不自动挂进容器。必要时显式配置工具证书变量或通过受信模板安装证书。

`values`允许普通包源/代理/测试服务变量，不允许覆盖PATH、PYTHONHOME、PYTHONPATH、VIRTUAL_ENV、BASH_ENV；NUL/非法变量名拒绝。工具PATH固定项目.venv/bin优先，之后是显式path_prepend与镜像PATH。配置仅写入私有任务bootstrap和工具环境，不参与Runner解释器启动。新任务/新代使用其准备的bootstrap；不要假设修改settings会热更新正在运行的任务。

## 添加项目依赖

镜像预装的Kit/pytest在固定测试环境；Runner自身独立且不可写。没有项目venv时使用预装环境。需要新增包时，Agent可通过Bash在项目工作区显式执行：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest tests -q
```

这是有requirements.txt的项目示意，不由应用自动执行。新venv不继承镜像测试venv里的Kit/pytest，requirements应包含实际所需包；Kit版本参考当前任务模板，当前首个模板为aitest-kit==0.6.0。依赖缺失、包构建失败、网络/证书错误均保留真实工具失败，不能改用镜像解释器冒充成功。

后续Bash中的python/pip和AITest工具均动态使用项目.venv；AITest以所选Python的`-I -m aitest_kit.cli`运行，工作区同名模块不能覆盖安装的Kit。存在.venv但解释器缺失/不可执行时，AITest返回project_python_unavailable；Bash仍可用绝对基础Python路径检查/修复。没有跨调用activate要求，不缓存第一次选择的Python。

离线模式可复用已准备的项目环境，或从已经导入的兼容wheel安装：

```bash
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements.txt
```

pip不是系统包管理器；依赖缺编译器、系统库或命令时，更新受信测试模板，不在任务里sudo/apt，也不改Runner依赖。项目环境随工作区保留，同任务热态不重复安装；交付代码/requirements或lock，Windows重新建venv，不搬Linux.venv。

## 最终部署复验

使用实际任务容器，分别记录两种部署配置的正负结果，不能只在开发机宿主curl成功：

| 项目 | 受控外网 | 关闭外网 |
| --- | --- | --- |
| Coordinator控制/历史/结果 | 成功 | 成功 |
| S05受限模型请求 | 成功 | 成功 |
| 指定包源下载与实际pip/import | 成功 | 网络拒绝；已有本地wheel仍可安装 |
| Windows中转HTTP/实际proto RPC | 成功 | 网络拒绝 |
| 其他任务/管理API/非授权宿主端口 | 拒绝 | 拒绝 |
| 未授权IPv4/IPv6/可用通用代理出口 | 按部署策略拒绝 | 拒绝 |

S05尚未接线时模型项保持待验，不用夹具冒充供应商通过。包源至少跑一次实际目标包安装/import；RPC应核对真实调用回执。复验不修改公司业务数据，不自动重放结果未知操作。本机测试只证明权限/解释器/工具接线，不能替代上述最终网络证据。
