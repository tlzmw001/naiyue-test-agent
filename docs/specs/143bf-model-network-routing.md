# Spec143BF 独立模型网络配置

用户已选择独立 `[model_network]`，不与工具/MCP 的 `[network]` 共用路由。现有模型目录统一创建/关闭 SDK HTTP 客户端；本机 DeferredModels 未传网络配置。云端 Runner 固定请求 Coordinator，后者 ModelRelay 按受信模型路由出网，当前均无产品网络配置。

## 配置与行为

- 本机私有 settings.toml 新增 model_network，CloudSettings 的私有 cloud.json 同名字段。复用 NetworkSettings：auto/direct/proxy、proxy、proxy_username/proxy_password、no_proxy，支持已有 HTTP/HTTPS 代理与旁路匹配。默认 auto、不配置旁路时保留当前 SDK/HTTPX 环境代理行为。
- direct 不采用环境代理；proxy 采用手动地址；显式 no_proxy 优先，代理认证沿既有编码规则。非法配置明确失败，不回退直连。代理凭证不进入日志、公开配置投影或 Runner 快照。
- 保持现有 CA 环境设置与证书/主机校验；本片不新增 TLS 跳过、SOCKS/PAC、provider 覆盖或热更新。修改后重启对应服务，模型目录继续按原生命周期创建一次。

## 接线与所有权

正常本机链：settings.toml → AppSettings.model_network → DeferredModels → ModelCatalog.prepare_async_clients → OpenAI/兼容/Responses、Anthropic、Ollama 的同步和异步 HTTP 客户端。共用目录的主会话、子智能体、压缩和记忆模型调用得到相同路由；不改单次调用/超时/重试语义。

正常云端链：cloud.json → CloudSettings.model_network → ModelRelay 的唯一共享 HTTP client。按固定 provider origin 构造 transport mounts，让外部模型走代理、旁路模型直连；复用现有路由表，不新增动态目标、每请求建连接或会话代理状态。Runner → Coordinator 固定通道保持不继承环境代理；模型根密钥与网络代理凭证不下发 Runner。

失败链：配置校验失败在加载边界记录安全错误；代理不可达时传播原 SDK/转发失败，不重试直连。准备部分失败与应用关闭继续经既有 catalog/relay 关闭资源，Ollama 替换前的未使用客户端也保留到关闭，不留孤儿客户端。本机沿用DeferredModels现有G5懒加载锁防止重复创建目录，不新增锁或改变嵌套；Coordinator客户端只在owner loop无await创建并复用。

## 影响与验证

修改 settings、模型目录/客户端生命周期、DeferredModels、Coordinator settings/relay；新增一个模型网络适配模块，把既有 selected_proxy 接到 httpx/httpx2，避免复制旁路算法。同步更新完整配置示例、云端示例/说明、测试和交接。涉及超过 8 个文件，但不增加依赖、改变模型 JSON schema、任务协议或 UI。

测试先红后绿：两端加载/默认/非法配置及脱敏；真实各 SDK 客户端的 auto/direct/proxy/bypass、代理认证、同步/异步实际请求和关闭；本机懒加载接线；Coordinator 多 provider 路由及关闭；固定 Runner 通道、转发与观察原回归。运行相关模型/网络/云端测试、完整后端、前端和生产构建。真实 Windows 代理与公司 CA/模型链路另做原机验收，不用假代理证明真实供应商可用。

回滚只需回到旧程序并删除新增配置节；本片不修改数据库/真实私有配置，不提交、push 或发布。

## 启动边界

策略在现有模型目录准备阶段装配到最终发请求的客户端，不迁移SDK最初的构造流程。因此进程已有的代理环境变量本身须为SDK可解析的有效格式；畸形scheme或需要未安装可选依赖的环境代理，可能在显式策略覆盖前导致SDK构造失败。此时需先移除或修正该环境变量。有效HTTP(S)环境代理可被direct/proxy覆盖，且显式proxy失败不回退。若后续要求连SDK初建也完全不读取代理环境，须把策略前移到模型工厂，并另行核对初建客户端所有权与失败清理；本片不以临时修改进程环境来规避。

## 本机验证（2026-11-10）

- 白名单遗漏用真实load_settings先复现 `Unknown runtime settings section`，加入model_network后转绿。新增专项46通过：四类SDK同步/异步的环境代理、直连、手动代理、显式/auto旁路；区分手动代理和环境代理的实际目标；认证编码、失败不直连；两端加载/脱敏/懒加载接线、固定origin转发/关闭和httpx/httpx2的CA校验。
- 完整后端按CI文件分片运行：3322＋3334＝6656通过。首次缺少fd导致两片各一项文件搜索测试失败，补齐项目固定版本原生工具PATH后，两项单独复跑及两片完整复跑通过；未修改失败测试或断言。
- 前端78文件666项通过；TypeScript/Vite生产构建、标准macos-arm64 wheel和Twine检查通过。wheel内新模块及三处关键接线源码逐字节一致。
- 结果与命令记录：`build/model-network-20261110/verification.json`，详细日志 `build/model-network-*.log`。本机测试使用真实SDK连接环回替身，无真实供应商调用；Windows代理、公司CA和真实模型链路仍待原机验收。本片不修改子智能体工具描述，也未提交/push/发布。
