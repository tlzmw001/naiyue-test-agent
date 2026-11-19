# Spec143BQ-S2：结果窗口和旧工作流退役

状态：产品入口已退役，历史回执和本机证据保留。依赖 [S1](143bq-01-result-tool-and-save.md) 的工具保存闭环通过，不先移除唯一可用的取回入口。

## 1. 用户决定与产品表现

结果窗口保留：任务结果列表、普通产物选择、大小/状态、文本预览及“保存到本地”。手动保存继续支持多选、现有目录选择、进度、冲突预检和显式覆盖。

退出以下可执行入口：

- “交给主 Agent 分析”及其材料重新核对、专用分析草稿合并。
- 独立浏览器下载按钮；用户文件取回统一走现有本机保存。
- 旧任务的“应用代码／创建分支”和专用代码交付导出。
- `cloud_result_read` 的模型注册与分析材料注入指令。

主 Agent 继续处理来自对话中用户指令和 S1 的工具结果，不从结果窗口自动发送消息。手动保存成功不创建模型 run，不修改用户输入草稿。

## 2. 源码包与 Git 的边界

当前普通产物目录接受合法普通文件，包括源文件和压缩包。云端 Agent 可按用户指令把变更文件打包到当前 run 的 `.nyt-output/<run_id>/`，再作为 artifact 保存；Git 推送沿用现有 Shell/网络/凭证与用户授权，不由本片新增后台动作。

不承诺自动把 workspace 全量上传，也不承诺结果窗口中的 manifest/patch 是完整源码。旧任务如果只有基线和补丁，没有普通源码产物，本片不再提供应用、建分支或自动还原；仍可查看已有结果摘要/历史。未执行的旧请求返回能力已退役，不回退到旧路径。

只更新当前使用说明和相关提示；Runner 仍按原捕获/轮换策略运行，不借退役改写旧 result schema 或对历史数据做批量删除。

## 3. 真实消费者与处理

| 当前链路/数据 | 决定与理由 |
|---|---|
| App → CloudTasks → Detail → Results → Receive 的 onAnalysisDraft | 删除传递、按钮与回调；由 S1 替代 |
| `useConversationDrafts.mergeAnalysisDraft`、`useConversation.addAnalysisDraft` | 删除专用合并；普通文字草稿保持 |
| 前端发起 start 的 material_refs、`run_api.prepare_input` 的自动分析注入 | 退出新请求语义；明确拒绝非空旧字段，不能静默忽略 |
| `LocalReceives.analysis`、`LocalMaterials.analysis`、analysis-draft 路由 | 移除实现；旧写 URL 返回明确退役错误 |
| `cloud_result_read` 与 RunCapabilities.cloud_materials 模型读取端口 | 新工具接线通过后移除注册、实现和仅服务它的绑定 |
| `MaterialRef`、receive/material 存储、验证文件的 `_get`/files 能力 | 保留仍被 files delivery 和历史读取消费的部分，不因名字带 material 整包删除 |
| `CloudCodeDelivery` 及 hooks/types/API 的 apply/branch/export 分支 | 删除新操作入口和真实执行链；历史 receipt 解码保持 |
| code baseline、patch 应用/branch helpers、`delivery_inputs.recover_archive` | 沿调用方确认后移除只服务旧交付的消费者；共享给普通文件写入的 helper 保留 |
| `CloudResults.downloadLink` 与仅供它的浏览器 ticket 链 | 按真实引用移除 UI 和无其他消费者的 ticket 实现 |
| 受认证 result 元数据、预览、artifact bytes 与 receive | 保留，新工具和手动保存都依赖 |
| 历史消息中的 material_refs、来源名称、已完成交付记录 | 保留展示/解析，不重写原消息或删除已保存文件 |

旧内部 Python 函数能 import 不构成长期 API 合同；同时不能把本次 UI 简化扩大成删除所有 owner HTTP 读取端点。实施须检查 README/当前 Spec、CLI、服务路由、前端、测试、脚本的真实使用，输出保留/删除清单。

## 4. 兼容与中断操作

### 4.1 对外请求

配套前端和本机服务版本启用新入口。旧 analysis-draft、旧代码交付写请求返回 `cloud_result_workflow_retired`（HTTP 410）和“保存普通产物或由云端打包/Git”的说明，不返回成功空对象。

旧模型待执行 `cloud_result_read` 调用不重放；沿用未知/退役工具反馈提示改用新工具。已有历史 ToolMessage 仍作为历史显示，不伪造新的文件位置或成功结果。已经正常结束的旧会话在恢复时从本次工具选择移除单个 `cloud_result_read` 条目。其余工具继续按当前规则校验，`cloud_result` 需要用户单独选择。打开历史本身不改写旧工具列表、消息或检查点。

