# 130-02 图片历史与认证读取

复用既有canonical checkpoint和chat archive，不新增原图文件或独立图片数据库。规范化后的标准图片块为唯一保存版本。图片元数据投影携带块index、MIME、名称、尺寸、字节数、摘要和canonical消息source_revision；事件、历史列表和普通详情不携带base64。

正常链：历史/事件message_view→元数据→认证GET `/sessions/{sid}/messages/{message_id}/images/{index}`（source_revision、conversation_revision）→会话归属和版本校验→数据库单条有界读取；尚未归档则读取官方graph状态→同一canonical摘要校验→no-store二进制。数据库锁释放后解码。不存在404，未保存409，消息或会话版本变化409。不存在从delivery文本投影恢复像素的路径。

大消息分页通过SQL仅投影图片元数据，不加载整条base64；详情content接口去掉像素并保留文本。备份现有数据库自然包含canonical图片，session删除现有事务删除图片所属记录，不增加文件生命周期。重生成重新标记本轮输入，旧历史门禁规则不变。

验证：真实HTTP图片发送→元数据无像素→认证下载实际解码→错误版本拒绝→错误会话不可读；大图超过历史正文额度仍有元数据；canonical不变、事件无像素。后续05验证备份/删除和持久化。

验证：新增历史下载用例先红（images缺失），认证下载／错误版本／事件无像素与receipt共10 passed；完整主链后续专项15 passed。归档列表SQL投影metadata，现有DB保存规范化图片。
