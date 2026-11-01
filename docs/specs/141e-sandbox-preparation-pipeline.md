# Spec141E：首次准备的调用合并与并行流水线

## 1. 目标及事实

用户已确认继续减少重复操作、并行准备及减少远端认证，不实施容器池、不升级公司Docker。
Spec141D原机完整准备均值24.490s，本机快照13.901s，远端7.646s，安装2.864s。
原生同一git --version中位312ms，复用asyncio循环没有改善。每减少10次Git约省3.12s仅是估算。
完整10秒是公司原机验收目标；max(本机6s,远端6s)+安装3s+其余0.5s是设计预算，不是实现保证。
本片不得以暖输入复用或本机Mac结果冒充首次达标。按实际减少次数和阶段重叠报告新的估算与限制。

## 2. 当前正常/失败链

execution_binding._prepare_sandbox_session → prepare_input → InputService.prepare → SnapshotBuilder.prepare →
service.create（预检、预留、远端创建、保存/核验、独立SSH start、attach+hello）→ install_input → check_sandbox。
快照内部先后查询源HEAD/目录/格式、两项配置、暂存清单及排除清单，读取并检查内容，fast-import写blob，
独立index/write-tree构造树，必要时commit-tree，复查来源，再validate_tree检查实际对象并生成bundle。
失败由原领域异常传播；创建身份/未知状态持久保存，管理路径按原身份恢复。准备任务归服务，切换会话不会取消。

## 3. Git合并（不改变公共接口）

- 初始源目录/对象格式/HEAD查询合为一次rev-parse；无HEAD仍git_input_no_head，错误格式/布局仍拒绝。
- core.filemode/symlinks通过一次Git自身bool解析的get-regexp读取，缺项默认true；保留global/local、重复值
  最后生效及非法bool失败。开始和结束各执行一次，不缓存跨快照来源状态。
- fetch后一次取得source commit/tree，检查commit与原HEAD相同；相同快照内传递不可变tree，不重复rev-parse。
- fast-import一次写blob并创建完整候选tree/commit，保留blob SHA校验；用get-mark/ls取得commit/tree。
  所有路径必须按Git协议引用，deleteall后写完整文件集合，保留删除/二进制/Unicode/执行位/链接。
  clean tree选择原HEAD，dirty tree选择新commit，仍由已有update-ref发布最终input引用。
- 删除由本流替代的input.index read-tree/update-index/write-tree及独立commit-tree路径。
- validate_tree的真实对象/大小/内容/链接/属性检查完整保留；GitSource仍走原有远端源合同。
  用既有batch-check额外核对实际commit的tree OID，实际manifest再与已读取的mode/blob SHA/size逐项匹配；
  不能只相信fast-import返回的合法形状OID。空树按标准SHA1空tree哈希核对，不增加进程。

目标：标准干净fixture由29次降至20次，脏工作树不增加commit-tree。该次数改善不能单独保证6秒快照。
本机内部同时缩短依赖链：初始四项只读清单/配置查询并行（固定四个分支，无新用户配置）；
完成清单/属性检查后，再创建私有目录，并行读取工作文件与初始化/fetch私有对象库；
import完成后并行末次来源核对与真实对象验证。每组至多四个worker，所有future均结算，
失败按原来源检查优先、secondary仅记类型。不得提前创建目录影响源仓库untracked清单。
不在源检查之前做任何源写入；源读取使用既有TaskProcessRunner时仍保持降权，不能把用户目录Git交控制UID执行。
不引入新依赖，不改GitRepository/HostProcessRunner生命周期、不更换事件循环、不手写Git对象数据库。
fast-import协议依据：https://git-scm.com/docs/git-fast-import（commit、get-mark、ls）。

## 4. 服务拥有的两分支准备

用户已确认失败合同调整：允许本次新建的空环境自动回滚，现有环境、已安装输入、启动过任务和unknown资源不自动销毁。
因此同一不可变setup/selection下并行A prepare_input与B service.create，二者进入原有准入/额度/持久化路径。
若A在B开始前已经失败，仍直接传播，不创建无用环境；其余失败按本节回滚合同收尾。
删除此前为零创建保证新增的on_ready/Event链，API/InputService/SnapshotBuilder恢复原签名，不保留并行旧入口。
不在事件循环执行SQLite或同步Git；不持同步锁等待另一分支。gather return_exceptions并由await_settled排空，
任一分支失败/取消都等待已启动IO结算；只在两者成功、progress.check通过后install_input。
进度显示仍使用现有snapshot/container/connection枚举：远端未完成显示container，远端完成仍等快照时回到snapshot。
不新增前端状态或全局线程池。相同binding继续single-flight，其他会话独立。

