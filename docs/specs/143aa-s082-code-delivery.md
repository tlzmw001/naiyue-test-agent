# Spec143AA：S08.2 受控代码交付与 A 消费者冻结

状态：2026-11-02代码及本机验证已完成；依赖143w，验收前保留A正式入口。用户已授权连续开发，完成本片本机验证后停在S09.1 Windows＋Linux实机门禁之前。

## 1. 承诺与当前主链

143b §7的三个动作分别执行，不由下载或分析隐式触发。当前接收台账已有固定I/R、manifest和patch，但尚不能安全写项目。新链为用户选择已接收结果 → 准备交付 → 验证I归档和R闭合关系 → 显示变化/冲突 → 用户确认执行。Agent不获得交付工具或写入许可。失败链：坏patch/错hash/目录类型冲突在首次项目写入前失败；应用中途IO失败保留日志和明确partial，显式继续仍核对旧/新内容，不自动回滚用户新改动。

导出目标首版限定为当前已授权项目根内的一个新目录；用户填写目录名，不接受远端提供的绝对路径。`destination_handle`使用当前项目的不透明UUID，服务端再次解析当前授权execution root并核对身份；它不是任意文件系统路径。以后支持系统目录选择时另行签发目标授权，不扩大本片访问范围。

## 2. 固定输入与输出验证

优先使用该binding的原source_handle指向的本机输入归档。缺失时通过owner-only `GET /api/cloud/v1/inputs/{input_id}/archive`下载固定大小/SHA/ETag归档并校验；task token不可访问。归档按现有validate_package安装到私有树。无tar.gz随机读取承诺。

使用受控私有Git index/objects读取I并`apply --cached --binary`，绝不把不可信patch交给用户目录。完整树条目、mode、type、链接目标、大小、SHA256逐一对应O；闭合路径集合与I→O差集一致，额外patch路径也拒绝。对象批量传输/流式输出有界，Git配置、hooks、filters和换行转换禁用。输出原始字节作为导出/apply权威。

## 3. 动作与恢复

- export：完整O安装到私有staging，验证后发布到不存在的目标；禁止覆盖既有目录、链接、reparse，单个目录发布不允许竞争覆盖。
- branch：必须使用原binding对应的本机捕获回执与原HEAD对象。私有index以原HEAD为基底，受管集合I路径＋捕获时删除项＋O路径，保留未纳入的文件和gitlink。不checkout，不改原index/HEAD/工作区。先导入必要Git对象，再CAS创建新ref；重试只认同一预期提交。捕获时冻结的text/eol/autocrlf策略作用于Git blob；拒绝filter/LFS/ident/working-tree-encoding。O新增路径也按冻结规则查询，不读取当前工作区attrs。
- apply：仅I→O变化路径，预检原始当前内容仍等于I、新文件不存在；无关dirty文件允许。日志、备份与期望旧/新身份先持久化，逐文件复核并原子写入；临时新文件准备完成后再次核对旧值，新增文件独占发布，删除前重验。可观测到的并发改动返回冲突。普通文件系统不提供跨进程内容CAS，最终复核到替换之间无法阻止不合作的外部writer；界面要求暂停本机Agent/编辑器对同一路径并写，不宣称零竞争或整目录事务。日志保存逐路径阶段；partial/unknown通过同ID显式继续，对已写新内容只核对不重放。

导出日志固定同一个暂存目录及其文件系统身份；复制ACK丢失后直接核对/发布，部分普通文件只有与固定O对应内容的严格前缀一致才允许追加缺失字节，缺失文件独占创建。不同前缀、额外路径、错误类型/链接或身份变化拒绝，保持现场，不反复另开随机目录遗忘旧副本。准备与执行同受producer容量限制；preparing/executing台账在同一项目数据库的删除门禁中保护，worker收束后才允许删除本机会话。

## 4. 本机接口与UI

所有入口沿现有client auth/instance guard及project/session归属。DTO在`contracts/cloud_delivery.py`：

- POST `/api/client/v1/cloud-deliveries?local_project_id`：DeliveryRequest，持久登记preparing并由owned producer验证至prepared；相同ID参数冲突409。这里只准备，不写项目。
- GET `/cloud-deliveries/{command_id}?local_project_id&local_session_id`：读取持久状态，无自动写入/重试。
- POST 同路径`/execute`：DeliveryConfirm `{confirm:true}`，项目/会话查询参数；明确执行或继续同ID。

DeliveryRecord仅含固定身份、动作、名称、状态、变化、冲突、错误码及branch_head；私有根/备份/绝对路径留在本机存储。项目库schema23包含接收(schema22)与交付台账，迁移、backup/restore及session purge同步。执行使用已有manager/domain生命周期，关闭必须等待生产者收束。UI在已接收结果下提供三个独立动作、名称、预检结果和确认按钮；正在执行/不确定时保留ID，切换会话不串操作，不在进入结果面板时自动写入。

