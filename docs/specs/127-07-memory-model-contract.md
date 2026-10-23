# Spec127-07：精简后台记忆模型契约

2026-10-22，用户批准先精简输入输出，再验证结构化返回通道。沿127-03/04/05已接线流程，不改变公开memory工具、数据库、检索算法、TTL、生效门禁或真实配置。不增加模型请求、依赖、发布或全库重整。先前48次真实调用预算已耗尽，本片仅离线验收；原冻结样本和分数不改。

后续版本边界：[127-08](127-08-memory-semantic-ab.md)已在新的24请求预算内完成真实text/json_schema对比；本文件的离线结果保留为07时点证据。真实默认模型暴露Responses单次上限会被模型默认值覆盖，旧SDK mocks缺少非空默认上限，[127-09](127-09-openai-output-limit.md)另行修复；不能继续把07 mock上限检查当作已覆盖该分支。

## 1. 当前问题与主链

MemoryJobs → memory_job_runner._prepare → build_batch / _related → 一次已配置模型调用 → parse_model_result → proposal_request → MemoryAccess.query → memory_mutations.commit。现有来源/旧记忆对象同时承担模型输入和程序校验；模型还需抄写action、expected_version和proposal嵌套。第二轮7条格式失败中，6条仅重新安放字段即可通过旧schema，1条另缺action/scope；这不是语义或提交成功证据。

正常场景：保存的用户约定含source_key和原文 → 模型返回平铺context建议 → 程序补workspace、title、reason和完整来源 → 03提交。更新仅能选择最终实际发送的完整related记录ID，预期版本来自调用前冻结快照；等待期间另一会话更正/遗忘 → 03拒绝旧版本，其他建议/概览独立保留。失败/截断/拒绝/协议不符不触发格式修复调用，沿原partial/failed和失败跳过语义。

## 2. 模型输出

顶层仍为overview（goal/progress/blockers/next_step）和proposals数组。条目平铺，核心必填kind、subject、body、basis、intent、sources。body<=800字符且继续经过03 UTF8限制；sources保留本批source_key和精确、唯一quote，不接受模型提供身份、偏移、时间。

- 可选scope（新记录缺省workspace，personal需显式选择）；可选memory_id（仅本批实际发送且完整的related记录）。有ID为update，无ID为record；旧action、expected_version、proposal嵌套、tags、deadline不再是后台输出字段，严格拒绝，不保留双入口。
- title可选：新增缺省subject，更新缺省旧title；subject仍必填并保留原匹配职责。reason可选：新增使用描述提议来源/依据/意图的程序文本，更新缺省保留旧reason；真实技术选择理由仍写正文。
- machine/module/target/aliases/gaps可选。新增未提供使用None/空列表；更新未提供或null保留冻结旧值，显式非空值替换，aliases/gaps的显式[]表示清空。条件null不支持后台清空，显式工具合同不变。
- 更新scope缺省继承，显式不同则拒绝；kind仍须匹配。重复ID跨scope无法唯一绑定或预览不完整时拒绝更新。旧tags由快照保留；已有deadline沿03继承。
- gaps仅表示阻止本条断言成立的缺口。用户确定流程不要求流程已执行；用户确定环境不要求所有测试已通过。更新转正须显式清除已补足的旧gaps，不能因省略自动清除。无法复现目标的有限经验保持候选，不放宽门禁，也不悄改e06冻结评分。

Pydantic定义同时生成请求schema和本地条目校验；03完整MemoryProposal/MemoryInput仍作必要的业务边界校验。复用proposal_request转换，模型输出不直接入库。

## 3. 输入及可信快照

模型source只看source_key、role、text、occurred_at、name、tool_status及明确的分段/截断标记；完整session/thread/message/reply ID、revision、offset由程序保留。batch只提供sources、complete、skipped；位置/history_version不交模型填写。previous_coverage保留有意义的遗漏，不暴露任务/run身份。

批次装箱与最终消息限额使用同一个model_batch投影估算，避免仍按内部完整信封计算而把实际能容纳的文本全部跳过。完整信封继续用于源版本、身份、敏感检查和偏移还原。

