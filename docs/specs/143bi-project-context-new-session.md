# Spec143BI：项目右键新建会话

## 目标与当前链路

项目右键菜单目前只有“移除项目”。主入口由 App → useConversation.newSession 打开新会话草稿，首次发送才调用当前 scope 的 WebApi.create；ProjectWorkspace 保留各项目独立的 App/API/草稿。新增入口必须选择被右键的项目，不能误用当前项目客户端，也不能直接创建空记录。

## 修改合同

- ProjectNavigation 在“移除项目”上方显示“新建会话”，沿用 SessionMenu 样式、菜单关闭及焦点返回方式。原移除入口保留。
- ProjectWorkspace 复用 select，给 ScopeSelection 添加可选 newSession 意图，revision 区分重复点击。自动展开并进入目标项目。
- App 沿现有 selection effect，等待模型与已有切换/控制操作就绪，再消费一次 newSession 意图，调用原 chat.newSession；关闭移动会话抽屉。普通进入项目和打开已有会话语义不变。
- 当前项目菜单使用与主入口相同的禁用条件；其他项目由其 App 就绪后消费意图，不操作原项目会话。
- 不改后端、公开HTTP接口、配置或依赖，不改变项目创建/移除与会话保存合同。

## 正常与失败路径

正常：右键项目 B → 菜单新建 → select(B, newSession=true) → B App 就绪 → newSession 打开 B 的草稿 → 首次发送经 B API.create → B API.start。每个 revision 只执行一次，保留 A 的会话及草稿。

失败：B 的模型尚未就绪，意图等待，不向 A 创建会话；首次发送若 create 失败，由原 useConversation 创建逻辑展示错误、保留正文和请求身份，不新增重试路径。目录缺失/信任校验继续由现有创建/执行入口处理。

## 影响文件与验证

生产：components/ProjectNavigation.tsx、ProjectWorkspace.tsx、App.tsx、projectTypes.ts。测试：ProjectWorkspace.test.tsx 与独立浏览器菜单回归；交接摘要 docs/handoff.md。

先写失败测试，验证菜单顺序、当前/未访问/已访问目标、一次消费、草稿隔离、未就绪和失败。运行完整前端 Vitest 与 TS/Vite 构建；浏览器桌面/375px核对菜单、点击结果、键盘关闭、原移除功能和移动抽屉。真实服务与 Windows 原机不由前端模拟验证替代。

## 2026-11-16 本机验证

- 先红：旧代码下新增菜单/目标草稿与顺序测试5项失败；扩展后的方向键断言也先复现失败，再补菜单处理。
- 专项：`npm --prefix frontend run test -- --run src/ProjectWorkspace.test.tsx src/session-creation.test.tsx`，33项通过。原菜单测试保留移除、Escape、焦点返回及外部点击断言，更新首项焦点为新建会话。失败用例使用现有结构化错误映射。
- 完整前端：`npm --prefix frontend run test -- --run`，78文件678项通过。`npm --prefix frontend run build`，TS/Vite成功，只有既有chunk体积提示。
- 生产浏览器：`cd frontend && READING_PROD=1 npm run test:browser -- e2e/project-context-new-session.spec.ts`，1440px与375px共4项通过。覆盖菜单顺序、方向键/Escape/Shift+F10、菜单无越界、抽屉关闭、移除弹窗可取消、跨项目草稿保留、不提前创建记录；显式断言无未声明请求和页面异常。桌面与窄屏菜单截图均已查看。
- 浏览器脚本根据真实选择状态等待会话就绪，补齐项目浏览的只读目录mock，移除重复关闭已收起抽屉的步骤；未修改产品行为来掩盖脚本错误。
- L1/L2/L3：入口与回调真实存在，ScopeSelection传递目标scope/newSession/revision，目标App消费一次并调用原newSession。目标API首次create/start及失败保留均有测试，没有新增后端创建入口或废弃包装。
- 证据：`build/project-context-new-*.log`、`build/project-context-new-verification.json`及`frontend/test-results/`截图。仅本机前端，未改配置/依赖/后端，本次仅本地提交，未push/发布；Windows原机和真实服务未在此轮验证。
