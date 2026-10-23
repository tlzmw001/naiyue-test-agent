# Naiyue Test Agent

本地运行的通用编程与测试 Agent，提供浏览器界面和终端交互。通过配置的模型、项目提示词、Skills 和工具读取项目、修改文件、执行命令，并保留会话历史。

- 本地 Web：流式 Markdown、工具执行过程、权限审批、会话管理和历史滚动加载；首任务自动标题、文件差异预览/patch 下载、可见思考和最后任务重新生成。
- 终端 CLI：多轮对话、恢复历史、审批、停止、Steering 和 Follow-up。
- 模型接入：OpenAI、兼容 OpenAI 的接口、Anthropic、Ollama。
- 工具：Read、Ls、Find、Grep、Write、Edit、Bash、PowerShell，以及一层前台子 Agent。
- `0.1.1` 新增 `aitest_kit`、百度/Tavily 回退的 `web_search` 和 `web_fetch`，配置见[工具说明](docs/aitest-web-tools.md)。
- `0.2.0` 统一模型请求准备与上下文预算，补充调用次数、耗时和 SDK HTTP 尝试诊断；支持分离配置代理地址、用户名/密码、直连名单及新会话默认工具，见[设置示例](docs/examples/settings.example.toml)。
- `0.3.0` 新增 Bash/PowerShell 后台任务及统一辅助侧栏，支持按既有子 Agent 实例续跑；同时交付外部工具有限重试、CIDR/通配直连规则、迁移前备份、显式还原及任务收尾 checkpoint 修剪。使用说明见[0.3.0功能与配置指南](docs/0.3.0功能与配置指南.md)和[全量设置示例](docs/examples/settings.example.toml)，范围见[后台任务](docs/specs/125-background-shell-tasks.md)和[子实例续跑](docs/specs/126-subagent-continuation.md)。
- Find/Grep 使用安装包内的 `fd` / `rg`；前端已经构建，使用发行包无需 Node.js 或 npm。
- `0.4.0` 新增 [history历史查询](docs/history-tool.md)和[memory记忆查询与维护](docs/memory-tool.md)：同目录会话原文、项目/个人记忆、来源校验、候选转正、到期及遗忘；完整用户轮保存后可在后台整理概览与记忆，新轮有界注入偏好/相关条目及首次近期会话索引。复用工具启停、权限和后台任务查看/停止，不需要embedding模型；同时迁出应用输出归档并修复单次模型输出上限，范围和效果边界见[0.4.0版本说明](docs/releases/0.4.0.md)。
- `0.3.1` 修复 Windows 下稳定文件在 Read 后 Edit 误报冲突的问题，删除 Edit 两处重复路径解析并集中元数据比较规则；保留内容冲突与原子替换保护，安装验证增加实际 Read → Edit → Read 链路。
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

