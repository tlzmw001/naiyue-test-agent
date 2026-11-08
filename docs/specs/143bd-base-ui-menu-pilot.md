# Spec143BD Base UI 菜单对照试点：公开 API 受控菜单与 Skills 交接

## 现状与边界

分支 `frontend-ui-refactor`。当前已接入 Radix 的 `ThemeMenu` 与 `CommandMenu`（含受控二级子菜单）为对照基线；生产入口保持 Radix 实现。本轮新增 `@base-ui/react@1.9.0` 作为隔离候选，在测试专用入口 `e2e-harness/` 内建立同合同、同断言的 Base UI 实现，并接真实 `SkillsMenu` 组件验证「指令菜单 → Skills」交接。不替换生产 Radix 代码，不改视觉体系，不做其他菜单迁移，不进入阶段 4 视觉与性能。保留全部未提交工作（MCP、记忆、Skills、Spec143BC 等）。

## 基线事实（已核对）

- React 19.3.0、@radix-ui/react-dropdown-menu 2.2.1、@playwright/test 1.65.0、Vite 8.3.4。
- `@base-ui/react` 当前发布版 1.9.0（npm 实查）。
- 菜单→Skills 交接链：`ComposerControls` 持有 `menu` 状态；「技能/skills」项 `onSelect` 后 `runCommand('skills')` → `setMenu(null)` 再 `setMenu('skills')`；`SkillsMenu` 自持目录/加载/失败状态，初始焦点为自带的关闭按钮（`autoFocus`），Escape 经 ComposerControls 根级处理关闭并 `focusEditor()`；`close` 回调同样 `setOpen(null)` + `focusEditor()`。
- 上轮错误结论（Sub 非受控、无法逐层 Escape）已在 Spec143BB 撤回，不作为本轮依据。

## 候选接入方式

`e2e-harness/main.tsx` 新增 BasePilot 场景：以 Base UI Menu 公开组件实现与 Radix 版相同的主菜单 + 受控子菜单 + Skills 交接 + dialog 内嵌组合；`SkillsMenu` 使用真实组件与真实状态转换（api mock 提供正常/空/加载/失败目录），不使用简化弹窗替代。生产入口 `index.html` 不引入 Base UI。

重点核验（以实际 d.ts/源码为准）：受控 `open/onOpenChange`、`closeParentOnEsc`、开关原因信息（eventDetails.reason）、关闭后焦点目标（finalFocus 类 prop）、Portal container、非模态所需选项。只使用公开 API；若需要私有属性、事件派发模拟或多轮 RAF 才能保持行为，如实计入成本。

## 行为合同（与 Radix 基线一致）

1. ThemeMenu：鼠标/键盘打开；非首项选中后重开聚焦当前选中项；方向键/Home/End 循环；单选回调一次；Escape/外部点击关闭后焦点符合现有语义（Escape 回触发器；外部点击尊重用户点击目标）。
2. CommandMenu 子菜单：方向键与 hover 展开；Escape 先关子层回 SubTrigger、再关父层回触发按钮；禁用项不执行；选中状态正确；回调一次；会话切换后关闭。
3. 指令菜单 → Skills：旧菜单关闭后 Skills 面板获得并保持焦点，键盘可操作，Escape 关闭并回编辑器；覆盖正常/空/加载/失败目录；不被旧菜单焦点恢复覆盖。
4. dialog 与窄屏：菜单及子菜单挂在所属 dialog 的有效 DOM 子树内；Escape 不误关父 dialog；375px 与 resize 后不溢出。

允许调整测试定位器以适配组件 DOM/角色差异，不放宽行为断言。

## 影响文件

`frontend/package.json`（新增 @base-ui/react 依赖）、`frontend/e2e-harness/main.tsx`、`frontend/e2e/base-ui-pilot.spec.ts`、`frontend/e2e/fixtures/`（如需目录 mock）、`docs/specs/143bd-*.md`、`docs/handoff.md`。生产 `src/` 不改动（本轮只读对照）。

## 验收