EnvironmentService.create增加内部可选on_created事实回执，仅新建流程成功保存running且确认容器/双卷均新建后回调；
已有记录返回不会回调。创建前精确检查同名双卷是否存在，若有复用旧卷则不授予自动回滚资格；
SSH检查放在原create脚本内，不增加认证。该检查不声称抵御拥有Docker管理权限的外部管理员竞态。
回执只设置本次PreparationProgress.created_here，不携带远端权限，不以时间戳/get-before-create猜测新建身份。
失败收尾在释放activity.use后取得activity.manage预留，重新核对精确project/session/binding、running状态、
input_id/input_operation_id/input_snapshot全空、且控制端operation记录为空；同时本流程从未进入install_input。
仅created_here=True且以上条件满足，才由既有_perform_action销毁精确容器/双卷。回滚失败记secondary并保留原异常。
unknown、已有环境、任何安装标记或用户操作记录均保留，不自动重试不明删除。
显式停止/取消沿原stop语义，仅停止本次新建的空环境；不把取消扩大为自动删除。禁止interrupt管理自等。
所有分支和必要收尾仍经await_settled排空，不把线程取消当作远端已停止。
保留ready输入/已建环境同ID复用，但不新增跨会话快照缓存或放松来源新鲜度。

## 5. 把远端start合入首次attach

create_remote只做创建→保存container_id→完整核验，移除独立SSH start。
只有已保存、lifecycle=creating且service._create显式授予的main通道连接采用初次启动模式。
SandboxTransport.start_created在传输owner-loop登记精确连接身份的单次启动许可，_open在第一次尝试时消耗，
finally撤销未使用许可；连接失败/重连不重新授予，普通hello、recover不登记许可。
新增这份短暂许可是为区别“首次创建”和“崩溃遗留creating后的只读恢复”，不能只靠lifecycle推断授权。
attach脚本先发完整只读事实；
控制端以running=False核验身份/daemon/双卷/策略，发送既有ACK后，脚本才start精确container_id并exec attach。
start输出不进入帧流。attach ready和hello成功后service才保存running。
普通running、bulk、recover连接保持只读核验及必须running，不因连接失败自动启动已有环境。
首次start和hello同处DockerEnvironment.handshake(start=True)，故一级handshake计时仍包含全部首连成本；
旧内部调用默认start=False，只有service._create传入True。
丢失回执时service仍保存unknown；下一次核对原身份，不重做create/start。已保存身份与确认门槛不删。
本机Docker保持原有启动方式。协议对envd及现有镜像无新增要求；旧镜像的attach/hello可直接使用。

## 6. 观测、锁及范围

分段实验原stage时间改为明确可重叠：保存真实开始/结束，用区间并集算阶段覆盖，另报重叠时间，
不能把两分支耗时相加或将负unattributed误作正常。旧串行事件测试仍可只传duration。
不新增身份/权限缓存或生产锁；同binding的P1/GIP1预留与短DB事务保留。锁层级补充并行两分支的拥有/收尾规则。
范围：git_snapshot.py/git_repository.py、execution_binding.py、preparation.py、service.py、docker_environment.py/
docker_management.py/sandbox_transport.py、分段脚本及对应测试、
本Spec/lock-hierarchy/handoff。不改真实配置、数据库schema、公开API签名、依赖或发行版本。
envd清单中共享git_snapshot源码将变化；按用户此前“允许更新构建清单，远端镜像保持”的授权，
只同步release/envd.json的最终源码哈希，保留envd0.5.6候选版本。协议未增加要求，现有镜像不重建/替换；
实际兼容性必须由新控制端接既有候选镜像验证。该清单同步只服务构建门禁，不冒充新镜像发布。

## 7. 红绿与验收

先红后绿：精确Git调用上限/clean identity/dirty parent及特殊路径/空树/格式/配置/非法输出；
原有真实对象批量验证、源变化/暂存区变化/模式变化/过滤器/链接/敏感内容与预算测试保持。
用事件同步证明快照和远端同时开始；一支失败另一支在途不释放、没有install、失败时新建空环境精确销毁、取消时停止、
secondary失败不覆盖主异常、重复取消排空、同请求single-flight和另会话继续可用。
远端脚本替身验证未ACK/校验失败不start，ACK后start在exec之前，普通连接不start；保存/核验顺序仍显式断言。
分段测试验证重叠并集和非负未归属，不伪造冷准备达标。

相关测试→独立安全/架构复核→完整后端/前端→标准构建/Twine→源码外安装→专用本机Docker。
本机资源仅用独立上下文/精确自建对象；不得操作公司服务。公司完整首次准备/两会话/取消/恢复仍待原机复验。
交付报告实际调用数、阶段/关键路径、失败注入结果和未达目标项；不在本片发标签或替换Release附件。

## 8. 当前主链与关键数据（实现后核验）

正常例子：首次发送选择Docker，SessionExecutionBindingService._prepare_sandbox_session拥有不可变setup，
以同一project/session并行启动prepare_input与EnvironmentService.create。前者产出GitInput及snapshot
（manifest含mode/blob SHA/size，671文件fixture的bundle_size=4530744）；后者经原准入创建并保存精确
EnvironmentRecord（binding_id/container_id/双卷/执行策略），只有容器和双卷均新建才调用on_created设置
progress.created_here。SSH首次handshake通过start_created授予单次许可，事实核验通过并ACK之后才start。
两分支均成功且progress.check通过，install_started先置位，再install_input；安装及check_sandbox完成才ready。
并行计时按真实区间并集计算，不能将snapshot和远端各阶段直接相加。

