# R1-05 单一服务与会话协议

状态：2026-10-24 用户批准实施；服务底座已接线并完成本机验收，未提交或发布。CLI 迁移在 R1-06。

## 1. 本片边界与当前数据流

当前 `nyt` 是 Web 启动入口，`nyt chat` 才是终端聊天。两者各自创建 Application，
同库第二实例会被数据库锁拒绝。R1-04 已有一个 Application、多 ProjectHost 和 chats。
Web 目前仅绑定默认宿主，但已有 HTTP/SSE、认证、运行幂等、有限回放和错误映射。

本片新增可独立启动/发现/连接的本机服务，复用上述实现；不改变 `nyt`/`nyt chat`
现有入口，不实现项目页面/目录浏览器、Docker/envd、变更集或远端 Agent。
新服务以 chats 为默认宿主，项目通过明确 ID 打开；不依据启动 cwd 猜项目。
不改依赖、版本、真实配置、用户数据库或既有 HTTP 签名。

正常：客户端 A/B 同时请求启动 → OS 锁只允许一个所有者 → 所有者创建唯一
Application → 发布私有发现信息 → 客户端认证握手 → 各自指定项目/会话发消息 →
同宿主共用 RunDelivery → 两个订阅独立读取同一 run 的事件。

失败：已有服务配置与客户端不同 → 握手明确拒绝，不新建第二应用、不打开项目库；
服务崩溃 → OS 释放锁，发现文件可残留 → 新所有者取得锁后替换发现信息，旧实例
连接不能冒充新服务；历史按既有恢复合同打开，不自动重发失联命令或重放进程。

## 2. 所有权、发现和启动

- 服务身份：实际 OS 用户、规范化 data_dir；配置不是另一个服务身份。
- `data_dir/service/owner.lock` 为系统文件锁，整个服务生命周期持有，最后释放。
  锁句柄不可继承；不删除锁文件，不以 PID/文件存在/端口占用替代实际锁。
- `data_dir/service/discovery.json` 保存实例 UUID、HTTP origin、随机访问凭证及
  握手身份。只允许普通单链接文件，拒绝符号链接；凭证不进入 repr、日志或 argv。
  POSIX 新建私有目录/文件；Windows 使用当前用户数据目录既有 ACL。
- 启动器只解析配置以定位 data_dir；取得服务锁后才装配模型和应用库，初始化失败释放锁。
- 发布发生在应用准备完成后，写入同目录临时文件、同步并原子替换；正常退出清除
  本实例发现信息，失败有日志；锁文件保留。崩溃后的残留由下一所有者覆盖。
- 新模块 `python -m naiyue_test_agent.service` 为显式服务入口；配置路径、data_dir、
  host/port、前端资源路径均由选项/既有默认提供；只允许回环地址，默认端口 0。
- `ensure_service` 先尝试认证连接；没有所有者才受控拉起脱离终端的服务进程。
  并发拉起的输家不创建 Application，客户端有界等待胜者发布。服务启动错误写入
  私有诊断文件，仅记录安全错误码/类型；启动失败/超时明确返回，不无限等待。
  启动者用独立 daemon 线程 wait 自己拉起的子进程，不占 asyncio 默认线程池；
  客户端取消/超时不杀共享服务，服务可继续被其他客户端连接。
- 关闭客户端不会关闭服务；显式服务退出按现有关闭合同停止任务、保存并关闭库，
  最后释放服务锁。进程崩溃不依赖 finally 才释放 OS 锁。

## 3. 握手与认证

- `POST /api/v1/handshake`：产品版本、协议版本 1、OS 用户、规范化 data_dir、
  规范化模型配置路径和 settings.toml 路径逐项核对；返回身份和服务实例 UUID。
- 使用现有 Bearer、Host、Origin 校验和响应安全头；非浏览器客户端显式发送 Origin。
  无凭证、错误 Host/Origin、配置或版本冲突均有小型错误，不回显输入和凭证。
- 客户端禁用环境代理、重定向；发现 URL 只允许回环 HTTP origin，无用户信息、
  路径、查询和 fragment。握手核对预期实例 UUID，不按 PID 杀进程。
- 握手只比较配置来源路径，不声称文件内容热更新检测；配置刷新仍走既有显式入口。

## 4. 会话协议与实际消费者

- `/api/v1/scopes/chats/...` 对应普通对话；`/api/v1/scopes/{project_id}/...`
  对应登记项目。路径后缀复用现有 `/api` 下的会话、run、审批、控制、history、
  模型/工具/权限、压缩/重新生成、观测、MCP、后台任务 HTTP 路由。
