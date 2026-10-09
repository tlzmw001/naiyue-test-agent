# Spec 004：本地模型配置文件格式

状态：2026-09-29 已实现本地文件加载、四种适配对象构造与现有 Agent 运行层接线。真实工具循环只验证当前 HeiyuCode 配置；OpenAI 官方、Claude 和 Ollama 未做真实模型调用。服务端存储和密钥管理以后按部署形态重新选型。

后续兼容扩展：[Spec 049](049-model-context-limits.md)在每个模型条目增加可选的 `context_window_tokens` 和 `max_output_tokens`，未填写时分别为 128000/16384；本文件以下示意仍是初始格式快照。

## 本次要解决的问题

原有 `naiyue_test_agent/agent_runtime.py` 的选模 middleware 接收上层注入的 `resolve_model(session_id)`，但没有模型目录或配置加载器。现在一个会话选中模型 ID 后，可以从本地配置找到供应商、实际模型名、端点和凭证，再构造 LangChain 的 `BaseChatModel`。同一供应商可能配置多个模型，因此供应商配置与模型条目分开。

## 文件位置与示意格式

约定本地文件为 `.env/models.json`。`.env/` 已由 Git 忽略。以下是**示意值**，不是已安装或已验证的模型配置；真实 key 不写入文档：

```json
{
  "schema_version": 1,
  "default_model_id": "heiyu-gpt-5.5",
  "providers": {
    "heiyu": {
      "kind": "openai_compatible",
      "base_url": "https://www.heiyucode.com",
      "api_key": "<在本地文件填写真实密钥>"
    },
    "openai": {
      "kind": "openai",
      "api_key": "<在本地文件填写真实密钥>"
    },
    "anthropic": {
      "kind": "anthropic",
      "api_key": "<在本地文件填写真实密钥>"
    },
    "local": {
      "kind": "ollama",
      "base_url": "http://127.0.0.1:11434"
    }
  },
  "models": {
    "heiyu-gpt-5.5": {
      "provider_id": "heiyu",
      "model": "gpt-5.5"
    },
    "openai-main": {
      "provider_id": "openai",
      "model": "<已开通的 OpenAI 模型名>"
    },
    "claude-main": {
      "provider_id": "anthropic",
      "model": "<已开通的 Claude 模型名>"
    },
    "local-main": {
      "provider_id": "local",
      "model": "<本机已下载且支持工具调用的模型名>"
    }
  }
}
```

示意中的尖括号值不能作为已配置模型直接运行。实际文件只写当前有凭证或本地模型的条目；不要求四种供应商同时具备运行条件。`default_model_id` 必须指向 `models` 中的一个有效条目。

### 字段规则

| 字段 | 规则 |
| --- | --- |
| `schema_version` | 必须为整数 `1`；未来格式变更显式迁移，不猜测旧格式。 |
| `providers` | 非空对象；键是本文件内唯一的 `provider_id`。同一供应商可以被多个模型引用。 |
| `kind` | 首期只接受 `openai`、`openai_compatible`、`anthropic`、`ollama`。前两者都用 `ChatOpenAI`，但兼容网关必须显式给 `base_url`。 |
| `api_key` | `openai`、`openai_compatible`、`anthropic` 必须为非空字符串；`ollama` 首期不需要。只存在本地忽略文件和进程内存，不输出到错误、日志或轨迹。 |
| `base_url` | `openai_compatible`、`ollama` 必填；`openai`、`anthropic` 首期使用各自 SDK 默认官方端点，不开放自定义 URL。 |
| `models` | 非空对象；键是用户可选的 `model_id`。每个条目必须有已存在的 `provider_id` 和非空的真实 `model` 名。 |
| `default_model_id` | 必填，指向一个模型 ID；仅在新会话未选模时使用。已选模会话的模型由应用层决定，不允许静默回退。 |

JSON 不允许注释或尾逗号。加载器应拒绝未知字段、重复键、缺失文件、无效 JSON、缺失引用、空 key 或非法 URL，错误指出文件/字段/模型 ID，但不包含密钥值。读取文件不执行其中任何命令或变量插值。当前本地单用户文件应限制为所有者读写（`0600`）；这只是本地权限边界，不是加密或 SaaS 密钥管理保证。

