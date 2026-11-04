# Spec143AD：S09.1模型查询参数与任务按钮对齐

状态：2026-11-02，用户报告R1已完成输入/上传/任务创建，模型目录查询被422拒绝；要求新建、删除按钮字号/尺寸直接参考既有设计，并询问结果文件保存位置。

## 1. 模型查询协议修复

实际链：CloudApi.models → 本机service.cloud_tasks.observation(view=models) → ManualCloudTasks.view → CloudClient.request → Coordinator runner_routes.models → owner_authorize(query_fields=()) → models.options。共享本机观察路由默认组装limit=50，models因此收到不支持的查询字段。用户直连原机对照裸GET为200、带limit为422；任务已ready，此故障阻止UI选择模型和首次发送。

修正只在本机转发组装处为models使用空查询；snapshot仍保留limit/thread_id，usage仍保留limit/after_model。原本机分页数值校验、跨绑定授权、认证和不适用thread/after字段拒绝保持；不放宽Coordinator白名单，不改变公开模型目录返回值或引入新的协议版本。

回归通过真实本机ASGI入口、真实CloudClient和Coordinator生产模型路由/owner_authorize；只有模型目录读取与runtime计数操作使用受控夹具，不发供应商请求。默认/显式本机limit均不得被转发；本机未授权/非法参数不访问上游，故意直连带limit仍须422。现有宽松Fake Remote未覆盖严格查询合同，不能继续仅凭假服务返回模型列表验收。

## 2. 按钮直接对齐既有控件

视觉基准为现有App.newChat、SessionMenu菜单删除项及确认按钮，沿Spec113的字号/字重/圆角刻度。新建任务复用App.newChat与plus图标，移除云端通用按钮样式对该控件的覆盖，只移除主栏布局专属margin；不继承新的悬停变色。新建表单提交改为自然内容宽、靠右，采用既有确认动作的内边距/14px字体。

云端删除入口按原会话删除菜单项的14px、9px/10px内边距、无边框与危险色；确认按钮按原删除对话框14px、9px/15px内边距及红底。对话框标题16px/600、正文13px、尺寸/间距沿原确认对话框。布局上下文不同，菜单项整行宽度不强塞进云端工具栏；比较字体、内边距、圆角、行高与实际按钮高度，不以不同文字的自然宽度相等作为要求。

只修改云端列表标记、自有CSS，不改变主会话样式、按钮命令、预览/确认、disabled和键盘焦点行为。浏览器读取原控件的实际computed style再比较云端控件；同时检查浅深主题、375px、稳定hover和取消删除零副作用。

## 3. 文件保存位置保持现状

“下载”通过浏览器Blob下载，位置由浏览器配置/另存为设置决定；代码文件默认下载的是固定patch/清单，不自动展开成工程。“接收到本机”由本机服务保存到项目数据库相邻cloud-results/blobs内容地址目录，供离线材料读取；与浏览器下载目录无关。“导出新目录”在当前授权项目根下创建用户指定的新目录；分支与应用也属于项目内独立预检/确认动作。本片解释并补全文档，不改变目录、下载API或交付合同。

## 4. 交付与验收

延续R1的fsync修正与文案清理；候选累计覆盖R1＋本片，仍排除未采用的build_proxy_env候选和独立Spec142草稿。公开包以基线＋源码补丁指纹标识，不修改旧Release附件或远端服务。

验证包含先红后绿的严格模型查询回归、全量后端/前端、默认/候选构建、标准多平台wheel与安装检查，以及真实本机HTTP/受控owner的UI尺寸对照。公司真实模型出站/B12首次发送仍由Windows原机接续；不把受控目录测试或本机UI检查称为真实供应商请求成功。
