# Spec085-03A：工具设置基础与 Read 的完整接线

日期：2026-10-16。状态：**用户授权后已实施并验证。** 接续已完成的[085-02](085-02-tool-package-boundaries.md)。原“ToolSpec/Registry/settings及启动接线”继续拆小：本片先打通配置→工具schema/提示→执行；随后把相同机制接入其余工具，再实施注册合同与动态工具/权限。不改变[总Spec085](085-tool-system-rebuild.md)最终架构，用户讨论配置目录与Pi实现后明确要求实施；只按本片范围接线。

## 1. 当前问题和本片范围

改造前Read在tools/read.py用MAX_PAGE_LINES/MAX_READ_BYTES、Field.le/max_length和固定错误文字表达额度；CLI/Web直接无参创建工具。若只加一个配置类，模型看到的schema和执行额度仍会不同步。

本片只接Read现有文本能力的四项设置，另落实用户随后确认的统一默认目录与模型可选路径。按用户最新意见，CLI/Web启动时默认读取工具设置；文件缺失、无法读取、解析或字段校验失败时采用代码默认设置，不新增CLI路径参数。仍返回原BaseTool，当前权限/HITL/错误转换与其他六工具行为保持；不提前加入图片、软链接、超长行新算法、ToolSpec或动态注册。

完整配置样例仍是最终设计文件，不能把尚无消费者的图片/权限/双Shell字段读入后假装已生效。此阶段只接受下表字段；其他表/键属于配置校验失败，记录诊断并整份回退代码默认。其余工具片逐项扩展同一个加载器，不逐字段拼凑一份部分有效的配置，也不建设多套解析器。

## 2. 新增、修改与删除

以下路径均相对naiyue_test_agent。

| 文件 | 具体变化 |
|---|---|
| tools/settings.py（新增） | ReadSettings、BuiltinToolSettings、ToolSettings三个冻结且strict/extra=forbid的Pydantic模型；load_tool_settings(path=None)用标准库tomllib读取并校验 |
| tools/read.py | 两个现有工厂增加可选keyword-only settings: ReadSettings；用当前App设置派生公开/含runtime执行schema，生成准确description和handle_validation_error，_read显式接收同一设置；去掉原额度常量和包含固定数值的消息常量 |
| tools/__init__.py | 导出本片真正使用的ReadSettings、ToolSettings和load_tool_settings；不增加未实现的Registry导出 |
| application/cli.py | 不增加启动参数；进入启动try后、打开SessionStorage/加载模型前调用load_tool_settings()，创建Read时传settings.tools.read |
| web/app.py | lifespan最前调用同一个默认加载器，拿到文件设置或代码默认后创建Read和应用；不新增工具配置路径字段/参数；原模型config_path改为用户目录缺省 |
| application/skills.py | 当前还import Read的MAX_READ_BYTES用于Skill元数据扫描；移成其自身命名的64KiB发现预算并保留原行为。Skill索引扫描与一次Read返回额度是不同职责，不能因为调小Read页就把已有Skill从索引中移除 |

ReadSettings字段固定为：

| key | 缺省 | 校验/消费者 |
|---|---:|---|
| default_limit | 2000 | 严格正整数且不大于max_limit；未提供limit时的页行数及完整文件快速返回条件 |
| max_limit | 2000 | 严格正整数；公开和执行schema的limit上界、参数错误提示 |
| max_output_bytes | 65536 | 严格正整数；完整读取/逐行读取/跳行缓冲及正文预算，超长行提示使用此值 |
| max_path_chars | 4096 | 严格正整数；公开和执行schema的path长度限制 |

本片不添加第二份默认值文件、通用schema生成平台、pydantic-settings依赖或ToolSettings的全局可变单例。Read的两个工厂目前均有真实测试消费者，均按同一配置语义接线；固定工作区工厂的消费者迁移/删除仍放注册合同片，不新建兼容工厂。

## 3. 函数如何互相调用

已接入的接口（下列为省略正文的签名说明）：

```python
def load_tool_settings(path: str | Path | None = None) -> ToolSettings: ...

def create_session_read_tool(*, settings: ReadSettings | None = None): ...
def create_read_tool(workspace_root, *, settings: ReadSettings | None = None): ...

# _read原来的参数保持；在keyword-only区增加必需的settings。
def _read(..., *, settings: ReadSettings, access=None) -> str: ...
```

