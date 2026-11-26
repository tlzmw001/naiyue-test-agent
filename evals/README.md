# Naiyue Agent 独立评测

在仓库根目录使用 Python 3.12 创建单独环境并安装依赖。

```sh
python3.12 -m venv build/eval-venv
build/eval-venv/bin/python -m pip install -r requirements.txt -r evals/requirements.txt
build/eval-venv/bin/python -m evals run \
  --config /path/to/models.json \
  --model YOUR_MODEL_ID \
  --dataset evals/data/smoke.jsonl \
  --output build/evals
```

也可以把虚拟环境创建在仓库外。模型配置用 Naiyue 的模型目录格式，模型凭证按该配置指定的环境变量提供。加 `--judge-model YOUR_JUDGE_MODEL_ID` 会给术语题增加语义评分。省略该参数时只运行代码规则，报告的 rubric 留空。真实执行会调用配置所指定的模型供应商。

每次执行在输出目录下面建立一个独立的 `run-*` 目录，包含 `report.html`、`results.json` 和 Inspect 逐题日志。退出码 0 表示已启用断言均通过，1 表示至少一项业务断言失败，2 表示评测配置、执行或评分出错。默认串行运行。系统任务记录应用层首字和整题 Agent 耗时，多轮按本题各轮耗时之和计算。P95 和 P99 使用最邻近秩法。输出 token 吞吐以有完整用量的样本为分母，计时窗口覆盖整题 Agent 执行。首批系统题只提供真实读取工具，多轮按固定脚本澄清。

## 系统提示词数据集

`evals/data/system-prompt.jsonl` 有八条用例，覆盖首次自我介绍、纠正身份误导、当前可用工具、读取能力缺失、基于本地文件回答和直接解释测试术语。其中五条使用单次模型调用，三条使用临时项目会话。两条 Skill 对照题向有、无指定 Skill 的工作区提完全相同的问题，并核对零工具调用。

运行时将 `--dataset` 改为 `evals/data/system-prompt.jsonl`，并传入 `--judge-model YOUR_JUDGE_MODEL_ID`。该数据集每题都含语义细则。省略裁判时，报告仅代表工具调用等代码断言结果。

## 子 Agent 介绍词与路由数据集

`evals/data/subagent-routing.jsonl` 有七条单步用例。每题的 `roles` 定义父模型可见的角色名和介绍词。委派题按预期角色和调用次数核对 `subagent`，并验证参数和新实例约束。无需委派的题检查零工具调用。对照题保持角色名和用户输入不变，交换角色介绍并核对路由改变。运行时替换 `--dataset`，直接使用代码判分。

## 工具选择与参数数据集

`evals/data/tool-calls.jsonl` 有 23 条单步用例，覆盖 18 个通用内置工具和 Runner 的交付目录工具，以及无需调用、首行边界、根据历史更正路径和添加无关背景的等价表达。edit 题以已经取得原文为起点。每道题提供相关工具和容易混淆的候选。模型提出的命令、文件写入、停止任务等请求只参与判分。将 `--dataset` 指向该文件即可运行，评分使用工具契约和每题的预期参数。

## 受控多步规划数据集

`evals/data/planning.jsonl` 包含六条题。其中四条按脚本回填工具结果。使用 `mode: planning`，每次把模型的工具请求和本题当前步骤匹配，回填该步骤的工具结果后再请求模型。原文、成功和失败响应都由 `plan_steps` 声明。评分核对步骤、约束和模型宣告的终态。只读题另核对第二行报告的确切值。其余两条给出同一个文件目标，分别在待修改和已达标的初态运行。评测循环只允许操作临时目录中的目标文件，读写调用使用产品的文件操作实现，最终按文件全部字节判分。已达标题另外检查模型确实读到了目标行、整次没有提出写入。四组均可单独运行，规划只用代码断言。

固定模型联调验证调用和判分的代码链路。以下命令使用评测环境运行集成验证，样例中有一次刻意安排的错误答案，用于验证报告分别记录轨迹成功与任务失败。

```sh
PYTHONPATH=tests build/eval-venv/bin/python -m pytest -q evals/tests/cli_integration.py
```

