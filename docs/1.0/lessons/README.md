# 系统课堂笔记

本目录只维护课堂索引，当前学习位置和十章小节规划统一见[学习路线](../learning-roadmap.md)。以下含历史版本，当前机制以笔记版本提示和当前源码为准。

第一章见[Lesson18](lesson-18.md)/[Lesson19](lesson-19.md)。第二章从[Lesson20](lesson-20.md)进入，模型目录见[Lesson24](lesson-24.md)，HTTP资源见[Lesson25](lesson-25.md)，旧版失败链见[Lesson26](lesson-26.md)/[Lesson27](lesson-27.md)/[Lesson28](lesson-28.md)。

新版异常地图见[Lesson29：两条路径与完整地图](lesson-29.md)，数据类见[Lesson30](lesson-30.md)，描述选择见[Lesson31](lesson-31.md)，分类器适配见[Lesson32](lesson-32.md)，模型快照见[Lesson33](lesson-33.md)，模型委托及数据流复盘见[Lesson34](lesson-34.md)，无效响应见[Lesson35](lesson-35.md)；Lesson28保留旧版学习过程。各课理解证据分别保留。

write提交边界及核对门禁见[Lesson36](lesson-36.md)，用户停止数据流见[Lesson37](lesson-37.md)。

Spec081–083异常重建见[Lesson38](lesson-38.md)至[Lesson44](lesson-44.md)，结束原因归一化见[Lesson45](lesson-45.md)。课堂内容/理解证据保留各课，索引不重复即时进度。

第三章消息循环、装配/执行与状态合并见[Lesson46](lesson-46.md)，hook注册与顺序见[Lesson47](lesson-47.md)。

wrap_*的handler与正常/短路/异常分支见[Lesson48](lesson-48.md)。

显式context与动态选模见[Lesson49](lesson-49.md)。

invoke/ainvoke/stream/astream与流输出见[Lesson50](lesson-50.md)。

第三章总图与阅读入口见[Lesson51](lesson-51.md)。

第四章会话消息与观察事件两条路径见[Lesson52](lesson-52.md)。

关联ID、sequence与事件数据合同见[Lesson53](lesson-53.md)。

发布器队列、重入与监听交付见[Lesson54](lesson-54.md)。

完整消息快照、基线、修订与移除见[Lesson55](lesson-55.md)。

delta分片、草稿与完整校准见[Lesson56](lesson-56.md)。

turn、控制与上下文过程事件及第四章收尾见[Lesson57](lesson-57.md)。

第五章session/thread/run与busy准入见[Lesson58](lesson-58.md)。

IOCall、next/send/throw与同步/异步驱动见[Lesson59](lesson-59.md)。

run阶段、结果处理、必要收尾与主/次异常见[Lesson60](lesson-60.md)。

steering/follow_up队列、模型边界与自然结束继续见[Lesson61](lesson-61.md)。

用户停止、run私有信号与外部取消见[Lesson62](lesson-62.md)。

await_settled、线程IO与已开始工具的收尾见[Lesson63](lesson-63.md)。

异步任务归属、事件消费断开与应用关闭见[Lesson64](lesson-64.md)。

前五章六组完整流程、测试与逐阶段走读手册见[Lesson65](lesson-65.md)。

第六章工具注册、参数schema与结果回填见[Lesson66](lesson-66.md)。

笔记从 `lesson-18.md` 起连续编号，沿上一级[知识地图](../learning-roadmap.md)的10章推进；一章可以拆成多次小课。旧 `docs/lessons/lesson-1.md` 至 `lesson-17.md` 保留为历史材料，新课以当前代码为准。

完整小课收束时记录：

1. 本课的小目标、真实文件/函数与阅读位置。
2. 具体数据结构、调用链和正常/异常数据流。
3. 已讲解的核心行与复杂语法、框架源码依据。
4. 用户提出的问题、提示过程、尚未弄懂的行和待复访点。
5. 本人复述、修改、调试或练习的实际结果。
6. 分别标注已讲解、引导后理解、独立实现、可应对追问；功能验证单独记录。
7. 下一次从哪里继续。

2026-10-11用户调整教学节奏：当前默认模块功能→完整分支→各层交互/函数→关键函数，其他点名深入。停止按几行代码逐轮提题，后续记录优先按分支组织；Lesson41上一题不要求补答，未回答内容不记理解。

分支测试/VS Code入口见[分支调试指南](../exception-branch-debug-guide.md)及相应课堂。用户2026-10-12确认低频记录，不保存每轮“继续/懂了”流水，不重复维护多份当前进度。

基本Python知识可以简略处理；核心代码不跳过，复杂语法和React/TypeScript机制配具体例子。未开展的教学和未完成的练习不能记录成已完成。

`docs/关键知识点.md`仍只在用户明确要求记入某项内容时修改。

Read的路径、分页与失败分支见[Lesson67](lesson-67.md)。

ls/find/grep的发现、搜索与输出限制见[Lesson68](lesson-68.md)。

write/edit的两遍编辑、冲突核对与提交边界见[Lesson69](lesson-69.md)。

Bash进程、输出归档与停止分支见[Lesson70](lesson-70.md)。

工具批次、异常转消息/传播与循环去向见[Lesson71](lesson-71.md)。

现有权限决定、Read扩展及工具授权边界见[Lesson72](lesson-72.md)。

审批中断、一次性授权与恢复，以及第六章收束见[Lesson73](lesson-73.md)。

前六章改动的新旧调用链、对象关系与复习测试地图见[Lesson74](../../2.0/当前架构与调用链复盘.md)；按用户2026-10-20要求，最近调用链与事件讨论统一在[2.0目录](../../2.0/阅读索引.md)维护。

第七章上下文组成、项目规则/Skill按需加载与当前请求装配见[第75课](第75课-上下文组成与请求装配.md)。

第七章输入估算、压缩触发与发送预算，以及已知/未知失败边界见[第76课](第76课-输入估算与压缩发送预算.md)。

第七章安全切点、工具配对、摘要构造/校验与图消息替换见[第77课](第77课-安全切点与摘要替换.md)。

第八章session、thread/checkpoint、聊天归档及事务边界见[第78课](第78课-会话数据库与归档关联.md)。

第八章终端主线程、调用worker、长期异步loop与事件显示/结果补显接线见[第79课](第79课-终端输入与异步运行接线.md)。

第九章网页请求访问/输入/会话范围校验、请求回执与后台任务归属见[第80课](第80课-网页请求准入与运行任务归属.md)。
