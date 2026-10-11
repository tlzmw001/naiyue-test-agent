# Lesson 21：供应商字段校验与服务URL

> 第2章，2026-10-10。读model_access/__init__.py:184–205及_base_url:109–121；模型条目与SDK构造下一段读。

## 输入与输出

```text
providers字典（JSON已解析，顶层已检查）
    → 逐项取provider_id / raw
    → 通用字段与kind检查
    → 按kind严格检查字段
    → api_key非空 / base_url本地校验
    → checked_providers[provider_id] = provider
```

教学数据形状：{'demo-gateway': {'kind': 'openai_compatible', 'base_url': 'https://example.test/v1', 'api_key': '<示意占位>'}}。不使用真实密钥，不代表存在该服务。

## 逐行核心

- checked_providers={}保存本次加载已通过provider校验的对象；for provider_id,raw in providers.items()逐项解包，ID是自定义配置名称，kind是适配类别。多个ID可共享kind，不把ID当供应商协议。
- _nonempty检查ID；第一次_object要求kind、只允许kind/api_key/base_url，先安全取得kind并排除未知字段；不是此时所有字段必填。provider仍指向原raw字典。
- kind必须str且属于openai/openai_compatible/anthropic/ollama；or短路使kind为列表等情况不会执行集合成员检查而抛TypeError，改为固定ModelConfigError。
- Ollama字段恰为kind/base_url；兼容网关恰为kind/api_key/base_url；openai与anthropic恰为kind/api_key。required=allowed赋同一集合给两个变量，当前只读取。这是项目配置契约，不是LangChain/SDK全部能力的声明，官方类型当前不从provider接收base_url字段。
- 第二次_object按选定契约验证，缺字段/多字段立即失败；第一次通用检查和第二次条件检查职责不同。
- 存在api_key则_nonempty，存在base_url则_base_url；校验API key非空不等于鉴权成功。函数返回值这里未重新赋给provider，不清洗/规范化原配置。
- 最后checked_providers[provider_id]=provider登记原字典引用，尚未构造SDK模型；任一条失败终止加载，不返回部分catalog或跳过坏项。

## _base_url机制

- _nonempty先要求URL是非空字符串，得到url；urlsplit返回SplitResult，可读scheme/hostname/port/username/password/path/query/fragment。示意https://example.test:443/v1对应scheme=https、hostname=example.test、port=443、path=/v1、query/fragment为空、username/password=None。
- parsed.port单独取值，因无效文本/超范围端口会在属性读取时抛ValueError；urlsplit并非完整安全/连通性验证。参考[Python3.12文档](https://docs.python.org/3.12/library/urllib.parse.html#urllib.parse.urlsplit)。
- scheme：ollama允许http或https，其他kind只允许https；不把Ollama的http规则误称成只允许localhost。hostname须非空，username/password不得有非空值；port未指定None或正数才通过，0拒绝，上界由parsed.port检查。
- query/fragment必须无内容，path可包含/v1。and采用短路，valid只按真假使用，不必假设每步都是bool。
- try里的ValueError转成valid=False；下一步统一抛ModelConfigError的固定字段定位，不回显整条URL/原解析异常。不是吞错并继续加载。
- 成功return原url，不自动补/v1、不修改provider、不发网络请求，不证明域名可信/端点连通/模型存在。

## 验证

本轮现有`.venv/bin/python -m pytest -q tests/test_model_access.py::test_loads_all_model_kinds_without_network`输出`1 passed in 0.70s`，验证四种配置完成本地模型构造，不请求网络；不提前算用户已读SDK构造。

直接_base_url探针：gateway_https/ollama_http=ACCEPT；gateway_http、bad_port、out_of_range_port、zero_port、username、query、fragment=REJECT，退出0；只使用example.test与合成值，未读真实.env或发送请求。无产品/测试修改或全量应用构建声明。

## 掌握与下一步

- 已讲解供应商循环、两阶段字段校验、URL字段形状与判断；待用户反馈。
- 用户“继续”仅推进阅读；独立实现/面试追问未评估。
- 下一步model_access/__init__.py:207–222：model_id/provider_id/model_name分别是什么，模型如何引用checked_providers；窗口/输出/压缩/thinking/request参数分小段后读。

用户继续后进入[Lesson22](lesson-22.md)的模型条目引用链，供应商段不记为独立实现或真实服务验收。
