# Naiyue Test Agent

本地运行的通用编程与测试 Agent，提供浏览器界面和终端交互。通过配置的模型、项目提示词、Skills 和工具读取项目、修改文件、执行命令，并保留会话历史。

- 本地 Web：流式 Markdown、工具执行过程、权限审批、会话管理和历史滚动加载；首任务自动标题、文件差异预览/patch 下载、可见思考和最后任务重新生成。
- 终端 CLI：多轮对话、恢复历史、审批、停止、Steering 和 Follow-up。
- 模型接入：OpenAI、兼容 OpenAI 的接口、Anthropic、Ollama。
- 工具：Read、Ls、Find、Grep、Write、Edit、Bash、PowerShell，以及一层前台子 Agent。
- `0.1.1` 新增 `aitest_kit`、百度/Tavily 回退的 `web_search` 和 `web_fetch`，配置见[工具说明](docs/aitest-web-tools.md)。
- Find/Grep 使用安装包内的 `fd` / `rg`；前端已经构建，使用发行包无需 Node.js 或 npm。

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

首版发行目标：macOS Apple Silicon / Intel、Linux x86_64 / ARM64、Windows x64。每个平台分别提供 wheel，pip 自动选择。Linux 目标为 glibc 环境；Windows ARM 和 Alpine 暂未列入支持范围。macOS 标签标识内置程序的最低系统要求，Python 和第三方依赖可能有更高要求。跨平台运行结果以相应 CI 为准。

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

Web 与终端各自启动应用实例，同一工作区数据库由一个实例持有。切换入口前先关闭正在占用该数据库的服务。

## 工具、权限与数据

有工作区时默认启用文件工具。Bash / PowerShell 需要启用，并受权限规则及审批控制；解释器需由操作系统环境提供。命令使用当前用户的宿主文件和网络权限，当前不提供沙箱隔离。

会话、checkpoint、聊天归档和用量等保存在工作区关联的用户数据目录中，默认位于 `~/.naiyue-test-agent/data/`。模型上下文压缩保留独立聊天归档。安装或升级应用不迁移或清理这些数据。

升级前关闭应用，再执行：

```bash
python -m pip install --upgrade naiyue-test-agent
```

## 开发与发行

从源码开发时安装 `requirements.txt` 和 `requirements-web.txt`，并在 PATH 中提供 `rg` / `fd`。前端开发使用仓库 `frontend/` 下的 npm 配置。源码入口仍支持 `python -m naiyue_test_agent.web` 和 `python -m naiyue_test_agent.application.cli`。

发布维护、五平台构建和 PyPI 首次授权步骤见 [发行指南](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/releasing.md)。项目设计与开发记录保留在 GitHub 仓库，不进入 wheel。

## 许可证

本项目使用 MIT。发行包内的第三方软件保留各自许可证和版权声明。
