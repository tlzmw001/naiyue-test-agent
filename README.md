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
- 当前开发分支已提供 Docker/envd 文件与进程后端：每个沙箱独立容器、代码卷和状态卷，支持 `external` / `none` 网络、环境生命周期、后台任务与有界输出恢复。Git 输入可通过应用内部 API 准备不可变快照并传入沙箱，不改用户的 index/HEAD；未跟踪文件须明确选择，忽略文件首版不传，选定的已知凭据明确拒绝，不承诺清理 Git 历史。丢失回执时按原操作 ID 查询，结果未知时不自动重放；常驻服务正常关闭确认停止进程和原容器，保留卷和文件。纯本机使用无需 Docker。Web新会话可选择此计算机或Docker沙箱；已建会话固定原环境，历史可打开但失效环境不能执行。GitSource项目无伪造的本机目录，执行前须显式信任。现已支持显式取回沙箱结果，在本机校验 bundle 并审查输入快照到结果的差异，保留用户工作文件/HEAD/暂存区；未跟踪输出仍需明确选择。Web 在沙箱会话输入框上沿的“查看改动”中操作，原请求断线后只核对同一结果，已取回结果在销毁沙箱后仍可审查；现在可在审查后单独确认合入当前本地分支、创建本机新分支（不切换分支）或丢弃应用任务引用。未知交付只核对原操作，不自动重放；远端推送、PR和其他发布操作由用户自行处理，应用不提供这些入口；配置与验证边界见 [R1-10 Spec](docs/specs/R1-10-docker-file-environment.md)、[R1-11 Spec](docs/specs/R1-11-sandbox-process-recovery.md) 、[R1-12A Spec](docs/specs/R1-12A-git-input-snapshots.md) 、[R1-12B Spec](docs/specs/R1-12B-sandbox-sessions.md) 、[R1-13A Spec](docs/specs/R1-13A-sandbox-results.md) 和 [R1-13B Spec](docs/specs/R1-13B-controlled-delivery.md)。
- `0.4.0` 新增 [history历史查询](docs/history-tool.md)和[memory记忆查询与维护](docs/memory-tool.md)：同目录会话原文、项目/个人记忆、来源校验、候选转正、到期及遗忘；完整用户轮保存后可在后台整理概览与记忆，新轮有界注入偏好/相关条目及首次近期会话索引。复用工具启停、权限和后台任务查看/停止，不需要embedding模型；同时迁出应用输出归档并修复单次模型输出上限，范围和效果边界见[0.4.0版本说明](docs/releases/0.4.0.md)。
- 开发分支 [R1-13D-01](docs/specs/R1-13D-01-environment-profile-contracts.md) 支持在 `sandbox.profiles` 配置多套已准备镜像，由 `default_profile` 选择新沙箱会话的默认模板；旧 `sandbox.image` 仍可单独使用。[R1-13D-02](docs/specs/R1-13D-02-environment-refresh-selection.md) 已接服务端显式选择和热更新：认证的 `POST /api/v1/environments/refresh` 从服务启动时固定的配置文件只读重载 sandbox，成功替换列表，失败保留旧版，不需要重启。`execution-options` 返回 ID、名称、指纹；新建沙箱会话可提交 `profile_id` 与 `expected_profile_revision`，配置变化后须重新确认。会话先保存完整选择快照，原请求重试和恢复继续使用原镜像与预算，模板删除也不换绑。[R1-13D-03](docs/specs/R1-13D-03-environment-composer.md) 已接输入框上方的环境列表、刷新和首次发送准备：选择环境及权限/工具/推理设置时只保留草稿，首次发送才创建所选环境、确认设置并开始运行；失败保留文字和原请求。已有会话显示原保存环境名称。容器额度与空闲管理已由第四片接入；纯本机使用无需配置模板。
- `0.3.1` 修复 Windows 下稳定文件在 Read 后 Edit 误报冲突的问题，删除 Edit 两处重复路径解析并集中元数据比较规则；保留内容冲突与原子替换保护，安装验证增加实际 Read → Edit → Read 链路。
- `0.2.0` 支持 `nyt mcp list` 只读发现及根/子 Agent 调用本地/远程 MCP 工具，复用活动工具选择、审批与会话连接，支持按服务显式开启空闲回收；应用运行中可刷新配置以新增服务、调整名单和启停，CLI/Web显示来源、结果状态和归档位置，重启保留历史、旧 MCP 待审需取消后重新发起，见[MCP客户端配置](docs/mcp-client.md)。

