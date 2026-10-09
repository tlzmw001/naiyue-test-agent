# Web 工具与审批：入口、数据流和验证

2026-10-09，按 [Spec078](specs/078-web-tools-and-approval.md)接入现有工具。界面仍参考已确认的浅色聊天区，CSS Modules 延续原样式；本片没有新增依赖/SDK、改写循环或扩大工具权限。

## 使用

在项目根目录启动，指定你准备供 Agent 操作的工作区：

```bash
.venv/bin/python -m naiyue_test_agent.web --config .env/models.json --workspace /绝对工作区路径
```

已有 read/ls/find/grep/write/edit 被真实注册。工作区内操作沿原工具政策执行；工作区外普通读取/目录搜索可触发审批，敏感/无效路径仍拒绝，write/edit 继续原工作区约束。无工作区时继续仅聊天，不默认选择项目根或扩大到整个磁盘。

Bash 另需显式 `--host-bash`；每次命令沿原审批机制。它是宿主机命令，不是沙箱，不能把这个开关当成隔离保证。find/grep 仍使用已有 rg 依赖，缺失时沿工具错误回填处理。

页面默认折叠工具详情，显示工具名及处理中/成功/错误结果/未执行/待核对等状态。展开能看到模型实际提供的参数和对应 ToolMessage 正文；工具结果用转义文本显示，不当作助手回答或 HTML 执行。缺少历史执行来源时显示“已有结果”，不借用其他运行的状态。

审批区展示实际待审调用及后端规范化目标。每项默认无决定，全部选择批准或拒绝后“提交审批并继续”才可用。批准仅对该调用/目标一次生效；不提供前端修改参数/目标或授予永久权限的入口。HTTP失败保留选择，不自动重试；拒绝经原 HITL 返回错误 ToolMessage 给模型。

服务重启后的 interrupted/failed 会话仍不能自动恢复或审批；这需要后续独立恢复协议。当前审批依赖同进程 pending 状态，完整归档、分支、草稿持久化与成熟权限模式仍另做。

## 数据流与接口

```text
启动 workspace / host_bash
  → Web lifespan 注册现有工具 → 现有应用装配/metadata/HITL
用户提示 → 既有 arun → 模型 AIMessage.tool_calls
  → message_completed snapshot → Web/React 工具详情
  → 工具开始/结束事件 → 仅临时状态投影
  → ToolMessage → 按所属 AI 消息＋调用 ID 配对正文

需要审批 → run_finished(interrupted) / 当前 pending 批次
  → GET session 返回 approval_run_id / approval_requests
  → 页面逐项决定 → POST approvals
  → 先查幂等回执，再在准入锁内重新读取批次并校验来源/ID/顺序
  → aresume_tool_approval → 原一次性 grant / Command.resume / 原 checkpoint
  → 新 run_id → 工具真实执行或拒绝回填 → 模型续跑 → 保存后终局
```

审批请求：

```json
{
  "request_id": "浏览器生成的 UUID",
  "origin_run_id": "本批次原运行 ID",
  "decisions": [
    {"tool_call_id": "第一个真实调用 ID", "decision": "approve"},
    {"tool_call_id": "第二个真实调用 ID", "decision": "reject"}
  ]
}
```

`POST /api/sessions/{id}/approvals` 返回202及真实新run_id；JSON中的占位文字只说明格式，不是可直接提交的示例ID。严格schema拒绝额外字段/非法决定，目标、授权值不能由浏览器回传替换。过期原run、ID/顺序/数量不匹配409；重复相同request_id和正文优先返回原回执，正文冲突409。所有API沿原token/Host/Origin检查。202不是执行/保存成功，最终仍消费新run领域终局。

## 代码地图

| 位置 | 改动 |
|---|---|
| web/app.py、settings.py、__main__.py | 工作区注册/显式 Bash/审批路由 |
| application/session_service.describe_session | 派生 approval_run_id，不改DB表或恢复算法 |
| web/service.py、schemas.py | 公共待审列表、严格请求、锁内当前批次校验 |
| web/run_delivery.py | 复用已有独立生产任务和回执；调用异步审批恢复；工具事件投影 |
| web/serialization.py | 保留 AI tool_calls、ToolMessage 调用ID/name/status |
| frontend types/events/api/useConversation | 字段接线、工具状态、审批动作、新run订阅和终局刷新 |
| frontend components/ToolCall、ApprovalPanel、Conversation | 可折叠参数/结果、逐项选择、所属消息内配对和去重展示 |

Pi a69bef789 interactive-mode 的 start/end 根据toolCallId建立/更新ToolExecutionComponent；AgentX3fcfa0e BuiltInToolRegistry根据provider.supports提供specification/executor。这些是已核对的职责参照，不称Pi拥有我们的HITL协议。循环、权限、串并行和工具错误机制仍由原框架/项目模块负责。

## 本次验证

- Python全量 `.venv/bin/python -m pytest -q` → **927 passed in 33.58s**，本片新增9例。
- 前端 `npm --prefix frontend test -- --run` → **9个测试文件、25个测试通过**，本片新增9例；tsc+Vite构建成功，186modules，JS363.27kB/gzip113.80kB、CSS10.50kB。
- compileall、pip check、git diff --check通过；源文件均不超过500行，无.env/真实模型配置/原依赖/关键知识点变更。
- 红灯包含工具尚未注册/DTO丢字段、工具事件/审批组件缺失、原型键被误认已选、跨轮复用调用ID导致旧结果错配；修对应源代码后通过。混合审批测试起初错误假定ToolMessage列表按申请顺序返回，核对框架可先写拒绝结果后，改为精确断言全部ID及对应正文/状态，没有更改框架顺序或放宽成功条件。其他追加验收不冒称逐项TDD。
- 真实文件/工具经HTTP验证六工具同批次write→edit→read/ls/find/grep的结果；Bash显式开启、命令审批后执行合成printf；read不存在路径错误回到模型后运行完成；工作区外批准/拒绝、非法/过期/重复请求、重启审批阻断均覆盖。

离线TCP/两个独立进程：

```bash
.venv/bin/python -m scripts.probe_web_tools --timeout-seconds 60
```

输出：

```text
web_tools_probe=PASS mode=offline tcp=True approval=correlated mixed_decisions=True resume_run=new duplicate_safe=True tool_results=paired two_processes=True history_used=True
```

临时真实SQLite、原生异步假模型、真实read/ls工具、HTTP/SSE；恢复后工具ID和结果确实进入模型历史。该脚本复用原Web探针的进程/截止/关闭机制，原探针默认行为保留。

浏览器用同一生产UI和离线fixture服务，真实审批第一条批准、第二条拒绝，显示成功/未执行及后续回答；工具正文来自实际文件读取/拒绝ToolMessage。1280px和375px无横向溢出、控制台未观察error/warn；窄屏可滚动审批区选择并提交，刷新后结果仍正确配对。截图在本机本次Web工具验证目录。没有新增收费模型调用，不能将这些称为真实供应商工具决策质量或生产业务效果。

下一步先试用这一闭环，再沿入口→装配→消息/工具→审批→恢复→保存/显示进行系统学习。助手完成代码不等于用户已独立实现或能应对追问。