- 每个 scope 延迟打开、缓存一个 WebService/RunDelivery；models 由 Application
  显式借给 WebService，宿主不增加模型所有权。没有全局“当前项目”可变字段。
- 每条请求先认证、检查服务关闭状态，再解析明确范围；session/run/task ID 必须由
  该宿主查询。错项目不扫描别库；普通对话不取得文件/Shell 或项目 history/memory。
- 项目管理 `/api/v1/projects` 复用 ProjectAPI 的列举、查询、添加本机/Git、新建、
  重命名、重定位、关闭、删除登记和操作对账；不开放通用本机文件浏览（07）。
- 共享路由模块由旧 Web 与新服务同时使用；保留旧 Web 的 lifespan、静态文件和
  外部路径，避免复制整套转发方法。后台任务同样从本次绑定服务取所有权。
- 所有新协议的有副作用请求必须有 `X-Request-ID`（UUID）；body 自带 request_id
  时须一致。指纹包含 method、scope/path、query 和规范 JSON；相同 ID 参数冲突
  明确拒绝。已有 run/control 回执继续保留；外围回执还覆盖新建会话、项目与设置。
- 回执有数量/响应字节上限，接受前预留最大响应空间，pending 也计入；完成后改记
  实际字节。满时在副作用前拒绝新命令，不能淘汰后把旧命令重复执行。
  请求断开不取消已接受命令；回执保留在本服务实例内，重启后不承诺跨实例恰好一次。
  服务实例变化后客户端必须重新取快照，不自动重放有副作用请求。
- 运行结果仍为 session_id/run_id。SSE 流标识为 scope + run_id，sequence 为游标；
  每个订阅独立，断开不取消生产者。缓存过期返回 resync_required，重新读会话/run
  快照；run 已淘汰返回 run_not_found。容量沿用 WebSettings，有限保留、不承诺无限回放。
- 同会话同时启动第二个 run 返回 session_busy；不同会话共享应用根运行额度。

## 5. 关闭与异常

ServiceServer.shutdown 在 Uvicorn 等待 HTTP/SSE 连接前调用统一 shutdown_task：
先关闭新命令和 delivery 准入，再让 Application 停止运行/后台任务并结算必要保存，
再排空交付和已接受命令，最后由 lifespan 清理发现/服务锁。协议退出与信号退出
走同一入口；lifespan 直接退出也调用同一 task。普通客户端断开不调用 Application.aclose。
启动/清理原异常优先，次要清理错误有安全日志；不吞关键 IO 失败。
服务崩溃后的项目操作/会话恢复沿 R1-03/04，不增加自动恢复执行或远端进程保证。

## 6. 影响文件与接口

- 新 `service/`：身份与设置、实例锁/发现、启动入口、HTTP 应用及范围拥有者、
  幂等回执、项目管理路由、协议客户端/受控启动；各文件不超过 500 行。
- `web/app.py` 的会话路由提取到共享 router/dependencies；WebService 显式可借模型
  API，RunDelivery 增加由外层应用负责关闭时的排空入口，后台任务取当前绑定服务。
- 保留 `create_web_app`、WebSettings、CLI owner-loop 与全部旧路径；新服务入口真实
  装配 R1-04 Application，不是只在测试里可用的未接线模块。
- 新服务测试使用真实临时 SQLite/本机 HTTP/进程，模型用确定性替身；不修改
  conftest、不访问真实供应商。不提交、推送、合并或发布。

## 7. 验收

先确认以下测试预期失败再实现：

1. 两个真实进程争锁仅一位所有者；正常退出释放；崩溃释放且残留发现信息可恢复。
2. 两个客户端共用实例，关闭一个仍能使用另一个；不同 data_dir 的服务可并存。
3. 版本/配置/data_dir/实例冲突和非法发现 URL/链接/凭证拒绝，启动失败释放资源。
4. A/B/chats 路由隔离，双客户端读写同会话及独立会话；错宿主 ID 拒绝。
5. 并发重复新建/run/控制/项目操作不重复执行；参数冲突、缺 ID、回执满明确报错。
6. SSE 双订阅、断开续读、缓存过期取快照；客户端取消不取消已接受 run。
7. 运行中服务关闭收尾且锁最后释放；实际模块启动/发现/握手可用，无真实模型请求。
8. 全后端、前端测试、TS/Vite、标准平台 wheel/Twine、源外安装检查和迁移清理。

实现后更新当前主链与证据，解释 R1-06；未获下一片批准不迁移 CLI。

## 8. 当前主链与迁移收尾

- service.__main__ 从配置/参数取得位置，绑定回环随机端口，构造 ServiceServer；
  service.app lifespan → ServiceOwner → 唯一 Application（chats profile）→ 私有发现。
