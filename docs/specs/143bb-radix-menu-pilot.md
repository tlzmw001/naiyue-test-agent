# Spec143BB 受控菜单试点：Radix 菜单行为层接入评估

## 范围与边界

在 Spec143BA 修复与模态收敛之后，评估「公共无头菜单库（Radix DropdownMenu）能否替代自研菜单交互」。试点仅两处：`ThemeMenu`（基础菜单）与 `ComposerControls` 的「指令与功能」菜单及其「发送后滚动」二级菜单。权限模式、工具选择、MCP 服务选择与刷新逻辑保持现状；不迁移其他菜单，不引入 Tailwind/shadcn，不创建通用浮层平台，不升级既有依赖。新增依赖仅限 `@radix-ui/react-dropdown-menu`（已核对 2.2.1 peer 支持 React 19）。

## 迁移合同（与自研实现的现有语义一致）

- 非模态：`modal={false}`，背景保持可交互（Radix 默认 modal=true，必须显式关闭）。
- 打开聚焦：ThemeMenu 聚焦当前选中项（Radix 默认聚焦首项，需 onOpenAutoFocus 抑制后手动聚焦选中项）；指令菜单聚焦首个可用菜单项（与 Radix 默认一致）。
- 键盘：方向键循环、Home/End、Escape 关闭并恢复触发按钮焦点；二级菜单 ArrowRight/Left 展开与收起、Escape 逐层关闭（Radix Sub 内置）。
- 业务回调：主题/发送后滚动选择、openCloud、runCommand、图片选择各一次调用；disabled 项不执行。
- 关闭路径：外部点击、选项选择、Escape；焦点恢复由 Radix 提供，删除对应手写监听与键盘导航。
- 样式沿用现有 CSS Modules 与主题变量，菜单定位用 Radix（碰撞检测、flip/shift），窄视口与 resize 行为实测。

## 验证

1. **基线先行**：`command-menu.spec.ts`、`theme-menu.spec.ts` 先对自研实现写行为断言（打开聚焦、方向键、Home/End、二级展开/逐层关闭、外部点击、焦点恢复、一次调用），迁移后同一断言复跑。
2. **窄视口与边缘定位**：窗口边缘打开菜单、resize 后定位不溢出（Radix 碰撞检测的收益点）。
3. **原生 dialog 内嵌菜单**：如有真实消费者沿真实入口验证；否则用测试组合场景（dialog + 菜单）验证 Portal container 行为，并标注为组合能力证据。
4. **采用标准（按序）**：业务回调/禁用/失败行为保持 → 键盘/嵌套关闭/外部点击/焦点/定位通过 → 与原生 dialog 和快捷键守卫兼容 → 构建产物增量与全局样式副作用 → 删除的重复职责与新增适配成本。
5. 全量 Vitest、浏览器回归、构建与 `git diff --check`；报告实际数量、跳过原因与范围。

## 结论形式

输出「保留试点并建议扩展」或「收益不足、停止扩展」的证据结论；本轮不自动迁移其他菜单。

## 实际结果与结论（试点完成）

**源码事实复核**：当前安装的 @radix-ui/react-dropdown-menu@2.2.1 的 `DropdownMenu.Sub` 实现为 `useControllableState({ prop: openProp, defaultProp, onChange })`，**公开支持 open/defaultOpen/onOpenChange 受控**（源码与类型定义均已复核）；`SubContent.onEscapeKeyDown` 为公开 prop，preventDefault 可取消默认「关闭整棵树」行为。此前「Sub 非受控」「必须混用 MenuPrimitive 与私有 scope」「已证明无法保持逐层 Escape 与 hover」三条结论**有误，予以撤回**。私有 scope、跨包拼装、事件派发模拟和多轮 RAF 抢焦点均已废弃，不再作为实现路径。

**保留两处 Radix 实现**：
- `ThemeMenu`：Radix DropdownMenu。保留自研合同：当前选中项聚焦、方向键与 Home/End 循环、单选回调、Escape/外部点击关闭、焦点恢复。适配为 `modal={false}`、`loop`、稳定 `useCallback` ref 挂载聚焦选中项、`aria-labelledby={undefined}` 保留菜单名。补验了键盘打开、选择非首项后重开聚焦、边缘与 resize 定位。
- `CommandMenu`：新建并接入 `ComposerControls` 的「指令与功能 + 发送后滚动」分支，权限、工具、MCP 选择与刷新逻辑保持现状。主菜单使用 Radix DropdownMenu；二级使用**受控 `DropdownMenu.Sub`**，`SubContent.onEscapeKeyDown` 中 preventDefault 后 `setScrollOpen(false)` 并恢复 SubTrigger 焦点，**不再混用私有 scope 或 MenuPrimitive**。Radix Portal container 保持菜单在 Composer 表单内；窄屏用公开 `sideOffset` 和 `avoidCollisions` 做边缘回退（窄屏 -160，桌面不变）。

**验证结果**：
- 组合场景（测试专用 `e2e-harness/`，不进入产品）：受控子菜单 ArrowRight 展开、Escape 只关子层并恢复焦点；hover 展开与一次选择回调；原生 dialog 内通过 Portal container 使菜单在 top layer 内可用，3 项通过。
- 真实页面：指令菜单 8 项、主题菜单 5 项通过，含键盘、hover、逐层关闭、业务回调次数、禁用项不执行、会话切换、外部点击、窄屏与 resize 定位；原有快捷键与原生模态守卫的回归保持通过。
- 完整 Vitest **78 文件 664 项通过**；浏览器回归 **43 项通过、0 失败**（其余为明确的桌面/窄屏限定及未启用外观工具）；`npm --prefix frontend run build` 通过；`git diff --check` 通过。
- 当前构建主 JS 为 **806.26 kB（gzip 248.47 kB）**。本轮没有保留迁移前同条件构建基线，**不报告不可信的产物增量数字**。

**结论**：保留 `ThemeMenu` 与 `CommandMenu` 的 Radix 实现，证明公开 API 可以满足本试点合同；不把「删除行数」作为主要采用依据。后续只建议在同构、已有真实场景的单层菜单上逐项评估，不自动扩大迁移。权限/工具/MCP、其他菜单及阶段 4 视觉与性能优化不在本轮。真实后端、Windows 原机与真实公司链路未验证；dialog 内嵌为组合能力证据，不是新增产品菜单。
