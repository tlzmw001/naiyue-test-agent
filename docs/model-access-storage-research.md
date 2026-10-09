# 模型接入与凭证存储调研（设计建议）

状态：2026-09-29 调研结论，尚未实施。用户希望下一模型接入切片一次覆盖 OpenAI、Claude 和本地模型；现有 `create_agent` 路线与四层职责保持不变。

## 当前代码与数据流

目前 `naiyue_test_agent/agent_runtime.py` 的 `wrap_model_call` 在每次模型调用前按 `session_id` 获取 `(model_id, BaseChatModel)`，然后将模型传给 `handler(request.override(model=model))`。选择函数由上层注入，模型接入层尚不存在。`scripts/probe_tool_loop.py` 构造过一个 OpenAI 兼容的真实网关模型；`tests/test_agent_runtime.py` 使用假模型验证过工具回合后的 A→B 切换。没有模型目录、凭证解析或数据库。当前仅安装 `langchain-openai`；未安装 `langchain-anthropic`、`langchain-ollama`，本机命令路径中也未发现 Ollama。

## 参考实现与适用边界

| 项目 | 源码观察 | 可借鉴与限制 |
| --- | --- | --- |
| [Pi](https://github.com/earendil-works/pi/tree/a69bef789bc9/packages/coding-agent) | [ModelRuntime](https://github.com/earendil-works/pi/blob/a69bef789bc9/packages/coding-agent/src/core/model-runtime.ts) 分别装配凭证存储与 `models.json`；[providers.md](https://github.com/earendil-works/pi/blob/a69bef789bc9/packages/coding-agent/docs/providers.md) 允许环境变量或 `auth.json`；[auth-storage.ts](https://github.com/earendil-works/pi/blob/a69bef789bc9/packages/coding-agent/src/core/auth-storage.ts) 创建文件时设 `0600`；[models.md](https://github.com/earendil-works/pi/blob/a69bef789bc9/packages/coding-agent/docs/models.md) 给 Ollama/vLLM 等配置端点与协议。 | 模型定义与凭证来源分离，运行时再解析。它是本地工具的方案；`0600` 不等于加密，也不能直接当多用户 SaaS 的凭证设计。 |
| [AgentX](https://github.com/lucky-aeon/AgentX/tree/3fcfa0e5c224/AgentX) | [application.yml](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224/AgentX/src/main/resources/application.yml) 使用 PostgreSQL；[ProviderEntity](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224/AgentX/src/main/java/org/xhy/domain/llm/model/ProviderEntity.java) 保存提供商配置；[ProviderConfigConverter](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224/AgentX/src/main/java/org/xhy/infrastructure/converter/ProviderConfigConverter.java) 写库时加密、读库时解密；[LLMProviderFactory](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224/AgentX/src/main/java/org/xhy/infrastructure/llm/factory/LLMProviderFactory.java) 根据协议构建 OpenAI/Anthropic 模型。 | 可借鉴“持久元数据→运行时组装”及列表返回时脱敏。但其[加密工具](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224/AgentX/src/main/java/org/xhy/infrastructure/utils/ValidationUtils.java)把密钥写在源码中；不得将这一实现作为生产级密钥管理范例。 |

两份源码说明存在不同产品形态，不能由此推断业界“大多数”把 API key 放入 SQLite。

## 存储决策建议

**当前模型接入切片先不引入数据库。** 2026-09-29 用户进一步决定：本地阶段先从 `.env/models.json` 读取供应商与模型配置，允许本地忽略文件保存 API key；具体格式见 [Spec 004](specs/004-local-model-config-format.md)。这是单用户本地阶段的明确取舍，替代本段原先“只从进程环境解析密钥”的建议。密钥不得进入日志、轨迹、提交或文档示例的真实值；服务端阶段另行设计凭证存储。

等到本地 Web 真正需要“在界面里保存模型配置”时，**SQLite 可以保存非敏感的模型目录和凭证引用**。若要求用户在界面录入并持久保存 API key，单用户 macOS 版本优先评估系统 Keychain；数据库只存引用。若未来部署多用户 SaaS，则重新按租户隔离、授权、审计和轮换要求选服务端数据库与密钥管理服务；本地 SQLite 的选择不自动外推到 SaaS。

原因：SQLite 官方认为它适合本地应用数据和不少低中流量网站，但[同一数据库文件一次只有一个写入者](https://www.sqlite.org/whentouse.html)。标准 SQLite 文件不是加密的；[SQLite SEE](https://www.sqlite.org/see/doc/trunk/www/readme.wiki) 属于额外加密扩展。把 key 明文放入 SQLite，与把它明文放入 JSON 文件一样，都没有解决备份、误读、权限、日志与轮换问题。[OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) 建议限制访问、避免记录明文并管理密钥生命周期；如做应用层加密，加密密钥需与密文分开管理。

## 下一切片建议边界与验收

- OpenAI：原生 OpenAI 与当前 OpenAI 兼容网关使用 `ChatOpenAI`；兼容端点只保证所测协议行为，不假定所有厂商扩展字段可用。[LangChain 文档](https://docs.langchain.com/oss/python/integrations/chat/openai)
- Claude：使用 `ChatAnthropic` 与独立的 Anthropic 凭证引用。[LangChain 文档](https://docs.langchain.com/oss/python/integrations/chat/anthropic)
- 本地：首个明确后端选 Ollama 的 `ChatOllama`；以后自训练权重由推理服务承载，若服务实现 OpenAI 兼容协议，也可走经实测的兼容端点。模型本身必须支持工具调用，适配类存在不等于模型可用。[LangChain 文档](https://docs.langchain.com/oss/python/integrations/chat/ollama)
- 先写阶段 Spec 和失败测试：配置缺失/错误、密钥缺失、未知供应商、错误端点不静默回退；三种配置分别构造 `BaseChatModel` 并接到现有选模 middleware。用无密钥的确定性测试证明接线；真实 OpenAI、Claude、Ollama 分别通过模型→工具→模型轨迹验收。不可访问的供应商只能标为“接线已测、真实调用待验证”。

本轮仅研究与提出设计建议，未安装依赖、创建数据库、修改环境配置或验证新的真实模型调用。
