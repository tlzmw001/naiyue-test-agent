# R1-07A：默认 Web 接入共享服务

状态：2026-10-24 用户批准实施，已完成本机验收。基线为 R1-06 收尾后的工作树，逐文件快照见 build/r1-07a/baseline.json。仅在 codex/project-execution-root 分支实施，不提交、发布或修改真实配置。

## 1. 范围与当前主链

当前 `nyt` → web.__main__.main → prepare_workspace → create_web_app → 独占 Application/SQLite/Uvicorn。浏览器 WebApi 使用无范围 `/api`；终端 `nyt chat` 已经使用共享服务的 `/api/v1/scopes/{scope_id}`。两者不能同时拥有同一个库。

本片改为 `nyt` → ensure_service → 身份握手 → 已认证项目准备 → 输出带固定 scope、实例和凭证的本地访问链接 → 退出启动器。共享服务挂载构建资源；浏览器直接调用同一个服务的明确范围接口。关闭终端、页面、SSE 不停止持久 run。再次启动同 data_dir/config 连接原实例，配置不匹配明确拒绝。

7B 才实现项目侧栏、项目切换与普通对话入口；7C 才实现目录浏览、新建目录和页面信任流程。本片不改变既有页面布局，不实现 Docker/远端。

## 2. 启动与选择合同

- 保留 --config、--workspace、--session-db、--new-workspace、--host、--port、--frontend-dist；增加与终端一致的 --settings、--project、--trust-project。已有配置默认值沿用，不修改配置文件。
- 默认选择当前目录。已登记项目复用；新目录或未信任项目先在启动终端显示规范路径，TTY 输入 trust 才确认；非交互模式必须显式 --trust-project。取消不登记。
- --session-db 复用已有项目导入/恢复逻辑；失效目录的历史仍能打开，新执行由原 authority 拒绝，不换目录。显式旧库若因目录身份冲突不能导入，复用 R1-06 的 authenticated legacy 只读接口；链接绑定 legacy + 明确库路径，独立只读档案页不提供任何执行/登记功能，不装配模型。--new-workspace 冲突检查保持。
- 启动器不装配模型、打开库或持有服务锁。退出只关闭 HTTP client；共享服务生命周期保持 R1-05/06 合同。保留输出私有链接的使用方式，不自动拉起浏览器进程。
- token 由服务发现文件提供，NAIYUE_WEB_TOKEN 不覆盖现有服务身份；过往开发用固定 token 环境变量不再控制默认入口，文档说明该变化。

## 3. 浏览器协议

- 启动链接 fragment 包含 access_token、scope_id、instance_id，不使用查询参数。服务静态响应不含 token/路径/配置；只有构建资源公开，API 认证仍检查 Host/Bearer/Origin。
- 浏览器读取完整 fragment 后移除并保存到当前标签页 sessionStorage；刷新保留范围。缺失/无效 bootstrap 明确显示访问失效，不默认为某个项目。
- WebApi 固定持有 token、scope 和 instance；全部领域请求使用 `/api/v1/scopes/{scope_id}`，全部写请求带 UUID X-Request-ID；有 body.request_id 时 header 与其一致。无自动写重试。
- 每个请求带 X-Service-Instance；在认证后、幂等收据查找前核对实例，防止重启后旧页面把旧命令发给新实例。终端 ServiceClient 同步接线。流断开沿用有界 SSE/快照核对，不能重发原 run。
- 静态未知 /api 路径返回错误，不用 index.html 冒充 API 成功；CSP/no-store/no-referrer/禁止 iframe 保持。

## 4. 正常与失败例子

正常：两个命令窗口对同项目启动 nyt → 同实例握手、项目复用 → 两个链接固定同 scope → 页面 A 创建会话和 run（UUID 收据）→ 页面 B 打开同会话，读取同 run/SSE → 同会话并发提交返回 session_busy → 关闭 A 或启动器，B 仍可观察/控制。CLI 使用同 scope 也可继续观察/发起下一轮。

失败：原实例停止、新实例启动 → 旧页面携带原 instance → 服务认证/实例核对拒绝，必要时认证本身先失败 → 不分配收据、不打开 host、不执行。目录消失后仍可读取档案；新 run 返回 execution_root_unavailable，不回退。网络写请求响应丢失只显示未确认并保留输入，页面不自动再执行。

## 5. 文件与消费者核对

改动覆盖 web 启动器、service 静态资源/实例检查、ServiceClient、frontend bootstrap/WebApi/App 接线及启动测试、协议测试、前端现有 URL 断言和 README。核对 release_smoke、TCP probes、旧 create_web_app 消费者：主产品入口必须只使用共享服务；孤立路由/故障注入测试仍须保留同样的业务和异常断言，不能为旧启动器保留生产分支。

## 6. 验收

先红后绿：启动器复用/信任/取消/失效历史/配置错误/关闭失败；静态公开与 API 鉴权；实例错配前置于收据；浏览器固定范围、fragment 移除/刷新、写 ID 一致及无自动重试；真实 TCP 双客户端与 CLI/Web 共同观察/忙碌/断开。

