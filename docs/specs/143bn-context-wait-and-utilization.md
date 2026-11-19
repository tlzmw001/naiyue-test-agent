# Spec143BN：压缩等待可见性与运行中上下文占用

用户要求先排查慢点，无明确证据则展示实际阶段；运行时占用不消失。本轮不改压缩算法、模型参数或超时，不声称已定位真实模型耗时。现有日志context_summary_model_started/finished已包含摘要耗时，现场记录待补。

原因：context_view在busy时current.estimate为空；useContextView以run状态/快照为请求key，变更即隐藏旧值，且未使用流中的context_snapshot。压缩已有resources/model_request准备事件及compaction_result，前端统一文案遗漏了这些事实。

最小路径：保持同api、session、model、thinking、tools身份的已有上下文展示；运行中优先采用匹配session/run/model的流输入快照，查询接口的历史快照只能作为最近记录，不能假称为当前模型调用。无本轮快照时仅保留同身份的最近估算，明确标注尚待更新；跨身份立即清除。真实空值不补零。只读hook，不改后端预估和准入。

等待展示复用已有准备阶段与压缩结果：准备资源→分析历史并生成摘要→摘要已应用、等待运行收尾；无阶段时显示压缩处理中。增加从本次等待开始的秒数，结束/切换会话清理计时，不展示虚构进度、首token或细分后端阶段。不把compaction_result当作最终会话持久化完成。

涉及5个前端文件：useContext.ts、components/ContextPanel.tsx、context-inspector.test.tsx、App.tsx、composer-commands.test.tsx。沿现有布局，仅状态文本和小型计时，无新依赖、后端、配置、网络或锁变更。异常查询仍保留错误与重试；缓存不跨身份。先红灯验证流快照、同身份等待缓存/跨模型清除、busy查询不覆盖流快照、分阶段文案，再跑前端全量/构建和浏览器。真实glm耗时与Windows现场另验。


## 验证与限制

- 利用率新回归先红：build/context-visible-red.log；阶段新回归3例先红：build/compact-phase-red.log。后续修正测试中旧/新run ID重复的fixture，保证验证的是上一轮缓存与本轮流快照的区别；未放宽断言。
- 专项27项、完整前端78文件693项通过，TS/Vite构建通过；日志build/context-wait-{targeted,full,build}.log。构建时修正历史快照nullable检查，之后专项及构建复验通过。
- 生产preview Chromium 1440/375px的资源准备、摘要生成、结果同步共6场景通过；真实渲染期间API从可预估切换到busy空预估，利用率仍保留、草稿保留、无普通run误发、页面异常0。模拟时钟前移2100ms验证已等待2秒，非紧sleep。证据build/context-wait-browser/verification.json，截图已检查。浏览器mock SSE响应会结束，既有重连提示不作为真实网络性能证据。
- 未改摘要请求/分批算法、超时、用量校准、后台和真实配置；无真实模型请求或现场日志，不声称压缩已提速。现有context_summary_model_finished.duration_ms等日志可进一步定位摘要与其他等待。
- 查询key变化时的历史百分比标记为最近记录，只有匹配session/run/model的流快照标记本轮last_sent。后台历史快照可能是同run内较早调用，不能凭run相同当作当前输入。
- 同模式检查：ContextTrigger和详情共同使用useContextView，没有为云端面板或其他统计偷换累计用量；跨api/session/model/thinking/tools不保留缓存。原错误/重试与后端预算判断不变。未提交/push/打包。

## 审查回归修复（2026-11-17）

审查确定性复现三个缺口：流快照未进入缓存，失败后退回旧估算；历史回填只检查模型，跨thinking/tools重新填入旧估算；后台提示依赖整个run对象，普通文本delta也清除通知。

修复：有效流快照按首次观察时的配置身份保存到已有缓存，运行失败后仍保留有标注的最近记录；历史latest_request仅保留在最近请求，不能补入当前预估。配置变化后保留的同一流快照不再算本配置输入。后台通知只随运行ID、状态、阶段、停止/完成/压缩结果和提交/提示/连接状态变化而清除，正文delta不清除。

正式回归先红3例（build/context-fixes-red.log），修复专项29通过；再补运行中thinking/tools变更保护。最终完整前端78文件696项及TS/Vite构建通过（build/context-fixes-{full,build}.log），生产Chromium1440/375px六场景通过（build/context-fixes-browser/verification.json）。浏览器仍使用API/SSE模拟，不代表真实网络或模型性能。无后端或配置修改，未提交/push。
