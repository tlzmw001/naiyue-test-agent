# Naiyue Test Agent

本地运行的通用编程与测试 Agent，提供浏览器界面和终端交互。通过配置的模型、项目提示词、Skills 和工具读取项目、修改文件、执行命令，并保留会话历史。

- 本地 Web：流式 Markdown、工具执行过程、权限审批、会话管理和历史滚动加载；首任务自动标题、文件差异预览/patch 下载、可见思考和最后任务重新生成。
- 终端 CLI：多轮对话、恢复历史、审批、停止、Steering 和 Follow-up。
- 模型接入：OpenAI、兼容 OpenAI 的接口、Anthropic、Ollama。
- 工具：Read、Ls、Find、Grep、Write、Edit、Bash、PowerShell，以及一层前台子 Agent。
- `0.1.1` 新增 `aitest_kit`、百度/Tavily 回退的 `web_search` 和 `web_fetch`，配置见[工具说明](docs/aitest-web-tools.md)。
- `0.2.0` 统一模型请求准备与上下文预算，补充调用次数、耗时和 SDK HTTP 尝试诊断；支持分离配置代理地址、用户名/密码、直连名单及新会话默认工具，见[设置示例](docs/examples/settings.example.toml)。
- Find/Grep 使用安装包内的 `fd` / `rg`；前端已经构建，使用发行包无需 Node.js 或 npm。
- `0.2.0` 支持 `nyt mcp list` 只读发现及根/子 Agent 调用本地/远程 MCP 工具，复用活动工具选择、审批与会话连接，支持按服务显式开启空闲回收；应用运行中可刷新配置以新增服务、调整名单和启停，CLI/Web显示来源、结果状态和归档位置，重启保留历史、旧 MCP 待审需取消后重新发起，见[MCP客户端配置](docs/mcp-client.md)。

## 安装

需要 Python 3.12 或更高版本；当前 CI 基线为 Python 3.12。建议使用独立虚拟环境。

```bash
python -m venv .venv
```

激活环境：

- macOS / Linux：`source .venv/bin/activate`
- Windows PowerShell：`.\.venv\Scripts\Activate.ps1`

```bash
python -m pip install --upgrade pip
python -m pip install naiyue-test-agent
nyt --version
```

当前发行目标：macOS Apple Silicon、Linux x86_64 / ARM64、Windows x64。每个平台分别提供 wheel，pip 自动选择。Linux 目标为 glibc 环境；Intel Mac、Windows ARM 和 Alpine 暂未列入支持范围。macOS 标签标识内置程序的最低系统要求，Python 和第三方依赖可能有更高要求。跨平台运行结果以相应 CI 为准。

## 配置模型

默认读取用户目录下的 `~/.naiyue-test-agent/models.json`，Windows 对应 `%USERPROFILE%\.naiyue-test-agent\models.json`。自行创建目录和 JSON 文件，或启动时用 `--config` 指定已有配置。

下面是**配置格式示例**，将尖括号占位项替换为自己的值：

```json
{
  "schema_version": 1,
  "default_model_id": "main",
  "providers": {
    "gateway": {
      "kind": "openai_compatible",
      "base_url": "https://example.invalid/v1",
      "api_key": "<your-api-key>"
    }
  },
  "models": {
    "main": {
      "provider_id": "gateway",
      "model": "<your-model-name>",
      "api": "chat_completions"
    }
  }
}
```

OpenAI / 兼容接口的 `api` 可选 `chat_completions` 或 `responses`，应与实际端点能力一致。其他供应商、思考参数、缓存和工具预算的配置说明见仓库中的 [模型示例](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/examples/models.example.json) 与 [应用设置示例](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/examples/settings.example.toml)。

对于使用 `reasoning_content` 返回可见思考的兼容网关，可在对应模型的 `profile` 中声明 `"reasoning_output": "reasoning_content"`；Responses 可在 `request` 中配置 `"reasoning_summary": "auto"`（或 `concise` / `detailed`）。未配置时不额外请求思考摘要；模型没有返回可见思考时，界面不显示该区域。重新生成会重新执行最后一次任务及其工具，工作区文件不会回滚。

模型凭证由用户保管，不随包提供。使用远程模型时，发送给模型的消息和工具结果会交给所配置的供应商；本地应用不代表模型也在本地运行。

## 启动 Web

在需要操作的项目目录执行：

```bash
nyt
```

终端会输出本地访问链接，在浏览器中打开即可。默认绑定回环地址并自动选择端口。访问链接含本次服务的访问凭证，请按私有链接保管。

```bash
nyt --workspace /path/to/project
nyt --config /path/to/models.json
nyt --port 8080
nyt --help
```

`--port` 为显式覆盖示例；默认端口不固定。`Ctrl+C` 请求退出并等待必要收尾。

## 使用终端 CLI

