# Lesson 24：LangChain模型对象的构造与登记

> 第2章，2026-10-10。已讲model_access/__init__.py:124–154、309–318及ModelCatalog:49–79；客户端资源内部下一段读。

## 调用与数据

```text
配置加载器（供应商/条目/预算/request/thinking已处理）
    → _build_model(kind, model_name, provider, model_id,
        selection.native_settings, request_settings,
        limit_metadata, reserve_metadata, keep_metadata, summary_output_metadata)
    → ChatOpenAI / ChatAnthropic / ChatOllama对象
    → built_models[model_id]
同时：thinking_selections[model_id] = selection
```

返回是BaseChatModel派生实例，不是AIMessage/Agent/会话或模型回答。项目负责装配配置，LangChain适配器负责标准模型接口和协议；构造可准备HTTP客户端，不等于执行模型API。

## 核心代码

- _build_model参数中的thinking_settings来自selection.native_settings，不是原JSON里的supported_levels/default_level。request_settings已经映射成timeout/max_retries；四预算metadata来自前面步骤。
- output_limit=limit_metadata['max_output_tokens']取实际输出cap；metadata把四份字典挂入naiyue_model_limits/naiyue_compaction_reserve/naiyue_compaction_keep_recent/naiyue_compaction_summary_output。metadata是项目/框架附带说明，不会作为整份供应商HTTP参数直接展开；真正输出控制是max_tokens或num_predict。
- openai构造ChatOpenAI，传model/api_key/max_tokens/metadata、**thinking_settings和**request_settings，不从provider传base_url。
- openai_compatible同为ChatOpenAI，多传base_url。协议兼容不等于网关能力/行为等于官方，当前仅配置装配。
- anthropic构造ChatAnthropic，同样传模型名、key、输出cap、metadata以及两字典kwargs。
- 最后ChatOllama分支依据加载器此前只允许四kind的前提，传model/base_url、client_kwargs={'timeout':...}、num_predict、metadata、**thinking_settings；不展开request_settings的max_retries。私有_build_model不能视为独立未知kind校验器。
- **dict在函数调用中展开关键字，{}不加参数。示意兼容网关reasoning_effort=high、timeout=12.5、max_retries=0来自两个独立字典；不是把字典作为单一thinking参数传入。模型构造字段不保证与最终HTTPJSON名称完全相同。
- except Exception包装普通构造错误为ModelConfigError，保留model_id与原异常类名，不拼原始异常正文，from None抑制链展示；不捕获KeyboardInterrupt等BaseException，不在这里重试/发模型回答。
- 加载器第309行成功返回后才写built_models[model_id]，313登记该模型的selection。constructor失败不会给这个键赋正常模型，也不会继续用默认模型替代。

## 框架字段依据

本机langchain_core.language_models.base的metadata字段是Field(default=None,exclude=True)，说明为run trace元数据；BaseLanguageModel.model_post_init调用_add_version添加metadata.lc_versions，合作provider包可再添加自身版本。因此最终model.metadata可能比本项目四字段多lc_versions；不是误把供应商返回消息放入配置。只追这处初始化钩子，不通读整个框架。

## 本轮离线验证

实际调用_build_model构造四类型，使用合成模型/凭据/端点及显式预算，不调用invoke/ainvoke或读取真实.env：

```text
openai           → ChatOpenAI，BaseChatModel=True，output_cap=4096
openai_compatible→ ChatOpenAI，BaseChatModel=True，output_cap=4096
anthropic        → ChatAnthropic，BaseChatModel=True，output_cap=4096
ollama           → ChatOllama，BaseChatModel=True，output_cap=4096
```

四对象均有四项目metadata键及lc_versions，断言naiyue_model_limits仍指向输入limits。模拟ChatOpenAI构造抛ValueError('synthetic-private-detail')，输出models.demo: model construction failed (ValueError)，断言消息不含原正文且suppress_context=True。进程退出0，无真实请求；未改产品/测试，无本轮全量构建/供应商服务验收声明。

## 314–318和ModelCatalog：目录查询与资源入口

- for models循环结束后检查default_id是否在built_models键中；默认ID不是provider_id或供应商模型名。不存在则抛配置错误；这个检查在模型构造之后，不声称未知默认ID时零构造。
- catalog=ModelCatalog(default_id,built_models)创建目录，构造函数保存default_model_id/_models引用、初始_owned_http_clients=[]和_thinking_selections={}。类本身没有重跑加载器的全套校验；有效快照由load_model_catalog前置处理保证。
- catalog._thinking_selections=thinking_selections把循环累积记录接进目录，替换初始空字典；return catalog返回目录，不返回一次模型回答。
- @property model_ids→tuple(self._models)：迭代字典取keys，得到模型ID元组，保持字典插入顺序；每次访问计算，不取模型对象或名称。元组不可直接追加，不代表整个目录深度不可变。
- get(model_id)：先检查字符串和存在性（or短路避免错误类型hash），未知ID抛ModelConfigError，不选择默认替代；成功返回同一个已构造模型对象，不重读JSON/复制模型/运行模型/切换会话。default_model_id是目录默认项，不是所有会话共享的当前模型变量。
- thinking_selection先self.get(model_id)校验已知模型，丢弃返回对象；再查记录字典，已知模型无记录时提供ThinkingSelection(model_default,model_default,{})。未知模型不能伪装为默认等级。dict.get的默认表达式作为调用参数会先求值，不是只有缺键时才构造；实际返回有记录优先。
- _prepare_async_clients在owned列表为空时调用独立prepare_owned_clients(self._models.values(),owned)，传模型对象集合和资源登记列表；已有登记时不重复调用。具体所有权/SDK复制与失败清理下一段，不能笼统说永远只调用一次。
- aclose局部导入aclose_models并await它处理owned列表，只接显式登记的HTTP资源，不宣称所有模型默认共享池都会关闭。async_persistent.py:37实际调用准备方法，所有权接线已定位；默认应用资源函数整体仍第8章读。

本轮纯接口探针用FakeMessagesListChatModel实例（不invoke）：model_ids=('main','small')、default_id=main、get返回原实例且重复get同一对象；main有medium→high记录、small无记录返回model_default；get/thinking_selection的missing均抛Unknown model_id，owned初始空列表。退出0，无SDK/网络请求或真实.env。

现有未知默认模型ID用例：`pytest -q tests/test_model_access.py::test_invalid_config_fails_without_leaking_key -k default_model_id`实际`1 passed, 7 deselected in 0.81s`；只选择对应用例，不改/skip失败测试。无产品测试修改或全量应用验证声明。

## 当前掌握与下一步

- 已讲构造/登记/默认校验及ModelCatalog全部核心方法与资源入口，用户反馈待定。
- 独立实现及面试追问未评估。
- 下一步catalog_lifecycle.py:15–49的客户端所有权/准备：为什么区分借用与拥有，按模型类型怎样接SDK/HTTP对象；52–67关闭再分段。bind_model_output_limit在第7章结合摘要调用详读。

按用户继续进入[Lesson25](lesson-25.md)，先讲15–23输入/身份去重/Ollama资源登记，不把整个资源模块定位记为通读。
