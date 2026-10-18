# Responses最终参数补齐与定向复验

本次已完成：保留流式，补齐最终工具参数；根批次批准和双子智能体审批两项定向实网均通过。原[首轮报告](provider-acceptance-2026-10-17.md)与52次记录保持原样，不改写原失败。

## 事件证据与原因

[修复前诊断](responses-final-args-diagnosis-2026-10-17.json)新增1次请求：两个function_call开始项的arguments长度均为0，随后各收到function_call_arguments.done及output_item.done，最终参数长度35/36；期间没有function_call_arguments.delta。已安装LangChain转换器只从开始/增量构造普通调用参数，忽略这两类完成事件的最终参数，结果两次args={}。

这次证据确认当前网关的一种实际事件形态；不能反推原两次失败时未保存的每个原始事件。早期Agent假模型测试从已构造AIMessage开始，正常SDK事件样本则有参数增量，均未覆盖此缺口。

## 当前调用链

`load_model_catalog → _build_model → ResponsesChatOpenAI._astream → 请求局部SDK视图 → _FinalArgumentsStream/_ToolArguments → 官方ChatOpenAI._astream及转换器 → AIMessage.tool_calls → 原参数校验 → 权限/整批审批 → 工具执行`。

新增生产模块仅`model_access/responses_stream.py`；`model_access/__init__.py`只对api=responses选择子类。局部视图借用原SDK，未修改共享客户端或全局函数，ModelCatalog继续拥有HTTP客户端及关闭。普通与raw/headers路径均委托官方实现，不复制请求/流式执行器，不改依赖或真实配置。

工具参数按输出索引和身份归集，使用完成事件的严格JSON对象，向官方转换器交付一次完整工具项；乱序完成仍按已声明调用顺序交付。text/reasoning/usage事件即时透传；工具参数在定稿后交付，产品没有参数增量展示消费者，工具原本就等待完整模型响应。身份冲突、两类最终事件参数矛盾、非法JSON/非对象、未完成工具流明确失败，不猜测路径或宽松修补JSON。

## 实网定向结果

[续账复验报告](responses-final-args-validation-2026-10-17.json)保留诊断已用1次，不重放诊断。新增总额度9，SDK重试0，每个场景仅运行一次。

| 场景 | 新增HTTP发送尝试 | 结果 |
|---|---:|---|
| 修复前只读事件诊断 | 1 | 确认最终参数与旧转换器差异，无工具执行 |
| 根批次批准 | 2 | 单批两次Read参数正确；整批先暂停，只有未命中Allow的外部目标待批；批准后两条结果配对、读值正确、待批清除 |
| 双子智能体审批 | 6 | 同一父批两委派、独立作用域、两个同时待批；批准第一个后，它在另一个仍待批时完成；之后批准第二个，两个结果和保存历史正确 |
| 合计 | **9/9** | **定向验收通过** |

加上原52次，当前累计**61次客户端发送尝试**。不是供应商计费账单；网关内部重试不可见。根与子场景分别有1/3条流式消息与完成消息归并，未归并消息均为0。原生Responses、真实App/SQLite和临时合成文件；无用户库操作或生图API。

## 验证与边界

新公开SDK回归先红复现空参数，乱序完成回归先红确认顺序保护；复核发现两种最终事件矛盾的漏检后，新增回归先红后绿补齐。标准/部分增量、完成事件独有、headers、usage、并发借用、文字提前到达、取消/网络失败关闭及两阶段额度保留均有覆盖。架构/安全只读复核该缺口修正后均无确证遗留发现。

验证命令：`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short`；`cd frontend && npm test -- --run`；`cd frontend && npm run build`。最终数值见[Spec100第10节](../specs/100-real-provider-acceptance.md#10-流式最终参数修复用户已授权2026-10-17)。

实际结果：后端**2227 passed in88.78s**（`/tmp/naiyue-100-final-args-backend-final.log`）；聚焦146项通过；前端**18文件96 passed**，TypeScript/Vite **204模块成功**。365 Python AST、417源文件≤500行、446本地文档链接、compileall/pip check/diff、有限报告无URL/凭证形状及9次求和通过。

本次只扩展正式异步Responses流路径；同步SDK对照实验仍属官方适配器原行为。其他供应商/接口、推理档位、Windows/Linux原机、浏览器实网手工操作与业务效果未新增证据。无提交、push或发布。
