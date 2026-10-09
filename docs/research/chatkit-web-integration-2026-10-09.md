# ChatKit 与现有应用的接入评估

2026-10-09，只读调研；用户已确认ChatKit Playground聊天区域的外观，未确认必须采用其运行协议。推荐方案仍待技术选型确认，未安装SDK或实现Web。

## 当前项目

当前AsyncApplication可创建/选模/运行/审批恢复/停止，并提供带session/run/sequence的事件流。StreamingMessageProjection区分draft、完整消息、未确认文本；run_finished在保存确认之后。HTTP消费断开不停止生产。持久模式为本地同库单实例/单活动run，aget_messages是当前checkpoint，不是完整聊天归档。

## 参考版本与源码事实

在临时目录只读克隆，没有运行第三方项目或执行其安装脚本：

- ChatKit JS：22613848a656077a408788f028c7727e38d10449，类型包版本1.9.0。React binding调用Web Component，README/quickstart加载OpenAI CDN脚本，仓库未提供可完整追踪的组件运行实现。CustomApiConfig要求符合ChatKit协议的URL；不能直接替换成我们的RuntimeEvent接口。
- ChatKit Python：dacc133c280b39b9334d06ea73f0f1c199e59927，版本1.6.5。ChatKitServer.respond可自行输出ThreadStreamEvent，不强制把业务循环迁移到Agents SDK；当前包依赖确实包含openai-agents。
- Store定义线程、消息分页和附件的读写契约；可以用适配器对接已有数据库，并非必须再建一个数据库，但仍需明确两套消息表示的关系。
- Python server._process_events在ThreadItemDone时调用store.add_thread_item；handle_stream_cancelled默认保存非空未完成助手文本，并添加取消的隐藏上下文。我们必须覆盖/适配这套默认行为，保持未完成内容不成为已确认模型历史。
- JS公开接口有sendUserMessage/setComposerValue/sendCustomAction/fetchUpdates及响应开始/结束事件；未找到标准Steering/Follow-up方法。官方SDK指南说明某些命令在加载或响应流期间可能拒绝，应在busy时保护调用。不能据此断言ChatKit绝对不能做运行中输入，但必须做额外适配/运行验证。

源码证据：

- [JS类型与CustomApiConfig](https://github.com/openai/chatkit-js/blob/22613848a656077a408788f028c7727e38d10449/packages/chatkit/types/index.d.ts#L739)
- [React绑定方法](https://github.com/openai/chatkit-js/blob/22613848a656077a408788f028c7727e38d10449/packages/chatkit-react/src/useChatKit.ts#L13)
- [运行中命令指南](https://github.com/openai/chatkit-python/blob/dacc133c280b39b9334d06ea73f0f1c199e59927/docs/guides/keep-your-app-in-sync-with-chatkit.md#L64)
- [默认取消处理](https://github.com/openai/chatkit-python/blob/dacc133c280b39b9334d06ea73f0f1c199e59927/chatkit/server.py#L387)
- [消息流与Store写入](https://github.com/openai/chatkit-python/blob/dacc133c280b39b9334d06ea73f0f1c199e59927/chatkit/server.py#L837)
- [官方自建后端](https://developers.openai.com/api/docs/guides/custom-chatkit)、[官方动作](https://developers.openai.com/api/docs/guides/chatkit-actions)

这些是公开源码/文档事实，不是ChatKit与本项目接线已经通过的证据；未做实际组件busy/取消实验。

## 与Pi、AgentX比较

Pi a69bef789：interactive-mode输入分支根据session.isStreaming调用prompt(streamingBehavior=steer)，消息/工具事件由独立渲染组件消费；agent_end与agent_settled分别处理。AgentX3fcfa0e：AbstractMessageHandler.processChat的partial经MessageTransport发送，完整响应先保存再发送结束。借鉴职责分离，不把Java TokenStream当作Python事实。

推荐保留当前应用为执行与持久状态的权威，Web为HTTP适配和展示。ChatKit可接，但这一阶段直接消费现有事件更少协议映射，也便于保持Steering/Follow-up/活动停止。UI视觉仍按已确认的ChatKit截图实现，不变成工程仪表盘。

## 候选技术与实际环境检查

建议React+TypeScript+Vite做前端，普通CSS变量统一样式；Python FastAPI/Uvicorn做HTTP入口，官方SSE响应发送事件，fetch读取流并携带本地授权头。FastAPI lifespan拥有原AsyncPersistentApplication，不按每次HTTP请求重建Agent。

本机Node24.14.0、npm11.9.0可用；没有现存前端包，也未安装FastAPI/Uvicorn/ChatKit。PyPI/npm可达，2026-10-09查询版本FastAPI0.143.0/Uvicorn0.54.0/React19.3.0/Vite8.3.4。只读pip dry-run在现有requirements约束下成功，计划新增annotated-doc0.0.5/click8.5.0/opentelemetry-api1.45.1/starlette1.7.0及上述两个后端包，不升级已有依赖。仅证明依赖可解，不是安装或应用兼容测试。

官方依据：[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[FastAPI SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)、[Vite环境要求](https://vite.dev/guide/)。新语言/依赖/初始化配置须随阶段方案确认后再引入；不修改.env或迁移当前数据库。

## 两个方案

推荐：自建薄Web入口和聊天组件，直接适配当前应用事件；UI样式与已确认截图对照。前端有少量自己的状态代码，但不重写Agent循环。首片闭环以Spec077界定，工具审批等后续叠加。

备选：完整ChatKit自建后端，增加RuntimeEvent→ThreadStreamEvent、Store与取消/输入控制适配；保留LangChain。若采用，必须先用无模型/假模型探针验证运行中发送、显式停止与断开区分、保存确认三个核心契约，再写正式接线。

最小选项：先做同一推荐栈的对话/停止闭环，按切片加工具与审批；不先做一份将来丢弃的静态HTML或迁移现有运行时。

需保留的限制：普通工具settle无固定停止上界；历史页面首次只能恢复checkpoint可用记录，压缩前完整对话仓库另做；本地访问控制不等于SaaS身份/租户隔离。不把“原生外观”称为官方产品或像素完全相同。
