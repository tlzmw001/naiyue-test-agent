# Spec128：项目身份、执行目录与运行上下文拆分

日期：2026-10-22。阶段：01 的归档迁出已通过本机验收；Project/ExecutionRoot 数据迁移及 02 尚未实施。用户已同意第1、2项先定合同再分别实施；第3项目录搬迁必须等待当前 memory/history 改动提交，不能与本片混做。本文记录已确认边界，不能作为六项重构已完成的证明。

## 1. 已确认的范围与更正

1. 第1项拆开项目身份和会话执行目录；本次只完成数据与消费者拆分，存量会话按 Local 迁移。实际创建、附着 worktree 留给既有 worktree 方案，不新增创建/导入/切换/回收入口。
2. 第2项按生命周期整理 context：整个 run 的身份、能力与观察引用类型化只读；模型调用与批次临时状态显式管理；单工具许可继续复制 context 后局部注入。
3. 删除“新增根配置”的建议。`AppSettings` 已经组合各领域配置。
4. 只有 `ControlObserver` 和 `ContextOperationObserver` 具有相似 outbox/flush；`TurnObserver` 不同。本片不统一观察状态机。
5. CLI 的拥有者事件循环桥、Web 的归属校验、幂等、错误映射及应用关闭等待保持。以后采用子 API 也不删除这些职责。
6. 第3项领域分包等 memory/history 提交后再做；第5项存储职责拆分先于第4项门面组合；第6项 mixin 另立独立切片。
7. 不修改实际配置、依赖、版本或用户数据，不提交、push、发布；测试数据库和目录仅使用临时夹具。

## 2. 两份实施合同

- [128-01：项目与执行目录](128-01-project-execution-root.md)：数据归属、权限、恢复、子实例、目录失效与项目归档。
- [128-02：运行上下文](128-02-typed-run-context.md)：类型化依赖、调用临时状态、逐工具许可、根子与审批恢复。

两片分别 TDD、迁移消费者、验证，不能在一次无法区分原因的变更里同时搬包、重写存储门面、改锁和改变运行行为。类型放在允许上下层共享的现有边界；运行层不导入 Application、数据库或 UI。

## 3. 当前事实与数据流

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

## 5. 验收证据的区分

01 的归档迁出已有新红灯和专项转绿，结果按其验收记录单列；身份迁移与02仍只有合同。前一轮120项定向测试与 typed-context/Git 临时探针只证明当时现状和框架可行性，不能充当128实施验收。

实施后分别交付：当前正常/失败调用链、测试红灯与转绿、完整回归和标准构建结果、旧消费者清理清单、公开字段/持久格式映射、未实现的 worktree 功能边界。助手验证不等于用户已独立实现或调试。
