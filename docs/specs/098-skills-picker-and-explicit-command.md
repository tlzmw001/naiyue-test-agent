# Spec098：技能列表与显式技能指令

用户2026-10-17授权：`/skills`查看当前全部可获取技能，加号→技能打开同一列表，点技能把`/技能名`加入输入框，补充任务后发送。沿用097输入框/CSS Modules/异步应用，不新增依赖、配置、数据库或运行机制。

## 1. 当前链与目标

当前`SessionService._run_steps`→`prepare_run_resources`→`discover_project_skills`读取工作区`.agents/skills`、`.claude/skills`，索引交prompt_parts，规范资源目录交原Read权限；正文由模型按需调用Read。索引每次新运行准备，运行期间冻结；正文实际读取时可见新内容。默认来源agents优先，另一来源同名仍保留；无效/disable-model-invocation及现有路径不合格项不进入目录。

目前Web没有技能查询，输入框对部分`/name`按未知内建指令阻止，带连字符的名称则可能直接走普通消息。目标只补只读列表、草稿选择及指令识别，不另做Skill执行器或预读正文。

参照：Pi`InteractiveMode`把资源发现的Skill登记到slash列表；`AgentSession._expandSkillCommand`处理`/skill:name`，在发消息前读取正文并展开。AgentX当前Java源码未找到对应Skill入口。本项目已有Read/权限与按需加载合同，因此复用索引+Read，显式选择通过统一索引command和提示词表达，不复制Pi的正文展开。确定性测试只能证明指令/索引/Read接线，不证明真实模型必然服从。

## 2. 接口与来源身份

- 新认证`GET /api/skills`，工作区只来自WebSettings，不接受客户端路径或thread_id。WebService.skills→AsyncApplication.aget_skills(workspace_root)→run_blocking(discover_project_skills)。不创建会话、不加载AGENTS、不调用模型/工具、不更新图。
- 返回`{entries:[{source_id,name,description,command}],default_source,diagnostics:[{source_id,reason}],read_registered}`。不输出正文、绝对路径或同源重名诊断的winner/loser。超交付预算沿view_too_large；发现错误沿现有固定原因日志，整体查询失败返回skill_catalog_unavailable。
- 在既有SkillCatalog增加command_for(entry)，供公开列表和模型索引同时使用。agents来源普通名称用`/name`；claude来源始终用`/claude:name`，agents内建保留名用`/agents:name`。指令身份不随default_source切换，以免用户选中agents后该来源消失，旧`/name`错误命中同名claude；default_source仍控制自然任务的来源偏好。保留名包含compact/context/model/tools/permissions/agents/skills；新增内建指令时须同步检查同名技能。
- 模型索引每项增加`<command>`；policy明确开头命中command表示用户显式选择，要先Read对应SKILL.md；缺少指定技能、来源或Read工具时说明不可用，不换用另一来源。技能不扩大文件/工具权限。正文仍是Read结果，未在列表/首请求系统提示中注入正文。

## 3. 界面与草稿

加号包含“压缩上下文”和“技能 /skills”。输入`/skills`执行本地打开操作，不作为模型消息发送；两入口共用SkillsMenu向上列表，显示名称/说明/来源/实际command，可滚动、刷新，加载/空/错误/重试均明确。沿用现有白底、灰字和CSS Modules，不新增侧栏/顶部入口。

选择只在输入框添加前缀，例如草稿“检查失败用例”→`/case-review 检查失败用例`。若已有前缀确实属于本次技能目录，改选替换前缀并保留参数；不删未知路径/普通草稿。返回输入框焦点，不创建会话或发模型。未启用/未注册Read时仍可查看发现结果，但选择不可用且说明需启用read；不自动改工具集合。运行期间新增技能下一次新运行索引才生效，列表说明此边界。

发送`/case-review 参数`：Composer先识别内建命令；其余斜杠名称重新查询目录，精确匹配command且Read可用才沿原send走普通消息或Steering/Follow-up。保留原文字和参数，不注入正文。支持连字符、来源前缀和多行参数；不存在/查询失败/Read关闭时提示并保留草稿，不向模型发送未确认命令。不把`/tmp/file`等路径文本截成命令。

异步查询期间若换会话、编辑主草稿或待重发队列，旧响应不得发出新草稿、清除新草稿或显示旧错误；连续发送只进行一次确认。菜单关闭后旧GET不改新面板，切会话关闭，Escape返回输入框/入口，键盘可逐项选择。运行中仍使用现有Steering/Follow-up，不新增控制通道。

## 4. 调用例子与失败