related模型视图保留ID、scope、kind、status、title、body、conditions、complete；候选另保留basis、gaps、reason。version、aliases、tags、deadline由程序快照保留。输入预算移除可选related后，只允许剩余实际发送ID更新，不能引用被预算丢弃的记录。

active和candidate都通过带version的read取得完整正文/元数据；每次read独立核许可并检查同一版本，不能用search预览作为保留旧字段的依据。读取失败的记录不进入更新白名单。related仍是有界召回，未召回的记录不能更新，不承诺模型总能找到所有旧记忆。

_prepare返回本批完整batch、模型消息、已准备调用对象、旧覆盖快照、实际发送related快照和输出模式；_batch直接消费这些事实，不在模型返回后重新查询版本补齐。无新共享缓存/数据库事务等待模型。完整源仍用于敏感检查和引用还原。

## 4. 结构化输出通道

模型profile新增memory_output=text|json_schema|function_calling，缺省text，不根据名称猜测网关能力，不自动变更协议重发。仅用于后台memory调用，其他Agent/摘要/审批请求保持。

text沿原单次调用和严格JSON解析；json_schema/function_calling使用当前安装LangChain with_structured_output，include_raw保留原始AIMessage。OpenAI原生支持时传strict，Anthropic/Ollama使用各自已安装入口。构造过程不得静默降级；不支持或声明不符按批失败，记录安全错误，不包含提示/原始供应商异常正文。

实际请求schema计入输入预算；配置小到容不下schema和来源时不发请求，给出预算诊断。保留原max_output、SDK重试、总期限、停止/同步线程收尾、memory调用配对和用量。

当前固定SDK的include_raw实现为RunnableSequence内的RunnableParallel/raw绑定。输出上限必须绑定到实际raw模型；外层RunnableParallel不转发任意invoke kwargs。仅修改本次新建wrapper，原模型不变；结构或声明协议不匹配则在IO前拒绝。验证基线为LangChain1.4.2/core1.6.6、OpenAI适配1.6.6、Anthropic适配1.7.5、Ollama适配1.1.0，未升级依赖。Anthropic thinking等返回不同组合的模式目前不在该受支持结构内，显式配置后会失败而非自动回退；不承诺所有SDK组合可用。

接线中复现既有Ollama限额helper将num_predict放在顶层导致AsyncClient.chat TypeError；最小修复为浅复制现有ChatOllama并设置num_predict，再bind，SDK在options中输出上限。真实客户端、原有temperature/num_ctx及回调保持，原模型不改写。共享helper的摘要消费者由现有回归覆盖，不增加新的模型限额策略。

解析只消费raw，不使用可能宽松修复的通用JSON解析结果。json_schema仍拒绝工具调用；function_calling仅接受单个MemoryResult返回调用，无其他有效/无效调用，不运行工具；原始JSON参数可用时仍检查重复键、非有限值、截断和拒绝。顶层先校验，建议逐条校验，不因一条失败丢弃已合法概览/其他建议。

## 5. 修改范围与验证

生产：memory_batches.py、memory_job_runner.py，必要的新模型请求适配模块和model_access配置校验。测试：新增平铺/投影/快照及结构化请求测试；迁移旧后台夹具和release_memory_smoke，不改显式工具夹具的公开结构。probe_memory_semantics回调从选定模型配置接收同一output_mode并解析原始AIMessage，避免函数返回被验收报告误判为文本格式失败；请求预算/冻结标签/评分不变。文档：本Spec、127主Spec、工具说明、配置示例、handoff。共享并行目录改造保持，不修改实际.env/models.json或用户数据库。

先红灯覆盖：最小平铺新增、个人范围、旧包装拒绝；更新冻结版本/省略保留/显式空值、伪造和预算移除ID拒绝；输入无程序身份但来源仍精确；正常保存、并发更正/遗忘；三模式参数、schema预算、截断/拒绝/多调用/错误返回名、逐条partial、单次调用与事件配对。旧测试迁移保留原行为断言，不放宽或skip。