[首个切片的 Spec](../docs/specs/agent-evaluation-v1.md)

## 系统轨迹评测

`evals/data/trajectory.jsonl` 有 17 条系统题，每条都新建临时项目并走产品的会话与 Run 入口。模型看见用户请求、工具和工作区，参考路径及验收值仅交给评分器。数据行上的 `equivalence` 和 `boundary` 在 HTML 逐题表显示。仅使用配置中的被测模型。

```sh
python -m evals run --config .env/models.json --model heiyu-gpt-5.5 \
  --dataset evals/data/trajectory.jsonl --output build/eval-trajectory
```

| 等价类 | 边界与验收 |
|---|---|
| 已知路径的文件问答 | 一条记录，加入无关背景，空文件，文件缺失；缺失时禁止创建 |
| 日志内容检索 | 同一份日志中匹配 0、1、2 条，检查完整结果与数量 |
| 报告、索引与恢复 | 两份独立报告可交换；索引决定后一次读取；主文件缺失时使用备份；先读清单、再比对两份报告并交付 CSV |
| 代码问答及修改 | 同一阈值的前、等、后三点；修改现有 Python 常量并保留其他语法结构 |
| 文本用例与自动化 | 生成三行边界用例并核对 CSV；生成 pytest 脚本，检查三点断言均能识别错误返回值 |
| 术语及无关消息 | 给出可精确核对的简短答复，项目工具调用应为零 |

`trajectory.references` 的外层列出可接受路径。单条路径由若干阶段组成，阶段内的步骤可以交换或并发。相邻阶段要求前一次模型决策已有工具返回。可选的交付后回读列为另一条完整路径。实际侧使用模型提出的工具动作、参数及同一模型批次内配对的工具结果。每个动作最多匹配一次。没有回执、参数错误和意外失败的请求留在实际动作分母中。

每条题先按最终答复、产物和操作约束判断任务是否达成，再判轨迹。设匹配数为 M，实际动作数为 A，选中参考的动作数为 E，精确率 P=M/A，召回率 R=M/E，F1 为两者的调和平均。空分母约定取 1。例：该读 A、B，实际依次读 A、A、B，答案正确时 M/A/E 为 2/3/2，属按序档，P=2/3、R=1、得 76 分。

| 档位 | 条件 | 首版分数 |
|---|---|---|
| 精确 | 任务完成，完整满足参考阶段，没有额外调用 | 100 |
| 按序 | 任务完成，完整满足参考阶段，另有额外调用 | 60 + 20 × F1 |
| 任序 | 任务完成，所有必要步骤均匹配，但阶段先后不满足 | 40 + 20 × F1 |
| 部分吻合 | 任务未完成，至少匹配一个步骤 | 20 + 20 × F1 |
| 偏离 | 任务未完成，零匹配 | 0 |

若任务完成而现有参考无法解释，保留任务成功，标记待复核并留空轨迹分。最终均分仅使用已分档尝试。逐题 P/R 的宏平均包含待复核尝试。HTML 展示分布、均分和逐题结果；展开记录可查看选择的参考、配对的调用 ID、漏掉的步骤、额外调用和文件验收。业务失败或待复核返回退出码 1，评测执行故障返回 2。Inspect 的二值 accuracy 表示已验收并匹配参考的比例，轨迹百分制以 HTML 和 results.json 为准。

自动化题支持题面指定的直接布尔断言子集。评分器对正确实现和三个单点翻转逐个解释断言，验收时不导入生成的 Python 脚本。本组默认每题一次。

## 系统多 Agent 协作评测

`evals/data/collaboration.jsonl` 有五条真实会话用例，覆盖失败排查与测试设计的角色选择、两角色分工、报告缺失以及用户禁止委派。每题在新项目注册用例指定的子 Agent 角色，父子使用同一被测模型，独立会话通过实际工具回执交接。

```sh
python -m evals run --config .env/models.json --model heiyu-gpt-5.5 \
  --dataset evals/data/collaboration.jsonl --output build/eval-collaboration
```

