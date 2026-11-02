# Spec143K：S02 生命周期、持久目录与首个测试模板

日期：2026-11-01。用户已批准继续S02；S01结果见[143j](143j-s01-control-foundation.md)。本片不连接公司开发机，不切正式A入口，不实现Runner/输入上传/模型。先TDD，再专用本机后端验收，收尾讲S03.1。

## 1. 交付与前提

复用S01的显式新根、两组API、CT1、任务/命令DTO与注册表。任务已登记且受控input目录已准备时，可以启动、停止、查询/对账；正式POST tasks仍等S03.1提交输入后接线。无Runner时仅记录environment_phase=backend_ready，task.lifecycle保持starting，不广告Agent ready。

OpenSandbox SDK固定1.2.0，wheel SHA `15a0f3c3245018afa43b75fb14e892a64c2c8807276a0fdaf2a1a469f5e214ea`；作为cloud可选依赖。新增httpx-sse/python-dateutil/six固定版本，已有模型/HTTP依赖不升级。server1.2.0/execd1.1.0沿S00，管理服务和SDK分开安装。服务配置、凭证、端口、UID/GID、镜像与资源预算均显式提供。

backend_id是受信安装/构建时记录的Docker daemon身份，stock SDK没有独立daemon-ID证明接口。Coordinator核对当前显式部署身份与回执/任务快照，配置变化拒绝沿用旧任务；不能把配置字符串描述为SDK现场证明。后端URL限本机loopback；操作员更换daemon/后端必须重新预备并变更部署身份。实机验收由独立受信探针核对Docker事实，Coordinator不挂socket。

## 2. 正常与失败链

```text
owner POST task/commands → authorize/严格EnvironmentCommand
  → lifecycle.command创建受托管producer → shield等待
  → worker/CT1 → 原回执或CAS+容量预留+任务/accepted回执同事务
  → 固定目录/模板核对 → 持久dispatched → 锁外SDK create(timeout=None)
  → 核对metadata/image/无到期/实际身份预检 → worker/CT1保存backend_ready/成功回执

stop → 原回执/CAS/忙保护/持久stopping → 锁外kill → 查询确认已移除
  → worker/CT1保存stopped、清sandbox引用并释放活动额度；workspace/state保留
```

相同ID/摘要先返回原结果，先于当前revision、模板和容量检查；同ID不同摘要409。HTTP取消不取消producer。关闭先封口/排空producer，再关闭SDK和S01资源；没有持有CT1或SQL事务等待网络。

create ACK丢失/进程崩溃进入unknown，保留任务操作归属、代际和活动额度。用instance/task/generation/command metadata分页发现：唯一匹配才核对并接管；零/多匹配、身份不符、后端不可达保持unknown，不重发create。reserved阶段确认尚未dispatch才可继续原操作；dispatch之前落盘，宁可保守unknown。stop只对同一已核对sandbox ID重试幂等删除，并再次确认消失；旧容器未确认移除前禁止新代挂同目录。

## 3. 身份、存储和容量

实施细化：核对环境采用独立refresh命令，终局命令回执不因容器后续丢失而改写。refresh确认旧sandbox缺失后记录stopped，后续start才建新代；paused/异常/不可达仍unknown并占额。start与stop都从任务冻结的typed template_snapshot取得原版本，不依赖当前默认模板仍存在；新任务可选择新版，旧任务不隐式换镜像。template_digest覆盖该快照的语义字段。清理/核对不因新建预算已满而被拒绝。

CloudTask增加environment_phase（absent/preparing/backend_ready/unknown）、environment_operation_id及template_digest；默认值可读取S01内部记录，不新增A兼容层。SQL表/格式不变，字段仍随record_json整体持久化，generation/revision结构列与JSON同事务；旧库不会执行A迁移。

CommandReceipt继续作为唯一持久操作记录，result保存严格验证的EnvironmentOperation（kind、phase、task、generation、backend_id、冻结模板快照、sandbox ID、请求事实）。阶段reserved/dispatched/unknown/finished；status及HTTP状态一致，未确认不标成功。应用构造/校验DTO，storage/cloud/lifecycle.py拥有全部SQL和原子预留/终结，不在应用构造SQL。

