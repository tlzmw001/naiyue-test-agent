# Lesson 23：Thinking配置、默认状态与支持等级

> 第2章，2026-10-10。已讲model_access/__init__.py:289–308及thinking.py全部核心（7–63）；SDK构造下一段读，不提前算通读。

## 数据与调用图

```text
entry是否有thinking
    ├── 没有 → ThinkingSelection(model_default, model_default, {})
    └── 有 → _object要求supported_levels/default_level
               → validate_supported_levels得到tuple
               → requested取default_level并验证允许值
               → 下一段：原生能力检查、回退、参数映射
```

教学输入：{'thinking': {'supported_levels': ['off','low','xhigh'], 'default_level': 'high'}}。supported是配置声明的该模型能力集合，不是查询供应商所得；可请求项目等级high，即便不在supported里，后面才按规则回退。

## ThinkingSelection和默认值

- 全局有序THINKING_LEVELS=('off','minimal','low','medium','high','xhigh','max')，MODEL_DEFAULT='model_default'独立于等级序列；默认含义是不主动传思考参数，不等于off或模型不思考。
- @dataclass(frozen=True)生成初始化/表示等样板方法，三个字段按requested/selected/native_settings顺序接位置参数。注解不提供Pydantic类型/等级校验；校验由项目函数完成。
- requested记录配置希望的等级，selected记录最终选中的等级，native_settings记录传适配器的字典。frozen阻止字段重新赋值，不递归冻结dict内部，不宣称绝对不可变。
- selection=ThinkingSelection(MODEL_DEFAULT,MODEL_DEFAULT,{})准备默认记录；没有thinking则保留它。有thinking时两字段都必填/无其他字段，supported非空等规则仍要检查，即使default_level是model_default。

## validate_supported_levels逐行

- 参数是配置JSON数组应生成的list；tuple/字符串/None或空list拒绝。
- any(not isinstance(level,str) or level not in THINKING_LEVELS for level in levels)按各项产生非法标记，发现一项非法即True。展开可理解为普通for循环逐项类型/成员检查。
- 正常['off','low','xhigh']的标记全False；含ultra或非字符串的项为True。or短路使非字符串不再做后续必要检查；外层or发现非法列表元素后不会执行set(levels)，避免无hash对象引发TypeError。
- len(set(levels))!=len(levels)拒绝重复项，如['low','low']长度2但去重后1；不是把重复项清洗掉并继续。
- 固定ValueError拒绝非法集合，加载器捕获后转ModelConfigError并加models.<id>.thinking定位。成功return tuple(levels)，保持原顺序，不自动按等级排序；真正回退顺序由全局THINKING_LEVELS决定，下一段读。
- model_default不允许作为supported_levels元素，因为它不是显式能力等级；default_level则允许七等级或独立model_default。
- requested=thinking['default_level']取值，not isinstance(str) or requested not in (*THINKING_LEVELS,MODEL_DEFAULT)检查合法词汇。元组内*展开七项再追加model_default，不要求requested已经属于supported，否则无法进入后续回退。

## 本轮验证

现有`.venv/bin/python -m pytest -q tests/test_model_access.py::test_thinking_defaults_do_not_change_existing_model_parameters tests/test_model_access.py::test_invalid_thinking_configuration_is_rejected`实际`7 passed in 0.71s`。本地默认/无效配置证据，不证明供应商能力/推理效果。

纯函数探针输出默认ThinkingSelection(requested='model_default',selected='model_default',native_settings={})；字段重新赋值报FrozenInstanceError；正常list转('off','low','xhigh')类型tuple；duplicate/unknown/empty/not_list/non_string拒绝，退出0。无真实.env/模型请求或产品测试修改。

## thinking.py:26–43：等级解析顺序与切片