load_tool_settings()默认读取用户目录下的工具设置文件。已确认路径为Path.home()/'.naiyue-test-agent'/'settings.toml'，与models.json及现有sessions.sqlite统一用户目录，不自动创建文件，不搜索工作区或自动读.env。可选path只供可信Python调用与测试指定，不增加CLI/Web路径选项。读取一次：文件不存在正常返回默认；其他预期读取/编码/TOML/字段校验失败记录一次安全warning后返回整份默认；合法配置中省略的字段由模型默认补齐。只捕获配置处理的预期错误，不将任意程序异常都吞成默认值。每个工厂在创建时固定自己的ReadSettings引用；_read不在执行时重读配置，也不改模块常量或os.environ。另一App可以用另一份额度，现存工具不被影响。

read.py内部新增_build_read_schemas(settings)，用Pydantic create_model从_ReadInput基础定义派生公开ReadInput，再生成受本App额度约束的公开模型，再派生带ToolRuntime字段的执行模型。保留strict/extra/default/offset限制；不能从框架裁剪后的tool_call_schema反推校验器。原SessionReadInput静态类由这条真实生成路径替代，迁移实际消费者后删除。

运行模型装饰器采用生成的执行模型；公开schema是同源约束且无runtime。当前工厂仍返回BaseTool，公开模型留在构造函数局部；后续ToolSpec接入时直接持有该真实模型，不重新推导。需要arbitrary_types_allowed的配置仅用于含ToolRuntime的执行模型，不放宽公开参数校验。

description和handle_validation_error在工厂处使用这份设置生成；_read直接使用设置中的整数，超长行ToolException在产生处用同一预算生成具体文字。没有新增异常类、反馈白名单或中间错误包装。

```text
CLI main / Web lifespan
 → load_tool_settings()：默认文件有效则采用，否则代码默认
 → ToolSettings.tools.read
 → create_session_read_tool(settings=同一ReadSettings)
     ├─ _build_read_schemas → BaseTool公开/执行参数约束
     ├─ description / handle_validation_error
     └─ 内层read闭包 → _read(settings=同一对象)
 → 原build_*application → 原compiled Agent
 → 原批次/HITL → BaseTool校验/注入runtime → 原文件IO
 → 原ToolMessage或ToolException → 085-01公共ToolError
```

Python SDK已有build_*application签名不需要为本片改变：调用者可load_tool_settings(path)，将其中的ReadSettings交给工厂，再把BaseTool列表交给原应用入口。待整体内置集合/注册表确有消费者时再引入prepare_tool_system，不为转传一个设置先建application/tool_setup.py。

## 4. 一个正常例子和两条失败路径

审阅示例（不是用户现有配置文件）：

```toml
[tools.read]
default_limit = 100
max_limit = 300
max_output_bytes = 4096
max_path_chars = 4096
```

模型提出read(path='notes.txt')：省略limit，_read按100行和4096字节共同限制。小文件仍直接返回正文；需要分页时保留原lines/next_offset格式，正文不超预算，少量范围提示不计正文预算。显式limit=250可用，仍受字节预算约束；不把default_limit误当max_limit。

模型提出limit=301或字符串'250'：BaseTool按派生执行schema拒绝；handle_validation_error保留当前框架路径，提示最大300，让模型重新提出调用，文件正文零读取。当前整批HITL时序保持，同行另一合法调用需要审批时，该参数反馈仍可能等批次恢复，不把校验提前成新权限节点。

配置default_limit=400、max_limit=300，或true/字符串数字/未知key/TOML非法/文件不可读：加载器记录配置未生效的安全原因/异常类型，返回整份代码默认设置，CLI/Web继续启动。随后schema、提示和执行统一使用默认的2000行/65536字节等值，不混用文件中的其他字段。文件不存在属于正常缺省；其他失败不能静默。日志不放整份配置及Pydantic input_value。配置回退与模型工具调用错误仍是两个不同边界。

## 5. 对其他模块的影响与明确删除

- App/SessionService、run/resume、会话存储、审批metadata及模式不变；审批恢复继续使用原工具引用。
- CLI/Web不新增配置路径参数，默认读取规则一致；文件有效时生效，否则使用代码默认，不加前端管理接口。
- Skill发现保留自己的原扫描预算；只修改其原Read常量import这一真实耦合，不扩展Skill热加载/权限设计。
- 删除Read旧额度常量、静态SessionReadInput和固定数值提示的真实生产依赖；测试改用配置/工具schema或当前IO入口，不留旧模块变量转发以便monkeypatch。
- 其余工具/旧NAIYUE_WRITE/BASH额度读取此时仍有实际消费者，由对应后片连同调用方一起迁移。当前不宣称全部工具配置完成，不引入新旧配置优先级。
- 完整设计TOML样例保留“尚未全部接入”标识；README另给默认路径和仅含本片可用键的样例，避免用户把未来字段当成已支持功能。

