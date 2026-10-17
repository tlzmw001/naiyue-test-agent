# 工具阶段收尾：真实模型轨迹与数据流

2026-10-17入口版本边界：Spec093已把应用消费者和诊断探针迁到原生异步App，删除独立同步应用/持久化入口；下文早期源码名称和运行记录保留当时证据。当前API用arun/aresume_tool_approval、aget_session_history/arecover_session；框架本身的同步能力与本项目低层同步策略不因此删除。

状态：2026-10-02。此页是代码阅读笔记和合成样本的验收记录；“真实模型”指通过本地配置的 HeiyuCode 网关请求 `heiyu-gpt-5.5`，不能独立证明远端实际模型身份或业务用例质量。用户理解与独立实现仍待反馈。

## 先看本次的真实数据形状

可复现命令：`.venv/bin/python -m scripts.probe_live_workspace_tools`。探针在自动清理的临时工作区创建 `contract.md`，正文为 `CASE_PROBE\nstatus=unreviewed\n`。同一个应用会话依次运行三个用户请求：发现并读取、写入并编辑、审批 Bash。工具和应用入口与 CLI 注册的相同；探针直接调用 `LocalApplication`，没有验收 CLI 人工输入界面。

最终一次轨迹中的模型请求片段：

```python
AIMessage(tool_calls=[
    {'name': 'read', 'id': 'call_FEvrLVVgf6uzGLirZchG0wmo',
     'args': {'path': 'contract.md', 'offset': 1, 'limit': 2000}},
    # 同一 AIMessage 还包含 ls、find、grep 的调用；这里仅展示 read。
])
ToolMessage(name='read', tool_call_id='call_FEvrLVVgf6uzGLirZchG0wmo',
            status='success', content='CASE_PROBE\nstatus=unreviewed\n'
            '[read: lines 1-2; next_offset=none]')
```

上面 `AIMessage`/`ToolMessage` 是按真实调用 ID 和已观察参数构造的**形状展示**，不是直接打印的对象 `repr`；正文和分页提示由当前工具源码及本地相同参数探针确定。本次终端轨迹只打印工具名、调用 ID、状态和 `read` 参数，不打印任意正文。`tool_call_id` 与模型调用的 `id` 完全一致，是把结果交还正确调用的配对依据。

## 从入口到最终回答

1. `scripts/probe_live_workspace_tools.py` 构造七个工具，调用 `build_local_application`。应用读取模型配置，创建 `SessionService`、一个 `create_agent` 运行对象和内存 checkpointer；`create_workspace_session` 把规范化工作区根、模型 ID、私有 `thread_id` 归属于会话。
2. 每次 `app.run(session_id, [('user', text)])` 进入 `SessionService.run`：同会话 busy、待审批和未知副作用先被检查；随后生成新的 `run_id`，沿用同一个 `thread_id`，再把工作区根放入可信运行 context。LangChain 将 `('user', text)` 转为消息对象，并从该 thread 的 checkpoint 取回先前历史。
3. `create_agent` 的 `before_model` 可注入已排队的 Steering；选模 `wrap_model_call` 从可信 `session_id` 取当前模型，`handler(request.override(model=model))` 发起模型调用，规范化结束原因并记录安全用量/失败信息。模型若返回工具请求，得到带多个 `tool_calls` 的 `AIMessage`；不是工具函数自己决定何时执行。
4. `ToolBatchMiddleware.after_model` 根据本轮请求及工具的 `naiyue_execution_mode` 决定是否建立顺序计划。本机安装版 `langchain/agents/factory.py` 约 1088 行把工具与 `wrap_tool_call` 链交给 `ToolNode`，约 1687 行把工具节点接回模型入口；`langgraph/prebuilt/tool_node.py` 约 793 行的 `_func` 从模型消息解析调用列表，为每项构造带同一个可信 context、不同 `tool_call_id` 的 `ToolRuntime`，再通过 `executor.map(self._run_one, ...)` 分发。每项进入 `wrap_tool_call` 链，由项目的批次调度和观测 hook 包住实际工具。纯只读批次没有顺序计划，可能并发；轨迹里结果排列顺序不能证明实际完成顺序。`write/edit/bash` 声明 sequential，**含它们的同轮批次**才按模型调用 ID 顺序放行。本次真实写/编辑是模型分轮逐项请求，没有直接验证混合批次排序；该契约由假模型测试覆盖。
5. 工具函数通过框架注入的 `ToolRuntime` 取得可信 `workspace_root`、当前 `tool_call_id` 和需要时的一次性审批 grant。参数先经 Pydantic schema 校验，正常字符串结果被框架转为 `ToolMessage`，带原 `tool_call_id`；可恢复的 `ToolException` 经过安全错误映射成为 `status='error'` 的 `ToolMessage`。框架把消息合并进状态，下一轮模型会看到它。副作用结果未知则不能伪装为普通可重试结果，应用标记会话须人工核对。
6. 模型没有再请求工具时自然结束，`after_agent` 才检查 Steering/Follow-up/停止边界。本次三个用户请求是三个独立 `app.run`，复用同一会话和 checkpoint；Bash 审批恢复是第四次应用调用，复用同一 thread，但产生新的 `run_id`。