正常：加号→技能→WebApi.skills→GET→应用发现→元数据列表；选择agents/case-review→草稿`/case-review 检查失败`；用户发送→再次确认command→原chat.submit→原HTTP run→prepare_run_resources产生同command索引→模型提出Read→原权限/执行→同ID ToolMessage带正文→模型继续。模型主动选择仍按原描述匹配，新的显式入口不替代它。

同名：agents/demo与claude/demo同时展示，选择后者产生`/claude:demo`，模型索引关联claude路径。名为compact的技能使用`/agents:compact`，不会触发手动压缩。

失败：目录损坏项跳过并公开有限诊断，零有效项显示空；网络失败可重试，草稿不变。选择后文件被删，发送时重新查询拒绝；查询到发起新运行间若资源再变，运行准备重新发现，policy要求报告缺失。现有路径/禁用/权限规则保持，不因UI命令授予读权限，不声称加载成功或已使用。

## 5. 修改文件与验证

涉及超过8个文件：扩展application/skills.py、async_application.py，Web app/service及前端api/types/commands、Composer/ComposerControls；新增SkillsMenu.tsx及CSS、聚焦Python/前端测试。沿现有职责，不新建服务、注册表或运行层类。更新README/web-tools/handoff/future-work，Spec097加号一项标为该片历史快照。

先红灯验证：元数据查询/热读取/来源与保留名/不泄漏正文路径/无模型会话副作用；显式命令进入索引并由脚本模型Read实际正文；两个UI入口、前缀填入/替换/保留草稿/不自动发送、错误/空态/刷新/关闭、Read禁用、未知指令、异步草稿/会话竞态。保留原compact/权限/模型断言，菜单项数按新要求更新。

完成后全量pytest、vitest、TypeScript/Vite build、AST/≤500行及diff/文档链接检查。临时SQLite和真实项目技能文件、确定性模型实际构建页验证1440与375宽度，真实用户配置/目录不写入，不发供应商请求。学习掌握记录不更新，不提交/push。

## 6. 实施与验收记录

- 已接`WebApi.skills`→认证GET→`WebService.skills`→`AsyncApplication.aget_skills`→线程内既有发现；`SkillCatalog.command_for`供公开查询与模型索引共同使用，正文/路径不进入公开列表。既有发现格式、范围、Read资源授权保持。新建SkillsMenu组件/CSS及两份聚焦测试，没有新服务、运行层对象、依赖、配置或数据库变化。
- 正常数据：列表`{source_id:'claude',name:'case-review',command:'/claude:case-review',description:...}`→用户选择保留草稿→确认GET精确命中→原`useConversation.submit`→HTTP原run入口→资源索引`<command>`及对应路径→脚本模型Read→同ID成功ToolMessage含真实Skill正文→模型最终回复。UI不自行加载正文；当前可发现不代表已读取或已使用。
- 架构复核以探针复现来源随default_source改绑，先红后绿固定来源指令；有效数字起始名称此前落入普通文字分支，先红后绿纳入统一确认；待重发队列编辑此前未使确认失效，先红后绿将队列内容版本纳入主草稿/会话/发送方式等版本守卫。安全复核无新增高信心发现，最终架构复核上述项闭合。
- 全量后端：`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`→**2133 passed in85.19s**，日志`/tmp/naiyue-098-backend-final-complete.log`；前端`npm test -- --run`→**17文件88 passed**，日志`/tmp/naiyue-098-frontend-final-complete.log`；`npm run build`→TypeScript零错误、Vite200模块成功，产物`index-B2rmWoE2.js`/`index-Bt18rTJs.css`。原compact、权限/工具、模型/推理及其他套件保留通过。339 Python AST、339 Python与47前端文件≤500行、6份当前文档链接、compileall/pip check及diff均通过。
- 本地实际构建页使用临时工作区/SQLite/离线模型，验证加号仅压缩+技能、文字`/skills`不发模型、同名来源可区分、选择保留任务/焦点且不自动发送、显式claude调用经真实Read返回所选正文、1440和375无横向溢出。截图`/tmp/naiyue-098-ui/skills-desktop.png`、`skills-selected-draft.png`、`skills-mobile.png`；它们为临时验收资源，不写项目Skills或真实配置。真实供应商服从、用户级/共享Skill、Windows/Linux原机仍无本片验证证据。
- 失败边界：401/403、固定工作区不接受客户端切换、公开诊断去除路径、超预算、整体发现错误安全码、空态/重试/刷新、Read关闭、未知/消失技能、文字/会话/队列/卸载与关闭查询竞态均有确定性回归。原权限审批继续生效；不宣称沙箱或技能执行效果。
