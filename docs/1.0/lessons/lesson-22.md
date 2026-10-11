# Lesson 22：模型条目、供应商引用与参数入口

> 第2章，2026-10-10。已讲model_access/__init__.py:207–288；thinking逐小段继续，不提前算已读。

## 数据形状与调用链

示意：providers的local-service为ollama连接；models的main/small分别引用local-service，实际model为example-model-a/b。没有真实本地模型或网络证明。

```text
models['main']
    → model_id='main'（上层选择ID）
    → entry.provider_id='local-service'（连接引用）
    → checked_providers['local-service']（已校验配置）
    → provider.kind='ollama'（适配类型）
    → model_name='example-model-a'（发送给供应商的名称）
```

## 逐行要点

- built_models={}与thinking_selections={}在模型循环前各创建一次，后续按model_id分别保存构造好的模型对象和思考等级解析记录；此处只是准备容器，不产生客户端/HTTP请求。
- for model_id,raw in models.items()解包自定义模型ID与原配置；_nonempty检查ID。model_id不用与供应商model名称相同；允许多个条目引用同一provider或同一实际模型，参数是否不同依各条目后续处理。
- entry=_object(raw,label,required,allowed)：必填provider_id/model，允许额外thinking/request/context_window_tokens/max_output_tokens/compaction_reserve_tokens/compaction_keep_recent_tokens/compaction_summary_output_tokens。必填只有两个，其他是可选入口；只检查名称，不在这一行检查所有数值。
- 参数定位：thinking是等级能力/default选择，request是超时/SDK重试，context_window与max_output是窗口/输出预算，三个compaction字段是预留/最近保留目标/摘要输出上限；完整默认与消费点后续读。
- provider_id=_nonempty(entry['provider_id'],...)与model_name=_nonempty(entry['model'],...)分别读取并校验字符串；变量来自entry值，不是从外层ID自动推导。返回原字符串，不自动trim。
- if provider_id not in checked_providers抛明确ModelConfigError；没有默认供应商fallback，不把未知引用当SDK错误。通过后provider=checked_providers[provider_id]拿原字典，尚非SDK客户端。
- limit_metadata={}在每个模型的循环内新建，用于该模型下一段预算/来源信息；不沿用上一个模型的字典，也不是LangGraph消息或会话状态。
- 默认模型ID对应models键而不是provider键；它是否存在的检查在函数末尾，此处不声称已完成。模型名非空不证明远端模型已部署/工具能力。

## 离线证据

本轮`.venv/bin/python -m pytest -q tests/test_model_access.py::test_invalid_config_fails_without_leaking_key -k provider_id`实际`1 passed, 7 deselected in 0.74s`；只选择现有未知引用用例，不是skip失败/放宽断言，也不称整层全量通过。

临时0600配置的完整加载探针把_build_model替为记录器，返回object哨兵；无SDK构造或网络请求。输出model_ids=('main','small')、default_model_id=main，记录到(main,ollama,example-model-a,同端点)/(small,ollama,example-model-b,同端点)。把第一个条目的provider_id改为missing后，报models.main.provider_id is unknown，constructor_calls_after_unknown=0。这证明该引用检查在此例中先于构造，不表示坏项在后面时前面从未构造，也不证明远端服务。

## 223–244：窗口/输出预算与预留量

