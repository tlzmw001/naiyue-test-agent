# Lesson 25：模型目录的HTTP资源登记

> 第2章，2026-10-10。已讲catalog_lifecycle.py全部核心：15–49准备与52–67关闭；用户独立实现/追问未评估。

## 输入形状与资源层次

ModelCatalog._prepare_async_clients调用prepare_owned_clients(self._models.values(),self._owned_http_clients)。models是模型对象集合，owned是目录持有的可修改列表，函数就地登记，不返回一份新列表。

```text
ChatOllama模型
    ├── _client：ollama.Client
    │     └── _client：httpx.Client
    └── _async_client：ollama.AsyncClient
          └── _client：httpx.AsyncClient
```

owned形状为[(HTTP客户端对象,是否用异步关闭), ...]。True表示以后await client.aclose，False表示以后调用同步close；不是工具并发、会话异步或所有权权限开关。

## 15–23逐行

- seen=set()在本次准备调用中去重模型对象；for model in models取对象而非model_id/model_name。
- id(model)是对象身份整数，同一实例被两个目录ID引用时相同；不是业务ID或模型名称。若在seen，continue跳过本轮；否则seen.add登记后再分类型。两个独立实例即使同名仍分别处理。
- isinstance(model,ChatOllama)识别实际模型类；本段取模型已创建的同步/异步SDK内HTTP对象，登记其所有权。当前_build_model仅注入timeout，未接用户外借Ollama client，此生产路径有本地构造依据；不能推广到任意外部共享客户端。
- owned.extend(((model._async_client._client,True),(model._client._client,False)))外层二元组有两条记录，extend分别追加，得到两条顶层元素；append整个外层元组会形成错误的嵌套层次。
- 登记的是同一HTTP对象引用，不是复制客户端、创建网络请求或立即关闭；并不把model对象存入owned。
- 最后continue使Ollama本轮处理结束，不进入后面的OpenAI/Anthropic替换分支。

## 框架生产者依据

本机langchain-ollama1.1.0 ChatOllama._set_clients是Pydantic after model_validator，构造时创建ollama.Client/AsyncClient；Ollama SDK两构造分别使用httpx.Client/httpx.AsyncClient。_client/_async_client是固定版本内部字段，本项目据此接线，升级须回归验证，课堂不升级依赖。

当前本机构造validate_model_on_init=False，未发模型验证请求；不能把所有可能设置下的构造笼统叫零网络。

## 验证

本轮新建合成ChatOllama，不调用invoke/ainvoke。把[model,model]交prepare_owned_clients，输出owned_count=2、owned_flags=[True,False]，两HTTP引用分别匹配模型内部对象，退出0。探针最后显式关闭自己创建的两客户端，probe_http_clients_closed=True；这是清理验证，不是本段prepare会自动close。无真实.env或本地模型服务调用。

现有`.venv/bin/python -m pytest -q tests/test_async_resources.py::test_catalog_closes_shared_sdk_clients_once`实际`1 passed in 0.59s`；该用例用同一ChatOpenAI实例的两个目录ID，验证资源关闭一次，仅作通用身份去重接线，不能冒充Ollama实网验收。

## 24–34：SDK对象、代理和传输模块

- ChatOpenAI分支取root_client/root_async_client赋sync/asynchronous，它们是openai.OpenAI/AsyncOpenAI，不是HTTP传输对象；模型的client是chat.completions API资源对象，另与SDK根及根的_client区分。
- proxy=model.openai_proxy保存适配器代理设置，None等假值后面不加proxy选项；它不是base_url。属性可由OPENAI_PROXY默认机制取得，课堂不读真实环境变量或输出代理地址/凭据。
- transport_module=httpx把模块对象放进变量；后面按模块.Client/AsyncClient构造。不是立刻发请求/建客户端。
- ChatAnthropic分支局部import httpx2；读取_client/_async_client实际是cached_property：第一次触发SDK构造，结果写实例__dict__，以后复用。SDK类型anthropic.Anthropic/AsyncAnthropic；proxy读取anthropic_proxy（适配器可由ANTHROPIC_PROXY取默认）；模块选httpx2。
- 本机HTTPX0.28.1与HTTPX2 2.13.1是不同模块/类。查SDK同步构造校验：当前OpenAI接受httpx2或legacy httpx客户端，当前Anthropic要求httpx2.Client。因此不能按名字相同就当同一类，也不能说OpenAI默认HTTP库一定是httpx。本机两类默认wrapper均派生对应SDK DefaultHttpxClient并最终继承httpx2.Client。
- else continue跳过非Ollama/OpenAI/Anthropic模型（例如假模型）；不是证明所有自定义模型资源无需关闭，新增种类生命周期需单独接线。

## 共享默认资源的源码与本机证据

