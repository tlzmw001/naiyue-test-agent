# Spec143BA 前端模态交互修复与自绘模态收敛

## 现状与边界

前端交互原语全部自研且分散：外部点击关闭、Escape、焦点恢复、模态层管理由各组件分别实现，已产生四类可复现缺陷（阶段 1 Playwright 浏览器回归 7 失败）。本轮在 `frontend-ui-refactor` 分支上先修复四类缺陷，再把两个自绘模态（`SessionSearch` 搜索、`SessionMenu` 的重命名/删除确认分支）迁移到原生 `<dialog>`/`showModal()`。保留现有后端、接口、业务控制、CSS Modules 与视觉语言；不引入 Radix，不重写其他原生 dialog，不做全量菜单迁移（阶段 3）与视觉/性能优化（阶段 4）。

## 交互规则（本轮起对相关组件生效）

| 场景 | 行为 |
|---|---|
| 确认框取消/Escape | 焦点恢复到实际打开该确认框的入口；原入口消失或不可用时落到当前页面仍可用的合理目标，不固定跳到无关按钮 |
| 有模态打开时 Cmd/Ctrl+K | 不打开、不挂载全局会话搜索，模态关闭后也不补开；不扩大为禁用其他快捷键 |
| 无模态时 Cmd/Ctrl+K | 保留现有搜索能力；关闭搜索后焦点恢复到触发元素 |
| 附件菜单点击外部 | 菜单关闭；菜单内部点击仍执行原动作，不被外部关闭逻辑误伤 |
| 窄屏会话抽屉 Escape | 没有更上层弹层处理该按键时关闭抽屉，焦点回到打开入口 |
| 窄屏会话抽屉遮罩 | 关闭抽屉并恢复入口焦点 |
| 抽屉上方有菜单/模态 | 一次 Escape 只关闭应处理它的最上层 |

「有模态打开」的判定：存在 `dialog[open]`（原生 top layer），或存在 `aria-modal="true"` 的自绘模态（迁移完成前 `SessionMenu` 确认态仍以此存在）。普通非模态浮层（菜单、popover）不算模态。

## 阶段 1 修复（每项先红后绿，不改命令受理/重试/状态语义）

1. **`CloudTaskActions` 焦点恢复**：`close()` 现固定 `button.current?.focus()`（删除按钮 ref）。改为记录实际打开确认框的入口元素，关闭时恢复；入口不可用时落回操作区仍可用按钮。影响 `CloudTaskActions.tsx`。对应浏览器回归 `focus-restore.spec.ts` 4 用例。
2. **`CloudAttachments` 外部关闭**：附件小菜单无外部点击监听。在包含触发按钮和浮层的根元素外监听 `document` `pointerdown` 关闭；内部点击不受影响。对应 `attachment-menu.spec.ts`。
3. **窄屏抽屉 Escape 与焦点**：`Sidebar` 打开态监听 Escape（按上述模态判定豁免更上层），触发 `onClose`；`App` 在 `onClose` 后恢复「打开会话栏」触发按钮焦点。影响 `Sidebar.tsx`、`App.tsx`。对应 `mobile-drawer.spec.ts`。
4. **全局搜索快捷键守卫**：`Sidebar` 的 window keydown 在「有模态打开」时忽略 Cmd/Ctrl+K（不 preventDefault 由模态自身处理，也不挂载搜索）；无模态时行为不变。对应 `modal-shortcut.spec.ts`，另补无模态时打开搜索、关闭后焦点恢复的保护用例。

## 阶段 2 迁移（保留业务行为，删被替代实现）

**`SessionSearch` → 原生 `<dialog>`**：`showModal()` 提供模态隔离与焦点圈定；backdrop 点击关闭改用 `event.target === dialog`；Escape 走 `onCancel`（保持 `close()` 逻辑与焦点恢复）；组合输入 `isComposing` 分支、combobox 方向键/Enter/`aria-activedescendant`、初始聚焦 `input`、选择后不恢复触发焦点（切换会话）全部保留。手写 `keepDialogFocus` 调用删除（helper 仍有 CloudAttachments/CloudResults/ProjectPicker/ProjectRemoval 四个消费者，保留）。

**`SessionMenu` 确认态 → 原生 `<dialog>`**：`rename`/`delete` 分支渲染 `<dialog>` + `showModal()`；`busy` 时 `onCancel` 阻止关闭；提交失败保留错误提示、输入与重试；普通菜单分支保持 Portal 非模态与方向键导航；菜单态外部 `pointerdown` 关闭保留。关闭焦点恢复沿用 `Sidebar` 的 `origin` 机制。

迁移后逐项核对：背景不可交互（原生 inert）；背景滚动行为实测（不凭一条 CSS 宣称）；桌面与窄屏、明暗主题外观与迁移前基线截图对比无非预期变化；被替代的内部实现（自绘焦点陷阱、`aria-modal` 手写层）确认无消费者后删除。

## 影响文件

`CloudTaskActions.tsx`、`CloudAttachments.tsx`、`Sidebar.tsx`、`App.tsx`、`SessionSearch.tsx/.module.css`、`SessionMenu.tsx/.module.css`、`e2e/*.spec.ts`、`e2e/fixtures/service.ts`（按需补声明）；`docs/handoff.md`。

## 验证

- 定向回归：每项修复后跑对应 spec；阶段 1/2 收尾分别跑 `npm --prefix frontend run test -- --run`、`npm --prefix frontend run test:browser`、`npm --prefix frontend run build`、`git diff --check`。
- 阶段 1 原有 7 失败转绿、既有绿用例保留；新增用例按最终数量报告。
- 阶段 2 补桌面/窄屏、明暗主题外观核对（迁移前后截图对比）。
- 本轮验证均为「真实 Chromium + 模拟 API」；真实后端与 Windows 原机验收不在本轮范围。

## 实际结果与限制（验收证据补齐后）

- **焦点轨迹**：独立原生 dialog 探针与项目搜索模态对照，headless shell 与完整 Chrome（系统 Chrome）轨迹一致——首控件首次 Shift+Tab 后 `activeElement=BODY` 且 `document.hasFocus()=false`（焦点离开网页进入浏览器 UI 导航），背景控件未获得焦点，再次 Shift+Tab 焦点回到模态内部。圈定验收按「背景不激活 + 模态保持 + 导航回到模态内部」断言，不再归因 headless shell 缺陷（证据 `build/spec143ba/focus-probe/`）。
- **窄屏回归**：`mobile-modals.spec.ts` 3 项（搜索打开/取消/选择、重命名模态 Escape 只关最上层再关抽屉、删除确认打开取消）；窄屏深色抽屉与搜索截图（`build/spec143ba/after/mobile-dark-*.png`）。桌面已有失败重试、busy、请求次数断言不变。
- **滚动验证**：无模态时背景列表随滚轮滚动（正向对照）→ 模态打开后背景 scrollTop 不变 → 模态内结果列表真实滚轮 scrollTop 增加（`scroll-control.spec.ts`）。不再以 scrollIntoViewIfNeeded 代替用户滚动证据。
- **跳过分类**：布局限定（桌面/窄屏各自的交互形态）与外观工具（未设 APPEARANCE_DIR 时跳过）分别统计，报告不混用。
- 模态内 Tab 圈定是 showModal 的浏览器保证；焦点短暂离开网页属浏览器 UI 导航，产品不承诺 activeElement 每步都在 dialog 内，而承诺背景不激活与导航可回到模态内部。
