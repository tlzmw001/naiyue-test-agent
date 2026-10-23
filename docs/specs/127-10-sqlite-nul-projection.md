# Spec127-10：旧 SQLite 的 NUL 文本投影兼容

2026-10-23，0.4.0发布前CI复现；标签尚未创建。范围仅修复共享历史可见文本投影及其回归，不升级SQLite或Python依赖、不修改存储格式、权限、检索策略或真实配置。

## 现状与根因

主分支CI39904800053为4失败/4633通过，失败均涉及可见正文中的NUL：history搜索/精确分块、memory批次完整长度及概览字符计数。本机SQLite3.53.4通过；使用官方3.37.2源码构建的隔离CLI复现：单路径`json_extract`将`前文\u0000AFTER🧪`返回为`前文`，双路径提取仍返回完整转义的JSON数组。

`history_projection.VISIBLE_TEXT`先用单路径JSON提取正文，再交Python长度/分块函数；文本在Python函数收到前已经丢失。根入口为history查询，memory_batches及memory_overview_store复用同一投影，因此四项失败有同一生产者。数组的json_each文本值、搜索词项及工具文本元数据有同类截断风险。

## 最小变更

- 在现有history_projection连接配置中注册一个确定性JSON文本解码函数；仅接受SQL投影出的字段数组，返回其首个字符串或NULL。
- 可见正文、文本块的type/text、字符串块及工具文本元数据，使用同一路径两次的JSON数组投影保留转义，随后解码。字符串块从原消息按SQLite生成的fullkey定位，避免使用已截断的json_each.value。
- 查询词项同样使用受控JSON数组投影。全部查询值继续参数绑定，不把用户路径/SQL拼入表达式。
- 保留SQL中的角色、kind、checkpoint及图片载体过滤，不把完整消息JSON或图片/思考载体传给Python解码函数。概览与分批调用方自动复用修正，不建立平行实现。
- 不合法的JSON/IO沿现有错误反馈路径传播；不把失败伪装为空成功。

正常链：保存的消息→允许字段的JSON投影→完整解码→长度/匹配/分块→有界历史结果、记忆批次与概览计数。边界链：带NUL的非text类型仍不成为text块，完整NUL查询不退化成前缀，来源ID和名称不被截断。旧SQLite没有损坏保存的原始body_json，本片不迁移或重写用户数据。

## 验证与文件

生产只改`application/history_projection.py`。新增`tests/test_history_projection_sqlite.py`，在独立测试连接模拟已由3.37.2真实CLI确认的JSON1单路径行为，保留多路径的JSON数组语义；复用现有真实临时存储与history测试入口。先运行并记录红灯，再修源代码；不改已有失败断言、conftest、依赖或超时。

回归覆盖字符串/数组NUL的匹配、长度与分块还原，NUL查询，文本块类型及工具元数据。原CI四项、history/memory专项、完整后端、compile/pip/diff、四目标同源码候选与安装分别验收。发布说明/交接同步实际结果，远端CI及PyPI必须重新验证；旧失败日志和旧候选保留。

证据目录：`build/release-0.4.0-cqnrphs3/`，旧SQLite源码及探针在`sqlite337/`。临时旧引擎仅用于验证，不进入项目依赖或安装包。

## 当前验证

新增5项回归在旧实现全部红灯；修正后与history存储、批次及概览专项合计70项通过。另在证据目录sqlite340/编译真实SQLite3.40.1动态库，仅通过子进程DYLD_LIBRARY_PATH运行当前Python，不替换已安装依赖：加载已提交旧投影重现CI相同4项失败，当前实现70项全部通过。原断言及conftest保持。完整后端4642 passed in306.28s，788份Python/前端源码哈希无漂移；compile/pip/diff通过。四目标标准wheel/Twine通过，每包206份Python、4份前端资源及README与当前源码一致；Mac源码外干净安装的记忆主链/历史重开及原门禁通过，0供应商请求。独立只读安全复核无新增高置信发现。远端CI及标签/PyPI仍待验证，旧失败记录保留。

同形检查：history_projection的可见正文、块类型/文本、tool_call_id/name/status和查询词项一并修正，三个生产消费者无需平行补丁；内部图片/摘要/输入来源标志仍为受控布尔/枚举，不走可见文本解码。chat_queries的既有UI预览、旧索引迁移及async_persistent的artifact投影属于原有独立入口，不在本片改写；本片不将这些历史入口宣称为新增长期记忆合同。没有修改SQL构造、数据库格式或现有测试期望。
