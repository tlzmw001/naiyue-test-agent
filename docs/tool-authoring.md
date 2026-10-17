# 自定义工具：注册、停止与资源所有权

2026-10-16，当前实现入口为 `ToolSpec`、`ToolRegistry` 和框架注入的 `ToolRuntime`。090B补齐文件工具安全点检查；[091A](specs/091-tool-progress.md)已提供ToolProgress后端事件，091B已接Web/CLI阶段/计数/尾部及重连快照；进度不替代最终结果，界面停止中仍需工具配合清理。代码注册/会话活动集合见[085-05](specs/085-05-dynamic-tools.md)。

## 一个可注册的文件工具

把下面代码放进项目可导入的Python模块，在应用装配代码中导入工厂。框架注入runtime，模型仅能提交path；权限准备、整批审批和执行许可仍走同一管线。

```python
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.control import check_tool_stop
from naiyue_test_agent.tools.permissions import FilePermission, require_file_target
from naiyue_test_agent.tools.schemas import build_tool_schemas
from naiyue_test_agent.tools.progress import ToolProgress


class CountInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    path: str = Field(min_length=1)


def create_line_count_tool(*, progress_settings=None):
    public, execution = build_tool_schemas('LineCountInput', CountInput)

    @tool('line_count', args_schema=execution)
    def line_count(path: str, runtime: ToolRuntime[dict, dict]) -> str:
        """Count lines in one permission-approved file without returning its body."""
        target = require_file_target(runtime, path, operation='read')
        control = runtime.context.get('_run_control')
        stopped = 'line_count stopped by the user; no complete count was produced.'
        check_tool_stop(control, stopped)
        progress = ToolProgress(runtime, progress_settings)
        count, tail = 0, b''
        processed = 0
        try:
            with target.open('rb') as source:
                while chunk := source.read(64 * 1024):
                    check_tool_stop(control, stopped)
                    count += chunk.count(b'\n')
                    tail = chunk[-1:]
                    processed += len(chunk)
                    progress.report(phase='count_lines', completed=processed, unit='bytes')
                    check_tool_stop(control, stopped)
            check_tool_stop(control, stopped)
            return str(count + int(bool(tail) and tail != b'\n'))
        finally:
            progress.flush()

    return ToolSpec(line_count, public, side_effects='read_only',
                    permissions=FilePermission('path', 'read'))
```

`64 * 1024`是该示例的IO分块大小，不是模型输出或执行期限。实际工具预算应像内置工具一样由工厂接收配置，不把路径、URL、凭证或模型写死。

ToolProgress使用官方runtime.stream_writer，工具不用选择同步/异步运输：同步适配已注入本run接收器，正式异步流订阅custom。工厂可接收应用启动时的settings.progress，沿用同一份enabled/节流/文本预算；没有writer或独立调用缺少批次身份时不产生进度。report只报真实阶段及累计量，总量未知用total=None；文件正文/图片/凭证不要放进text。text表示最新完整尾部快照，不是追加片段。flush只提交最后一份真实待发更新，不生成心跳，不改变权限/停止/工具结果。普通观察失败安全诊断，框架控制及BaseException仍传播；控制异常后停用报告器，不在finally重试该writer。

启动时可把 `create_line_count_tool()` 放入应用构建入口的 `tools` 列表。已持有异步应用和会话时，也可执行：

```python
await app.aregister_tool(create_line_count_tool())
await app.aset_active_tools(session_id, [*app.get_active_tools(session_id), 'line_count'])
```

注册名需唯一；注册只登记定义，启用由会话活动集合决定。更新在下一次工具集合装配时生效，不改变已经准备好的批次。纯代码文件改变仍需Python重新加载/服务重启，当前不提供磁盘插件自动加载。同步应用对应 `register_tool` / `set_active_tools`。

## 三层分别保证什么

| 负责层 | 当前保证 | 作者仍需做的事 |
|---|---|---|
| 注册表 | 原始BaseTool、公开/执行schema及校验一致、runtime注入隔离、名称/结果通道、串并行/副作用注解、权限适配有效 | 注册成功不能证明循环响应停止、网络会返回、资源能关闭 |
| 运行层 | 权限与许可、批次排序/并发、统一ToolError、原RunControl、开始/结束事件、已有IO的settle | 不强杀任意Python线程，不提供任意SDK或远端副作用的硬取消保证 |
| 工具作者 | 用真实IO和失败注入测试证明自身合同 | 设置SDK的IO期限，在安全点检查停止，在finally/上下文管理器中释放自己拥有的资源 |

