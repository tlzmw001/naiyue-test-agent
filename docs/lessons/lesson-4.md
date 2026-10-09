# Lesson 4：模型配置如何进入 Agent（第 1 段）

> 当前只讲“本地文件 → Python 字典”。2026-09-30 已讲解本段；用户正确判断 `0644` 权限会在读取内容前失败，并在顶层字段校验讲解后自述理解。独立实现和面试追问能力待验证。课堂只读代码、记录脱敏结构，未修改 `.env` 或生产实现。

## 本章路线

| 顺序 | 小问题 | 代码入口 |
| --- | --- | --- |
| 1（本次） | 文件路径、权限、JSON 解析、重复键和异常怎样处理？ | `model_access.py:89-102`、`31-37` |
| 2 | 字典怎样通过 schema 和引用校验？ | `model_access.py:104-151` |
| 3 | 供应商和模型名怎样变成 LangChain 对象？ | `model_access.py:73-86` |
| 4 | 一次加载后的 `ModelCatalog` 怎样按 ID 取模型？ | `model_access.py:18-28` |
| 5 | 运行层怎样把当前会话选模接到 middleware？ | `agent_runtime.py:59-82`、`17-36` |
| 6 | 一个真实工具回合怎样证明读配置后选中了模型？ | `probe_configured_tool_loop.py:44-68`、`test_model_access.py` |

每次只展开其中一个问题，不因课程路线存在就把后续环节标为已理解。

## 当前代码地图与调用图

```text
scripts.probe_configured_tool_loop.main
  → build_configured_runtime_agent(config_path, ...)
    → load_model_catalog(config_path)
      → Path.stat()：文件类型与权限
      → Path.read_text()：UTF-8 字符串
      → json.loads(..., object_pairs_hook=_unique_object)：字典
    → 后续校验/构造/选模（下一段）
```

本次脱敏实测：`config_path=.env/models.json`，普通文件、权限 `0o600`；顶层键为 `default_model_id`、`models`、`providers`、`schema_version`。唯一模型条目沿引用解析为 `heiyu-gpt-5.5 → heiyu → openai_compatible → gpt-5.5`。只确认 `api_key` 存在，未读取或展示其值。

## 本段逐行理解点

- `model_access.py:89`：`path: str | Path` 允许调用者传字符串或 `Path`；没有传时默认 `.env/models.json`。函数被调用才会读文件。
- `:91`：`Path(path)` 统一路径对象；此时没有 IO。
- `:92-93`：进入 `try`；`stat()` 从文件系统取元数据。文件不存在属于 `OSError` 的子类。
- `:94-95`：只接受普通文件；目录等类型抛 `ModelConfigError`，不会进入模型构造。
- `:96-97`：`S_IMODE` 提取权限位，`& 0o077` 检查组/其他用户权限。`0o600 & 0o077 == 0`，通过；`0o644 & 0o077 == 0o044`，拒绝。
- `:98`：先 `read_text(encoding='utf-8')` 得到 JSON 字符串，再 `json.loads` 得到嵌套 Python 字典。`object_pairs_hook=_unique_object` 让每一层 JSON 对象先以键值对列表交给我们的重复键检查。
- `:99-100`：读文件的系统错误统一转成 `ModelConfigError`，只显示路径和异常类型；`from None` 不显示可能带敏感内容的原异常链。
- `:101-102`：文本解码或 JSON 格式错误也转换为不包含文件正文的错误。手动抛出的 `ModelConfigError`（如权限或重复键）不会被这两个 `except` 吞掉。
- `:31-37`：`_unique_object` 从空字典开始逐对插入；已存在相同键就抛错。示例 `{"model":"A","model":"B"}` 的键值对是 `[('model','A'), ('model','B')]`，第二对触发 `ModelConfigError`。最小验证输出：`duplicate-key example: ModelConfigError Configuration has a duplicate JSON key`。

本段结束时，`data` 只是“解析出来的字典”，不是 `ChatOpenAI`，也没有发起网络请求。真正的字段/引用校验和模型构造发生在函数后半段，留到下一小节。

## 当前理解检查

如果把文件权限从 `0600` 改成 `0644`，代码会在哪一行停止？这时 `data`、`ModelCatalog` 和模型网络请求分别是否已经产生？不要实际修改 `.env`；只用上面的数据流推断。

用户答复：权限变成 `0644` 后，会在读取文件内容前停止，后续数据未产生。该答复与代码执行顺序一致；`stat()` 已读取文件元数据，`data` 和 `ModelCatalog` 尚未产生。`object_pairs_hook` 已结合示意 JSON 讲解并展示了本地回调顺序；用户跳过对应检查题继续学习，不记录为独立掌握。

后续已讲解 `model_access.py:104-137` 的顶层字段、版本与供应商按 `kind` 分支校验。用户分别反馈“这里我懂了”“这块懂了”；记录为讲解后自述理解，不升级为独立实现或面试追问能力。下一小段从 `models` 引用与构造继续。

随后已讲解 `model_access.py:139-152` 如何从 `models` 条目取得 `provider_id`、检查引用并将 `_build_model` 的返回对象放入 `built_models`；用户反馈“理解了”。构造函数内部和异常路径留在下一小段，不因入口讲过就记录为掌握。

接着已讲解 `_build_model` 的四个构造分支与异常转换，以及 `ModelCatalog` 保存并按 ID 返回已构造对象。用户追问 `dict[str, BaseChatModel]`，已说明它是参数类型标注、字典值为基类或子类的实例，并区分 `type[BaseChatModel]`；用户随后反馈“理解了”。这是该类型标注的自述理解，未有独立实现或追问能力证据。下一小段进入运行层的 `build_configured_runtime_agent`。

已讲解 `build_configured_runtime_agent` 中一次加载 catalog、定义闭包 `resolve_model(session_id)`、动态返回 `(model_id, BaseChatModel)`，并将闭包传给运行层。用户反馈“理解了”；仍需独立追踪一次运行和修改练习。下一小段讲 `build_runtime_agent` 与 middleware 装配。

已讲解 `build_runtime_agent` 将默认模型、工具、系统提示词、响应格式、`RunContext` 和选模 middleware 交给 `create_agent`；用户反馈“理解了”。本机安装版 `create_agent` 的返回类型为 `CompiledStateGraph`，实现末尾调用 `graph.compile(...)`。这只验证装配边界与源码事实，不代表用户已独立实现或真实会话管理已完成。下一小段追 middleware 的每次模型调用边界。

已讲解 `make_model_selection_middleware` 从 `request.runtime.context` 取会话 ID、调用 resolver、记录选模并通过 `handler(request.override(model=...))` 继续执行，连同缺失 ID 的失败路径。用户反馈“懂了”。下一小段用假模型 A→工具→B 测试逐变量验证时序；仍无用户独立改动证据。

最后已按 `tests/test_agent_runtime.py` 的假模型 A→工具→B 样本，讲解 `selected`、`selections`、消息类型与 `tool_call_id` 断言的来源；单项测试运行结果 `1 passed in 0.82s`。用户随后明确要求讲解到此为止，转入开发讨论。课堂状态止于已讲解和若干局部机制的自述理解；独立实现、面试追问及真实多供应商切换仍未验证。
