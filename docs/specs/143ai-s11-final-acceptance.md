# Spec143AI：S11 组合验证、性能采样与最终Windows验收交付

2026-11-03 用户授权连续完成剩余Spec代码并自行执行最终Windows验收。前提为143AH已接线；本片不发布/部署/修改真实配置，不以本机夹具代替公司通过。

1. 盘点143G行为/性能矩阵和143E退出清单，逐项链接实际生产入口、已有测试/探针、当前证据等级和原机步骤；旧阶段文字按日期保留，当前状态明确。不得以测试绿灯关闭真实provider/Windows/Linux/性能门禁。
2. 先搜并复用现有cloud_probe及本轮更新/备份工具，补最小可执行组合验收与性能记录入口。只对用户指定的验收任务/目录操作；创建资源使用独立ID，删除/强停/恢复/GC需明确确认，不能无条件清理既有任务。样本来源、平台、版本、input/task/run/command/release身份、阶段墙钟、字节量、20轮p50/p95/max原样记录；无样本显示未测，不制造达标数据。
3. 本机确定性组合包括：多任务来源隔离/结果归属、连续输入与保存/恢复、更新失败/重启后原ID、新旧任务固定版本、完整备份新根恢复和GC引用保护。保留真实HTTP/已安装wheel进程测试。真实Docker不可用则保留明确原机步骤及失败原因，不自动启动/更改用户Docker服务。
4. 制作最终同源Windows x64＋Linux x64候选验收包，含标准wheel、已固定Kit/依赖锁、校验清单、固定探针与分步骤手册。包身份绑定基础HEAD＋实际源码指纹和所有产物SHA；尚未提交的候选不得伪称来自某个干净发布提交，不使用仅版本号区分。提供本地路径，不上传或替换现有Release。
5. Windows手册按首次准备→停旧本机服务/安装候选→开发机服务/Updater升级与模板预检→Web业务/照片1–10→输入/报告/代码取回与主Agent分析→Windows断开/重开/未知回执→双任务/真实HTTP与proto RPC/provider→更新/备份恢复/GC→性能→回传报告组织。列每一步执行机器、命令/UI动作、预期结果、停止条件及失败保留的最小脱敏材料；已有S00不无理由整套重跑。
6. 最终全量后端、前端测试/构建、pip check、四目标wheel/Twine、逐字节源包一致、Mac源外全新依赖安装、无A生产消费者及Spec142/配置保护验证；复核文件存在/真实实现/正式接线。更新handoff/project-plan/future-work/143F/G/E状态，区分代码收尾与用户验收待执行；不将S11全部实机门禁标通过。

## 组合探针合同

`python -m scripts.verify_cloud_workflow --settings PRIVATE_JSON --output NEW_DIR --confirm-runs N`，N必须等于rounds×task_count；`--resume`只续接同目录。该工具会创建独立验收项目/任务并发送真实模型请求，保留任务供观察，不自动删除/停止既有资源。私有settings包含connection_file、source、selection_file、template_id、model_id、message（必须含`{task_marker}`）、rounds（默认20，1–100）、task_count（默认2，1–2）、poll_seconds和timeout_seconds。结果报告的expected_report默认acceptance.txt；消息要求模型调用task_output_directory，并将唯一标记写入该报告。配置/消息不进入公开报告。

入口校验确认次数和物理目录不重叠→私有journal持久化probe ID/不可变settings摘要→真实capture两次/CloudClient.upload验证同I第二次0字节→CreateProject→两个独立客户端线程各自CreateTask/EnvironmentCommand start→逐轮StartRun→按原command-id轮询终局→按固定result-id等available→验证归属/租约下载所有文件并核验SHA和报告标记。每个POST前保存原ID和完整请求，恢复先GET原回执；未知/失败/CAS冲突直接中止，不换ID、不自动重发一轮。断开后恢复样本标为resumed、不计入连续墙钟性能分位。已下载文件仅校验不覆盖。跨任务result读取必须404，UI互见另验。

`scripts/cloud_workflow/{settings,journal,commands,scenario}.py`分离用户配置、私有持久状态、原ID命令对账和组合流程；沿用现有capture/upload/download与安全文件函数，不新增生产API或依赖。报告含probe/instance/input/project/task/run/command/result/release/generation身份、平台/Python/包版本、阶段墙钟、字节、p50/p95/max及样本数。ACK与模型运行总时长不冒充热环境开销≤1s；模型阶段、真实SSH吞吐、UI性能及机器2CPU/2GB条件须另记录，未测门禁明确pending。冷/暖冻结采样复用profile_result_capture，不能冒充完整工作流。

失败正常传播为非零退出；只记录错误类型/受控code和原ID，不记录token/URL/消息/响应体。首次握手的instance_id在任何上传/写请求前固定到root journal，续接即使同路径连接文件换了实例也必须拒绝。输出目录和设置均为私有，单进程文件锁拒绝并发复用；输出不能与source重叠（包括物理祖先别名），settings/selection/connection位于source外。候选包由独立打包入口读取现有标准wheel/Kit/lock/手册/固定scripts，生成文件SHA和基础HEAD＋实际源码指纹，ZIP不含私有配置、凭证、用户输入/验收结果或研究目录。

先写真实行为失败测试再最小实现；不增加运行时依赖、不改测试基础设施来掩盖失败。

## 本机收尾与原机边界

最终6283后端（0失败/错误/跳过，四分片唯一节点覆盖正式收集）、72文件594前端、标准构建、四目标wheel/Twine、513 Python/4 Web逐字节对包、Mac源外全新依赖安装、真实6产物manifest往返和禁止A消费者门禁通过。workflow双任务/两轮控制服务回归使用真实capture/freeze/文件字节；原IDACK丢失/跨实例拒绝、候选路径/篡改拒绝及已安装worker离线备份也已实际执行。验证和中间失败边界见[报告](../../build/spec143-s102-s11-20261103/report.md)，不是模型自述证据。

600/4000个8192字节文件，各冷1轮＋暖20轮，Mac组件warm p95分别0.1931s/0.7341s，原始样本及机器信息在capture-profile.json；这是多任务验证期间的组件观测，不作为2CPU/2GB或整链路门禁。新工具真实40轮模型请求未在开发机发出，Windows/Linux及公司provider/HTTP/proto RPC/完整性能由用户后续执行。143G已添加54个可核对测试入口的分组索引；E退出清单继续独立核验。旧验收环境中的额外terminal和结果互见不能靠本机测试追认修复。

交付入口为[最终手册](../cloud-final-windows-acceptance.md)、[空白回传模板](../cloud-acceptance-report-template.md)及本地最终候选ZIP。代码/本机验证/验收准备已完成，S11实机成绩保持pending；不自动发布/部署或删除真实资源，无新增用户掌握声明。