没有强制的timeout/cancel/cleanup三回调，不新增注册字段或停止异常类。`side_effects='may_mutate'`用于描述可能修改状态；它不是权限，也不会自动排队文件冲突。存在顺序要求的工具应声明 `execution_mode='sequential'`。同批存在串行工具时整批顺序执行，Write/Edit同目标冲突仍为busy，不提供跨会话文件事务。

## 停止、异常和清理

从现有 `runtime.context['_run_control']` 取停止信号，内部函数直接传递control。`check_tool_stop(control, feedback)`只在requested时抛原有ToolException；它不授权、不关闭资源、不取消任务。自定义工具无需导入运行层的RunControl类，也不应自行新建全局ContextVar。

循环在分块前后检查，外部SDK调用在返回后再检查。具体提示由工具准备，说明是否返回了完整结果、哪些副作用已经发生。普通可理解失败用 `ToolException('给模型看的具体提示')`；意外Exception由统一ToolError转换。不要把异常正文里的请求、凭证或文件内容直接当作提示。

`asyncio.CancelledError`、框架GraphBubbleUp/GraphInterrupt及其他BaseException控制路径，在释放自己资源后原样传播。不要用裸except或捕获BaseException生成工具反馈。必要清理失败仍按所属机制处理；原有文件临时文件删除失败会记录安全诊断，保留主异常，可能遗留临时文件，不得声称清理成功。

写操作应明确提交边界：

```text
取得目标占用 → 扫描/临时写入/复核：可检查停止并放弃
 → 最后一次停止检查 → 开始提交：等待真实结果
 → 成功或真实错误 → 关闭句柄/清理 → 释放占用
```

提交已经成功后，即使收到停止，也要返回成功；不能改写为“没有修改”。当前Write还可能创建父目录，提交前停止仅保证本次没有提交目标内容。检查点不能消除最后检查与系统调用之间的竞态，也不能中断系统调用或Pillow内部执行。

## 同步/异步IO与资源所有权

原生async工具用async SDK及 `async with` / `try/finally`，在可取消等待被取消后完成必要清理并保留原控制异常。同步SDK配置它本身支持的连接/读取等有限IO期限，来源为工具配置；业务执行总期限和单次网络等待期限分别决定。

框架可在线程中执行同步工具，但线程执行不是硬取消。外层 `wait_for` 超时、取消 `to_thread` 的等待或关闭事件流，都不能证明底层线程/远端计算已经停止；不要因此提前释放busy或另起重复修改。已启动IO按现有合同settle，自定义工具必须实际配合停止。框架无法替没有返回上界的第三方SDK建立硬终止保证。

只关闭自己创建并拥有的client、响应流、句柄和临时文件；App借给工具的共享client仍由App关闭。不要每次调用都关闭借用的client。启动外部程序优先复用 `HostProcessRunner` / `ProcessRequest` 的进程树、输出管道和consumer收尾；不要创建不受跟踪的后台线程/任务。090C已将Bash/PowerShell/Grep/Find统一为默认无执行期限：公开timeout省略/null/0无限，正整数指定本次秒数且无300秒上限。ProcessRequest内部使用timeout_seconds=None或正的有限秒数，工具负责把公开0归一为None；解释器探测、模型网络和必要清理仍有各自期限。“无限执行”不能关闭停止、背压或资源收尾。

## 作者的确定性验收

- 正常结果：真实工具经过注册、启用、权限准备和调用，返回对应call_id的正确ToolMessage；runtime不出现在模型schema。
- 停止：用Event暂停真实IO，在运行中请求停止，再放行；检查完整/部分结果声明、原文件字节、无后续模型及必要保存。
- 资源：等待期间busy保留；停止/失败后句柄关闭、临时文件清理、占用释放。注入清理故障时诊断可见，主异常和控制异常不被普通观察覆盖。
- 提交竞态：提交前停止不修改目标；已经进入提交时等待真实成功/失败，不自动重试。普通IO故障与停止同时发生时保留真实故障原因。
- 生命周期：成功/普通错误/取消/清理失败都可观察；不靠极短sleep或注释证明行为。不响应停止的SDK仍须在工具说明中交代限制。

当前内置实现可从 `tests/test_file_tool_stop.py`、`tests/test_file_tool_stop_integration.py` 和 `tests/test_ls_feedback_and_stop.py` 开始核对。前两份分别验证文件安全点与真实异步图/SQLite的接线；第三份保留Ls已获得的授权部分结果及不完整提示。进度见 `tests/test_tool_progress.py`、`tests/test_tool_progress_integration.py`、`tests/test_tool_progress_builtins.py`：包括工具结束前接收、同步并发1/2、排队、预算/身份/迟到/控制异常、四种应用配置接线及八内置真实IO点。
