# Spec143BE 阶段 4A：主对话阅读区整理、验收与性能基线

## 当前状态（2026-11-08）

**验收通过，阶段收束并归档。** 独立技术复核已通过，视觉整理、行为验收和最小性能基线的交付证据保留。所有改动仍在 `frontend-ui-refactor` 工作区，未提交、未推送；其他并行未提交工作保持。本阶段不再增加实现，不继续扩展菜单、输入框、侧栏或其他重构切片。

## 现状与生产调用链

- **阅读区**：`Conversation.tsx` 持有滚动/锚点和完整内容缓存，配对工具结果并按 `reply_id` 分组；正文使用 memo 的 `MessageMarkdown`，代码由 `CodeBlock` 异步高亮并带降级，表格已有横向滚动。
- **数据**：`useConversation` → `api.prepare(SessionView)` → `subscribeRun`（RunView → SSE）→ `applyEvent/update`。历史分页由 `history.loadOlder` 读取；本轮没有改变这些生产职责、HTTP/SSE 字段或业务状态合同。
- **工具卡**：`ToolCall` 根据 `tool_states` 展示运行阶段、耗时和输出尾部，由 `ToolResult` 分派普通、MCP、子智能体、FileChange 和 Edit 差异结果。

## 已有能力与本轮实际改动

### 此前已有，本轮只保留并验证

- GFM、代码高亮/降级、表格横向滚动、消息身份/配对/分组、历史加载和滚动锚点。
- 消息间距 32→28px、可见回复组分隔线、详情标签层级化、状态右置强调。
- MCP 等 `.result > pre` 的 320px 候选上限；普通结果当时仍直接返回 `<pre>`，不能与 MCP 混称都已覆盖。
- 真实主题偏好入口 `ThemeMenu` 及 `useTheme` 的 `data-theme`/localStorage 持久化。

### 本轮补齐

- **测试接线**：`tsconfig.harness.json` 明确覆盖 harness、fixtures 和本轮 `reading-area` / `reading-visual` / `reading-perf` 三份 spec；修正 `openConversation` 参数并提取共用读取页入口。删除 `useConversation.ts` 临时 `[dbg]` 日志。
- **流式 fixture**：独立 `node:http` 服务提供真实 HTTP/SSE；首批自动到达，后续批次由测试显式推进，全部批次前禁止结束。补齐 `message_completed`、最终 RunView、完成 `tool_states` 和 Session 快照，测试同时断言完整回答、最终响应体及离开后重选仍存在。
- **工具卡 CSS**：摘要 `align-items: baseline`，最小高度 40px；运行中与完成后的阶段/耗时注释缩进统一为 32px。
- **普通结果分支**：用既有 `.result` 容器包裹普通 `<pre>`，使普通与 MCP 长输出都实际受 320px `max-height`、纵向滚动和 `overscroll-behavior: contain` 约束。参数区原有 300px 规则保持，不虚构“底部渐隐”或 grid 重构。
- **视觉验收**：真实菜单设置明暗主题，断言主题值、localStorage 和行配色；同 fixture、视口、展开调用及摘要距滚动区顶部 96px 的可识别锚点。覆盖桌面/375px、普通/MCP 长输出和运行中阶段/耗时，不使用固定等待，服务在成功和失败路径均清理。
- **性能基线**：增加标准 `vite preview` 入口；生产构建和独立 HTTP/SSE 服务下，历史加载、工具展开、12 批流式/完成、会话切换各采样三次。保留 Node 操作耗时、页面内浏览器时钟、逐批原始值及 long task 数据。

## CSS 与行为核对结果

| 项目 | 实测结果 |
|---|---|
| 摘要基线 | 计算样式 `align-items: baseline` |
| 摘要最小高度 | 计算样式 `min-height: 40px`，实际高度 ≥40px |
| 阶段/耗时缩进 | 运行中与完成后的计算样式均为 `padding-left: 32px` |
| 普通结果 | `max-height: 320px`、`overflow-y: auto`，真实 wheel 后 `scrollTop > 0`，尾部标记可达 |
| MCP 结果 | 与普通结果分别走真实分支并做相同的计算样式、滚动和尾部可达断言 |
| 375px 调整 | 两类工具卡仍为 `open`，结果可见且尾部可达，文档横向宽度 ≤375px |
| 流式结束 | 首批在未结束时可见；显式推进全部 8 批后完成，最终消息不是 draft/unconfirmed，重选会话仍完整 |

新增断言先在旧实现上得到正确红灯：普通结果为 300px、摘要为 `center`；最小修复后转绿。流式完整链在原 headless Chromium 通过，没有有头环境前提，也没有修改产品会话/SSE 逻辑迁就 fixture。

## 有效截图与对照条件

`build/143be/before/` 与 `build/143be/after/` 各 8 张：