- 输入requested='high'、supported=('off','low','xhigh')，输出selected='xhigh'；只做本地等级选择，不查询远端、修改支持表或重试模型。
- MODEL_DEFAULT首先直接返回，不参加排序/回退；非法requested抛ValueError；显式等级时空supported拒绝；自身受支持直接返回自身。
- 全局索引off0/minimal1/low2/medium3/high4/xhigh5/max6。requested_index=THINKING_LEVELS.index(requested)，high为4。
- THINKING_LEVELS[requested_index+1:]从5开始含尾部，候选('xhigh','max')；按全局顺序逐项测试membership，遇到第一个支持值return；配置supported的排列顺序无影响。
- 向上都失败，才走reversed(THINKING_LEVELS[:requested_index])；前缀[:4]不包含high，反向迭代('medium','low','minimal','off')，优先向下最近一档，而非从off开始。high仅支持off/low时返回low。
- 规则是自身→向上第一个→向下第一个，不按整体绝对距离选。low仅支持off/max时返回max，虽然off在索引上更近；这是上升分支优先。
- 最后ValueError防无可用等级的异常输入。加载器已验证非空且全部为项目等级，正常情况下至少存在候选，不应走末尾；绕过前置校验传('ultra',)可触发。加载器对thinking块的supported非空校验仍先执行，不因resolver的model_default早返回取消该配置契约。
- 回接加载器时301–303先逐项native_thinking_settings验证配置等级能被适配，然后304调用本函数；此处先读纯函数，不把实际顺序说反。原生映射下一段细读。

本轮现有`.venv/bin/python -m pytest -q tests/test_model_access.py::test_thinking_level_prefers_next_higher_then_lower`实际`1 passed in 0.51s`。纯函数探针输出high→xhigh/low/high、model_default→model_default、low仅off/max→max、支持顺序打乱仍high→xhigh；索引4、上候选xhigh/max、下候选medium/low/minimal/off；非法请求/空支持/非法支持被拒绝，退出0，无真实.env/SDK或网络请求。不是远端能力证明。

## 301–308及native_thinking_settings：映射和记录

- 用户答High，正确说明medium不支持时先向上high，局部规则理解有证据；不自动升级独立实现或面试连续追问。
- 加载器先for level in supported逐个调用native_thinking_settings检查声明的等级能否由当前provider映射，返回字典在此处不保存/不发送。然后resolve选等级，再对selected做同函数映射，native_settings保存输出。不能把真实次序说反或将声明错误自动清洗。
- model_default先返回{}，表示无显式思考构造参数，与off不同。
- OpenAI/兼容返回reasoning_effort，off转none，其他合法selected原样。Anthropic的off转thinking={'type':'disabled'}，minimal当前映射拒绝，其他合法selected用reasoning_effort。Ollama off转reasoning=False，low/medium/high/max转reasoning字符串，minimal/xhigh当前拒绝；未知kind的非默认路径最后ValueError。
- 此函数生成的是LangChain模型适配器构造kwargs，不等于所有供应商最终HTTPJSON字段；最终协议由适配器处理。表只描述当前本地映射，不证明具体模型/网关能力。
- 举Ollama声明off/low/xhigh却默认low：前面的支持声明检查在xhigh报错，不能因本次未选它就继续；修声明/适配契约，而不是在加载时隐藏错误。请求minimal但声明只有low等有效原生等级，则resolver可向上到low；区别是请求回退与支持声明校验。
- ValueError统一转ModelConfigError加model定位/from None，整体加载失败，不替换成默认等级或AI回答。native_settings成功后ThinkingSelection(requested,selected,native_settings)保留原请求/有效等级/构造参数，避免升降档不可见。
- 沿用户例子requested=medium、supported=off/low/high、kind=openai_compatible，得到ThinkingSelection(requested='medium',selected='high',native_settings={'reasoning_effort':'high'})，只完成本地解析。

本轮映射接线/Anthropic off/原生不支持/Ollama minimal与xhigh拒绝/Ollama max现有6用例实际`6 passed in 0.60s`；同时纯函数验证三家off/显式等级/default的字典和拒绝分支。原测试含适配器对象字段及特定OpenAI请求payload断言，无网络请求或真实.env，不证明远端执行效果。无产品测试修改或全量应用构建声明。

## 当前掌握与下一步

- 已讲配置/支持列表/回退/映射与selection回填；用户正确回答medium→high，映射段待反馈。
- “继续”只表示推进，不记独立实现/面试追问通过。
- 检查题medium支持off/low/high已正确答high；没有本人代码/故障调试证据，独立实现未评估。
- 下一步_build_model:124–154与309–313：kwargs展开、四供应商适配器构造及安全异常边界，随后314–318/ModelCatalog。

用户继续后进入[Lesson24](lesson-24.md)的模型构造；本课原生映射只记录本地参数接线，不算远端执行证据。
