# Spec085-03B：Ls、Write/Edit 的基础设置

日期：2026-10-16。状态：**已授权实施并通过本机验证。** 接续[085-03A](085-03-read-settings.md)。本片把已验证的“配置→schema/提示→执行”机制接入另外三个工具；注册表、权限和工具功能增强继续按[总Spec085](085-tool-system-rebuild.md)分片。

## 1. 本片具体解决什么

当前Ls的500/2000条、50KiB、4096字符分别散在常量、schema和截断文字；Write/Edit共用_write_limit()读取NAIYUE_WRITE_MAX_BYTES，无法独立设置两个工具的输入预算。已有settings.toml只支持Read。

本片接Ls/Write/Edit的现有额度，不改文件授权/串行属性、原子提交或精确编辑算法。Find/Grep的整次期限与共享候选枚举涉及搜索执行链，双Shell设置涉及进程启动/收尾，分别在对应片贯通，不在本片先接一套随后替换的旧runner参数。

## 2. 接入哪些字段

| 设置组 | 字段/代码默认 | 真正含义 |
|---|---|---|
| tools.ls | default_limit=500、max_limit=2000 | 默认返回条目数/模型允许请求的条目上限 |
| tools.ls | max_output_bytes=51200、max_path_chars=4096 | 清单正文UTF-8预算/输入路径字符上限 |
| tools.write | max_input_bytes=1048576、max_path_chars=4096 | content的UTF-8字节总量/路径字符上限 |
| tools.edit | max_input_bytes=1048576、max_path_chars=4096 | 全部oldText+newText的UTF-8字节总量/路径字符上限 |

全部严格正整数；Ls默认值不大于max_limit。仍用同一个~/.naiyue-test-agent/settings.toml、同一加载器及缺省/整份回退合同。Read已支持的配置和执行保持，尚未接线的其他表仍诊断后回退，不假装支持。

Write/Edit的max_input_bytes不是原文件或修改后文件的大小限制。Edit仍能分块处理大文件，ASCII长度预检和UTF-8编码后字节检查保留；不把字节预算错误地配置成字符串max_length。

## 3. 新增、修改与删除

以下路径均相对naiyue_test_agent。

| 文件 | 方法/职责变化 |
|---|---|
| tools/settings.py | 新LsSettings/WriteSettings/EditSettings，BuiltinToolSettings增加对应字段；不增加加载器或配置优先级 |
| tools/schemas.py（新增） | build_tool_schemas(name, base, **field_definitions)用create_model派生公开/含runtime执行模型，保留严格校验/默认/原验证器；返回两个模型引用，不复制BaseTool |
| tools/read.py | 原_build_read_schemas的通用派生/注入部分迁到schemas.py，Read自己保留path/limit字段定义；两工厂使用同一公共函数，删除旧私有实现，不留转发 |
| tools/ls.py | 两个工厂增加keyword-only settings，按配置派生path/limit及缺省；_list_directory增加keyword-only settings供字节预算/截断提示使用；删旧DEFAULT_LIMIT/MAX_LIMIT/MAX_LS_BYTES/静态SessionLsInput及数值固定提示 |
| tools/write.py | create_session_write_tool(settings=None)固定WriteSettings并派生path约束/描述；将settings.max_input_bytes传给已有_write；删除_MAX_WRITE_ENV/_DEFAULT_MAX_WRITE_BYTES/_write_limit及静态SessionWriteInput |
| tools/edit.py | create_session_edit_tool(settings=None)固定EditSettings并派生path约束/描述；把独立预算传给已有_edit，删除_write_limit导入与静态SessionEditInput；_scan_target/_copy_to_temp与分块预算保持 |
| tools/__init__.py | 导出实际可用的三个设置类 |
| application/cli.py、web/app.py | 已有一次load_tool_settings之后，将各settings.tools.ls/write/edit交给相应工厂；不新增CLI/Web参数 |