活动预算默认2，保留任务预算默认10，均可配置。含未确认create/stop、已知容器或reconciling的预留持续占活动名额；后端断线/超时不释放。尚未公开创建任务，S02对已登记任务集合核对保留预算；S03.1接任务创建时同事务应用该预算。回执预算沿S01，原ID查询不受新命令满额影响。

首次创建使用当前generation；停止后重建推进generation并吊销旧凭证。task/template/release/image/UID/GID均固定；模板变更不隐式升级既有任务，首次启动冻结template_digest，后续不一致拒绝，显式升级留S10。

## 4. 目录、空闲与权限

只挂tasks/<id>/workspace、state（RW）、inputs/<input_id>（RO）；初始令牌挂载由S04接入，不先造无消费者token。路径由受控ID派生，逐级验证普通目录、所有者/权限及无链接；任务不能挂管理根、父目录或其他任务。不打开任务SQLite。停止/重建保留0600/0700内容，不chown/chmod现有树，不跟随任务链接。

模板UID/GID与专用Coordinator服务账号映射；非root、drop ALL、NoNewPrivs、固定bridge/publish loopback由后端策略与实机探针共同验。不同UID不默认可读0600。目录身份不符/丢失/符号链接不自动改权限/换空目录冒充恢复。只创建首次任务需要的目录，未知非空目录拒绝接管。

peer隔离的bridge ICC=false不能独自阻止任务访问宿主其他监听。受信安装必须为任务bridge配置宿主入站白名单（已建立/关联连接返回流量、任务API及明确中转入口）；先用宿主/peer真实正向监听确认，再验证任务侧拒绝，同时确认Coordinator到容器的SDK调用仍可用。该策略属于后端部署，不由无Docker socket的Coordinator假装读取或修改；公司目标机仍须核对实际规则。execd仅loopback发布也要独立核对任务侧不可达。

空闲停止要求已确认静止时间、无active run、审批/输出/后台/必要保存/历史未确认/unknown等blockers且无在途管理命令；准入事务再次CAS核对。S02没有Runner静止报告，默认不自动停止它；测试可用明确的受信夹具覆盖全部保护分支，S04接真实报告。显式stop同样拒绝当前run或必要保存等已知阻碍；主动中断/审批保存语义由S04接入。

## 5. SDK适配与模板预备

适配器只封装create/get/list/kill/preflight/close，不复制DockerCLI生命周期。create显式timeout=None、资源、受控entrypoint、挂载和metadata；list遍历所有分页，get404仅在已绑定同一后端/ID下作缺失事实。SDK的kill返回不独自证明停止；固定Docker产物实测确认API缺失与实际container移除一致。不使用pause释放额度。

初始模板由独立受信CLI预备，Coordinator不调用Docker构建。CLI接受固定发行wheel、基础镜像digest、UID/GID、后端和输出配置，不接任务Dockerfile/shell/URL。读取唯一release/sandbox-image.json固定Kit版本/SHA，构建独立不可写Runner环境与测试venv，预装Kit完整依赖/pytest及必要rg/fd/Python/Git/Bash。预检使用任务身份，实际import/CLI/doctor/pytest报告、解释器、cap/NoNewPrivs和路径不可写；失败不生成可用回执。

校验后的wheel/Kit字节直接用于元数据解析和构建上下文，不重开可变源路径。依赖层先于Agent wheel层，固定基础digest命中本机缓存时不无条件pull；新镜像以构建iidfile固定实际image ID，再完整预检，不能在预检时重新解析可变tag。

BuildReceipt包含image ID（不冒充registry manifest digest）、基础digest、Agent wheel SHA/版本、Kit SHA/版本、测试依赖清单、UID/GID、backend_id及预检结果。以私有文件作为首次受信模板配置；生命周期冻结其摘要。S10在同一预备函数/回执上加下载/登记/自动激活，不引入第二构建链。构建网络与任务网络分开，真实API key不进入Dockerfile/ARG/ENV或上下文。