- 用户对三ID检查题说“继续”，按要求推进，不强迫答题、不把未回答记为正确或已独立掌握。
- for field,default,source_field遍历两项三元组，分别是context_window_tokens/128000/context_window_source与max_output_tokens/16384/max_output_source。每轮解包字段名、缺省值、来源字段名。
- value=entry.get(field,default)只在字段不存在时用default；显式None/0/字符串不会自动替换默认。type(value) is int且>0，拒绝bool/浮点/字符串/非正数；失败直接ModelConfigError，不静默纠正。
- limit_metadata[field]=value记预算数值；limit_metadata[source_field]='config' if field in entry else 'default'记是否显式配置，与值是否等于默认无关。循环后补model_id。示意main、窗口32000/输出4096都显式，得到这两数值及两个config来源。
- limit_metadata附在模型对象的metadata['naiyue_model_limits']。context窗口是本地规划配置，不会扩大供应商实际能力；max_output还由构造传max_tokens或Ollama num_predict，实际返回可小于上限。消费/近似token/压缩阈值第7章复访。
- default_reserve=min(16384,max(1,context_window_tokens//4))：先整除取窗口四分之一，再下限1，再上限16384。窗口128000→16384，32000→8000，8000→2000。仅为默认公式，不是显式reserve上限校验。
- reserve_tokens=entry.get('compaction_reserve_tokens',default_reserve)允许显式覆盖，再检查正整数。reserve_metadata记tokens和config/default来源。reserve与max_output独立，默认常量同为16384不等于功能相同，预留参与压缩触发预算，不直接作为SDK输出上限。
- 本段未验证配置数字对应真实供应商能力或所有预算组合可发送；正整数校验不等于实际容量/成本证明。

本轮现有default_limits/limits_overrides/compaction_reserve三个用例实际`3 passed in 0.86s`，证明本地适配器参数、metadata默认/覆盖/来源与无效值拒绝，无真实请求。公式探针128000/32000/8000窗口分别16384/8000/2000，退出0；无真实.env或产品测试修改，不是全量应用构建或生产压缩效果验证。

## 245–274：最近保留目标与摘要输出上限

- default_keep=min(20000,max(1,window//4))，与reserve公式形状相同但常量/用途不同。窗口32000默认keep=8000；128000默认keep=20000。get可显式覆盖，再严格检查正整数，keep_metadata存tokens/source。
- keep是选择最近原消息的保留目标，不是实际保留硬上限或精准token数；当前cut函数从后累计达到目标，再找完整用户轮边界，后续预算切点还有其他约束。这里只定位用途，第7章深入算法；不是给摘要模型限制输出。
- default_summary_output=min(2048,max_output,max(1,reserve*4//5))，以原示例window32000/output4096/reserve8000得min(2048,4096,6400)=2048。乘4后整除5取约80%，max1防小reserve下整数结果为0；项目默认策略，不是供应商要求。
- 若output1024则默认summary1024；reserve1000/output4096则800；reserve1则1。默认2048不是所有显式配置的硬上限，80%也只用于默认。
- summary_output_tokens=entry.get(...,default_summary_output)只在未配置时用默认。显式配置校验：严格正整数，并且<=max_output、<=reserve；超过任一立即ModelConfigError。output4096/reserve8000时显式3000可通过，即使高于默认2048；不会静默截成默认。
- summary_output_metadata存tokens和config/default来源，显式值与默认相同仍config。后续压缩消费该上限，通过bind_model_output_limit设置摘要调用的单请求输出cap，不重写普通模型配置；具体bind机制后续详读。
- keep/reserve/summary/output互相不同：保留原消息目标、触发预留预算、摘要输出上限、普通输出上限。数字相等不等于同一个参数。本地预算计算不证明压缩质量或压缩后必定可发送。

本轮现有keep默认/覆盖/无效、summary默认/覆盖/无效及适配器单请求cap三个用例实际`3 passed in 0.90s`；不请求供应商。整数探针window32000各情况(output4096,reserve8000→summary2048)、(1024,8000→1024)、(4096,1000→800)、(4096,1→1)，keep均8000。无真实.env、产品/测试修改或全量构建声明。

## 275–288：请求超时与SDK重试

- request先为空字典，条目有request则_object要求dict、required=set()、仅允许timeout_seconds/sdk_max_retries。可整块省略、{}或仅覆盖一项；None不是缺省，不能通过dict校验。
- timeout_seconds从request.get取值，缺字段默认60；type必须严格int/float，math.isfinite拒NaN/正负无穷，且>0。布尔/字符串拒绝，不静默转换。SDK/HTTP超时不是Agent整个run的严格总时限。
- sdk_max_retries缺字段默认2，严格非负int；0合法表示不额外重试，2表示首次+最多两次重试。是否重试由SDK判定，不保证总发三次；响应503的已有mock可验证计数。
- provider.kind为ollama且显式包含sdk_max_retries就拒绝，包括合法的0。当前项目尚未接Ollama重试，不代表Ollama技术上无法实现；没有显式字段不会拒绝。
- request_settings={'timeout':timeout_seconds,'max_retries':sdk_max_retries}从项目配置名映射到适配器参数名。OpenAI/兼容/Anthropic的_build_model展开两项；Ollama只取timeout放client_kwargs，忽略中间字典里默认max_retries，不因此声称它默认重试2次。
- SDK重试在单次逻辑模型调用内部处理HTTP尝试；不等于再次执行整个Agent图、重新执行工具或增加用户消息。最终耗尽仍抛SDK异常，运行层的失败观察后续学；更多模型轮次/退避/网络阶段使总耗时不能直接当60或固定180秒。

本轮定向现有默认/单字段覆盖/无效/显式Ollama重试/OpenAI及Anthropic重试mock共`15 passed in 3.45s`。MockTransport重复返回503，两SDK默认2重试各3尝试，显式0各1尝试，符合原断言；不是外网故障/供应商计费或墙钟截止验证。读取本机OpenAI BaseClient._should_retry核对503属于可重试分支，是否重试还可受响应头等条件影响。无产品/测试/真实.env修改，不宣称全量应用构建通过。

## 当前学习记录

- 已讲到288行的模型引用、预算与请求策略；三ID检查题未答，请求段待反馈。
- 独立实现及面试追问未评估；“继续”只推进阅读。
- 下一步289–308及thinking.py：model_default、支持等级检查、向上/向下回退与原生参数；先给输入输出，再逐小段读。

按用户继续进入[Lesson23](lesson-23.md)的thinking默认记录和配置校验；本课模型/预算/request保持已讲范围，独立实现/追问未评估。