- launcher.ensure_service → 探测实际锁 → 有界拉起/连接 → ServiceClient.connect →
  已认证握手核对身份/实例；仅本方 Popen 由 daemon reaper wait，不因客户端退出杀服务。
- 新命令 → 前置认证/有界请求体 → CommandReceipts 原子预留 → 独立任务 →
  web.dependencies.get_service → ScopeServices.get → ProjectAPI/ChatAPI.open →
  已绑定 WebService/RunDelivery → 原领域 API/必要保存 → 同一结果供重试读取。
- web.routes 是旧 Web 与新服务唯一会话路由实现；后台任务也按请求取绑定服务。
  ProjectHost 不增加模型/关闭权；WebService 显式借根 models 和实际 MCP 配置路径。
  项目关闭后清除 scope 缓存；重定位后重建宿主，旧会话执行位置不改。
- SSE → 同宿主 DeliveredRun 的有界只读事件集合 → 各订阅本地游标；断开不影响
  producer。真实 TCP 覆盖活跃双订阅、中途断开/续读和阻塞模型下服务主动退出。
- 关闭：ServiceServer → ScopeServices.shutdown（唯一 task）→ 封准入/停止与保存/
  排空 → Uvicorn 结算连接 → lifespan → ServiceOwner 清发现并释放系统锁。
  元数据损坏、解锁/关闭句柄的次要失败有日志，启动/业务原异常保持。
- 已删除 web/app.py 中被共享 router 替代的路由主体；CLI owner-loop、旧 Web
  lifespan/静态资源/HTTP 地址仍有实际消费者，分别留到 06/07，不保留第二套路由。
- 新协议缓存是单服务实例窗口，有限数量和字节预算；未知结果不自动重发。
  尚无新项目页面/目录浏览器、CLI 协议接入、临时协议会话、Docker 或远端执行。

本片新增测试没有改 conftest 或既有测试断言；真实模块启动使用合法 HTTPS 的
离线模型配置，只构造客户端不请求供应商；聊天运行用确定性模型替身。

## 9. 本机验收结果（2026-10-24）

新增 34 项，最终命令与实际结果：

```text
PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q
4984 passed in 353.65s (0:05:53)

.venv/bin/python -m pytest -q tests/test_service_*.py
34 passed in 7.40s

PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_web_*.py tests/test_mcp_runtime_configuration_web.py tests/test_multi_project_*.py tests/test_storage_dependency_direction.py
372 passed in 43.25s

npm --prefix frontend test -- --run
50 files / 424 passed

npm --prefix frontend run build
TypeScript 无错误；331 modules；构建通过，保留原有 >500KB chunk 提示

.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/r1-05-dist
.venv/bin/python -m twine check build/r1-05-dist/*.whl
标准 wheel 构建通过；Twine PASSED

.venv/bin/python build/r1-05/installed_smoke.py
源外 --no-deps 安装、双客户端 TCP、A/B 运行隔离/冷历史、共享模型、无 marker、
实际模块并发拉起、客户端单独关闭、认证服务退出及锁释放全部 PASS
```

285 个包内 Python 文件与源码逐字节一致，完整文件集合及 README 一致；
冻结 917 份输入无漂移，生产 Python AST/500 行、compileall、pip check、git diff --check 通过。
wheel SHA256：`5f6484d33e78bca419623d641ea270a449ce074b6d7a12ee5a429cf045e237cb`。
汇总 `build/r1-05/verification.json`；预期红灯和中间失败均保留，未将后补测试冒称最初红灯。

红绿细化：新模块/客户端/拉起入口先观察缺失模块红灯；复核回归先观察容量准入、
握手上限、记录初始化和解锁异常优先的行为红灯，再修正。实际 TCP 退出等待环由
三次独立探针复现，随后新 ServiceServer 和活跃 TCP 测试转绿；daemon reaper 也先红后绿。
首轮模块启动测试使用不符合既有 HTTPS 合同的 HTTP 配置，纠正测试输入，未放宽
源校验；首次旧 Web 专项漏 native PATH 导致 1 failed/371 passed，补原有工具路径后
单项及完整 372 重跑通过。没有改既有测试断言、conftest 或测试基础设施。

架构、安全和合同复核发现已由本片回归关闭。源码改动只涉及四个既有 Web 模块，
新增 13 个生产模块和 7 个测试模块；application/storage/CLI 和前四片源码保持。
无依赖、版本、真实配置或用户库变化；无真实供应商调用、Windows/Linux 原机证明、
提交、推送、合并或发布。用户理解/独立调试未新增证据。
下一步讲解并批准 R1-06，批准前不迁移 CLI。
