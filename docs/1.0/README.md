# 1.0 系统学习资料

本目录保存当前项目的知识地图与系统学习笔记。2026-10-10用户自述第一章已理解，现进入第2章模型接入；一次一个小主题，理解与实践分别记录。

- [知识地图与阅读顺序](learning-roadmap.md)：代码行数、10章顺序、逐文件阅读清单、理解与实践验收。
- [课堂笔记目录](lessons/README.md)：后续逐课记录核心代码、数据流、问题、练习与掌握证据。
- [Lesson 18：启动入口与对象装配地图](lessons/lesson-18.md)：main入口已讲到末尾，掌握证据单独记录。
- [Lesson 19：lifespan注册与执行](lessons/lesson-19.md)：异步上下文的启动/关闭及工具/factory装配。
- [Lesson 20：模型配置入口](lessons/lesson-20.md)：路径/权限、JSON及顶层结构。
- [Lesson 21：供应商字段与URL](lessons/lesson-21.md)：kind、字段契约与端点校验。
- [Lesson 22：模型条目与引用](lessons/lesson-22.md)：模型ID、预算和请求策略。
- [Lesson 23：Thinking配置与等级](lessons/lesson-23.md)：默认记录、支持列表、回退与原生映射。
- [Lesson 24：模型构造与登记](lessons/lesson-24.md)：四适配器、参数展开与目录查询。
- [Lesson 25：HTTP资源登记](lessons/lesson-25.md)：模型身份、SDK/传输分层及资源关闭。
- [Lesson 26：模型失败与分类](lessons/lesson-26.md)：响应拒绝、分类、输入溢出与unknown诊断。
- [Lesson 27：统一失败数据](lessons/lesson-27.md)：异常转换、字段校验和诊断失败回退。
- [Lesson 28：应用层运行失败](lessons/lesson-28.md)：旧版课堂，阶段、观察状态与失败记录。
- [Lesson 29：新版异常的两条路径与完整地图](lessons/lesson-29.md)：内部报告与原异常传播、描述复用及终局时点。
- [Lesson 30：FailureDescription的数据契约](lessons/lesson-30.md)：字段、构造时校验及冻结。
- [Lesson 31：resolve_failure选择描述](lessons/lesson-31.md)：匹配顺序、None与unknown、诊断故障回退。
- [Lesson 32：分类器调用适配](lessons/lesson-32.md)：签名检查、单参/双参适配及闭包捕获。
- [Lesson 33：模型分类配置与运行快照](lessons/lesson-33.md)：按模型登记、当前读取与闭包快照。
- [Lesson 34：Runtime策略的模型委托](lessons/lesson-34.md)：模型入口适配及分类器/描述数据流复盘。
- [Lesson 35：无效响应与运行层异常](lessons/lesson-35.md)：异常生产者、保护分类和来源归属。
- [Lesson 36：工具结果不确定](lessons/lesson-36.md)：异常转换与cause、工具失败和会话核对门禁。
- [Lesson 37：用户停止数据流](lessons/lesson-37.md)：当前课堂，UserStop转换为图停止结果，模型调用与运行终局的区别。
- [异常系统总图与扩展指南](exception-architecture.md)：当前优先阅读；对象职责、装配/执行、双路数据、分类与App回退、完整分支/收尾，随后是源码与扩展参考。
- [异常架构复审](exception-design-review.md)：设计历史，记录旧范围为何不足及框架能力核对；当前实施见Spec079。
- [开发交接](../handoff.md)：工程进度、验证和每次工作的唯一下一步。

旧版课堂记录保留在[历史笔记](../lessons/)，不迁移或覆盖。新笔记从lesson-18连续编号，记录实际讲到的内容，不提前创建虚构课堂或把助手实现当作用户掌握。
