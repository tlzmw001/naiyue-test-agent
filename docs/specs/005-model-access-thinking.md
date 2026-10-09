# Spec 005：模型接入模块与默认 thinking 等级

状态：2026-09-30，已按本 Spec 实现并做确定性验证。本次只实现模型配置中的默认 thinking 等级、Pi 式回退与供应商参数映射；运行中临时改等级、缓存和错误事件分别进入后续切片。

## 当前数据流与问题

`build_configured_runtime_agent(config_path, ...)` 在装配时调用 `load_model_catalog`，后者读取 `.env/models.json` 并构造四类 `BaseChatModel`；运行层在每次模型调用前从目录选出模型。当前 `_build_model` 不传 thinking 参数，且 `model_access.py` 同时承担配置读取和供应商构造。用户希望按四层架构把模型接入代码放在一个明确模块下，命名沿用已存在的 `model_access`。

## 本切片的行为契约

1. 将 `naiyue_test_agent/model_access.py` 变成同名包 `naiyue_test_agent/model_access/`。`__init__.py` 保留现有 `ModelConfigError`、`ModelCatalog`、`load_model_catalog` 的导入路径和 `ChatOpenAI` 测试注入点；新增的纯等级解析放入 `thinking.py`。现有公开函数签名不变。
2. 模型条目可选 `thinking` 对象：`{"supported_levels": ["off", "low", "medium", "high", "xhigh"], "default_level": "high"}`。两字段都必填；未填写整个对象时沿用现状，不向适配器传显式 thinking 参数。`default_level` 还可为独立值 `model_default`，同样不传参数。只改代码所接受的格式和示意文档；不修改真实 `.env` 文件。
3. 七个显式等级按 `off < minimal < low < medium < high < xhigh < max` 排序。若请求值不在该模型支持集合中，先从自身向上找第一个支持值，找不到再向下找。支持集合非空、元素唯一且均属于七档；无效等级或缺字段在发网络请求前报 `ModelConfigError`。`model_default` 不参加回退。
4. 本切片的原生映射由供应商适配器承担：OpenAI/兼容网关使用 `ChatOpenAI(reasoning_effort=...)`，其中 `off` 对应 `none`；Anthropic 的 `off` 对应 `thinking={"type":"disabled"}`，其余本机适配器支持的 `low/medium/high/xhigh/max` 使用 `reasoning_effort`；Ollama 的 `off` 对应 `reasoning=False`，官方通用 API 文档列出的 `low/medium/high/max` 使用 `reasoning=<level>`，`minimal/xhigh` 在配置加载时拒绝。不支持原生表达的等级不能写进该供应商模型的 `supported_levels`。这只是请求映射；实际模型/网关是否接受，另用真实集成测试验证。
5. `ModelCatalog` 对上层暴露本次默认等级的 `requested`、`selected` 和发送参数的非敏感描述，便于检查回退；当前没有完整轨迹系统，不把目录元数据当作真实供应商执行证据。运行层继续使用现有 `catalog.get(model_id)`，无需更改 `create_agent` 循环。

## 文件范围与兼容性

- `naiyue_test_agent/model_access/__init__.py`：原配置读取、校验、模型目录和供应商构造；公开导入不变。
- `naiyue_test_agent/model_access/thinking.py`：等级集合、回退与原生参数的纯函数/小数据对象。
- `tests/test_model_access.py`：先写失败测试，覆盖默认不变、向上/向下回退、错误配置和四类适配对象收到的参数；现有跨工具回合测试继续通过。
- `docs/specs/004-local-model-config-format.md`：追加兼容字段说明，不改旧阶段验证事实。
- `docs/handoff.md`：记录红灯、绿灯、验证与学习状态。

旧配置文件的模型条目不带 `thinking` 时行为完全不变；因此保留 `schema_version=1`。这是可选向后兼容扩展，不要求修改本地配置或迁移。`model_access.py` 只做位置迁移，不顺便改其已有错误处理。

## 验证与限制

先执行 `.venv/bin/python -m pytest -q tests/test_model_access.py` 观察针对新增行为的失败，再做最小实现；随后执行 `.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent scripts`。确定性测试证明配置→回退→适配器对象参数的接线；不证明 HeiyuCode、Claude 或 Ollama 已实际按指定等级思考。真实网关测试需要单独确认能力和成本，并记录返回证据。本切片不增加依赖，不改 `.env`，不启用自动重试或兜底模型。

实际结果：新增测试先因 `model_access` 尚为单文件而在收集阶段报 `ModuleNotFoundError`；迁移并实现后，定向命令输出 `23 passed in 0.88s`，最终全量命令输出 `32 passed in 0.94s`，`compileall` 退出码 0，`pip check` 输出 `No broken requirements found.`。测试还验证了 `ChatOpenAI` 在本机版本生成的请求载荷含 `reasoning_effort`，以及假模型的完整 Agent 装配仍能按现有导入与测试注入点工作。这些是本地接线证据，不是供应商收到并执行等级的证据。

2026-09-30 回归修正：针对 Ollama `minimal/xhigh` 未经限制直接映射的问题，先新增配置加载失败用例，观察 `2 failed, 1 passed`（`max` 正向用例通过）；随后仅收紧 `native_thinking_settings` 的 Ollama 分支。定向用例 `3 passed in 0.65s`，全量测试 `35 passed in 0.76s`，`compileall` 退出码 0，`pip check` 无损坏依赖。该修正只保证通用适配器参数的文档边界，不代表具体 Ollama 模型支持所有列出的等级。