```bash
nyt chat
nyt chat --workspace /path/to/project
nyt chat --resume
nyt chat --resume <session-id>
nyt chat --no-session
nyt chat --help
```

常用交互命令：`:quit` 退出、`:stop` 停止、`:approve` / `:deny` 处理审批。完整交互说明以终端提示为准。

`0.2.0` 支持只读查询：`:usage` 查看当前会话累计模型用量和调用耗时，`:diagnostics` 列最近运行，`:diagnostics <run_id>` 查看模型、工具和 HTTP 尝试明细；有后续页时会打印下一页命令。Web 在原上下文侧栏的“会话累计”和“运行诊断”查看同一批已保存事实。临时会话 `--no-session` 无持久化查询；诊断只在有限保留范围内可用。口径与入口见[调用观测说明](docs/web-chat.md#调用观测与只读查询)。

Web 与终端各自启动应用实例，同一工作区数据库由一个实例持有。切换入口前先关闭正在占用该数据库的服务。

离线查看会话库备份：`nyt storage backups --workspace /path/to/project`；使用自定义数据库时追加 `--session-db /path/to/sessions.sqlite`。命令输出 JSON，区分有效、无效和未完成备份；只读取已有工作区身份，不创建新库，当前库丢失或损坏也能查询。默认定位只读取设置的 `storage` 段；显式指定数据库时跳过设置读取，不依赖模型/MCP配置。当前源码在已有库需要迁移时自动生成并验证整库备份，新库和当前版本库不会重复备份；备份失败停止迁移，迁移失败保留原始副本和已提交步骤，终端显示失败阶段与版本。

显式还原：先停止持有该库的服务，再运行 `nyt storage restore BACKUP_ID --workspace /path/to/project`（自定义数据库同样追加 `--session-db`）。命令显示选定目标和备份事实，必须输入完整备份ID确认；无跳过确认的参数。还原舍弃备份之后的数据库变更，原主库及wal/shm/journal逐字节保留在 `<数据库名>.maintenance/<operation_id>/original/`，完成后末行输出JSON结果。没有已有工作区身份、备份不属于该工作区、文件无法安全核对或库仍被占用时拒绝执行。命令不启动Agent、不运行迁移，也不撤销库外文件和网络副作用。

中断后Web/chat/SDK会阻止启动；保留现场，重复同一还原命令并再次输入完整ID，核对候选和原文件组后继续收尾。无法核对的标记或冲突文件保持阻断，不要自行删除维护标记绕过检查。成功只表示快照已安装并验证；随后应启动兼容版本/修复版，同一存在缺陷的程序仍可能重试原迁移。源码与本机验收细节见[Spec123](docs/specs/123-storage-backup-migration-and-restore.md)。

## 工具、权限与数据

有工作区时默认启用文件工具。Bash / PowerShell 需要启用，并受权限规则及审批控制；解释器需由操作系统环境提供。命令使用当前用户的宿主文件和网络权限，当前不提供沙箱隔离。

会话、checkpoint、聊天归档和用量等保存在工作区关联的用户数据目录中，默认位于 `~/.naiyue-test-agent/data/`。模型上下文压缩保留独立聊天归档。安装或升级应用不迁移或清理这些数据。

持久会话正常任务收尾时，默认按阈值修剪不再使用的旧 checkpoint，保留续聊、最后任务重生成、归档同步和恢复所需状态；聊天原文、工具结果和累计用量继续保留。默认单 thread 达到 128 份时检查，每次根任务收尾最多删除 128 份、32 MiB 编码载荷。待审批、未确认状态或无法验证的依赖会跳过；这不是整个历史库的容量上限，也不保证数据库文件立即缩小。旧 checkpoint 不再充当完整聊天档案的备份，损坏或丢失归档需依靠有效备份恢复。

可在应用设置中配置以下可选部分，修改后重启生效；无此部分使用上述默认值，非法设置会告警并禁用修剪。正常任务完成前会增加少量维护耗时，本机合成性能数据与限制见[修剪说明](docs/specs/124-checkpoint-retention.md)。

```toml
[checkpoint_retention]
enabled = true
trigger_checkpoints = 128
max_delete_checkpoints = 128
max_delete_bytes = 33554432
```

升级前关闭应用，再执行：

```bash
python -m pip install --upgrade naiyue-test-agent
```

## 开发与发行

从源码开发时安装 `requirements.txt` 和 `requirements-web.txt`，并在 PATH 中提供 `rg` / `fd`。前端开发使用仓库 `frontend/` 下的 npm 配置。源码入口仍支持 `python -m naiyue_test_agent.web` 和 `python -m naiyue_test_agent.application.cli`。

发布维护、四平台构建和 PyPI 首次授权步骤见 [发行指南](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/releasing.md)。项目设计与开发记录保留在 GitHub 仓库，不进入 wheel。

## 许可证

本项目使用 MIT。发行包内的第三方软件保留各自许可证和版权声明。
