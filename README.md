# Naiyue Test Agent

本地运行的通用编程与测试 Agent，提供浏览器界面和终端交互。通过配置的模型、项目提示词、Skills 和工具读取项目、修改文件、执行命令，并保留会话历史。

## 主要功能

- **Web 与终端**：流式回复、工具执行过程、权限审批、停止、引导和跟进消息。
- **多项目与会话**：在同一界面添加和切换项目，各项目保留独立会话与草稿；Web 和 CLI 共用本机服务。
- **图片输入**：粘贴截图、拖入或选择图片，支持多图、仅图发送及历史预览；需要模型明确支持图片输入。
- **文件与命令工具**：Read、Ls、Find、Grep、Write、Edit、Bash、PowerShell，支持跨轮后台命令及输出查看、停止。
- **子 Agent**：委派任务，并按需沿已有子实例的上下文继续工作。
- **历史与记忆**：查询项目会话历史，维护项目和个人记忆；支持会话保存、迁移前备份与显式还原。
- **外部集成**：MCP、网页搜索与抓取、AITest 工具，以及项目 Skills。
- **可选 Docker 沙箱**：按会话选择环境模板，隔离文件和进程执行；取回结果、审查差异后再确认交付到本机。

0.5.2 的更新与升级说明见[版本说明](https://github.com/tlzmw001/naiyue-test-agent/blob/v0.5.2/docs/releases/0.5.2.md)。

远端沙箱支持直接 SSH 连接 Linux Docker，Windows 可复用 OpenSSH 和跳板机配置，无需安装 Docker CLI。当前使用预先准备的 key/ssh-agent；公司原机验收及普通密码后台认证尚未完成，准备步骤见[远端 SSH 使用说明](docs/remote-docker-ssh.md)。Agent 0.5.2 可复用 [0.5.1 Release](https://github.com/tlzmw001/naiyue-test-agent/releases/tag/v0.5.1) 的 Linux x64 执行端离线镜像。

## 安装

需要 **Python 3.12.4 或更高版本**。支持 macOS Apple Silicon、Linux x86_64 / ARM64（glibc）和 Windows x64。

建议使用独立虚拟环境：

```bash
python -m venv .venv
```

- macOS / Linux：`source .venv/bin/activate`
- Windows PowerShell：`.\.venv\Scripts\Activate.ps1`

```bash
python -m pip install --upgrade pip
python -m pip install naiyue-test-agent
nyt --version
```

发行包已包含构建后的前端及 Find/Grep 使用的 `fd` / `rg`，使用时无需 Node.js、npm 或 Rust。纯本机使用无需 Docker；沙箱模式需另行准备可用的 Docker daemon 和兼容镜像。

## 配置模型

默认配置文件为 `~/.naiyue-test-agent/models.json`；Windows 对应 `%USERPROFILE%\.naiyue-test-agent\models.json`。也可以用 `--config` 指定文件。

以下为兼容 OpenAI 接口的配置示例，请替换地址、密钥和模型名称：

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

支持 OpenAI、兼容 OpenAI 的接口、Anthropic 和 Ollama。OpenAI / 兼容接口的 `api` 可选 `chat_completions` 或 `responses`，须与服务能力一致。

思考、视觉能力、工具参数流式及超时配置见[模型配置示例](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/examples/models.example.json)；工具、权限、代理、后台任务、图片额度和沙箱模板见[应用设置示例](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/examples/settings.example.toml)。凭证由用户自行配置，不随安装包提供。

## 启动 Web

```bash
nyt
```

在浏览器中打开终端输出的本地链接。首次使用项目时按提示确认目录与信任，也可以在界面中选择已有文件夹或创建新文件夹。新建会话时，在输入框上沿选择项目及执行环境。

```bash
nyt --workspace /path/to/project
nyt --config /path/to/models.json
nyt --settings /path/to/settings.toml
nyt --help
```

默认监听回环地址并自动选择端口。访问链接包含服务凭证，请妥善保管。关闭网页或启动器不会结束共享服务和持久任务；同一会话同一时间只接受一个运行。

共享服务在后台运行。正常退出服务、查看服务端警告和错误日志：

```bash
nyt service stop
nyt service logs
nyt service logs --follow
```

`stop` 会请求当前配置对应的服务正常收尾；若未运行服务，不会为了退出而启动新服务。查看日志时按 Ctrl+C 只退出日志查看，不会停止服务。默认日志文件为 `~/.naiyue-test-agent/data/service/service.log`；若启动时使用了 `--settings` 或 `--config`，停服时也传入相同路径。日志命令使用相同的 `--settings` 定位对应数据目录的 `service/service.log`。浏览器自身的错误仍在浏览器开发者工具的 Console 中查看。

## 使用终端 CLI

```bash
nyt chat
nyt chat --workspace /path/to/project
nyt projects
nyt chat --project <project-uuid>
nyt chat --resume
nyt chat --help
```

常用交互命令：`:quit` 退出、`:stop` 停止、`:approve` / `:deny` 处理审批、`:tasks` 查看后台任务。更多命令以终端帮助和提示为准。

## 权限与数据

本机工具以当前用户权限执行，Shell 和外部工具受工具开关、权限规则及审批控制。Docker 沙箱的文件和进程在独立环境执行，MCP 等外部集成仍按各自配置工作。沙箱结果须经审查和确认才交付到本机，销毁环境也需单独确认。

会话和记忆保存在本机；使用远程模型时，消息、图片和工具结果会发送给所配置的供应商。重新生成会再次执行任务及其工具，已有文件修改不会自动回滚。升级前请先正常结束旧版服务，具体步骤见 0.5.0 版本说明。

## 使用文档

- [图片输入与额度](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/composer-images.md)
- [历史查询](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/history-tool.md)
- [记忆查询与维护](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/memory-tool.md)
- [MCP 客户端配置](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/mcp-client.md)
- [网页搜索、抓取与 AITest](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/aitest-web-tools.md)

## 开发与发行

源码开发需安装 `requirements.txt` 和 `requirements-web.txt`，并在 PATH 中提供 `rg` / `fd`。前端使用 `frontend/` 下的 npm 配置。

完整测试、四平台构建、安装验证和标签发布流程见[发行指南](https://github.com/tlzmw001/naiyue-test-agent/blob/main/docs/releasing.md)。

## 许可证

[MIT](https://github.com/tlzmw001/naiyue-test-agent/blob/main/LICENSE)。