新增schemas.py有四个当前消费者Read/Ls/Write/Edit。第三次复用同一派生与runtime注入职责时集中这段逻辑，工具各自负责具体参数和业务额度，避免混入settings配置加载或未来registry登记职责；不为统一写法创建执行wrapper/新BaseTool子类。文件是有实际调用方的schema构造入口，不是占位模块。

调用方式示意（拟议代码）：

```python
create_session_ls_tool(settings=settings.tools.ls)
create_session_write_tool(settings=settings.tools.write)
create_session_edit_tool(settings=settings.tools.edit)

# 工具工厂内部固定相应配置；现有IO函数继续接具体预算。
_list_directory(root, path, limit, grants, call_id, settings=ls_settings)
_write(root, path, content, write_settings.max_input_bytes)
_edit(root, path, edits, edit_settings.max_input_bytes)
```

Ls的limit参数默认应来自本App配置，不继续在Python函数默认参数里绑定旧常量；由生成的Pydantic模型补默认，并在工具内对省略情形使用同一设置。不能只改变模型schema而实际正文仍用500。

框架边界：本片的公开模型是build_tool_schemas返回的public；含runtime的execution继承它并交给BaseTool.args_schema。LangChain另生成的tool_call_schema用于模型字段导出，会过滤runtime但不保留extra='forbid'配置；严格执行校验检查args_schema及真实Agent调用，不把模型导出模型误当执行校验器。省略Ls limit时Python函数默认也绑定本工厂同一settings.default_limit。

## 4. 正常与失败链路

```text
CLI/Web启动
 → 原load_tool_settings：得到含Read/Ls/Write/Edit的冻结设置
 → 各工厂生成schema、说明和原工具
 → 原App/Agent/批次/HITL/ToolError
 → Ls执行用配置字节预算
   或Write/Edit把各自输入字节预算交给原IO
 → 原结果/具体ToolException反馈
```

示例：write.max_input_bytes=4、edit.max_input_bytes=12。Write写两个汉字编码为6字节，在原_write字节检查处抛WRITE_LIMIT，经公共ToolError反馈同ID error，原文件未更改。Edit输入oldText/newText合计9字节则可继续按原精确匹配/冲突/提交链执行，不受Write的4字节预算影响。

Ls配置default_limit=2/max_limit=5/max_output_bytes=128：省略limit取2，显式请求4可以使用；请求6在BaseTool参数校验处反馈。正文受128字节限制，标记准确说明截断预算，不再写死50KiB。隐藏名称、排序、目录斜杠及既有审批语义保持。

配置本身非法时在启动加载器警告并整份默认，不先执行IO再发现配置错误；Write/Edit明确工具错误仍经过085-01，真正控制/机制故障保持原边界。

## 5. 消费者与删除边界

- tests/test_write_tool.py和test_edit_tool.py中设置NAIYUE_WRITE_MAX_BYTES的用例迁为显式设置；保留UTF-8字节上限、目标未改变及异常断言。非法旧env值的用例改在新设置/加载器入口验证非法值，另验旧env不再影响构造，不保留兼容解析。
- 搜索所有_write_limit/旧常量/静态输入类消费者后同步迁移；直接IO测试仍调用真实_write/_edit/目录函数，不保留废弃函数供测试使用。新字段不进入会话DB，也不改变事件/序列化合同。
- tests/test_tool_settings.py及入口集成新增三工具接线、跨App隔离、Read回归、schema/执行一致、严格runtime隐藏与异常反馈用例；可拆tests/test_file_tool_settings.py承载IO行为以控制文件规模。
- docs/examples/settings.example.toml增加本片已接线字段/注释；完整未来样例仍标清未支持部分。README/学习源码表/handoff/总Spec与文件附录同步。
- 自动父目录、换行、模糊编辑/diff/preview、软链接、新权限/动态ToolSet不在本片实现；原有保护和同步真实消费者继续使用。

