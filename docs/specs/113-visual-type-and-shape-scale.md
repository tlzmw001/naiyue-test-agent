# Spec113：前端字号、字重、行高与圆角统一刻度

日期：2026-10-18。状态：**已实现并完成本机验收，纳入本批提交**；纯样式切片，不改TSX、交互、接口或数据。

## 1. 问题与证据

颜色已在`frontend/src/theme.css`收敛为`light-dark()` token，暗色模式可用，组件CSS中无直接色值。版式与形状仍由各组件自行取值，视觉上不统一。2026-10-18对当前工作区前端（构建到临时目录，离线假模型，1440/375、浅/暗截图）统计：

| 属性 | 现状 | 观察到的问题 |
|---|---|---|
| font-size | 18种：10/11/12/13/14/15/16/17/19/20/21/22/25/26/30/32px及.85em/.9em；10px×22、11px×38 | 大量10–11px灰字；页头模型名13px与档位10px基线不齐 |
| font-weight | 400/500/550/600/650 | 550/650非常规档；Windows微软雅黑仅常规/粗体，550按CSS匹配规则落到粗体，跨平台不一致 |
| border-radius | 16种：2–24px与50% | 工具卡12px、代码块14px、表格直角，三种容器语言 |
| line-height | 7种：1.5–2.1 | 正文、界面短文本、多行说明、代码没有分类 |
| 对比度 | `--faint`(#9a9a9a)白底约2.8:1 | 输入框下方11px提示等正文用途低于WCAG AA 4.5:1 |
| Markdown | 无h1–h4/列表/引用规则 | 标题用浏览器默认（h2 24px/700），明显跳出16px正文 |

另见：用户消息下的分隔线把一轮对话切开；“回到最新”按钮以滚动区而非768px消息列居中；侧栏新建按钮带边框且下方留白约48px。

## 2. 目标与边界

- 方向：沿用户已确认的ChatGPT式克制界面；本片只统一刻度和少量由刻度直接决定的样式，不重新设计布局、配色或品牌。
- 范围：`theme.css`新增非颜色token；`App.module.css`与`components/*.module.css`全部字号、字重、行高、圆角改用token；补Markdown正文规则；新增一个CSS约束测试。
- 不改：任何`.tsx/.ts`生产文件（当前另有会话正在修改Conversation/ToolCall/App等TSX）、DOM结构、类名、文案、交互、键盘行为、API、颜色值和暗色方案。复制按钮图标化、页头布局、审批卡交互等留给后续切片。
- 不新增依赖（不引入stylelint），约束用现有vitest。

## 3. Token定义（theme.css）

字号——一角色一档，界面最小12px：

| Token | 值 | 角色 |
|---|---|---|
| `--fs-caption` | 12px | 计数、时间、快捷键、徽标、表单辅助说明 |
| `--fs-meta` | 13px | 工具摘要、菜单说明、代码块标题栏、面板分区标题 |
| `--fs-ui` | 14px | 侧栏会话、按钮、菜单项、输入框控件、页头 |
| `--fs-body` | 16px | 消息正文、输入框、搜索输入、Markdown h3/h4 |
| `--fs-lead` | 18px | Markdown h2、品牌字、统计数字 |
| `--fs-heading` | 20px | Markdown h1、大统计数字 |
| `--fs-display` | 28px | 欢迎标题、上下文总量 |
| `--fs-icon` | 20px | 以字符绘制的图标按钮（×、⋯、‹） |

相对单位`em`/`%`仍允许（行内代码、表格随所在正文缩放）。

| 类别 | Token |
|---|---|
| 字重 | `--fw-regular` 400、`--fw-medium` 500、`--fw-semibold` 600；强调只用600，层级主要靠字号/颜色 |
| 行高 | `--lh-tight` 1.3（标题/大数字）、`--lh-ui` 1.5（短界面文本）、`--lh-relaxed` 1.65（多行说明、代码）、`--lh-body` 1.75（消息正文） |
| 圆角 | `--r-mark` 2px（图表标记/条）、`--r-xs` 6px（行内代码、kbd、徽标、小按钮）、`--r-sm` 8px（图标按钮、菜单项、输入框、普通按钮）、`--r-md` 12px（侧栏行、搜索结果行、小型浮出菜单）、`--r-lg` 16px（卡片：工具、代码、表格、审批、待发送、通知、弹出面板、对话框）、`--r-xl` 24px（输入框、用户气泡）、`--r-full` 999px（圆形与胶囊） |
| 等宽 | `--font-mono`：ui-monospace优先的系统等宽栈 |

## 4. 映射规则

- 字号：10/11/12px→caption，13→meta，14→ui，15/16→body，17/19→lead；字符图标按钮20/22/26px→icon；统计数字20/22→lead/heading；30/32→display。移动端`.message`15px与欢迎标题25px覆盖删除，正文保持16px（与ChatGPT移动端一致），28px标题在375px宽度下不换行。
- 字重：550→500，650→600。
- 行高：消息正文→body；1.5→ui；1.6–2.1的多行说明→relaxed（上下文面板子项原2.1靠行高撑间距，改为relaxed加2px上下内边距）。
- 圆角：按第3节角色映射；工具卡由12px升为16px与代码块一致；`approvalSubmit`与“回到最新”为胶囊。
- `--faint`只用于禁用态、占位符和装饰字符；用作可读文字的位置（输入框底部提示、技能来源、指令快捷键）改为`--muted`。

## 5. 由刻度直接决定的少量样式

1. Markdown：h1 20/h2 18/h3–h4 16，均600、行高tight、上1.4em下.5em；ul/ol缩进1.5em、li间距.25em；blockquote左侧2px线、次要色；hr细线。
2. 表格：外框16px圆角，仅保留行间横线和表头底色，与代码块同一容器语言。
3. `.replyDivider`保留元素，去掉可见线，只保留间距。
4. “回到最新”：块级`margin-inline:auto`在消息列内居中，仍为sticky底部。
5. 侧栏新建：去掉边框、与搜索同一行高/字号，减少上下留白；收起态尺寸规则保持。
6. 页头模型按钮：模型名ui、推理档位meta，与同一按钮基线对齐。

## 6. 调用链与影响文件

纯CSS：`main.tsx`导入`theme.css`→`global.css`；各组件import自己的`*.module.css`。token为`:root`自定义属性，模块CSS用`var()`引用，无运行时JS参与，失败场景只有“引用了未定义token”（回退为初始值，视觉错误但无异常），由测试防止。

| 文件 | 变化 |
|---|---|
| `frontend/src/theme.css` | 新增非颜色token块 |
| `frontend/src/App.module.css` | 替换刻度；Markdown/表格/分隔线/回到最新/新建按钮 |
| `frontend/src/components/*.module.css`（14个） | 替换刻度；faint文字改muted |
| 新增`frontend/src/design-tokens.test.ts` | 约束测试 |

## 7. 测试与验收

TDD：先写`design-tokens.test.ts`，以`import.meta.glob('?raw')`读取除`theme.css`外全部CSS，去掉注释后检查：

- `font-size`只能是`var(--fs-*)`、`inherit`或em/%；`font-weight`只能是`var(--fw-*)`或`inherit`；`line-height`只能是`var(--lh-*)`或`inherit`；`border-radius`各分量只能是`var(--r-*)`、`0`或`inherit`。
- 模块中引用的每个`--fs/--fw/--lh/--r/--font-*`都在`theme.css`定义；`--fs-caption`不小于12px。

预期先红（现存约150处字面值），实现后转绿。之后运行：

- `npm --prefix frontend test -- --run`（全部前端测试）
- `npm --prefix frontend run build`（tsc + vite）
- 离线演示服务+headless Chrome：1440/375、浅/暗、含工具调用/代码块/表格/标题的会话截图前后对比

不调用真实供应商，不改配置/数据库，不提交。

## 8. 风险

- 另一会话正在改TSX：本片不改TSX和类名，冲突面仅限CSS；若该会话同时新增CSS，需同样遵守本测试。
- 字号整体变大（10/11→12、组件控件12→13/14）可能让窄屏控件换行，以375px截图核对；必要时只调间距，不回退到小于12px。
- 无像素级视觉回归工具；截图对比是人工证据，不等同于跨浏览器/Windows原机验收。

## 9. 实际实现与验收（2026-10-18）

- TDD：`design-tokens.test.ts`先红（294处字面值、刻度token缺失）；首次红灯因vitest替换CSS导入（`?raw`也返回非字符串）属错误原因，改为用`import.meta.glob`取路径、`node:fs`读源文件后得到正确红灯。实现后3项转绿。
- 结果：组件CSS的字号由18种收为8个token加em相对值；字重3档；行高4档；圆角7档；`theme.css`新增24个非颜色token。16个CSS文件，+243/−209行；TSX/TS生产文件零改动。
- 由刻度决定的样式：Markdown h1–h4/列表/引用/分隔线；表格16px圆角外框、仅行间横线；`.replyDivider`去线；“回到最新”按消息列居中；侧栏新建/搜索同为14px无边框行，会话行9px内边距、组标题上移留白；页头模型名14px/档位13px；按钮与菜单项统一14px（紧凑按钮13px）；可读文字不再使用`--faint`。
- 验证：`npm --prefix frontend test -- --run` 39文件288项通过；`npm --prefix frontend run build`（tsc+vite）526模块成功；`git diff --check`通过。离线假模型演示服务+headless Chrome在1440浅/暗与390移动端截图前后对比，标题、表格、代码块、工具卡、侧栏、输入框与暗色均按预期；未做Windows原机与跨浏览器像素验收。
- 并行改动：实施期间另一会话持续修改Conversation/ToolCall/useConversation等TSX（截图中的“已有结果”“重新生成”来自该改动）；本片未触碰这些文件。未提交、未调用真实供应商、未改配置或数据库。
- 后续候选（未实施）：复制/重新生成改为图标按钮、页头/审批卡布局、`--faint`作为占位符的对比度复核、字体族选择。


## 10. 合并提交前复验

用户要求与Spec111/112一起提交push。本轮保留全部已确认的样式调整，当前39文件289项前端测试通过，TypeScript/Vite构建成功；1440px浅/深色与375px生产构建临时页面已核对工具差异、思考、代码块、会话栏和输入框，窄屏scrollWidth/innerWidth=375/375，控制台warn/error为空。截图`/tmp/naiyue-ship-desktop.png`、`/tmp/naiyue-ship-dark.png`、`/tmp/naiyue-ship-mobile.png`。临时页面/服务/工作区已清理，viewport复位；0次真实供应商。架构/安全专项没有确认样式阻塞，分隔线去除属于本Spec已批准格式，不恢复旧视觉。