## 5. 消费者冻结与参考

读取旧A git_delivery的ref CAS、路径/日志恢复算法，不能复用要求bundle/clean worktree的主链。DeepSeek Harness源码ce7073d的私有index/objects与原仓库alternate可借鉴；不采用其`git add --ignore-errors`。本片不新增Git提交作为传输格式。S08末生成带源码SHA、文件hash、消费者位置和保留原因的机器清单，覆盖静态/相对/字面量动态导入、lazy export、CLI、API、打包、脚本与测试。A仍有正式消费者，冻结清单允许有依据的残留；新增未知消费者/hash变化需显式重核。S09才核销和删除。

## 6. 验证

TDD覆盖二进制、CRLF、mode、链接、增加/修改/删除；坏patch/额外路径/hash；缺失I下载与task鉴权拒绝；分支初始未提交改动/初始删除/非受管文件和gitlink、冻结attrs、CAS冲突；apply无关dirty、目标冲突、N次写故障、并发改动、partial显式恢复、链接父目录/Windows锁失败。真实Windows文件系统、Linux容器和业务网络属于S09门禁，本机模拟不能替代。

实现后补上命令、实际输出及产物引用；代码存在、实质、接线和正常/失败调用链都须可检查。


## 7. 本机验收与交接（2026-11-02）

最终全量两分片3318＋3565＝6883后端，原6785项回归行为保留（版本派生参数归一化核对）、新增98，无失败/跳过；前端75文件613项，默认/候选构建、四目标wheel/Twine/Mac源外安装通过，532生产Python源逐包一致。[最终证据](../../build/spec143-s082-20261102/verification.json)、[独立复核及修正](../../build/spec143-s082-20261102/review.md)、[浏览器报告](../../build/spec143-s082-20261102/browser-verification.json)。

正常/故障覆盖完整I/O及binary delta/literal预算、CRLF/执行位/安全链接、受管分支与原HEAD/索引/工作区不变、冻结attrs及其旧HEAD fallback隔离、ref丢ACK/父链接、预检与确认分离、跨会话拒绝、原始代码实际HTTP取回/应用、并写冲突、partial及原stage前缀续补、特殊类型拒绝、全局producer预算和活动交付删除门禁。真实mkfifo只读闭合探针拒绝特殊条目，未阻塞打开。真实浏览器确认导出后才写临时项目文件；显式发送后显示材料引用，XSS与桌面/375px检查通过。临时服务/目录/URL清理完成。

完整回归曾发现默认工具集合/工厂新关键字及保留envd构建摘要边界；修正产品装配，原工具断言未改；执行清单同步本地版本0.5.7及源码摘要，65源码/45依赖集合不变。本轮未发布执行包或部署镜像。消费者冻结900条，核对unchanged。真实Windows、Linux容器、公司网络与模型上游未验；唯一下一步S09.1，门禁通过后才正式切换与清理A。

## 提交前补齐：任务删除后的本机来源身份（2026-11-02）

原source_for从binding.create_command_id指向的创建请求取source_handle，但任务删除按既定合同会将该请求正文脱敏；完整本机材料和I仍在时，后续交付因此失败。来源身份应属于本机接收记录的必要交付元数据，不能依赖可被任务删除清理的请求正文。

ReceiveRecord新增必需source_handle（UUID，无宿主路径）。ReceiveRepository.reserve在原项目库事务中从该binding的原create operation取得来源，核对binding/session/project/input及source所属会话后固定到接收JSON；不新增SQL列或schema迁移，不做历史B兼容回退。交付source_for改用接收记录中的handle，并交叉核对binding/instance/task/input与本机会话来源。云端删除仍脱敏创建正文；不按相同input_id猜测原HEAD/路径，也不恢复被删的任务。

验证以真实本机SQLite、完整材料和有效删除回执为入口：删除后不访问远端，export/apply/branch仍可完成；缺失/错来源、跨会话/项目、输入或绑定身份不符均明确拒绝。此修正不改变材料/交付授权、I→O核验、确认写入或原HEAD/index保护。

提交前修正验证：12项新增先红后绿，删除后断网export/apply/branch及错误来源拒绝/事务回滚通过；相关127项通过。最终完整后端3341＋3599＝6940、前端625/构建、四目标wheel/Twine与Mac源外安装通过，537 Python源逐包一致；907条消费者守卫通过。证据见`build/commit-cloud-s08-20261102/verification.json`，真实S09.1仍待用户Windows/Linux执行。