失败例子：本机源文件检查失败，但远端创建仍在途。await_settled先排空两分支，保留GitInputError为主异常，
不安装输入。释放activity.use后，在activity.manage预留下重新读取同scope的运行记录及完整operation列表。
只有本次新建回执、输入三标记均空、无任何用户operation、且未进入install，才调用既有销毁路径。
容器/双卷删除确认后，失败会话及输入诊断仍保留；用户正常删除失败会话时可回收输入。删除失败只记secondary，
不会把原GitInputError改成清理异常。已有/复用卷/unknown/已安装/已有任务均不走该自动回滚。
取消仍执行stop，并等待必要IO结算，不能把线程取消当成Docker停止。

替换清理：移除独立index/read-tree/update-index/write-tree/commit-tree快照路径及零消费者oid_from_index；
远端独立start被首次attach替代。短暂试验的on_ready/Event链已全部移除，API/InputService/快照原签名保持。
GitSource、常规recover/重连、输入安装后的unknown恢复、真实对象和路径/内容校验均保留实际调用。

## 9. 本片验证结果及交付边界（2026-10-30）

- 标准CI分片后端3302+2809=6111通过，0失败/0跳过；前端69文件567通过，TypeScript/Vite构建通过。
  命令：`.venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/spec141e-20261030/backend-0-rollback.xml`
  及index=1（本机子进程PATH使用项目已准备的原生工具）；证据为对应log/XML。
  第二分片包含一个专门构造重复METADATA的负例UserWarning，四份真实wheel均通过Twine检查。
- 四平台标准wheel/Twine通过，373份Python和4份Web源码逐字节一致，application SHA256为
  `45e8731689f63fd7f4cfa59085accd97298e89630cc90874bd4c4e0878e2be15`。
  全新虚拟环境verify_wheel_install通过；候选verify-install验证377文件。
- 最终安装wheel在源码目录外通过SSH管理/attach路径连接原有ARM64镜像，首次准备三轮
  1.677859/1.709398/1.655918秒，每轮20Git、671文件、4530744字节bundle，观察有效、清理3/3。
  SSH由专用Colima VM字节流替身提供，未模拟公司两跳认证/VPN；不得称为原生Windows证据。
- 同一最终安装包的默认8MiB验收passed：完整Read p95=3.170ms，20Skills p95=7.913ms，
  输出期间读取p95=21.470ms，输出完整8388608字节，停止56ms，另一会话经停启/重连仍可读。
  假模型暖运行35ms，0供应商请求；不代表真实模型/业务延迟。
- 真实容器故障注入：确认远端新建后再令本机源检查失败；精确容器/两个卷均删除、原异常保留、
  失败会话可删除/输入已回收，另一已准备会话仍可读。旧卷、既有环境、输入标记、completed/unknown
  任务记录和重复取消另有确定性边界测试；未弱化原来源/内容/失败断言。
- 测试helper调整理由：FakeEngine仅在自身mkdir成功后记录fresh_resources；handshake接受并检查
  生产已使用的可选start参数，原恢复/会话/权限断言保留。敏感输入失败测试按用户批准的新回滚合同，
  从“绝未创建”改为“所有已创建对象均已销毁且无安装/任务”，仍检查会话删除和下一次准备成功。
- 专用VM自建容器/卷清零后停止，默认context仍default。既有镜像ID仍为
  `sha256:20235fe60e36b675f96881b3e2f2003f685fb6ffd24766660372cca904f3e961`，未重建、部署或替换公司镜像。

完整证据索引：`build/spec141e-20261030/verification.json`，安装分段/功能结果分别为
`installed-profile-validated.json`、`installed-acceptance-validated.json`，回滚为`real-rollback.json`。

性能预算复算：把公司原机各Git命令平均成本注入真实本机依赖图，快照为6.672114秒/20Git；
这是人为延迟模型，不是Windows实测，未模拟原机文件IO/保存和并发进程启动可能串行。
按原有约2.97秒非Git工作有0～2.97秒仍落在关键路径、远端约少一次2.16秒认证、安装仍2.864秒估计，
完整准备约9.5～12.5秒，仍可能超10秒。该区间是条件预算，不能冒充通过；公司原机三轮报告为下一判据。

按用户此前候选附件授权，仅向既有v0.5.6追加Windows性能候选ZIP及SHA并补说明；不commit/push、
不发新标签/PyPI、不替换旧附件。远端继续原envd0.5.6/AITest0.6.0候选镜像，安装包内仍0.5.6，
必须verify-install核对本片源码。公司首次准备10秒、网页和真实业务仍未验收。