聚焦测试及专项审查完成后冻结源码；一次完整后端、一次完整前端、TypeScript/Vite、标准平台 wheel/Twine 与源码外新环境安装。保留实际输出和构建字节核对，不用旧测试结果证明当前成果。未连接真实模型或外部业务服务。

迁移说明：原 create_web_app 的独占启动分支删除。历史路由/离线探针改用 scripts.web_probe_app 的显式固定宿主测试装配，复用真实 create_service_app、鉴权/收据/路由；它只提供故障注入观察入口，不属于安装包或产品启动分支。新的产品启动/跨客户端测试使用正常 ScopeServices，不能用固定测试宿主证明项目隔离。

## 7. 专项迁移中确认的收口

- 浏览器空闲会话增加只读有界快照和会话列表同步：仅可见页面定时GET，不启动恢复/模型工作；发现其他客户端的新run再进入既有SSE。离开会话/卸载中止请求，晚响应不能写入新选择，活动本地提交/配置/恢复或已知run运行时不覆盖界面。
- 全局收据继续保留成功和结果未知；只释放已确定没有执行副作用的拒绝（invalid_request、stale_approval、stale_run、session_busy、run_capacity_exceeded），允许用户明确再次提交；不自动重试。释放须核对当前task身份以避免并发等待者删掉新条目。运行已不在delivery时，缓存控制收据不得返回旧保留文本。
- 旧路由测试的max_receipts是该范围运行交付窗口；测试服务全局窗口独立，不把“创建会话”算作旧窗口的一次run。保留原失败/顺序/权限断言。
- 信任提示按现有CLI用JSON转义规范路径，控制字符不能伪装授权目录。

全量迁移补充：ASGI离线基准无TCP监听，测试装配为发现DTO分配临时loopback authority，业务WebSettings与原ASGI请求来源保持；真实启动器仍由正式listener分配端口。原os.replace写忙碌测试的注入时机移到共享服务初始化之后，仍精确断言工具提交次数，不把服务发现文件混入工具副作用。未修改任何业务断言或放宽权限。

同类回执核对：缓存的session-specific POST/PATCH/PUT（包括返回历史的prepare）也须确认会话仍存在后才能重放；DELETE重复仍返回其原成功收据。新增删除会话后重放prepare的确定性反例，先红后绿，禁止缓存绕过404返回已删除档案。

## 8. 当前验收与证据边界

最终完整后端 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short -x`：**5046 passed in 420.09s**；53个前端文件 **436 passed**；TypeScript/Vite、标准Mac ARM64 wheel、Twine、源码外全新虚拟环境安装通过。新增13个后端、12个前端用例。包内294份Python/4份Web资源和README一致，948冻结输入无漂移。最终wheel SHA256：`cf9f4983a3680e34f5ea08f22c8e25115b7b165c3ec22afa1c1cb1829e260b66`。

真实TCP覆盖两个默认启动器复用实例、固定项目、浏览器观察/断开、终端停止同run、同会话忙碌。真实应用内浏览器打开构建页面、发送固定模型消息显示回答；冲突旧库只读页显示已有历史、无输入/执行入口。原Playwright扩展不可用，已用应用内浏览器完成实际操作。只使用可销毁的固定模型测试数据，没有模型供应商请求或用户掌握证据。

首轮全后端4失败/5041通过：只读ASGI基准的port0发现DTO与写忙碌测试捕获了服务自身discovery写入，已迁移测试装配/注入时机并保留原业务断言，最终完整重跑全绿。回执释放、临时销毁、多key跨scope复用、删除后prepare历史重放均保留先红后绿证据；专项架构/安全复核已收束。保留两份候选和首次构建预检输入错误，不伪称一次无失败交付。最终汇总 `build/r1-07a/verification.json`；Vite仍有JS包超过500kB提示，构建零错误。

只更新重构工作树产品源码；主目录并行Spec129/XML未纳入构建，未提交/push/合并/发布。服务静态目录、监听地址/端口由服务首次启动选定，已有服务不会因再次运行启动器而重挂载或迁移监听；显式端口不匹配继续按R1-05拒绝。唯一下一步是讨论并批准7B，7C和后续环境/沙箱仍未实现。

## 9. 7A 收束时的下一片建议（历史，7B 已交付）

用户已批准 7B，当前实现/验收及 7C 计划见 [R1-07B](R1-07B-project-navigation.md)。下列内容保留为 7A 时点。

- 左侧显示已登记项目，可展开会话；普通对话独立显示。已有项目下新建会话归属该项目，普通对话归属chats。
- 页面选择保存(scope_id, session_id)，每个WebApi保持不可变scope，切换不改旧会话身份。输入区显示当前会话真实项目/此计算机；离开只取消观察，不停止任务；草稿和晚响应按完整归属隔离。
- 导航读取项目/会话目录不为每个项目提前编译图；复用已有项目登记和metadata读取，缺少的只读目录接口在7B Spec明确。仅打开具体会话时装配所需宿主。
- 空、加载、失败、未信任/目录不可用分别显示；不可用项目仍能查看历史，新执行继续由后端拒绝。
- 以A/B项目切换、同名会话、延迟返回、跨页面变化、普通对话隔离验证。选择/新建文件夹和目录浏览器属于7C，7B不实施Docker/远端。