## 6. TDD与验收

先跑新测试观察正确失败，再接配置/公共schema/工厂/CLI/Web，迁移测试旧入口并删除。至少验证：

1. 三组字段的默认/正数/严格类型及Ls交叉约束，文件合法生效、非法整份默认和安全诊断。
2. Ls缺省、显式较大limit、UTF-8字节截断、准确反馈及外部目录批准/拒绝。
3. Write/Edit独立预算，中文编码字节检查、超限零修改、Edit大原文件可编辑及原冲突/提交未确认分支。
4. 四工具派生模型同源、runtime不向模型暴露；同进程不同App配置互不污染；现有Read27个配置/入口案例保持。
5. CLI/Web真实工具调用得到配置结果，旧env无生产消费者，串行调度和必要事件/清理保持。

定向pytest→后端全量→compileall/pip check→前端测试/构建→diff/引用/文件≤500行检查。不开真实模型请求、不改真实配置/依赖/用户库、不提交发布。

## 7. 实施、迁移与验证结果（2026-10-16）

- 已实现第2—5节：四工具共用schemas.py，Ls/Write/Edit设置接实际schema/说明/IO，CLI/Web真实消费同一加载结果；当前可运行配置样例已扩展。不新增配置入口或加载优先级。
- 删除Ls旧三额度常量、Read私有schema构造、三工具静态Session输入类、Write旧env常量/_write_limit及Edit导入；生产/scripts无旧入口消费者。唯一旧env字符串留在证明“不再读取”的回归测试。固定workspace工厂、现有权限/审批和元数据串行声明仍有真实消费者，保留到注册片迁移。
- tests/test_write_tool.py的4字节/非法额度案例迁到WriteSettings，test_edit_tool.py迁到独立EditSettings，原错误/编码/零修改断言保留；没有修改conftest或共享测试helper。新增test_file_tool_settings.py；test_tool_settings_entries.py增加CLI/Web三工具执行及两App的Ls隔离。
- 红灯：先28个设置/行为案例和3个入口案例失败，原因是设置/公共构造不存在、配置未接线及旧env仍生效。首次实现后定向162通过/3失败，发现新测试误把框架导出的tool_call_schema当严格执行模型；最小探针确认框架重建它时丢弃extra配置，改为检查真实公开基类与执行入口，并增加3个实际Agent非法参数零IO案例。后者首次用无checkpointer的低层helper无法装配会话Ls，改用已有App helper及内存checkpointer，保留全部行为断言；未改生产HITL或削弱校验。

实际执行：

```text
.venv/bin/python -m pytest -q tests/test_file_tool_settings.py tests/test_tool_settings.py tests/test_tool_settings_entries.py tests/test_ls_tool.py tests/test_write_tool.py tests/test_edit_tool.py tests/test_application_ls.py tests/test_application_write.py tests/test_application_write_cli.py tests/test_application_edit_cli.py tests/test_web_tools.py tests/test_tool_batch_scheduling.py
168 passed in 4.41s

.venv/bin/python -m pytest -q
1162 passed in 37.30s

.venv/bin/python -m compileall -q naiyue_test_agent tests scripts
exit 0
.venv/bin/python -m pip check
No broken requirements found.

# cwd=frontend
npm test -- --run
Test Files 9 passed (9); Tests 27 passed (27)
npm run build
tsc --noEmit + vite build: exit 0, 186 modules transformed

git diff --check
exit 0
```

新增行为34例；与此前Read设置/入口合计61例，单跑三个设置测试文件61 passed in1.01s。修改生产文件均≤500行，settings.py86行、schemas.py16行、Read346行、Ls176行、Write203行、Edit247行。当前样例TOML严格加载、旧引用/配置与描述检查通过。不代表Windows、实网模型、生产业务或用户独立掌握。下一步只审阅[085-04工具定义/启动注册](085-04-tool-registration.md)。