- `desktop-light.png` / `desktop-dark.png` / `mobile-light.png` / `mobile-dark.png`：完成态普通长输出。
- `desktop-light-mcp.png` / `desktop-dark-mcp.png`：完成态 MCP 长输出。
- `desktop-light-running.png` / `mobile-dark-running.png`：运行中阶段、已历时/距最后活动及输出尾部。

before 使用当前工作区的 `/tmp` 隔离副本，仅将 `App.module.css`、`ToolCall.module.css`、`ToolResult.module.css`、`ToolResult.tsx` 恢复为 HEAD 版本；after 使用当前代码。两边运行同一份本轮 spec 和 fixture，临时副本及测试服务已删除/关闭。

[capture-comparison.json](../../build/143be/capture-comparison.json) 校验 8 对截图的场景、主题、视口、展开调用及 `summaryTop=96` 一致。普通/MCP 结果计算上限分别为 before 300px、after 320px。完成态加载 56 条较早历史后，前后绝对 `scrollTop` 相差 224px，对应 56×4px 的消息间距变化；对照使用相同摘要锚点，不错误要求绝对滚动值相等。两份 `capture-manifest.json` 保留每张的实际值。

截图命令（after；before 使用上述隔离副本和 `READING_EXTERNAL_SERVER=1`）：

```bash
CAPTURE_DIR=/Users/zmw/naiyue-test-agent/build/143be/after \
  npm --prefix frontend run test:browser -- e2e/reading-visual.spec.ts --project=desktop
# 8 passed
```

## 最小可信性能基线

原始产物：[perf.json](../../build/143be/perf.json)，`status=complete`，不是未执行脚本或优化宣称。

- Chromium **158.0.8127.18**，原 headless，1440×900；生产 `vite build` + 独立 `vite preview`，独立随机端口 HTTP/SSE 服务。
- 每轮使用全新 BrowserContext/Page，避免重复导航沿用旧端口和 init script；冷浏览器存储。
- 数据：120 条历史，首次 1 条、分页 30 条；普通结果 49,020 字符，MCP 47,615 字符；12 批流式共 2,442 字符；切换目标 1 条确定消息。
- `nodeMs`：Playwright 点击/推进前至确定 DOM 断言；流式收尾还包括最终 RunView/Session 响应。
- `browserRenderMs`：真实 click 捕获阶段（流式为推进前）至目标 DOM 条件及两次 `requestAnimationFrame` 完成。它与 Node 起止点不同，尤其流式还包含 SSE 到达，不能直接作差解释为“纯渲染耗时”。

| 场景 | Node 中位数（范围），ms | 浏览器时钟中位数（范围），ms |
|---|---:|---:|
| 历史加载至 `m-089` 渲染 | 59（56–62） | 47.8（47.7–47.9） |
| 工具卡展开 | 36（35–36） | 31.9（31.6–32.2） |
| 流式首批 | 4（4–5） | 25.9（23.7–27.6） |
| 后续批次：每轮批内中位数 | 3（3–4） | 30.8（30.7–30.8） |
| 流式完成与最终快照 | 13（13–13） | 30.6（30.6–30.8） |
| 会话切换至目标新消息 | 58（56–59） | 48.1（47.9–48.2） |

PerformanceObserver 在本轮测量动作中记录到 **0 个 ≥50ms long task**；每个动作的实际 `longTasks: []` 已写入原始产物。首轮重复导航使用旧服务端口，第二轮会话按钮触发器误按可见文本查找，分别通过全新上下文和真实 `aria-label` 修正；第三次完成。未将问题归因于浏览器模式。

执行命令：

```bash
npm --prefix frontend run build
READING_PROD=1 npm --prefix frontend run test:browser -- \
  e2e/reading-perf.spec.ts --project=desktop
# 1 passed；perf.json 已生成并校验四场景各 3 轮、流式每轮 12 批
```

## 完整验证

```bash
npm --prefix frontend test -- --run
# Test Files 78 passed；Tests 664 passed

npm --prefix frontend run test:browser
# 69 passed，0 failed；91 skipped（布局限定、显式截图、生产性能条件）

npm --prefix frontend run typecheck:harness
# passed

npm --prefix frontend run build
# 449 modules；passed；主 JS 806.62kB，gzip 248.57kB；既有大 chunk 警告

git diff --check
# passed
```

类型检查精确覆盖本轮三份 spec、fixtures 和 harness，不声称所有旧 E2E 都已纳入。旧 `scroll-control.spec.ts` 的 3 个 null 类型错误已移入[独立待办](../future-work.md)，不并入本阶段验收或归档范围；其浏览器用例在上述完整回归中通过。

## 剩余边界

- 截图和数字来自本机确定性 fixture，不替代真实后端、真实模型、Windows 或生产业务效果/SLO。
- 本阶段建立性能基线，未实施或宣称性能优化；后续比较必须复用同数据、浏览器、视口、构建和起止定义。
- 未改后端、组件库、HTTP/SSE 或业务状态合同；未提交、未推送。阶段已收束并归档，不再增加本阶段实现。