本机LangChain两适配器默认HTTP客户端获取函数有缓存；不同模型的SDK对象可能不同，但底层HTTP对象相同。两模型同端点/timeout60、不同合成模型名的探针实际：两类sdk_instances_distinct=True，default_sync_transport_shared=True、default_async_transport_shared=True。

Anthropic第一次读_client前__dict__无该键，之后有；类方法带@cached_property并用默认HTTP工厂构造SDK，已从当前源码追到生产者。缓存HTTP来源与每实例SDK缓存是不同层。

这解释后续为什么创建owned资源而不直接关闭默认缓存池：关闭共享HTTP对象可能影响另一个使用者。当前只是类型/身份探针，无invoke/网络/真实.env；不以共享观察推断所有配置必共享。35–49替换接线下一段验证。

## 35–49：创建owned HTTP并重接模型

- options先复制当前sync SDK的timeout，proxy有值再加入代理项；不凭空把超时改回HTTP库默认，不把base_url当proxy。当前探针只验证合成timeout60，无真实代理场景验收。
- transport_module.Client(**options)创建同步HTTP对象，立即owned.append((sync_http,False))；再建AsyncClient并登记True。构造异步客户端是普通对象创建，不需要await，不表示已执行异步请求。立即登记方便外层后续失败时清理，函数此段本身没有catch或自动close。
- sync.with_options(http_client=sync_http)及异步对应调用返回新SDK实例，保留原SDK配置并换入指定HTTP对象；本机两SDK的with_options是copy别名/复制接口，原SDK不被就地变更。timeout/base_url/max_retries保留由源码/探针核对，不输出真实凭据。
- 只创建新对象而未写回模型，原调用路径仍使用旧SDK；因此OpenAI同时替换root_client/root_async_client及client/async_client两个API资源字段。当前源码ChatCompletions走self.client，Responses走self.root_client.responses，必须覆盖两条路径；API资源._client也指新根SDK。
- Anthropic覆盖__dict__['_client']与['_async_client']，替换cached_property保存的SDK结果；后续访问属性拿新值，不重新从默认HTTP缓存创建旧SDK。模型请求读取这两属性的messages API，消费者源码已定位。
- 原借用SDK/默认HTTP不登记到owned、不手动关闭；新SDK指向owned资源，目录退出关闭新资源。不是关闭所有框架缓存，也不是模型请求/事件的深拷贝。

## 本轮接线验证

现有`.venv/bin/python -m pytest -q tests/test_async_resources.py::test_two_async_applications_do_not_share_owned_transports`实际`2 passed in 0.63s`（OpenAI/Anthropic），没有实网请求。

单独双目录探针：new_sdk_instances=True、owned_flags=[False,True]、sdk_uses_owned_http=True、settings_preserved=True、transport_isolated=True；OpenAI api_resource_rebound=True，Anthropic cached_sdk_rebound=True；关闭第一目录closed_first_only=True、borrowed_left_open=True，两类均符合。最后关闭自己创建的目录资源，退出0；原缓存传输作为借用不关闭。验证引用/资源隔离/关闭结果，不是实际API调用或生产并发负载。无真实.env/代理凭据、产品测试修改或全量构建声明。

## 52–67：关闭、普通失败与首异常

- aclose_models接owned记录列表，成功无显式return→None，普通关闭失败最终抛首异常；不是返回bool/模型回答。ModelCatalog.aclose await该函数，在应用资源退出或初始化失败收尾中使用，不是每个模型回答后关闭。
- seen是本次关闭调用的HTTP对象身份集合，与准备阶段按模型对象去重不同；同一个HTTP对象重复登记只尝试一次。先seen.add再close，失败的重复引用也不会在同一次循环中再尝试；不保证跨多次函数调用只关闭一次。
- True分支await client.aclose；False分支await asyncio.to_thread(client.close)。传的是绑定方法对象，不是先在事件线程执行client.close()。当前CPython to_thread把函数放默认执行器、await结果；此协程等待，其他事件循环任务仍可调度，for循环本身逐项await而非并行关闭。
- 普通Exception捕获后logger.error只记录固定事件与类型，不包含原正文；primary=primary or error初始取首个异常，以后保留已有普通异常对象，循环继续尝试后续资源。全部尝试不等于全部关闭成功。
- 最后primary is not None则raise primary，抛原对象，未删除错误正文或包装成安全新异常；本函数日志不输出正文不等于上层未经处理的traceback也安全。默认结果展示由上层负责。
- except Exception不捕获Python3.12的CancelledError（BaseException子类），外部取消可中断循环；to_thread也不提供强行终止工作线程的保证。应用层的收尾保护/取消顺序第5/8章复访，不能以此函数宣称任意取消/进程强杀均完整关闭。