### 统一用户目录与模型入口（最新用户确认）

默认目录为~/.naiyue-test-agent/，模型models.json、运行/未来权限settings.toml、原sessions.sqlite共用位置；不新增auth.json或项目覆盖层，不自动移动真实.env/凭据或回退搜索旧路径。CLI/Web原--config改为可选，保留显式覆盖；load_model_catalog省略路径时也使用同一默认模型路径，私有文件检查/结构校验/错误上抛保持，不套工具配置回退。Python App构建API的config_path参数仍原样。

新增根层config_paths.py的get_agent_dir/get_models_path：当前实际消费者为模型加载、CLI/Web默认值、settings读取、已有会话默认目录及探针脚本。工具层不能为取默认目录反向依赖模型/应用，因此放无框架依赖的根层；不引入配置服务或路径对象。现有脚本默认值同步，但本轮不发实网探针。settings.toml没有时不自动创建文件/目录。

与配置自动读取配套，新增tests/conftest.py的fixture仅把tools.settings模块内get_agent_dir引用导向临时目录，不替换真实模型加载/Path.home或其他默认路径；测试默认路径时显式注入测试home后恢复该函数引用。理由是现有CLI/Web测试不得读取开发者个人settings.toml，原应用与IO断言保持。

### 与权限配置的边界

此回退合同针对工具额度等运行设置。此前用户确认“权限配置读不了/非法，在创建Agent前报错终止”的合同仍保留，不能因额度回退丢掉Deny。用户已确认工具与权限共用settings.toml；后续权限片接入时，整文件语法错误无法确认Deny应启动失败，能解析但工具额度非法时仅额度回退，权限字段非法仍失败。本片没有权限配置消费者，permissions表作为尚未支持的字段诊断并回退，不能宣称新权限已接线。

## 6. TDD与验收

新增tests/test_tool_settings.py；优先扩展test_read_tool/test_read_tool_name、CLI与Web现有测试，不复制Agent运行框架。现有测试helper若需要传settings，说明原因是配置不再来自模块常量，原行为/异常/顺序断言保留。

先写失败测试，再实现：

1. 合法TOML/缺省/未知键/严格类型/正数/交叉约束/读取和解析失败；确认合法文件生效，缺失正常默认，其他预期失败有诊断且整份默认；程序异常不能被配置回退吞掉。
2. Read的schema、description、具体错误文字、默认页及max_limit/字节执行共同改变，默认值回归；同进程两App用不同配置互不污染。
3. 显式limit大于default但不超过max可用；多字节UTF-8按字节计，分页标记/offset语义保持，runtime不出现在模型schema中；参数错误不包含注入对象。
4. 原工作区/审批/隐藏策略/ToolError链通过；外部批次审批后仍使用原配置，Skill索引不受Read页额度影响。
5. CLI/Web不增加路径参数，真实装配读取默认位置；合法额度生效，非法文件仍按默认额度启动；完整未来样例遇到尚未支持的字段会诊断并回退，不能假装各字段已生效。

定向pytest → 后端全量pytest → compileall/pip check → 前端现有测试/构建 → diff检查；生产文件不超过500行。不发真实模型请求、不改真实配置/依赖/数据库、不提交发布。本片实际运行结果见第8节，前片通过不替代本片验收。

## 7. 依据与后续

本轮已核对本机Read的全文件/分页/超长行、schema与Skill元数据消费者，以及CLI/Web实际装配时点。Pi a69bef789的createReadToolDefinition把选项固定进工具execute和description，setActiveToolsByName随后重建工具与prompt；AgentX 3fcfa0e的BuiltInToolRegistry.createToolsForAgent与Provider.defineTools/createTools分开定义和装配。这里只迁移“定义、描述、执行使用同一事实”的职责，不照搬其配置格式。

