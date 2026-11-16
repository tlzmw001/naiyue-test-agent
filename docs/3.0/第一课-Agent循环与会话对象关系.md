# 第一课 · Agent 循环与会话对象关系

2026-11-07，对应场景 01。本页整理用户的章末总结与课堂纠正。

## 用户总结

以下为用户复述的简写，原有理解中的偏差在下一节逐项纠正。

- SessionService 和 SessionPersistence 提供多会话管理与持久化能力，在 Host 下创建。用户新建会话时确定项目，再创建对应的 SessionAPI、RunAPI。
- SessionAPI 调用 SessionService 创建会话，最终得到 AgentSession。管理和运行入口能访问同一会话，将职责分开。
- AgentSession 初始化 lifecycle，让它取得控制、执行等依赖。共享 runtime 连接这些操作。
- runtime.run 最初为空，开始运行时放入本轮对象，运行后进行收尾。

## 对应纠正

| 原理解需要补准的位置 | 纠正后的关系 |
| --- | --- |
| 服务不限定会话的组织形式 | 类可以复用；具体实例管理所属 Host 的会话。项目和普通聊天 Host 各有自己的服务与持久化协作者。 |
| 新建会话时再创建 API | Host 装配时，服务、持久化协作者和 API 已经创建并连接。之后多个会话共用这些入口。 |
| 两个 API 都能访问会话 | 它们通过同一个 SessionService，用 session_id 查到同一个 AgentSession。 |
| AgentSession 最后初始化 lifecycle | 它先创建 runtime，再创建 controls、approvals、subagents、lifecycle；四者接收同一个 runtime，lifecycle 还接收其余协作者。 |
| run 为空就是可以运行 | 为空表示无根运行占用。审批、保存未确认等条件仍可能阻止普通新运行。 |

```text
打开或复用 Host
└─ 装配 SessionService S、SessionPersistence P、SessionAPI、RunAPI
   ├─ SessionAPI 与 RunAPI 共用 S、P
   └─ S._sessions[session_id] → AgentSession
      ├─ _runtime = SessionRuntime(session_id, state)，记为 R
      ├─ controls、approvals、subagents 接收 R
      └─ lifecycle 接收 R、上述协作者及 P
         └─ 新一轮通过准入：R.run = ActiveRun(本轮编号、观察与资源等)
```

## 循环与收尾补充

章末复述主要覆盖应用对象。图内机制补充为：用户消息进入图，模型提出带调用编号 c1 的工具请求，执行节点调用工具，结果通过 ToolMessage.tool_call_id=c1 配回请求，模型再获得结果并回答。

正常收尾依次处理子运行与控制窗口、必要保存、运行占用释放和终态。保存未确认时，即使已释放占用，会话仍受保存门禁限制。[运行与收尾实现](../../naiyue_test_agent/application/sessions/session_execution.py)

理解证据为用户能复述主要对象链和共享状态关系，以上纠正已经反馈。本人图循环/收尾调试、独立实现与面试追问表现待实际检验。