## 七个工具在这条轨迹里各做了什么

| 工具 | 输入与真实样本 | 代码实际职责及边界 |
| --- | --- | --- |
| `ls` | `path='.'` | 先分类目录；工作区内列名称、排序并为目录加 `/`，默认最多 500 项、结果 50 KiB。工作区外目录需审批。显示名称包括 dotfiles，不代表可读正文。 |
| `find` | `pattern='*.md'` | 用 `rg --files` 发现候选，遵循 `.gitignore`，再按 glob 选名称；本次结果包含 `contract.md`。目录边界和 `ls` 一样，缺 `rg` 是工具错误。 |
| `grep` | 搜索 `CASE_PROBE` | 用 `rg` 查正文、返回文件名和行号；目录搜索只收工作区内 Read 策略允许的文件，外部目录不能凭一次审批批量读正文。本次结果包含 marker。 |
| `read` | `contract.md`，后来 `draft.md`；模型传 `offset=1, limit=2000` | 先判路径、权限与 UTF-8；显式分页参数使正文后附 `next_offset` 提示，即便只有两行。正文单页最多 64 KiB/2000 行；工作区外普通文件需逐调用审批。 |
| `write` | 创建 `draft.md` 两行 | 整文件创建/覆盖，默认输入上限 1 MiB，目标必须在工作区且父目录已存在；同目标进程内预留后在同目录写临时文件，再替换目标。本次不是局部编辑。 |
| `edit` | 把 `status=unreviewed` 精确替换为 `status=reviewed` | 只修改已存在的 UTF-8 文件；原文精确匹配、非重叠，分块扫描、生成临时文件并在提交前核对文件未变。最终磁盘文件逐字等于 `status=reviewed\nmarker=CASE_PROBE\n`。 |
| `bash` | `command='printf TOOL_PROBE_OK'`, `timeout=10` | 有效命令都先进入 HITL 审批；工作区只是 cwd，不是沙箱。批准后以受限继承环境启动宿主机 shell，读 stdout/stderr、等待退出；本次结果含 `exit_code=0` 与 `TOOL_PROBE_OK`。长输出的归档、超时未知副作用另有测试，本次短命令没有覆盖。 |

## Bash 审批时，消息和状态怎么变化

模型提出 Bash 调用后，`HumanInTheLoopMiddleware` 的 `when=bash_requires_approval` 返回中断。此时 `result['__interrupt__']` 存在，状态里有提出命令的 `AIMessage`，**尚无 Bash `ToolMessage`，进程也未启动**。`SessionService` 将待审调用 ID、准确命令及工作区保存为 `pending_tool_approval`，应用结果的 `approval_requests` 给调用方展示。

探针先检查审批项恰好一个、工具名是 `bash`、命令精确等于 `printf TOOL_PROBE_OK`、工作区等于临时目录，然后才调用 `resume_tool_approval(session_id, ['approve'])`。恢复时应用用原调用 ID、命令和工作区构造一次性 `BashApprovalGrants`，在相同 thread 上发送 `Command(resume=...)`。Bash 工具执行前再用 `runtime.tool_call_id` 消费这个 grant；三者不匹配就返回错误，不执行命令。执行结果成为原调用 ID 的 `ToolMessage`，框架再调用模型完成回答。这解释了“审批暂停”与“工具执行成功”为什么是两个独立事实。

## 断言、失败案例和证据边界

探针从 `AIMessage.tool_calls` 逐个取 `name/id/args`，从 `ToolMessage` 取 `name/tool_call_id/status/content`；检查两组 ID 完全相同、状态成功，检查 `ls/find/grep/read` 命中固定样本、磁盘文件的最终字节、审批前无 Bash 结果、批准后 stdout/退出码以及最终有模型消息。它验证的是实际工具和回填，不以模型声称“完成了”作为依据。

第二版探针曾把最终 `read` 结果断言成**只能是文件正文**而失败。源码及本地最小探针证明：显式 `offset/limit` 会附加 `[read: lines 1-2; next_offset=none]`；下一次真实运行也观察到模型传这两个参数。修正断言后通过。这是验收代码误解了工具返回契约，不是修改工具掩盖失败。

最终真实模型命令退出 0；工具定向假模型测试 `109 passed`，项目全量测试 `414 passed`、Python 编译检查退出 0、`pip check` 无损坏依赖。测试只用了合成文件和短命令；它没有证明业务用例质量、多用户隔离、OS 沙箱、异步/流式取消、跨重启恢复、其他供应商兼容性或长 pytest 输出的真实效果。