`0.2.0` 支持只读查询：`:usage` 查看当前会话累计模型用量和调用耗时，`:diagnostics` 列最近运行，`:diagnostics <run_id>` 查看模型、工具和 HTTP 尝试明细；有后续页时会打印下一页命令。Web 在原上下文侧栏的“会话累计”和“运行诊断”查看同一批已保存事实。当前开发分支的临时会话 `--no-session` 也可查询本实例的用量与诊断，聊天档案、回复边界和检查点共用独立 RAM SQLite；关闭即丢弃，不创建项目身份或聊天数据库，不开放项目 history/memory。工具输出仍可能按下文规则写入外部归档。诊断只在有限保留范围内可用。口径与入口见[调用观测说明](docs/web-chat.md#调用观测与只读查询)。

Web 与终端各自启动应用实例，同一工作区数据库由一个实例持有。切换入口前先关闭正在占用该数据库的服务。

离线查看会话库备份：`nyt storage backups --workspace /path/to/project`；使用自定义数据库时追加 `--session-db /path/to/sessions.sqlite`。命令输出 JSON，区分有效、无效和未完成备份；只读取已有工作区身份，不创建新库，当前库丢失或损坏也能查询。默认定位只读取设置的 `storage` 段；显式指定数据库时跳过设置读取，不依赖模型/MCP配置。当前源码在已有库需要迁移时自动生成并验证整库备份，新库和当前版本库不会重复备份；备份失败停止迁移，迁移失败保留原始副本和已提交步骤，终端显示失败阶段与版本。

显式还原：先停止持有该库的服务，再运行 `nyt storage restore BACKUP_ID --workspace /path/to/project`（自定义数据库同样追加 `--session-db`）。命令显示选定目标和备份事实，必须输入完整备份ID确认；无跳过确认的参数。还原舍弃备份之后的数据库变更，原主库及wal/shm/journal逐字节保留在 `<数据库名>.maintenance/<operation_id>/original/`，完成后末行输出JSON结果。没有已有工作区身份、备份不属于该工作区、文件无法安全核对或库仍被占用时拒绝执行。命令不启动Agent、不运行迁移，也不撤销库外文件和网络副作用。

中断后Web/chat/SDK会阻止启动；保留现场，重复同一还原命令并再次输入完整ID，核对候选和原文件组后继续收尾。无法核对的标记或冲突文件保持阻断，不要自行删除维护标记绕过检查。成功只表示快照已安装并验证；随后应启动兼容版本/修复版，同一存在缺陷的程序仍可能重试原迁移。源码与本机验收细节见[Spec123](docs/specs/123-storage-backup-migration-and-restore.md)。

## 工具、权限与数据

有工作区时默认启用文件工具。Bash / PowerShell 需要启用，并受权限规则及审批控制；解释器需由操作系统环境提供。命令使用当前用户的宿主文件和网络权限，当前不提供沙箱隔离。

Bash/PowerShell可在应用设置中配置执行总上限（秒）；未配或`0`仍不限时。模型调用的`timeout`省略/null/0使用该配置，正整数只能缩短上限；配置非法会阻止启动。Find/Grep、AITest和网络工具继续使用各自期限。当前源码已支持主Agent显式设置background=true跨轮执行，通过task_list/task_output/task_stop查询与停止；应用退出时停止受管命令。后台任务列表保留在本次应用内存中，重启不接管旧进程；主Agent前台命令默认等待120秒后自动转后台，执行总期限不重置；已结束后台任务的简短状态在下一次用户输入时进入主Agent系统提示词，单批条数/完整提示UTF-8字节由max_notice_items/max_notice_bytes配置（默认20/8192，严格正整数）；成功接受模型响应后确认，失败保留，不自动唤醒模型。

独立管理入口见[Spec125-05](docs/specs/125-05-background-management.md)：CLI输入`:tasks`分页查看本应用任务，`:tasks before`继续下一页，`:task output ID`查看输出，`:task stop ID`停止；这些命令在Agent运行中也可用。Web顶部的侧栏图标统一打开“子智能体／后台任务／上下文”三个页签；输入框旁的上下文图标仍可直接跳到上下文页签。任务页签显示当前工作区的活动数、所属会话、状态、输出尾部与归档引用，并提供停止；图标再次点击、关闭按钮或Esc均可收起。切换聊天保留面板，子Agent详情按新会话重新定位；切页签保留已挂载的内容选择。页面可见时每3秒刷新有限任务元数据，任务页签可见且选中任务时才读尾部，隐藏页面暂停读取。完成只更新界面提示；查看不会消费下一轮模型通知，也不会启动新推理。清理未确认保留“需核对”，读取失败显示未知并允许刷新。[06组合验收与性能范围](docs/specs/125-06-background-acceptance.md)区分本机运行、跨平台构建和真实模型效果。

当前源码的[子Agent续跑](docs/specs/126-subagent-continuation.md)已接入原`subagent`工具：主Agent省略`agent_id`创建干净实例，传返回的完整ID继续同会话中成功且保存确认的实例。结果包含ID与报告，Web工具卡显示正文；`max_model_calls`仍限制本次委派，后续委派重新计数，同次审批恢复共用额度。[生命周期第二片](docs/specs/126-02-subagent-continuation-lifecycle.md)补齐正常重开、父/子压缩、重新生成退役及修剪/备份还原一致性；恢复不重放旧子工具，不把未确认实例推断为可续跑。[查找与展示第三片](docs/specs/126-03-subagent-discovery-delivery.md)将新建、继续与查询合并为一个“子智能体”开关：启用subagent即配套开放只读subagent_list，旧会话不改库；模型按需查询，不自动读取列表。每页条数/完整JSON字节/任务预览长度可通过tools.subagent配置，默认20/8192/160。CLI/Web按稳定实例区分当前委派与审批恢复，侧栏显示可继续候选或原因；停止绑定当前子执行/审批，已有历史继续保留。[标准安装验收](docs/specs/126-04-subagent-acceptance.md)使用共享固定模型场景，核对同ID三次委派、关闭重开后的历史、每次调用限额和真实文件写入次数。用户直聊不做；这些源码变更尚未发布。

```toml
[shell_tasks]
max_execution_seconds = 0
foreground_wait_seconds = 120
max_background_archive_bytes = 67108864
max_notice_items = 20
max_notice_bytes = 8192
```

从`0.4.0`起，Shell/AITest/MCP的新受管输出归档位于配置的`data_dir/workspaces/<项目UUID>/artifacts/naiyue-agent-output/`；`--no-session`不创建项目身份、不开放项目history/memory，其归档使用`data_dir/sessions/<session_id>/artifacts/naiyue-agent-output/`。归档实际位置必须在执行目录外，返回绝对路径；旧工作区内的文件不自动搬迁。后续Read仍受原权限检查，后台task_output则按已登记任务读取。每次调用分别受自身额度约束；已移除工作区累计归档额度，旧`tool_output.max_workspace_archive_bytes`告警并忽略。日志不自动删除，手动清理仍需预览并确认。前台归档触限只停止归档，后台任务触限会停止自身。存在活动后台任务或未确认的收尾时，拒绝删除所属会话或清理输出；停止后才能继续。任务完成不自动发起模型请求，用户下一轮可按任务ID查询。详见[0.4.0版本说明](docs/releases/0.4.0.md)。

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

从源码开发时安装 `requirements.txt` 和 `requirements-web.txt`，并在 PATH 中提供 `rg` / `fd`。前端开发使用仓库 `frontend/` 下的 npm 配置。源码入口支持 `python -m naiyue_test_agent.web`、`python -m naiyue_test_agent.cli` 和 `python -m naiyue_test_agent.cli chat`；旧命令 `python -m naiyue_test_agent.application.cli` 保留为兼容启动入口。应用按会话、对话、子 Agent、观测、记忆分包，CLI 与 Web 是并列适配器，数据库维护集中在顶层 storage 包，详见[当前目录与入口对应表](docs/specs/128-03-domain-packages-and-cli.md)。存储由 `Database` 管理连接与事务，`ProjectStore` 组合领域仓储，run 必要保存由单个工作单元提交；项目库与个人记忆库共用 `MemoryRepo`，详见[存储职责与事务边界](docs/specs/128-04-database-repositories-and-uow.md)。

发布维护、四平台构建和 PyPI 首次授权步骤见 [发行指南](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/releasing.md)。项目设计与开发记录保留在 GitHub 仓库，不进入 wheel。

## 许可证

本项目使用 MIT。发行包内的第三方软件保留各自许可证和版权声明。
