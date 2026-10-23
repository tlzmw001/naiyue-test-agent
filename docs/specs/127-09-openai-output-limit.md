# Spec127-09：Responses 单次输出上限被默认值覆盖

2026-10-22。127-08真实验收已经结束且源指纹未漂移；本片只修复已承诺的单次输出上限绑定，不更改记忆语义、实际配置、依赖或旧样本，不追加真实请求。

## 1. 复现与根因

实际默认配置经load_model_catalog创建ResponsesChatOpenAI，构造max_tokens=模型上限16384。memory_job_runner._limits选择记忆max_output_tokens=4096，prepare_memory_output调用共享bind_model_output_limit；当前helper绑定max_tokens=4096。

实际24个请求均携带max_output_tokens=16384，两协议相同。最小离线探针：构造max_tokens=16384的ResponsesChatOpenAI，然后_get_request_payload(...,max_tokens=4096)得到16384；传max_completion_tokens=4096得到4096。根因为当前SDK默认参数已经使用max_completion_tokens，Responses转换先处理绑定的max_tokens再处理默认max_completion_tokens，后者覆盖前者。

旧SDK mocks未设置OpenAI模型的默认上限，没有覆盖这个分支；raw.kwargs里出现max_tokens只证明绑定对象字段，不证明最终HTTP请求。保留原失败证据，不能称旧测试已覆盖真实默认值场景。

## 2. 最小修复与调用边界

仅model_access.bind_model_output_limit：ChatOpenAI及其Responses/Reasoning派生类使用当前SDK的max_completion_tokens绑定覆盖默认值；Anthropic/普通fake继续max_tokens，Ollama保留现有num_predict模型副本。签名、共享模型、回调、SDK重试及客户端生命周期不变。

正常：调用方确定更小上限→helper绑定相同SDK规范字段→Completions请求max_completion_tokens/Responses请求max_output_tokens为单次值。失败：供应商或预算/停止仍沿原异常路径，不通过切换协议或改默认配置回退。不要新增另一层请求限额解析。

实际消费者只有memory_output.prepare_memory_output和context_compaction的摘要请求；二者都应使用单次值。根Agent正常请求不使用helper，仍保留模型配置默认值；不将本片扩展到全局预算策略。source search及全部既有测试验证消费者。

## 3. 验证

先在test_model_memory_output补实际SDK/MockTransport：OpenAI Completions与Responses × text/json_schema/function_calling，模型默认16384，单次77，断言实际wire为77、只发一次、原模型与默认值不变。确认Responses三例红灯后改helper。已有原生包装/摘要参数测试迁移到规范字段名，保留精确上限和模型不变断言，不放宽业务检查。

产品修改范围仅helper；测试修改test_model_memory_output.py、test_model_limits.py。专项、完整后端、前端test/build、compile/pip/500行/diff、标准Mac wheel及源码外安装分别报告。新24次语义结果仍属于修复前代码；保存当时的源码指纹及实际上限，不将其冒充修复后真实验证。修复后的wire由SDK mocks证明，真实模型预算本轮已经用完。

## 4. 实际结果与影响核对

新增6项SDK实际请求回归，先得到**3 failed/3 passed**，三个Responses模式均复现16384而非77，Completions原有三个模式通过。最小修复后专项`.venv/bin/python -m pytest tests/test_model_memory_output.py tests/test_memory_structured_output.py tests/test_memory_job_runner.py tests/test_model_limits.py tests/test_context_compaction.py -q`为**95 passed in2.38s**；已迁移两处旧参数名断言，精确上限/次数/共享模型不变断言保留。未skip、放宽断言或升级依赖。

影响核对：`rg -n 'bind\([^\n]*max_tokens|bind_model_output_limit|max_tokens=' naiyue_test_agent`确认helper真实消费者仅memory_output及context_compaction，均通过此入口修复。构造器的max_tokens=output_limit只设模型默认值，没有同时绑定别名的覆盖问题，保留；根Agent普通请求继续使用模型默认上限，不增加新分支或全局限额策略。

最终完整后端`PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q`：**4637 passed in306.85s**，测试前后664份Python源码SHA一致。前端50文件/422项通过（4.64s）、TS/Vite零错误（保留既有体积警告）；206产品/45脚本/413测试及前端源500行、compile/AST/pip/diff通过。

标准Mac ARM64 wheel、Twine及源码外干净安装通过，默认记忆主链、重开及现有安装门禁通过，安装阶段0供应商请求。206份Python/4份前端资源与最终源码一致；wheel SHA-256 `29db12b55fc033719c014b7891747aa2f426ae7686d5f387c71fc496c5678bf8`。其他平台本片未重新构建/原机运行。实际配置SHA不变、用户数据库未操作，无提交/push/发布。

证据保存于`build/spec127-08-g24w0im4/{model-access-before-cap-fix.py,tested-source-cap-fix.json,backend-cap-fix.log,frontend.log,build-cap-fix.log,wheel-cap-fix.json,install-cap-fix.json,verification.json}`。源码外安装验证默认记忆主链；原生通道的带默认值覆盖由源码环境SDK模拟HTTP测试证明。本片没有修复候选筛选/分类/语言噪声，没有新真实调用，不将本修复或全绿测试宣称为自动记忆质量整体达标。