开发分支 [R1-13D-04](docs/specs/R1-13D-04-sandbox-lifecycle.md) 已接沙箱资源管理：输入框上沿“管理沙箱”可查看全部项目的登记状态、启动/停止/核对原资源，明确确认后销毁原容器、代码卷和状态卷。`sandbox.lifecycle` 默认运行占用 2、保留数量 10、空闲 30 分钟停止、每 30 秒检查；未知状态保守占用额度。活动任务、待审批和未确认后台操作受保护；停止保留文件，正常关闭常驻服务会在运行和进程排空后停止原容器，关闭客户端不触发该行为。生命周期策略随环境配置热更新，降额不杀已有任务；数量上限不等于磁盘字节上限，不会自动按时间删除文件。R1-11 的退出行为由本片替换；[R1-13D-05](docs/specs/R1-13D-05-real-docker-acceptance.md)已用本机真实 Docker 和两套镜像完成组合验收，含隔离、热更新、额度、后台/审批保护、退出重开、原操作核对和清理。该证据使用离线固定模型，不代表真实模型效果或公司远端部署。

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

左侧先搜索、再新建会话，普通对话与各项目作为同级分组排列；会话只能在所属范围内置顶和手动排序，置顶行左侧显示图钉，不支持跨项目拖动。会话标题下显示已知的本机/沙箱绑定与任务状态；当前会话的运行和审批按实时事实更新，其他展开范围按保存的目录信息展示，不为显示状态启动环境。新建会话时，在输入框上沿选择项目和执行位置（此计算机 / Docker 沙箱）；添加项目也从这里进入，侧栏不再单独提供入口。项目入口可以选择已有文件夹或创建新文件夹；默认位置不限制可选择的目录。选择沙箱时，额外未跟踪文件默认不选，输入、容器和安装沿同一创建请求核对，失败后保留会话和状态。已有会话不会因选择其他位置换绑，更换位置会新建会话。沙箱会话可直接点击“查看改动”，显式取回结果、逐文件审查差异，再确认合入当前本地分支、创建本机分支或丢弃任务引用。任务运行中可审查已保存差异，取回和交付需等待运行结束；文件不会自动写回本机。销毁环境仍需单独确认，历史与已取回结果保留。会话历史、项目记忆和个人记忆保存在控制端；当前 R1 的 Agent 推进仍依赖本机服务。

终端新建：`nyt chat --workspace /path/to/project --execution sandbox --environment python-tests --include-untracked notes.txt`；`--environment` 选择配置模板，省略则使用当前默认；可重复 `--include-untracked` 选择文件。CLI从服务取得选中模板的指纹再创建，配置变化明确拒绝，不自动改选。GitSource 使用 `--project UUID --execution sandbox`，必要时另加明确的 `--trust-project`。`--resume` 按原绑定恢复，不能同时用 `--execution` / `--environment` 换绑；`--no-session` 不支持沙箱。Docker 需预先配置镜像及可用的 daemon/context；纯本机使用不受影响。Web可在输入框上方选择本机或已配置模板，并在列表内刷新配置；选完可继续编辑消息，首次发送才自动创建独立容器和传入项目快照，准备成功后执行任务。可以预先在镜像中安装依赖，不要求每次手动登录容器初始化；应用不会自动识别并预装所有项目依赖。

`--port` 为显式覆盖示例；默认端口不固定。`nyt` 连接或拉起共享服务，输出私有访问链接后退出；关闭启动终端或页面不会关闭服务和持久任务。两个终端可分别启动并打开页面，与 `nyt chat` 共用同一实例。新项目先在终端核对目录并输入 `trust`；非交互启动须显式指定 `--trust-project`。已有项目可用 `--project UUID` 选择，应用配置路径用 `--settings` 指定。旧版开发用 `NAIYUE_WEB_TOKEN` 不再覆盖服务凭证，请使用本次输出的完整私有链接。

## 使用终端 CLI

```bash
nyt chat
nyt chat --workspace /path/to/project
nyt projects
nyt chat --project <project-uuid>
nyt chat --resume
nyt chat --resume <session-id>
nyt chat --no-session
nyt chat --help
```

常用交互命令：`:quit` 退出、`:stop` 停止、`:approve` / `:deny` 处理审批。完整交互说明以终端提示为准。