- `npx vitest run`、`npm --prefix frontend run test:browser`、`npm --prefix frontend run build`、`git diff --check` 全部执行并报告分类结果。
- 交付 Radix / Base UI 对照表（四组场景）、各自必须保留的适配代码及原因、正常与关键失败路径接线说明。
- 同条件构建：Base UI 仅存在于测试入口，生产包不变属预期，明确标注「生产增量未测」；如需包级辅助数据仅作参考并注明口径。
- 评价顺序：行为正确性 → 接线清晰度 → 维护成本 → 样式迁移成本 → 构建/性能证据；行数仅辅助。
- 结论三选一（采用 Base UI / 继续 Radix / 证据不足），并给出唯一下一步。


## 实际结果与结论（试点完成）

**撤回两项已被证伪的归因**：①「Base UI 关闭总线绕过父级受控回调」不成立——独立脚本 `/tmp/naiyue-143bd-round2-review.mjs` 证实父级受控回调正常收到 `false + escape-key/item-press`，`menu` 正常变为 null；②「hideOthers 导致 Skills 焦点丢失」未获证实，已撤回，实际为两处具体接线问题（Skills 外点监听被 rAF 早退吞掉、Skills Escape 焦点落到旧权限/工具触发器）。两处 Radix 回归均已最小修复并计入 Radix 维护成本。

**对照表（全部通过，链接实际测试）**：

| 场景 | Radix | Base UI | 测试 |
|---|---|---|---|
| ThemeMenu（键盘/鼠标打开、重开聚焦选中项、循环、回调一次、Escape 与外部点击焦点目标） | 通过 | 通过 | `e2e/theme-menu.spec.ts`、`e2e/base-ui-pilot.spec.ts` 主题用例 |
| CommandMenu 二级（键盘/hover 展开、逐层 Escape、禁用项、会话切换、选中态、一次回调） | 通过 | 通过 | `e2e/command-menu.spec.ts`、`e2e/base-ui-pilot.spec.ts` 指令用例 |
| 指令菜单 → Skills（焦点保持、键盘、Escape 回编辑器、空目录、挂起加载、失败重试） | 通过（含空目录焦点与两项 Escape/外点回归修复） | 通过（normal/empty/pending/error+retry） | `e2e/command-menu.spec.ts` Skills 用例、`e2e/base-ui-pilot.spec.ts` Skills 用例 |
| dialog 内嵌与 375px 父子定位、选择后焦点恢复 | 通过（组合能力证据） | 通过 | `e2e/controlled-sub-pilot.spec.ts`、`e2e/base-ui-pilot.spec.ts` dialog/resize 用例 |

**Base UI 的公开 API 有效适配点**：`MenuRoot` 受控 open/onOpenChange（含 escape-key/item-press 原因）、`SubmenuRoot.closeParentOnEsc={false}` 逐层关闭、`Popup.finalFocus` 按 closeType 恢复、`Portal.container`、受控 RadioGroup。Base 候选已改为产品等价接线（menu 状态所有者/runCommand/close/select 合同），不是独立布尔简化场景。

**选型结论：建议继续使用 Radix——当前收益不足以覆盖替换和重新验证成本。** 依据：现有 Radix 实现已用公开 API 满足全部合同并处于生产入口；替换为 Base UI 需要重写接线、重跑全部回归、引入第二库的文档/升级验证与重复验证成本。Base UI 的 `closeParentOnEsc` 与 onOpenChange reason 信息属于有限收益，**不足以覆盖这些替换与重新验证成本**；因此不采用替换方案。若未来 Radix 出现真实能力缺口，按本 harness 复评。

**未验证/边界**：候选未进入生产构建，生产包增量为「未测」而非「零」；真实后端、Windows 原机、公司链路未验证；dialog 内嵌为组合能力证据，未新增产品菜单。harness 候选代码保留供未来复评。

**验证**：完整 Vitest **78 文件 664 项通过**；浏览器回归 **61 项通过、0 失败**（65 项跳过 = 桌面/窄屏布局限定 + 未启用外观工具）；`npm --prefix frontend run build` 通过；`git diff --check` 通过。当前生产主 JS **806.58 kB（gzip 248.57 kB）**。
