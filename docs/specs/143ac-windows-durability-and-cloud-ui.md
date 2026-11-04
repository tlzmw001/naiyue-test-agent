# Spec143AC：S09.1 Windows文件持久化与云端控件对齐

状态：2026-11-02，用户要求修复公司Windows输入捕获阻断，并对齐既有主界面控件、删除云端常驻说明。保持原S09候选/私有Docker wrapper与验收现场，不修改公司site-packages或已部署镜像；本机完成修正后交付新候选供复验。

## 1. Windows文件持久化

截图报告Web Git输入捕获在本机失败，Coordinator仍无project/任务，尚未上传。真实主链为本机source/create API → cloud_files.capture.capture → 冻结文件/生成归档和元数据 → 逐文件fsync → rename发布。最后以rb打开已生成文件调用fsync；公司Windows最小探针报告EBADF。Unix允许的只读文件flush不能作为Windows合同。

修正capture.py最后确认使用不截断的r+b句柄，保留每个最终文件fsync、目录确认和先确认再发布的顺序；源项目仍只读，不删减fsync或忽略错误。测试以Windows只读句柄拒绝语义覆盖Git/selection及gzip/tar，并核对全部最终文件确认、内容身份和源字节不变。EIO等真正持久化失败必须仍记录安全诊断、不发布输入。

同类核验包括cloud_files中所有fsync：写入句柄、管理库O_RDWR句柄与既有Windows目录处理保持。若结果导出同样对source_handle的只读句柄fsync，使用单独r+b句柄，先和仍保持打开的源句柄核对完整文件身份，再flush；不把项目读取入口改成可写，不改变路径/链接/身份检查。对应正常、确认失败和身份变化测试属于同一兼容性修复。

正常：最终字节关闭 → 可写句柄flush确认全部文件 → 原子发布 → 返回原input_id。失败：flush失败或身份不符 → 安全错误/清理临时捕获，不声明输入ready或导出completed。来源/格式/网络配置及公开API保持。

## 2. 既有界面作为视觉基准

视觉基准为当前App/Composer/ModelMenu/ProjectPicker，而非新主题。复用theme.css已定义的14px交互正文、13px表单/辅助控件、12px元信息、字重/行高/圆角与浅深主题变量；标题不再用18px大号。原生下拉沿主会话发送方式控件的透明背景、13px字体和窄边界处理，不引入新的下拉框运行时或依赖。保留原生键盘、标签、disabled和focus-visible。

CloudTasks.module.css当前通用button:hover比primary背景规则优先，造成浅背景/白文字；云端操作按钮取消这种悬停改色和primary透明度变化，保持普通/悬停颜色一致。规则只限云端自己的工具栏/动作，不覆盖复用的ModelMenu、Conversation复制按钮、工具卡等子组件。代码交付输入/下拉同样采用既有字号和边界。

删除CloudTasks列表说明、CloudTaskCreate两段说明、CloudTaskDetail草稿/业务中转固定解释，以及CloudResults/CloudResultReceive/CloudCodeDelivery中的固定教学/免责声明。保留真实加载/空态、运行状态、网络/保存失败、unknown核对、冲突和删除/释放/交付的显式确认，不隐藏用户需要处置的状态；保留主聊天原有文案与流程。

无业务协议、草稿保存位置、执行/停止/释放/删除、材料接收、代码交付和网络语义变化。UI测试先证明固定文案当前存在；样式由真实浏览器对普通/悬停的背景/文字/透明度、原生下拉字号、主/云复制控件与模型菜单，以及桌面/375px浅深主题渲染检查。

## 3. 范围与验证

后端改动限capture、必要的导出flush点和对应测试；前端限云端CSS与上述组件的文案及对应行为测试，不修改主会话组件。源码/测试之外同步本Spec、S09接续说明与handoff，不夹带先前代理候选、独立Spec142或真实配置修改。

确定性回归、全量后端/前端、默认/候选前端构建、标准wheel构建/检查及源外安装分别记录；Windows真实复验仍由用户原机运行。不能把Mac对Windows flush语义的模拟或本机浏览器检查称为真实Windows门禁通过。候选仍按源码指纹明确身份，不自动提交、上传或部署。
