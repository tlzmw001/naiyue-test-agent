# Spec143AF：S09.1正式接线与S09.2旧链收束

2026-11-03用户明确调整顺序：先完成S09.1/S09.2代码，再统一实机验收。本指令替代143e/143f中“实机门禁后才删除A代码”的实施顺序，不代表公司验收通过或远程部署授权。仅修改本仓库源码、测试、当前文档和标准构建；保留初始dirty/Spec142、真实配置、用户数据库、目录、卷、镜像及现有远端服务。

## 1. 当前链与目标

迁移前正式Web由main→ProjectWorkspace→App装配，只有cloud-candidate模式接云端菜单；默认仍经Composer环境菜单、SandboxManager和旧结果API操作A。共享服务Application.core装配EnvironmentAPI，SessionService通过execution_binding选择Host或Docker/envd能力；service/environments、web/result_routes和CLI sandbox选项仍可调用旧链。914条冻结记录覆盖直接/传递消费者、测试和发行工具，不等于914个应删除文件。

目标：正式Web直接接当前CloudTasks/CloudResults，普通主会话使用Host能力；独立云端任务由既有Coordinator/Runner链执行。移除A创建/准备/管理/结果/交付入口、DTO及无消费者实现，不保留候选开关或旧链回退。CLI保留普通chat和已有nyt cloud命令。协议/配置显式拒绝旧A请求。用户进一步明确不需要兼容旧sandbox会话：不保留其专用历史展示、状态、setup解析或恢复入口；旧绑定在身份边界拒绝，不能退回Host或普通无目录聊天。普通本机会话历史继续支持。

正常例：正式加号菜单→CloudTasks→本机ManualCloudTasks→已鉴权Owner命令→Runner→固定结果→LocalReceives/LocalDeliveries；主会话的普通发送仍经RunAPI→HostFileSystem/HostProcesses→必要保存，cloud_result_read只读取已接收材料。

失败例：读取或恢复持久化execution_binding=sandbox的旧会话在身份校验处被明确拒绝；没有Docker调用、Host降级、专用兼容DTO或数据迁移。旧sandbox配置在加载边界明确失败，不改写用户文件。云端unknown/partial继续当前既有对账合同。

## 2. 实施范围与接口

- 前端：main、ProjectWorkspace、App、Composer/新建会话、api/types及相关测试去掉候选标志与A环境/结果组件，接正式云端入口。
- 本机装配：core/lifetime/HostManager/SessionService移除EnvironmentAPI及后台准备；会话的本地执行准入直接保留，旧sandbox存储值仅在边界判为不支持，不保留兼容读取链。删除A路由、结果方法、CLI参数和公开旧DTO，B公开API保持。
- 共用Git：先搜索真实调用方，将GitRepository及B结果/分支仍需的有界Git命令和实际仍消费的原生错误保留在不含A快照DTO的模块；删除A快照/bundle/ref发布/merge包装。
- 无本地目录的纯Git URL项目新建/信任入口随A导入链退出；本地Git目录仍走LocalSource捕获。既有项目注册表数据不迁移或删除。
- A专有实现：application/environments、envd、Docker代理/SSH工具transport、A仓储和sandbox配置退出。注册表及会话库的历史schema迁移保留，不对已有表执行DROP；普通本机旧库查看继续保留。
- S09.2：依据真实消费者处理旧构建/探针/脚本/发行清单和当前文档。历史Spec/课堂标版本边界；B实际仍用的公共Git/native/锁/进程/归档能力不能按名字删除。
- 测试：先以正式入口、旧链不可达、旧会话拒绝及旧配置拒绝的失败测试约束迁移。旧A线格式/取消的API断言退出；路径/FD/环境/权限/停止/unknown/必要保存等行为迁到当前Host/B入口，保留正常/异常/顺序/配对断言及迁移对应表。

## 3. 顺序与验证

1. 记录初始dirty指纹/补丁与未跟踪文件备份（build/spec143-s09-20261103），不stash/reset/切分支。
2. 正式前端与本机装配接线，先红后绿；每组先修构建/导入错误再继续。
3. 按消费者图迁移共用能力与行为测试，退出A生产包、旧运行API和配置；不扫描或清理真实资源。
4. 处理余留工具/构建/当前文档，将机器门禁从“冻结A不漂移”切换为“禁止A生产消费者/包成员”，保留原冻结清单作为迁移证据。
5. 定向与完整后端CI分片、前端全量和正式构建、标准wheel/Twine、包内容/源码一致性及源码外新环境安装；真实本机HTTP/浏览器检查正式菜单和主链。架构/安全专项复核及对抗检查。

涉及锁作用域或嵌套变化时同片更新lock-hierarchy。最终列删除项、保留项及实际消费者、行为测试对应及实机pending。S10更新管理与Spec142不在本轮范围；本机测试不替代真实Windows/Linux、公司HTTP/RPC、供应商、容器故障与性能证据。不提交、发布或部署。

## 4. 实施收尾记录

本机共享服务协议由1升级到2，拒绝复用仍运行旧合同的服务。TaskAPI去除旧沙箱聚合后仍在list/output/stop前用当前Host的会话仓储验证身份；三入口跨项目拒绝测试先红后绿。普通本机会话、图像材料、后台任务、审批和必要保存继续当前链。58个A专用生产模块、19个前端旧组件/测试/类型文件及29个旧工具/清单文件退出，完整路径记录在本轮build目录；不把文件数量当作行为覆盖证明。测试迁移分类见[本轮迁移说明](../../build/spec143-s09-20261103/test-migration.md)。

## 5. 最终本机验证

6159后端全部通过，0失败/错误/跳过，与完整收集一致；72文件/594前端通过，正式构建退出0（保留既有大chunk警告）。四目标标准wheel/Twine、483个生产Python及4份前端资源逐字节对包、58个退役模块缺席与Mac源外新venv真实安装冒烟通过。真实本机HTTP/Chromium走通正式入口、中央窗口、已有/新目录实写、预检无执行与分析草稿不自动发送，云端是受控Owner。架构/安全专项收口，无关Spec142与真实配置保护通过。详见[本轮报告](../../build/spec143-s09-20261103/report.md)和[结构化记录](../../build/spec143-s09-20261103/verification.json)。

S09.1/S09.2代码及本机验证完成；15个后续单元完成12项，余S10.1/S10.2/S11。公司/供应商/Windows/Linux/性能验收仍按用户要求统一后置，不追认为已通过；未提交/发布/部署，无新增用户掌握证据。