`0.2.0` 支持只读查询：`:usage` 查看当前会话累计模型用量和调用耗时，`:diagnostics` 列最近运行，`:diagnostics <run_id>` 查看模型、工具和 HTTP 尝试明细；有后续页时会打印下一页命令。Web 在原上下文侧栏的“会话累计”和“运行诊断”查看同一批已保存事实。当前开发分支的临时会话 `--no-session` 也可查询本实例的用量与诊断，聊天档案、回复边界和检查点共用独立 RAM SQLite；关闭即丢弃，不创建项目身份或聊天数据库，不开放项目 history/memory。工具输出仍可能按下文规则写入外部归档。诊断只在有限保留范围内可用。口径与入口见[调用观测说明](docs/web-chat.md#调用观测与只读查询)。

当前开发分支的 `nyt chat` 连接或自动拉起同一操作系统用户、同一数据目录的共享服务。多个终端可同时使用；同会话只接受一个运行，其他终端可查看和停止它。持久终端退出只断开客户端，运行继续；`--no-session` 的专用租约断开会停止临时工作并销毁 RAM 聊天，不开放项目 history/memory。新项目需确认真实目录，非交互使用显式 `--trust-project`；`nyt projects` 列项目 UUID，`--project` 选择它，配置可用 `--config`/`--settings` 指定。连接失败不会自动重发执行请求。详见 [R1-06 合同](docs/specs/R1-06-cli-service-client.md)。默认 `nyt` 的共享接入已在 [R1-07A](docs/specs/R1-07A-web-service-client.md) 接线，页面固定连接该服务实例，并在 [R1-07B 项目侧栏](docs/specs/R1-07B-project-navigation.md) 中切换普通对话与已登记项目。每个项目保留独立会话和草稿，离开只断开观察；目录元数据不会加载所有项目的运行图。[R1-07C 文件夹入口](docs/specs/R1-07C-project-folders.md) 提供本机目录浏览、选择已有文件夹和创建新文件夹，确认真实目录与信任后添加项目；默认位置不限制其他目录。未登记的旧版身份继续使用正式导入流程，目录界面不会覆盖旧身份。显式旧库遇到目录身份冲突时仅开放历史档案，不重新登记或执行。

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

当前 R1-02 将 SQL/schema 按领域收拢到 `storage/conversation_sql`、`storage/memory_sql`、`storage/observability_sql` 与 `storage/subagent_sql.py`；应用层通过仓储事务接口装配记忆批次和写入，存储层独立导入不加载应用层。当前目录、事务主链和验证见 [R1-02](docs/specs/R1-02-storage-dependency-direction.md)。

当前开发分支的 [R1-07D](docs/specs/R1-07D-admission-and-project-local.md) 已替换旧默认宿主工厂：由 `open_service_application` 创建根应用，通过 `app.projects.open(project_id)`、`app.chats.open()` 或 `app.temporary.open(identity)` 取得明确宿主，再使用其会话/运行等子 API。注册项目会话与子 Agent 引用项目本机绑定；用户显式重新定位并确认信任后，旧对话继续在新位置执行，原路径只作审计。首轮模型提示旧路径失效，旧修订的待审许可需取消后重新请求。目录失效时历史可读，执行明确拒绝，不回退其他目录。本机环境仍使用当前用户权限，不提供沙箱保证。

当前开发分支以一个 `Application` 组合 `sessions`、`runs`、`history`、`tasks` 等领域 API，磁盘和临时存储共用这些接口。终端通过明确的 HTTP 请求和 SSE 订阅调用共享服务，旧 `cli_async` 和 `app.call` 拥有者桥已被替换；应用不持有终端循环。Web 保留认证、归属、幂等和错误映射。原组合迁移的历史版本见[应用组合与调用边界](docs/specs/128-05b-composed-application-and-domain-apis.md)，当前终端合同见 [R1-06](docs/specs/R1-06-cli-service-client.md)。

当前 R1-03 已接入 `app.projects`，在独立注册表登记本机/Git 来源、导入旧 UUID、记录跨库操作进度；可选 `[projects].default_parent_dir` 只作新建默认位置，详见 [R1-03](docs/specs/R1-03-project-registry.md)。R1-04 接入 `await app.projects.open(project_id)` 和 `await app.chats.open()`：各宿主拥有独立根/子图、saver、会话与任务，应用共享模型、个人记忆库和根运行额度。普通对话保存在 `data_dir/chats/sessions.sqlite`，只开放个人记忆；新登记项目按注册表核验执行位置，不写 marker。详见 [R1-04 的资源、归属与关闭合同](docs/specs/R1-04-multi-project-runtime.md)。R1-05 已接入独立共享服务底座、认证握手、明确项目范围的 HTTP/SSE、有限幂等回执及协议客户端，见 [R1-05 服务合同](docs/specs/R1-05-single-service-and-protocol.md)。`nyt chat` 已在 06 迁为协议客户端；默认 `nyt` 的 Web 服务接入、目录选择器与项目/会话侧栏在 07。

会话执行由 `RunLifecycle`、`ControlQueue`、`ApprovalBook`、`SubagentRegistry` 组合，借用同一份 `SessionRuntime` 状态、当前 run 和短锁；具体行为与失败边界见[会话协作对象](docs/specs/128-06-session-collaborators.md)。

发布维护、四平台构建和 PyPI 首次授权步骤见 [发行指南](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/releasing.md)。项目设计与开发记录保留在 GitHub 仓库，不进入 wheel。

## 许可证

本项目使用 MIT。发行包内的第三方软件保留各自许可证和版权声明。