新增内部CLI提供显式cloud服务启动与模板准备入口，两个Uvicorn监听共享一个runtime/SDK生命周期，owner仅loopback、task仅配置bridge地址；不启动本机Agent或改变默认Web/CLI。选用已有argparse/uvicorn，不新增Web框架。准备入口是独立进程的Docker构建权限，serve入口不接收Docker socket。

## 6. API与实施文件

实施细化：commands还接受refresh，只核对既有环境；仍无POST tasks捷径。SDK 1.2.0官方server的GET/list使用exclude_none，expiresAt缺失表示无到期；协议夹具遵循这个真实wire形状，不把null强塞进生成客户端。SDK原日志可能包含上游正文，opensandbox命名空间经有限事件handler投递，由适配层保留动作/类型/状态；禁SDK透明create重试，初次POST拒绝和后续查询失败分别判断。PID上限由后端docker.pids_limit配置与独立物理检查保证，Coordinator不暴露SDK不能设置的无效pids配置。

新增owner POST /api/cloud/v1/tasks/{id}/commands：严格command_id/kind(start|stop_environment|refresh)/expected_revision/expected_generation；返回持久CommandReceipt。新增owner POST /api/cloud/v1/commands/{id}/reconcile：空对象请求体，原命令身份对账，不换ID。对账producer启动时重新读取持久回执；不能使用等待前的旧running快照覆盖刚完成的终局。两路只在backend已接线时注册；task app无这些路由。GET task显式显示后端阶段，GET command继续S01查询。无POST tasks占位接口。

拟新增contracts/cloud_lifecycle.py；storage/cloud/lifecycle.py；application/cloud/lifecycle.py、lifecycle_actions.py、directories.py、backend.py、backend_runtime.py；cloud_runner/preflight.py；cli/cloud.py及受信模板预备模块。扩展S01 settings/runtime/registry/DTO/app和pyproject可选依赖；每文件≤500行。纯转发/临时入口同片删除，旧A仍有正式消费者，不提前删除。

## 7. 测试、证据与收束

1. 先失败测试：同ID、CAS、并发额度/满额原ID、unknown占额、重复start、模板/后端不符、代际推进/吊销与事务故障。
2. SDK fixture验证实际请求timeout=null、metadata/资源/挂载、分页/404/错误类型/取消/关闭；不用假的SDK通行代替真实协议。
3. 真实SQLite+ASGI验证准入/后台producer/回执、重开对账、原操作无重放、两监听与鉴权；Event/Barrier控制竞态。
4. 专属本机Linux后端：两个任务隔离，实际UID/caps/挂载/网络，Kit与pytest报告，无TTL及控制服务重开，0600/0700停止/重建；独立Docker观察只用于验收，不进Coordinator。
5. 安全/架构复核后，完整后端两分片、前端测试/标准构建、wheel/Twine/本机源外安装；同步锁表/实际主链/handoff，讲S03.1，不自动跨片。

本机仅复用已停止的nyt-spec143-s00专用Colima VM，不改变默认context、不动其他VM/旧容器；所有新资源带本轮唯一标记。远端公司新门禁与真实业务仍待用户在对应机器验证，不能以Mac ARM后端/工具替身追认。无提交/push/发布/公司部署授权。

## 8. 实际接线与验证（2026-11-01）

已实现，正式A入口仍保留。生产入口为`nyt cloud serve --settings <private.json>`：`cli.cloud.load_settings → serve_cloud → open_cloud_runtime → open_cloud_backend → create_cloud_apps → 两个Uvicorn监听`。生命周期为`owner commands → CloudLifecycle.command/_reserve → LifecycleRepository.reserve → lifecycle_actions.start/stop/refresh → OpenSandboxBackend → finish`。HTTP取消不取消生产者，关闭排空后再释放SDK/数据库/根锁。