旧 schema 枚举和值可保留在只读历史解码器中，不能为了读历史让可执行旧分支继续可达。新 delivery 写入口只接受 files；迁移接口的退役响应与当前消费方测试同片提交。

### 4.2 升级时正在写入的旧交付

升级前停止旧代码交付准入，按原 producer 关闭/排空已开始 IO。已有 completed/failed 回执只读展示；prepared 尚未写目标的旧操作标为 retired。

对 executing/partial/unknown：不自动 resume，不自动回滚用户文件，不删除私有写入日志/必要备份。本机迁移把它们标为 `legacy_recovery_required`，界面显示原目标、已确认写入范围和诊断位置；处理这些异常残留是按原日志核对的受限恢复，不保留通用“继续应用代码”按钮。正常升级验收必须构造这些状态验证不丢证据。

若核对发现旧未终结操作仍必须读取 Coordinator 的基础输入，S3 开启前须将这个明确依赖登记为 recover 占用，或先按旧版本完成/明确放弃该操作；无法核对时保持回收禁用并报告升级检查失败。移除旧按钮不构成解除恢复依赖的证据，也不能用本机日志仍在替代远端输入保护。

已经退役的旧操作不能阻塞所有后续新文件保存；必要恢复占用按它实际保护的路径/缓存保留，冲突目标拒绝新写。Spec143BP 的本机输入缓存清理继续保留必要恢复依赖，不能以新 UI 隐藏为已无依赖。

### 4.3 用户草稿与历史

草稿内现存文字保持；旧分析材料草稿若还能读取，显示“材料分析入口已退出，请按来源任务保存产物后继续”。不能将用户输入清空、自动发送，或把失效材料静默当作有效附件。

历史材料标签仍显示真实来源与可用状态；去掉会引导执行已移除工具的实时提示。历史正文原样保留，不追加新的模型内容。

## 5. 界面与 API 接线文件

主要前端：`App.tsx`、`useConversation.ts`、`useConversationDrafts.ts`、`api.ts`、`cloudApi.ts`、`cloudReceiveTypes.ts`、`cloudDeliveryTypes.ts`，以及 `CloudTasks/CloudTaskDetail/CloudResults/CloudResultReceive/CloudFilesDelivery/CloudCodeDelivery` 组件。

主要后端：`service/cloud_receives.py`、`cloud_results.py`、`cloud_downloads.py`，`web/schemas.py`、`web/service.py`、`application/sessions/run_api.py`、`material_inputs.py`，`application/cloud_local/materials.py`、`receives.py`、`deliveries.py`、`delivery_inputs.py`，`cloud_files/delivery_verify.py`、`delivery_branch.py`、`delivery_apply.py`、`delivery_git.py`。

文件列表是影响面，不是整文件删除许可。例如 `delivery_apply` 的普通文件写入/恢复算法仍有直接消费者；`delivery_verify` 的共享类型仍被普通产物交付使用。以符号和入口为单位迁移，单文件不超过 500 行。

同步当前使用文档与 Spec143AZ 的版本边界，历史 Spec/课堂保留原事实并标明被替换部分。Spec143BP 的旧交付保护条款在实现退出且残留状态受保护后才能收窄；本轮只设计，不提前宣称消费者不存在。

## 6. 正常/失败验收

正常：在旧/新两类任务中选择普通产物 → 保存到本地 → 原接收与 files delivery 完成；界面路径准确，主会话草稿不变，模型调用数为零。

失败：已有目标冲突 → 原预检展示冲突与可覆盖状态；用户取消零写入。无普通产物的旧补丁结果显示“没有可保存的普通产物”，不能显示假的源码文件或恢复按钮。

迁移：旧 browser bundle 请求已退役操作得到明确 410；历史引用、旧已完成回执仍能打开；中断旧交付保留证据；新版工具可继续取回旧任务的普通 artifact。

删除旧测试只针对不再支持的业务动作；保存/权限/异常身份/中断保护断言迁到当前入口或退役合同，不能通过减少断言造成“全绿”。详细测试与检查见[验收矩阵](143bq-04-acceptance.md)。

已切换的界面从云端任务列表的“旧交付记录”只读查看备份位置和已记录的写入。旧浏览器票据路由已移除，历史回执仍由原交付仓储解码。

## 7. 审查修订

旧代码交付的 preparing 未进入写入阶段，释放会话删除和项目关闭使用的进行中保护。已开始写入的旧导出按原根身份和已登记导出目录识别，对同一项目的其他目录允许正常保存。原目录身份无法核对时，拒绝有风险的目标写入并保留证据。