代码核对角色和委派次数、交接中的路径和目标标识、子会话的读取证据以及回传给父 Agent 的报告。通过子 Run 事件与父工具调用 ID、子实例 ID 建立关联，直接从产品会话历史读取子轨迹。最终答复沿用端到端的代码断言。报告有协作各维度的通过数和逐题证据。首版交接使用明确的文件名和任务关键字，子报告使用可核对的编号。

开放式协作另有 `evals/data/collaboration-open.jsonl` 两题。测试设计题验收可执行的边界测试清单，双角色题验收回归证据与测试用例的对应关系。工具回执由代码核验，允许子 Agent 读材料，或者父 Agent 先读并转交必要事实。采用后者时，裁判检查转交内容是否准确、完整。LLM 按用例细则分别给交接语义、子报告质量、最终整合打 0 至 2 分，报告保留每项分数及理由。协作通过要求代码断言和三个语义维度全部通过。最终整合满分且运行完成时计为端到端通过。执行开放式数据集需显式传入 `--judge-model`。

## 动态多轮交互评测

`evals/data/multi-turn.jsonl` 包含澄清材料、更正输入和收窄结果范围三类任务。每题固定首条消息、模拟用户的私有目标及最终断言。之后由模拟用户根据真实回复生成新消息，Agent 每轮使用同一会话，四次执行使用独立项目、数据目录和会话。每次最多四轮 Agent 回复。

```sh
python -m evals run --config .env/models.json --model heiyu-gpt-5.5 \
  --user-model heiyu-gpt-5.5 --dataset evals/data/multi-turn.jsonl --output build/eval-multi-turn
```

模拟用户使用 `--user-model`，在第二轮或之后可以结束对话。评测按数据集中固定的答案与工具证据判断成功。报告展开后可查看实际对话、停止原因、Agent 耗时和单列的模拟用户耗时。`pass_4` 只有同一道题四次全部通过时为 true，至少一次未达标且四次都有有效结论时为 false，遇到执行错误或缺少结果时留空。总通过率以可判定的题数为分母。固定对话的 `smoke.jsonl` 仍可原样运行。
## 从本机历史抽取评测候选

在仓库根目录手动运行 `python -m evals.curate scan`。传入产品实际的数据目录，那里包含 `projects.sqlite`。输出目录必须是新目录，并位于产品数据目录和登记的项目目录之外。

```sh
python -m evals.curate scan --data-dir "$AGENT_DATA" --project "$PROJECT_ID" \
  --from 2026-11-16T00:00:00Z --to 2026-11-23T00:00:00Z \
  --all-percent 10 --seed 42 --output "$CURATION_DIR/week-all"
```

`--project` 可重复，省略后扫描已登记项目。时间须包含时区，区间包含起点，排除终点。配置 `--rules` 后分别用 `--matched-percent` 和 `--unmatched-percent` 设置比例，默认 100% 和 0%。规则文件使用 `{"any":[{"all":[{"run_status":["failed"]}]}]}` 形状。条件支持 `tool_error`、`min_model_calls`、`min_tool_calls` 和 `min_user_turns`。工具条件可以指定 `root`、`child` 或 `both`，默认 `root`。

以下规则会选出“`read` 报错且会话已有两次用户输入”的运行，也会选出以 `failed` 结束的运行。

```json
{
  "any": [
    {"all": [{"tool_error": {"name": "read", "outcomes": ["error_result", "exception"]}}, {"min_user_turns": 2}]},
    {"all": [{"run_status": ["failed"]}]}
  ]
}
```

查看输出的 `report.md` 可核对样本数量及选中理由。`manifest.json` 是后续步骤使用的冻结清单，包含来源身份、覆盖缺口、各池分母和种子。命令退出码 0 表示已生成报告，部分项目跳过时可从报告查看原因。指定范围内所有项目均无法扫描时，命令生成缺口报告并返回 2。扫描无需模型配置。抽样前仅使用身份、顺序和消息类型等元数据。[离线采样与标注设计](../docs/specs/agent-evaluation-history-curation.md)

## 提取与标注这批候选

扫描在封存 `manifest.json` 后，只为选中的运行补取脱敏片段，并写入 `materials.json`。对先前仅有清单的批次，可用原产品数据目录恢复提取。已提取成功的材料保存在本地。回复分支已改变、诊断过期或无法读库时，候选带缺口记录。