Pydantic官方[动态模型文档](https://docs.pydantic.dev/latest/examples/dynamic_models/)说明create_model可从原模型派生并保留校验器。实施时以本机已安装版本和真实BaseTool/Agent测试核验，不能只据文档声称注入或字段约束已正确接线。

本轮另跑一次内存机制探针，退出0：从当前ReadInput派生max_limit=300、max_path_chars=80的公开模型，再派生含ToolRuntime的执行模型；真实@tool的model-visible schema保留上限且无runtime，301/字符串250/未知参数被公开校验拒绝，整数250通过。没有文件IO/模型请求或新增产品模块；此探针只支持拟议schema接法，不代表配置加载、Read执行或CLI/Web接线完成。

后续顺序：其余工具设置/实际额度迁移 → ToolSpec/Registry与内置集合/启动校验 → 动态ToolSet与新权限管线；仍按总Spec先展示每片代码合同再获准实施。PermissionSubject/Auto/动态工具参数注入回归不在本片伪造占位实现。


## 8. 实施结果、迁移与验证

2026-10-16按用户“可以实现吧，然后展示下一个”授权完成。新增config_paths.py与tools/settings.py；CLI/Web默认模型路径和Read设置接线，11个模型探针脚本默认值同步。load_model_catalog保留显式文件参数及原私有权限/JSON/模型校验；不兼容搜索旧默认路径。未移动或改写真实.env、用户模型/设置及数据库，不新增依赖或请求模型服务。

Read删除MAX_READ_BYTES/MAX_PAGE_LINES、静态SessionReadInput和固定数值消息；工厂派生严格schema并固定本App设置，_read使用同一预算。Skill发现的旧Read常量依赖移为自身原64KiB预算。SDK显式配置可复用原工厂/应用接口；CLI/Web统一自动加载一次。

新增tests/conftest.py只隔离tools.settings的目录引用，避免当前CLI/Web测试受个人配置影响；不改模型加载/Path.home/现有应用断言。新两测试文件覆盖默认目录、缺省/非法/不可读/严格冻结、真实Read与CLI/Web、两App隔离、审批恢复冻结、Skill不受页额度影响；错误日志不含原配置/异常正文。models.json默认路径用例仍走真实文件读取/JSON校验与目录构造，SDK构造依赖用已有Fake，保留四模型构造记录断言。

红灯：新设置文件18 failed，入口文件5 failed；前者因接口尚不存在，后者含旧模型路径/CLI必填等差异。入口模型用例首次漏拦旧默认路径，曾触发旧模型配置读取，未请求模型或输出凭据；随后给测试加只允许临时模型文件读取的guard，并隔离与本用例无关的SDK内置资源读取，后续回归全程使用测试配置。没有降低原业务/权限/结果断言。

| 验证命令 | 实际结果 |
|---|---|
| .venv/bin/python -m pytest -q tests/test_tool_settings.py tests/test_tool_settings_entries.py --tb=short | 27 passed in0.81s |
| 10文件定向回归（含Read、权限/批准恢复、Skill、分层与Web/持久CLI） | 补最后4例前143 passed in3.12s；全部新增用例已进入下列全量 |
| .venv/bin/python -m pytest -q --tb=short | 1128 passed in37.28s |
| .venv/bin/python -m compileall -q naiyue_test_agent scripts tests | 退出0 |
| .venv/bin/python -m pip check | No broken requirements found. |
| npm --prefix frontend test -- --run | 9文件/27 passed |
| npm --prefix frontend run build | TypeScript/Vite退出0，186 modules transformed |

当前可用样例见[settings.example.toml](../examples/settings.example.toml)，使用真实加载器验证与ToolSettings默认值相同、无回退警告；全部生产Python文件≤500行。完整tool-runtime.example仍含未接线字段，不能直接作为当前配置。最终静态校验与git diff --check均退出0：Spec链接有效、Python可解析、全部生产文件≤500行、生产/脚本无旧默认路径、新文件无行尾空白、两个TOML可解析。原Web边界测试中的/.env/models.json是未授权URL反例，明确保留，不误删为旧默认入口。

主要当前链：CLI/Web→load_tool_settings→create_session_read_tool→派生schema/提示→原Agent/HITL→_read(settings)→原ToolMessage/ToolError。两App预算不串，审批期间修改配置不影响原工具，参数错误无runtime字段。模型入口默认get_models_path，文件无效沿原ModelConfigError，不能套额度回退。

未做实网模型、真实用户库或Windows执行，未提交/push；实现/测试不记为用户掌握。唯一下一步审阅[085-03B Ls、Write/Edit基础设置](085-03b-file-tool-settings.md)，尚未实施；搜索与Shell设置跟随其实际执行链片，不预建无消费者字段。