本轮确定性假客户端探针：first同步关闭报ValueError、重复first、async成功、later同步报RuntimeError，记录close_order=['first_fail','async_ok','later_fail']、duplicate_attempts=1、correct_execution_threads=True，抛出的对象is first_error=True；失败日志2条、不含原模拟正文；空列表返回None。退出0，不依赖sleep窗口、不改产品/测试、无真实SDK网络/.env。只证明该关闭函数普通异常/顺序与线程机制，非生产关闭/取消故障矩阵。

## 补充问答：异步关闭入口与资源所有权

用户正确回答首项失败、第二项成功后仍收到异常；随后问为什么不全用异步关闭、为什么当时维护HTTP与SDK。本轮只解释既有设计，不改资源实现。

- 对外入口本来就是await catalog.aclose→await aclose_models；内部有AsyncClient.aclose与Client.close两种API，后者用await to_thread转换成异步等待，不在事件循环线程直接close。HTTPX/HTTPX2当前Client均无aclose且close非协程，AsyncClient.aclose为协程，本轮inspect确认。
- 不能把同步Client.close简单加await或改一个async def就变非阻塞。只保留异步传输是另一种资源/接口契约，需要明确同步能力及适配器双客户端路径；当前不是全部async def语法统一就能成立。没有在课堂中作async-only迁移决策。
- 历史依据[Spec075:33–35](../../specs/075-integrated-async-application.md)：异步应用factory取得模型/存储资源所有权，退出先等所属运行再关资源；本机默认HTTP缓存跨模型/应用共享，不能关借用池，with_options绑定新目录自己的HTTP。Spec075:71记两应用隔离测试红灯→独立资源，Anthropic类型不兼容改用已有httpx2。历史记录与当前源码相互对照，不伪造新实网试验。
- 维护的是SDK客户端实例与HTTP资源生命周期。LangChain/官方SDK继续负责消息接口、协议、鉴权、重试；项目控制创建/绑定/关闭时点与归属。新SDK副本是把自有HTTP注入调用链的手段，不重写一套SDK/模型运行时。
- 全局缓存池若由A/B共用，关闭A时直接关闭原池会影响B；“不管关闭”则缺少明确应用收尾。现方案借用原池不关闭，绑定并登记自有资源，只关自有；课堂前面的隔离/引用探针已有局部证据，不宣称多用户/生产负载/远端停止保证。

## 澄清：框架多SDK能力与应用生命周期契约

用户追问框架是否保证多SDK、我们的资源管理是保险还是场景需要。明确收窄先前表述：共享默认连接池是正常复用路径，不是框架不支持多SDK的缺陷，也没有证据说明普通多模型调用必须用这套替换才能工作。

- 实际额外要求是：一个AsyncApplication显式退出/初始化失败时，关闭本次factory拥有的资源，而其他应用/目录继续使用；不把借用的默认池当应用私有资源。当前两个应用独立close是Spec075既有契约/用例，不只是没有场景的防御代码；单一长寿命应用只借共享池不主动关闭，不能据此说必然出问题/泄漏。
- 底层框架/SDK提供模型调用及HTTP注入/copy能力，但我们使用的ChatOpenAI/ChatAnthropic当前版本没有统一模型close/aclose/上下文管理接口，也没有自动按本项目Application对象划分默认HTTP缓存所有权。应描述为应用组织生命周期，而非框架整个功能做不了。
- 当前公开模型字段探针：ChatOpenAI有http_client/http_async_client，ChatAnthropic字段没有这两项；资源独享可利用已有能力，SDK复制/缓存属性重接是当前实现路径，不宣称唯一必要方案。
- 本轮独立临时进程用两类模型各构造两个SDK，默认HTTP共享，手动关闭A根SDK之后，B引用的同一HTTP也变closed。只是验证主动关闭共享资源的后果，不是正常多SDK调用测试失败，也未发送B实网请求。此进程不使用正在运行项目的客户端。
- 本项目新资源方案的保证前提是各factory加载自己的模型实例/目录；不同会话并不各关一套SDK，同应用会话复用目录。生命周期归属与多用户数据/权限隔离不同，不扩成SaaS支持声明。

## 当前掌握与下一步

- 已讲准备/关闭及应用所有权原因，补充澄清共享非故障、独立关闭是实际契约；用户正确判断关闭失败传播，问答理解待反馈。
- 独立实现/面试追问未评估，不用助手探针替代用户操作证据。
- 检查题首客户端失败、第二项成功已正确回答“收到异常”；独立实现/连续追问未评估。
- 下一步model_access/errors.py：先读ModelFailure/ModelResponseRejected与分类入口，区分配置/请求/响应失败；完整应用取消/收尾仍留第5/8章。
