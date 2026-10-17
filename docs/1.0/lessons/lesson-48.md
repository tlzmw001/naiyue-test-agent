# Lesson 48：wrap_*的handler进入、返回与分支

> 第三章第5主题，2026-10-12。已验证确定性测试并提供讲解材料；用户复述、本人走读和独立实现未评估。

## 职责与输入输出

wrap_model_call/ wrap_tool_call在对应节点执行函数内部包裹下游调用，区别于before/after作为图节点连线。handler是框架提供的下一层可调用对象，不固定等于模型SDK或工具主体。注册[A,B]时A最外层，A收到通向B的handler，B收到通向底层执行的handler。

- 模型输入ModelRequest，观察request.messages/request.model；本课handler正常返回ModelResponse，result内含AIMessage。
- 工具输入ToolCallRequest，观察request.tool_call的name/args/id；本课handler正常返回ToolMessage，包含content/tool_call_id/status。
- 框架还支持AIMessage、ExtendedModelResponse、Command等相应合同，本课不扩展这些分支。不调用handler时仍须返回受支持的结果，不能随意返回None。

## 完整分支与测试入口

复用[现有测试文件](../../../tests/test_agent_middleware_order.py)新增test_wrap_handlers_enter_return_and_preserve_boundaries参数分支；没有新生产机制或平行运行时，不称TDD。

| 参数ID | 动作与证据 |
|---|---|
| pass | A进→B进→底层→B出→A出；完整模型→工具→模型 |
| model-short | B直接返回ModelResponse；底层模型与工具均未执行，外层A收到short answer |
| tool-short | B返回带call-1的合成ToolMessage；工具主体未执行，模型仍收到结果并继续 |
| model-error | 假模型抛固定failure；B/A捕获记录后裸raise，没有exit，调用方收到同一异常 |
| tool-error | 工具主体抛固定failure；B/A捕获记录后裸raise，没有tool exit/第二模型调用，原异常身份保持 |

short分支仅证明可提供替代结果，本例数据是合成测试结果，不证明真实工具成功。工具异常是否转换为结果还受ToolNode错误配置影响；本课RuntimeError实际传播，不泛化所有工具错误。

```text
正常模型调用：A.model.enter → B.model.enter → model → B.model.exit → A.model.exit
正常工具调用：A.tool.enter → B.tool.enter → tool  → B.tool.exit  → A.tool.exit
模型异常：A.model.enter → B.model.enter → model → B.model.error → A.model.error
```

等价调用关系（省略响应规范化和Command收集）：A.wrap(request, lambda req: B.wrap(req, execute))。进入正序、返回逆序来自嵌套调用；before/after顺序则由图连线决定。handler(request.override(model=...))在本次模型请求中使用新模型，不会自动更改会话配置。

handler可调用多次：复用tests/test_failure_annotations.py::test_annotation_does_not_add_wrapper_frames_or_change_typed_retry，本机ModelRetryMiddleware首次handler抛ModelTimeoutError、再次正常返回，attempts长度2。它证明内置middleware能力，不表示项目默认启用了这项Runtime重试；SDK重试与重放资格/预算仍按独立设计。

## 验证与走读

```bash
.venv/bin/python -m pytest -q 'tests/test_agent_middleware_order.py::test_wrap_handlers_enter_return_and_preserve_boundaries[pass]'
```

优先pass，再选其他参数。整文件本轮6 passed in0.20s；上面既有重试用例1 passed in0.78s；compileall该测试及diff检查退出0。没有实网、业务效果或全量回归的新证据。

入口断点：参数测试trace=[]；创建Agent时步过，invoke进入调用链。WrapMiddleware._call当前帧看self.label、kind、scope、outcome、trace；调用前request.messages或request.tool_call，正常返回后看result.result或result.content/tool_call_id。异常路径result没有本次正常返回，查看failure及捕获分支，不重新执行handler求值。

源码：本机langchain1.4.2 factory.py的_chain_model_call_handlers/ _chain_tool_call_wrappers组合A/B；types.py的wrap_*合同；tool_node.py的_run_one把execute回调交给工具wrapper。项目call_observation.py的选模wrap_model_call和_RecordToolCallMiddleware使用handler与裸raise，复用第二章异常合同。

当前下一步：本人走读参数pass，按疑问看返回/短路/异常；后续第6主题显式context与动态选模，只补本章调用点。