验证命令：`.venv/bin/python -m pytest tests/test_memory*.py tests/test_probe_memory_semantics.py tests/test_release_memory_smoke.py tests/test_model_memory_output.py -q`；完整后端沿既有fd/rg PATH执行tests；前端既有test/build；compileall/pip check/500行/git diff --check；标准Mac wheel核对全部源码字节，并在源码外干净安装验证默认记忆主链。原生协议wire在源码环境使用实际SDK/MockTransport验证；不把字节一致说成已在安装环境分别运行全部原生协议。真实A/B、网关兼容性、泛化效果另需新的调用预算，不使用历史13/24冒充本片质量。

## 6. 红灯、迁移与证据边界

平铺合同首轮12失败/10通过，失败对应旧包装和缺少模型投影；原生通道先验证配置和实际wire失败，再补SDK绑定。Ollama文本通道也先独立复现上述TypeError，再修共享helper。probe模式增量先3失败再通过。未skip、放宽断言或修改冻结语义样本。

已有w04假模型原先通过测试侧通道持有旧ID，最新输入又使用显示名称，真实related为空；这不再满足“实际发送的ID才能更新”的合同。连续三次失败后暂停改动并报告，再用只读探针证实related为空及准入拒绝。迁移为有主题的“并发规则”请求，并让假模型从实际related读取原ID；原CAS/遗忘抑制、partial、概览与权威存储断言全部保留。预算用例保持原限额和来源非空断言，通过统一模型投影修复装箱，未提高限额。

四个实际适配器（OpenAI Completions/Responses、Anthropic、Ollama）×两种结构化通道使用SDK及MockTransport，无真实供应商请求：检查真实请求schema/上限、一次调用、memory事件配对、合法建议与非法条目部分成功。结构化解析只用raw，不采用LangChain解析器产生的parsed或宽松JSON修复结果。工具结果在SDK已转换成对象时无法还原服务端原始JSON重复键；有原参数字符串时保留严格重复键检查。

安全与架构独立只读审查无新增高置信发现，分别离线66和94通过；probe增量独立17通过。以上证明本地协议与门禁接线，不证明真实网关接受schema、提取质量提升、自然语言支持关系成立或生产可靠性。原48次预算、13/24评分及e06缺可复现目标而保持候选的冲突原样保留，未追加真实请求。

## 7. 最终工程验收

本片新增69项，最终完整后端`PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q`：**4631 passed in303.88s**；运行前后664份Python源码SHA一致。第5节记忆相关命令：**334 passed in9.39s**；前端`npm --prefix frontend test -- --run`：50文件、**422 passed**；`npm --prefix frontend run build`：TS/Vite零错误，保留既有chunk体积警告。compileall、pip check、AST、206产品/45脚本/413测试/123前端源的500行检查、git diff --check通过。

标准`.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec127-07/delivery-wheels-final`、Twine及`.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec127-07/delivery-wheels-final --output build/spec127-07/delivery-install.json`均退出0。wheel336文件、206份Python和4份前端资源与最终源码字节一致；SHA-256为`ff45a073ec234d183ef93560d34b06a2f2c259937a5e3ebb83db738da70f855e`。干净安装在源码目录外走默认text记忆主链，概览、保存、任务查询、首次索引、独立用量及重开读取通过，重开调用/任务为0、真实供应商请求0。其他平台本片未重建/原机运行，不发布此同版本本地候选。

证据：`build/spec127-07/{verification.json,backend-current.log,tested-source-current.json,reviews.json,frontend-verification.json,structure.json,sdk-versions.json,wheel-content.json,build-final.log,install-final.log,delivery-install.json,review-final.diff}`。初版/中途日志与候选保留；backend-final仍有最后一处提示词清理前的版本，不作为最终源码指纹证据。最终代码差异仅4个产品模块、2个脚本及10个测试文件；公共memory API、数据库/检索/TTL、真实配置和并行Spec128产品代码未改。未提交/push/发布。唯一后续：另定真实请求预算后，用冻结样本比较精简text与明确支持的结构化通道，再判断格式/分类/转正/噪声质量；不能将本片标为自动提取质量已关闭。