正常实机：登记受控输入/任务夹具→owner API启动两容器→第三任务容量拒绝→任务写0600/0700→owner停止并确认Docker移除→新generation恢复原文件/权限；仅报告backend_ready，Agent循环尚未装配。API无公共任务创建捷径，输入夹具不冒充S03导入。

失败实机：真实OpenSandbox已返回202后故意丢弃POST响应→持久unknown与额度保留→关闭/重开控制根→同command_id返回原unknown→按四项metadata找到唯一原容器→核对/预检后成功；实际POST只有一次。另验证OpenSandbox重启后原容器无到期时间、Coordinator实际进程重启后活跃容器/终局回执/工作区不变。

| 验证 | 实际结果与证据 |
| --- | --- |
| S02新增行为 | 34项；`tests/cloud`全量加存储依赖为161 passed/5.85s，含S00/S01原测试；见`build/spec143-s02-20261101/cloud-final.log` |
| 产品后端全量 | 两分片3005＋3303＝6308 passed；323.16s/367.81s。唯一warning来自既有恶意重复METADATA测试夹具 |
| 前端与标准构建 | 69文件568 passed；TypeScript/Vite通过，仅既有bundle大小提示；无前端源码修改 |
| 发行候选 | 标准Linux ARM64/x64、Mac ARM64 wheel和Twine通过；403个生产Python文件与当前源码逐字节匹配、三包同源；Mac源码外干净安装通过，Linux ARM64包在真实镜像中执行，x64本轮只交叉构建 |
| 最终测试模板 | 基础digest固定；Agent wheel SHA `0bc218db46fe5f993611ea5e4d434301a6db3fc3fb2cf1508bbc00715e1c3b57`；实际image ID `sha256:b7148df5e8a19a0c45e67f0d4973676a3406c4bc51088c0eae9f5546bc0e1e30`；Kit0.6.0、CLI/doctor/pytest/JUnit/结构化结果及Markdown报告、pip check、Git/Bash/rg/fd实际通过 |
| 物理身份/隔离 | 独立Docker观察确认UID/GID1000、drop ALL、NoNewPrivs、2CPU/2GiB/PID256、精确三个挂载、input只读、execd仅loopback发布，Coordinator无socket；真实peer文件/网络及宿主/管理负向探针通过 |
| 网络部署教训 | 首次测试白名单漏掉ESTABLISHED/RELATED，SDK预检超时并持久unknown；定向连接探针确认根因，补本次bridge返回规则后原ID对账通过；最终源码镜像重跑网络及后端重启通过。ICC=false不等于完整宿主隔离 |
| 复核与修正 | 安全复核的校验后重读wheel竞态、架构复核的对账旧快照竞态均先红灯复现、修正转绿并二次复核关闭；原失败日志保留 |

完整汇总及可核对产物见`build/spec143-s02-20261101/verification.json`。旧1104源文件中本片仅改5个（S01 runtime/settings/contracts/app及CLI入口），新增13个生产/8个测试或支持源；没有删旧A消费者、修改既有测试基础设施/断言或新增兼容包装。模板准备与将来S10更新共享同一函数，SDK采用固定cloud可选依赖，已有依赖不升级。镜像/临时证据保留，本次专用容器/网络/规则/运行期DNS文件清理后专用VM恢复停止，其他VM和默认Docker context不变。

限制：本机Linux ARM64隔离验收不替代公司Linux x64、Windows中转/休眠、模型出站或真实HTTP/RPC业务；模板内已有Kit但S04尚无Agent循环/历史投递/控制通道，Runner ready性能尚不能计量；空闲判定有保护测试，S04取得真实静止报告前无自动停止调度。无公司部署、提交或发布。

下一片只讲解/确认S03.1：沿143b同一manifest/归档/传输合同，先做最小Windows目录捕获、单次流式上传、安全提交、任务输入绑定及一次容器内安装；输入安装模块/回执由S04.1真实Runner沿用，不另造临时上传或安装协议。完整来源/续传/规模在S03.2，Agent运行就绪在S04.1。不自动跨片编码。
