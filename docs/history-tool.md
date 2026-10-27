# history：当前项目的会话历史查询

从0.4.0提供，设计与验收见Spec127-01。history已加入CLI/Web共享内置工具，遵循现有会话及子角色的工具启停和权限策略，默认只读允许。已有会话保持自己保存的工具集合，需要按现有工具选择入口启用。

Spec128-01之后，保存数据按project_id选择，当前执行目录用于准入和权限校验。来源会话的旧目录消失不影响已保存正文；当前调用会话必须属于所绑定项目且执行目录有效。`--no-session`不开放项目history。实际worktree创建/登记尚未实施，不能把数据范围拆分当成worktree功能已交付。

模型可先列出其他会话，再按完整会话ID搜索、读取已保存的可见正文。这里不调用额外模型，也不恢复或继续执行目标会话。示意调用参数如下，session_id须使用列表返回的真实ID：

```json
{"action":"list_sessions","limit":10}
{"action":"search","session_id":"列表返回的完整ID","query":"venv 代理"}
{"action":"read","session_id":"列表返回的完整ID","limit":10}
```

- `list_sessions`：仅当前项目UUID范围、排除调用会话，按保存的活动时间排序。返回真实标题、ID、保存状态及时间，并附已生成概览/来源范围/版本/生成时间。没有概览时为`null`/`not_generated`；历史被改写后标`stale`，追加以`has_newer_messages`表示。保存状态不代表此刻实时运行情况。
- `search`：指定一个会话，查询中的空白分隔词须全部在同条可见正文出现，ASCII大小写不敏感。它是字面关键词检索；有`has_more=true`时，即使本页为空，也应携带同一query及`next_cursor`继续扫描。
- `read`：指定会话读消息页，页内按时间正序。大正文返回`offset/next_offset/text_chars/complete`；继续读同一条时传回`sequence/revision/conversation_revision`和`offset=next_offset`。offset按可见正文的Unicode字符计数。

search/read默认查根会话；`agent_id`可指向该会话已经登记的真实子实例，不接受任意thread或数据库路径。返回真实消息身份和必要父消息/工具配对信息。只读human、ai、tool的普通字符串或文本块；system、摘要、reasoning、内部图片载体、artifact及工具原始参数不会被输出或用于关键词命中。普通可见正文仍可能包含用户原先写入的敏感信息；该工具没有任意文本脱敏的保证。

目录不是跨页冻结快照；消息分页固定首次保存上界，后续追加不会混入旧页。正文改写、会话重生成或归属变化后，旧游标/消息版本会返回明确错误，重新查列表或消息页即可。读取运行中的会话只看到已经归档的内容；归档缺失或尚未准备时通过coverage标明，不隐式补录或运行目标图。

预算由启动时读取的`settings.toml`控制，以下为默认值示意，不会自动修改本机配置：

```toml
[tools.history]
default_limit = 10
max_limit = 50
search_batch_messages = 200
preview_chars = 300
read_chunk_chars = 4096
max_output_bytes = 16384
max_query_chars = 2000
```

limit不能超过配置上限或50；query不超过配置上限或2000字符；max_output_bytes至少4096。整包JSON含元数据及游标计入输出预算，预算紧张时减少消息条数或缩短正文，不越过未返回的消息。异常不能伪装成空成功；无持久化、范围不符、旧版本、存储故障均通过现有配对工具错误反馈，普通查询失败允许Agent继续。

history本身仍只提供按需查询，无内部模型调用。Spec127的memory模块负责保存后整理概览及新会话首次有界近期索引；read/search根会话结果附当前保存概览，不用概览代替原文。工具只返回已保存数据，不会在每次启动注入完整历史。
