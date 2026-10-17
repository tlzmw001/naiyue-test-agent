# Spec094：输入框工具与权限模式管理

状态：2026-10-17 用户确认布局并授权实施。本片只覆盖输入框控件，不包含子Agent侧栏、上下文/Skills查看或手动压缩。

本机实现已接线。后端全量2096 passed in80.72s；前端12文件57项通过，TypeScript/Vite189模块成功。HTTP首批5项及前端缺失API先红灯；首建读取失败、旧GET覆盖与模式菜单焦点另有红灯回归。自动化是原生异步假模型/真实框架与SQLite证据；桌面1280及375px离线Web页面已实际检查，无真实供应商/原生Windows结论。

## 目标及当前链路

现有Composer底部左侧只有运行中的Follow-up复选框，右侧为停止/发送。
`AsyncApplication.get_registered_tools/get_active_tools/aset_active_tools/get_permission_mode/aset_permission_mode`
已经接通注册表、会话选择与SQLite；Web尚无定义查询和选择HTTP入口。
`SessionView`已经包含`active_tools/permission_mode`。主Agent每次模型请求重取活动工具；权限决定在工具批次内保存。

本片沿用当前浅色、细边框、圆角输入框及CSS Modules。左下角为当前模式和工具按钮，右侧为原发送方式、停止/发送。
模式和工具菜单向上展开，支持键盘、Escape、点击外部关闭；375px窄屏可换行，菜单不横向溢出。
既有Follow-up改为发送方式选择，默认仍为Steering，只在运行中显示，不改变控制协议。

## 接口与职责

- 应用层新增`AsyncApplication.get_session_control_options()`，提供真实默认活动工具、默认模式及可用模式；没有Auto配置时不包含Auto。只读查询不建立会话、不调用模型。
- `WebService.session_control_options()`：组合上述选择信息与`get_registered_tools()`的名称/描述；不传执行对象、注入参数、配置或凭证，遵守现有交付体积上限。
- `GET /api/session-controls`：上述元信息。
- `PATCH /api/sessions/{id}/tools`，严格JSON `{active_tools: string[]}`，允许空集合；名称/重复检查继续由注册表完成。
- `PATCH /api/sessions/{id}/permission-mode`，严格JSON `{permission_mode: Default|NoAsk|Auto|BypassApprovals}`。
- 两个写入口先通过当前WebService的工作区归属/恢复检查，再调用现有异步setter，保存成功才返回实际选择；非法选择为422固定错误码，不回传异常正文。所有入口沿用全局Bearer/Host/Origin校验。
- 恢复中的只读历史不允许修改，正常运行和前置审批期间可选择后续模式/活动集合，不改已有审批批次。
- 不新增数据库列、运行层机制、依赖、插件上传或配置文件写入。

## 前端对象和接线

- `types.ts`新增`PermissionMode/SessionControlOptions`，SessionView复用模式类型。
- `api.ts`新增三条查询/选择方法及固定错误的中文反馈。
- `useConversation`增加工具/模式选择及短期保存状态。应用确认后仅合并相关选择，不覆盖聊天/运行进度。
- 未建立会话时，可先选择工具/模式：首次修改创建空会话，迁移当前草稿，保存选择；不发送消息或启动模型。
- `Composer`加载实际元信息，权限模式持续可见；初次加载未确认时显示加载状态，不伪造默认值。
- 新增`ComposerControls.tsx`承载模式菜单及工具清单，避免把菜单交互堆入App/会话执行hook。菜单显示各模式解释、Auto不可用原因、当前工具数及子Agent作用范围说明。
- 模式在文本框获得焦点时支持Shift+Tab轮换可用项；其他控件保留原Tab/Shift+Tab，输入法组合期间不处理快捷键。
- 权限模式显示保存确认值，保存中禁用重复修改和发送；服务确认失败则提示并重新读取实际选择，不自动重试写请求。草稿保留。
- 首次创建成功但读取失败仍保留新会话ID及草稿，重试复用该ID；会话刷新捕获工具/模式选择版本，晚到的旧快照不能覆盖请求发起后已确认的新选择，聊天/审批等其他字段照常更新。
- 运行时切换模式提示“下一批工具调用生效”，当前审批不受影响；工具变更提示“下一次模型请求生效”。主Agent工具开关不修改活动子实例或角色配置。

## 正常例子

用户在空白输入框写了草稿后，选择NoAsk：

`ComposerControls → useConversation.selectPermissionMode → create/session（首次）→ PATCH permission-mode`
`→ WebService会话归属检查 → AsyncApplication.aset_permission_mode`
`→ SessionControlMixin.set_permission_mode → 原SQLite选择保存 → 内存更新`
`→ 返回NoAsk → 更新输入框模式/会话列表，草稿不变，模型未调用`。

用户在已有Ask审批时改为BypassApprovals：仅保存后续模式，原approval_batch_id/items仍相同，工具仍未执行；必须通过原审批按钮处理。
BypassApprovals仍受显式Deny与执行时目标检查约束。

## 失败例子

浏览器提交未知工具：注册表抛ValueError，Web映射422 `invalid_tool_selection`，原活动集合不变，消息不调用模型。
Auto未配置：setter拒绝，422 `permission_mode_unavailable`，原模式不变；菜单同时展示不可用说明。
网络/保存未确认：不重复PATCH；显示未确认提示，读取会话实际值，读取仍失败时保留已确认值并提示重试查看。

## 文件清单

新增：本Spec、`frontend/src/components/ComposerControls.tsx`、其CSS Module、`frontend/src/session-controls.test.tsx`、`tests/test_web_session_controls.py`。
修改：`application/async_application.py`、`web/app.py/service.py/schemas.py`、前端`types.ts/api.ts/useConversation.ts/App.tsx/components/Composer.tsx/App.module.css`。
收尾更新：`docs/handoff.md`顶部、`docs/future-work.md`实际待办、`docs/web-tools.md`入口说明。
生成：按既有`npm run build`生成忽略的`frontend/dist`，不手改产物。

## 验证

先HTTP失败测试证明缺少路由；先组件失败测试证明缺少入口。
覆盖元信息/鉴权/严格请求、空集合/非法/重复工具、模式可用性、会话隔离/持久重开、前置审批保持。
前端覆盖查询失败与重试、模式和工具保存、Shift+Tab限定、草稿保留、初次选择不运行、保存失败、旧会话响应不污染当前会话、原Steering/Follow-up语义。
运行定向及全量pytest、Vitest、TypeScript/Vite构建、compileall/pip check/500行及diff检查。
用隔离测试工作区和确定性模型启动真实Web，浏览器检查桌面和375px，不读写用户配置或调用真实供应商。