## 读取与接线的数据流

1. 调用 `build_configured_runtime_agent(config_path, ...)` 装配 Agent 时读取 `.env/models.json`，解析并校验为一份内存快照；失败则装配失败，不用硬编码默认模型继续运行。当前尚无正式应用启动入口。首期不做文件热重载；编辑后重新装配 Agent 或重启进程。
2. 例如会话选择 `claude-main`：应用层保存这个模型 ID；解析时从 `models["claude-main"]` 得到 `provider_id="anthropic"` 和具体 `model`，再从 `providers["anthropic"]` 得到 `kind` 与 `api_key`。
3. 模型接入层用 `ChatAnthropic` 构造 `BaseChatModel`；运行层原有 middleware 仍只接收 `(model_id, BaseChatModel)`。`create_agent` 的工具循环与消息结构不因配置文件而改变。
4. 若同一会话改选另一个已加载的模型，下一次模型调用边界由现有 resolver 返回新对象；修改文件本身不等于会话切换。

`.env/passwd.md` 是现有诊断脚本的旧凭证来源。实施时只为创建新的本地配置读取其中已有的 `OPENAI_API_KEY`，不覆盖或删除旧文件；旧诊断脚本继续原样工作。迁移过程不能打印凭证，也不成为应用启动时的读取路径。

## 实施与验证范围

本次先写失败测试，再实现 `naiyue_test_agent/model_access.py` 的文件加载/四类模型构造，以及 `agent_runtime.py` 的 `build_configured_runtime_agent` 接线。从现有本地凭证创建了只包含已可用网关的 `.env/models.json`，没有伪造尚未配置的供应商。新增 `scripts/probe_configured_tool_loop.py`，用合成探针工具验证“读配置→选模→模型→工具→模型”，不输出密钥或探针值。`requirements.txt` 记录新增适配依赖，`tests/test_model_access.py` 覆盖正常构造、错误配置、权限与假模型跨工具回合选模。构造测试和假模型接线与真实供应商调用分开报告；OpenAI 官方、Claude、Ollama 的真实模型→工具→模型轨迹需在各自可用时单独验证。

实际验证：新测试首先因缺少 `build_configured_runtime_agent` 在收集阶段失败；实现后 `.venv/bin/python -m pytest -q` 输出 `21 passed in 0.63s`，`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0，`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。命令 `.venv/bin/python -m scripts.probe_configured_tool_loop --model-id heiyu-gpt-5.5` 真实运行输出 `status=PASS model_calls=2 tool_executions=1 final_matches_tool_result=true`。此结果证明本地文件中该网关配置的真实工具循环；不证明另外三个后端的网络服务、密钥或模型工具能力。

暂不实现 SQLite、Web 表单、文件热重载、凭证轮换或多用户隔离。上服务端时可以换掉文件读取边界，但不能把本地明文文件方案直接部署为多用户凭证方案。

后续兼容扩展（2026-09-30）：[Spec 005](005-model-access-thinking.md) 给单个 `models` 条目增加可选 `thinking` 对象，包含 `supported_levels` 和 `default_level`。旧配置不含此字段时继续沿用模型默认，不修改真实 `.env/models.json`；上述历史验证结果仍是扩展前的结果。

请求策略兼容扩展（2026-09-30）：[Spec 010](010-model-request-policy.md) 给单个 `models` 条目增加可选 `request` 对象。例如 `"request": {"timeout_seconds": 60, "sdk_max_retries": 2}`；每个字段均可省略，缺省时在线模型分别使用 60 秒和 2 次 SDK 重试。Ollama 仅使用 60 秒或显式覆盖的超时，显式 `sdk_max_retries` 会被拒绝；其重试行为待本地模型可用后设计。`timeout_seconds` 必须是有限正数，`sdk_max_retries` 必须是非负整数。真实 `.env/models.json` 本次未修改。
