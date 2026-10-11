# 系统课堂笔记

本目录用于系统学习的课堂记录。第一章见[Lesson18](lesson-18.md)和[Lesson19](lesson-19.md)；2026-10-10用户自述已理解。第二章从[Lesson20](lesson-20.md)进入，模型目录见[Lesson24](lesson-24.md)，HTTP资源见[Lesson25](lesson-25.md)，模型失败见[Lesson26](lesson-26.md)，统一失败数据见[Lesson27](lesson-27.md)，现读[Lesson28：应用层运行失败](lesson-28.md)。自述理解、正确回答、独立实现和面试追问分别记录。

新版异常地图见[Lesson29：两条路径与完整地图](lesson-29.md)，数据类见[Lesson30](lesson-30.md)，描述选择见[Lesson31](lesson-31.md)，分类器适配见[Lesson32](lesson-32.md)，模型快照见[Lesson33](lesson-33.md)，模型委托及数据流复盘见[Lesson34](lesson-34.md)，无效响应见[Lesson35](lesson-35.md)；Lesson28保留旧版学习过程。各课理解证据分别保留。

write提交边界及核对门禁见[Lesson36](lesson-36.md)，当前进入[Lesson37：用户停止数据流](lesson-37.md)。未作答的检查保留未确认，不补记掌握。

**当前进入Spec081后的衔接课堂：[Lesson38](lesson-38.md)。** 用户表示此前整体链路未理解，先追普通模型超时的原异常属性回传，再补观察隔离，之后回第二章stop_reason。旧Lesson29的reporter/id表是历史机制；图中定位不记作所有核心代码已逐行学习。

笔记从 `lesson-18.md` 起连续编号，沿上一级[知识地图](../learning-roadmap.md)的10章推进；一章可以拆成多次小课。旧 `docs/lessons/lesson-1.md` 至 `lesson-17.md` 保留为历史材料，新课以当前代码为准。

每次记录：

1. 本课的小目标、真实文件/函数与阅读位置。
2. 具体数据结构、调用链和正常/异常数据流。
3. 已讲解的核心行与复杂语法、框架源码依据。
4. 用户提出的问题、提示过程、尚未弄懂的行和待复访点。
5. 本人复述、修改、调试或练习的实际结果。
6. 分别标注已讲解、引导后理解、独立实现、可应对追问；功能验证单独记录。
7. 下一次从哪里继续。

基本Python知识可以简略处理；核心代码不跳过，复杂语法和React/TypeScript机制配具体例子。未开展的教学和未完成的练习不能记录成已完成。

`docs/关键知识点.md`仍只在用户明确要求记入某项内容时修改。