```sh
python -m evals.curate scan --resume --data-dir "$AGENT_DATA" --output "$BATCH_DIR"
python -m evals.curate annotate --batch "$BATCH_DIR" --config "$MODELS_CONFIG" --model "$ANNOTATOR_ID"
```

`annotate` 把脱敏片段发送给显式指定的模型端点，操作前可在 `materials.json` 核对输入。命令默认串行处理，用 `--candidate CANDIDATE_ID` 可以只标注指定条目。每条结果单独写入 `annotations/<candidate_id>.json`，里面有推荐方向、证据引用、改写、缺项、用例对象和配套文件内容。对应一条调用超时或返回无效 JSON 时，保留类型并继续处理其它条目。重复运行会恢复失败项，保留相同材料与配置下已完成的标注。S3 将使用这些草稿生成审核预览和正式数据集。

人工可用 `annotate --batch "$BATCH_DIR" --candidate "$CANDIDATE_ID" --draft "$DRAFT_JSON"` 导入 `{"proposals":[...]}`。人工补充的依据使用 `manual` 引用，在 `changes` 记录构造过程。模型模式省略 `--draft`，人工模式无需模型配置。自动脱敏覆盖常见凭证格式、私密路径及 URL，不能识别每种业务自定义敏感内容。

## 审核并导出评测数据集

运行第一条命令后打开批次里的 `review.html`。报告逐方向展示原证据、输入、判分和隐藏目标、正式 JSONL 行以及 fixture。可见输入和配套文件用于复现历史边界，评分目标保存在 JSONL 的判分字段。相似题只供人工对照，可以选择关联已有题。

```sh
python -m evals.curate review --batch "$BATCH_DIR"
python -m evals.curate review --batch "$BATCH_DIR" \
  --candidate "$CANDIDATE_ID" --direction "$DIRECTION" --revision "$REVISION" --decision accept
python -m evals.curate export --batch "$BATCH_DIR" --version v1
```

审批版本来自报告。改动草稿时，用 `annotate --draft` 导入修改，然后刷新审核报告并确认新版本。可选决定为 `accept`、`needs-material`、`link` 和 `discard`。关联已有题使用 `--decision link --existing CASE_ID`。要对照增量版本，在 `review` 中传入 `--previous VERSION_DIR`。

代码判分题可以在采纳时附带 `--examples CHECKS_JSON`。文件的 `positive` 与 `negative` 分别是交给现有评分器的合成证据。例子至少包含 `completed`、`answers` 和 `calls`，根据评测方向还可能需要工作区或子 Run 证据。两份证据都应是已完成的运行，正例须通过代码断言，反例须至少命中一条失败断言。报告分别显示可加载状态、代码断言自检和真实运行状态。有裁判细则的题还需要实际运行才能得到语义评分。

导出发布到批次的 `versions/<version>`。可以用 `--previous VERSION_DIR` 保留已有增量用例并合入本批采纳项。已发布的同名且内容相同的版本可重复使用，内容冲突报告错误。将整个版本目录交给评测的使用方，其中包含按方向划分的 JSONL、fixture 和版本指纹。命令会打印调用现有评测入口所需的参数。

## 跑一遍从历史到报告的验收

在仓库根目录启用评测环境后执行。测试会登记一个临时项目，生成两轮会话归档，按条件选中第二个 Run，经过标注、审核、导出，再将同一份两轮轨迹题交给现有评测入口执行两次。标注和被测模型均使用固定回复，读取文件走产品工具。

```sh
PYTHONPATH=tests python -m pytest -q evals/tests/test_curation_roundtrip.py
```

两次运行都能读到文件。遵守用户更正的正例读取 final.txt，得到 RUN-47，轨迹为 exact；沿用原路径的反例读取 draft.txt，得到 RUN-18，轨迹为 divergent，端到端失败。生产模型可在评测环境中用下列命令运行自己的导出版本。报告位于输出目录下的 run-* 子目录。

```sh
python -m evals run --config "$MODELS_CONFIG" --model "$MODEL_ID" \
  --dataset "$BATCH_DIR/versions/v1/trajectory.jsonl" --output "$REPORT_DIR"
```
