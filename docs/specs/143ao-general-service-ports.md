# 143AO 通用服务入口与本机配置可见性

状态：实现与本机验证完成（2026-11-04）；未提交、发布或部署。用户已批准：一个network_id多个通用TCP入口；Windows只通过本机profiles选择自己的ID，不增加租户鉴权、占用租约或自动接管。不发布、不修改真实部署配置，Spec142草稿保留。

## 数据流与接口

页面 → 本机profile校验 → Coordinator固定端口授权 → 本机OpenSSH -R → Linux端口 → Windows目标；run:start读取同一network_id的当前启用入口并给Runner。

NetworkEndpoints改为bind_host与ports字典（入口ID→端口，1–64项，ID字母数字/下划线/横线/点，1–64字符；端口1024–65535）。全Coordinator地址/端口不得重复。数量上限限制进程和命令体资源，不按应用协议分类。管理员编辑cloud.json并重启Coordinator分配端口；Windows页面不能增改监听端口。

ServiceTarget保留address/enabled，增加可选name（0–80字符，禁止控制字符）；旧目标无name正常读取。地址接受http/https/grpc/grpcs/tcp/tls；HTTP可省略默认端口且保留路径，其他协议要求显式端口且不接受业务路径；全部拒绝凭证、查询、片段和控制字符。TCP不等于UDP/QUIC支持；tls表示TLS-over-TCP，仍保留原域名校验。通用工具是否支持业务协议单列。

NetworkChange.slot从http/grpc枚举改为入口ID，通用入口使用本机/entries/{slot}避免test/cancel动作冲突，保留已发布旧URL为同函数兼容入口，请求字段保持；Coordinator prepare/probe/finish验证入口在管理员分配集合中，本机change验证集合后才能保存意图。未知profile在本机即拒绝，不访问云端；UI只消费本机profiles，不引入全局network枚举。

## 升级与状态

0.5.8已发布的http_port/grpc_port仅在NetworkEndpoints输入边界转换为ports.http/ports.grpc；新旧字段混用拒绝，序列化统一新结构。本机旧networks.json中的端点在加载时规范化，保存时写新结构；slots键、revision、command_id、pending和cancel意图不变，不自动重放新操作。无name的旧pending保留原始payload，避免改变原ID幂等身份。此兼容仅针对已发布配置/持久状态，不保留平行运行分支。

配置增删入口前停止关联任务与本机服务；仅新增入口且全部既有ID/端口与桥地址不变时允许扩展，页面读取新入口；删除/改绑已有端点仍明确拒绝，不能自动改绑。配置变化恢复步骤由文档说明；本轮不新增动态管理员端口管理。已启用但被删除的入口导致任务路由生成明确拒绝，不能悄悄漏掉路由。

## 行为与失败

正常：管理员分配四项 → 用户填写名称/任意TCP目标 → 保存意图 → prepare检查整个network静止 → 只停止/重建当前入口 → SSH精确成功ACK及TCP探测 → finish提交revision → 下一轮Runner收到全部启用入口。
失败：未知入口0持久变更；地址不合法0连接；SSH失败保留pending；旧回执丢失按原ID恢复；其他入口继续重连。原有运行/后台任务阻止整组切换，关闭网页保活，主动断开持久保持。共享SSH/Owner下管理员仍须保证不同Windows使用不同ID；本机可见性不是服务端权限隔离。

## 文件范围与验证

复用contracts/cloud_networks、application/cloud/settings、cloud_local/networks、cloud_files/service_tunnels、两侧network_routes、Runner现有services上下文、CloudNetworks组件/cloudApi；新增字段同步序列化、持久状态与测试。超过8文件，锁范围和顺序不变。

先红后绿：多入口及协议无绑定、端口去重/未知入口、旧配置及pending迁移、第三入口重连、profile本机拒绝、Runner跨轮完整路由、UI多项/名称/断开/配置可见性。随后完整后端、前端测试与构建、标准wheel/Twine/安装烟测；Windows真实SSH/公司业务协议仍由原机验收。文档同步隧道手册、完整配置参考与例子，0.5.8升级指南保留版本边界。

## 实施复核

已将固定槽位生产分支改为ports字典；仅保留已发布端点输入格式与旧本机API的边界兼容。JSON meta存储沿用slots，name随目标保存，空name省略以保留旧pending幂等payload。独立审查确认并修复：test/cancel路由碰撞（canonical entries路径）、change在保存前检查原端点防改绑、UI动态键只读own属性。对应回归先红后绿。旧落盘失败测试夹具补齐真实合法端点，以触达原有落盘失败边界，未改变断言；新UI测试使用仓库现有原生DOM断言，没有引入matcher依赖或修改测试基础设施。最终全量后端6467通过（512.10秒，0失败/错误/跳过），74文件615前端通过，TypeScript/Vite构建与标准Mac ARM64 wheel/Twine/源码外新venv安装通过。源码与包内容一致，真实SSH未测；独立架构/安全复核关闭三项发现。见[验证记录](../../build/general-service-ports-20261104/report.md)及[迁移/使用手册](../cloud-service-tunnels.md)。正式0.5.8不含本轮变更。
