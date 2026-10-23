# Spec128：项目身份、执行目录与运行上下文拆分

交付编排说明：本节点从已批准合同整理。早期没有完整文档冻结，不声称精确还原原始文档历史。

C1交付项目身份与执行目录拆分，身份字段和消费者分别归属项目与执行目录；运行上下文、目录迁移、存储和会话组合由后续节点交付，实际worktree管理另起Spec。

身份合同及正常/失败主链见[128-01](128-01-project-execution-root.md)。各节点源码按逐文件SHA256核对冻结清单；完整回归记录属于对应实施时点，不据此声称未来节点已完成。

## 1. 已确认的范围与更正

1. 第1项拆开项目身份和会话执行目录；本次只完成数据与消费者拆分，存量会话按 Local 迁移。实际创建、附着 worktree 留给既有 worktree 方案，不新增创建/导入/切换/回收入口。
2. 第2项按生命周期整理 context：整个 run 的身份、能力与观察引用类型化只读；模型调用与批次临时状态显式管理；单工具许可继续复制 context 后局部注入。
3. 删除“新增根配置”的建议。`AppSettings` 已经组合各领域配置。
4. 只有 `ControlObserver` 和 `ContextOperationObserver` 具有相似 outbox/flush；`TurnObserver` 不同。本片不统一观察状态机。
5. CLI 的拥有者事件循环桥、Web 的归属校验、幂等、错误映射及应用关闭等待保持。以后采用子 API 也不删除这些职责。
6. 第3项领域分包等 memory/history 提交后再做；第5项存储职责拆分先于第4项门面组合；第6项 mixin 另立独立切片。
7. 不修改实际配置、依赖、版本或用户数据；测试数据库和目录仅使用临时夹具。各次实施时的未提交记录是历史验收时点；本交付序列已经用户授权提交并推送开发分支，不含合并、标签或发布。

## 3. 设计基线事实与数据流（01/02实施前）

本节保留2026-10-22重构前的事实；本节点的身份与消费者实现见128-01；运行上下文和目录搬迁在后续节点分别交付。

现有 `WorkspaceLocation` 已有 UUID/root/database_path；数据库在 data_dir 下，绑定仍限制单个 root。session、history 与 memory 来源校验仍把 root 用作项目范围。工具、审批、Shell cwd、项目指令/Skills 同样消费该 root。

```text
prepare_workspace(UUID, root, database)
  -> SessionStorage.bind_workspace(UUID, root)
  -> SessionState.workspace_root
  -> SessionExecution / RunObservation.bind(RunServices)
  -> make_invocation_context(扁平字典)
  -> 模型准备 / 许可 / 批次 / 工具
  -> session/history/memory 查询或必要保存
```

当前上下文除依赖外还保存模型轮次、批次计划和逐工具许可，不能把所有字段一起变成共享 RunScope。

最新读取时并行memory概览已使用项目schema17；16属于memory写入与来源时间。个人记忆为format2。128-01迁移必须从实施时实际版本顺延，不预占版本号或覆盖并行memory改动。个人记忆的检索、版本、遗忘抑制和格式不因本片词汇变化重做。临时`--no-session`不创建项目身份文件，也不开放项目history/memory；其归档按临时session_id置于data_dir。

## 4. 用户确认的执行目录失效合同

错误码统一为 `execution_root_unavailable`，原因仅使用本片已确认的枚举：

| 原因 | 条件 |
| --- | --- |
| missing | 路径不存在 |
| not_directory | 路径存在但不是目录 |
| not_registered | worktree 不在 Git worktree list 中，或没有应用登记记录 |
| wrong_project | Local 身份文件 UUID 与 project_id 不符，或受管 worktree 登记不属于项目 |

只在新的执行入口检查，不定时扫描、不监听文件系统。不把 Path.is_relative_to、同分支或同 remote 当项目身份。首片不创建 worktree，未登记的 worktree 不能靠传入路径成为受管目录；未来登记与 Git 校验的生产接线按 worktree Spec 验收。

目录失效后：新 run、Shell/后台新命令、文件读写、子实例续跑拒绝；历史档案仍可打开。已经运行的后台进程保持现有生命周期，list/output/stop/aclose 不依赖执行目录有效。任何入口都不得改用主目录或进程当前目录。

用户随后明确：应用归档迁出执行目录，放到项目拥有的数据位置。这样删除执行目录不影响后台输出；磁盘写入等真实归档故障继续沿现有停止/反馈合同，不引入忽略归档错误的特例。
