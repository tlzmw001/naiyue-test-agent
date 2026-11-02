# Spec143Y：云端任务控件与主会话对齐

2026-11-02，用户要求解释释放/删除与草稿位置，并对齐复制按钮、模型菜单和推理强度。本片是S06.4补齐，不进入S07.2，不提交/部署，不修改真实配置与用户数据。

## 1. 当前数据流与问题

释放只停止容器并保留任务持久目录/工作区/历史；删除经过固定预览、停止证明和墓碑清理任务数据。草稿保存在本机项目库naiyue_cloud_bindings.body_json，未显式保存的编辑仅缓存到当前标签页sessionStorage；不是Coordinator草稿，不构成跨设备/关闭标签页保留承诺。依赖能否跨环境保留取决于是否装在持久目录，容器临时层/进程不保留。

主会话使用ModelMenu＋ModelCatalog.thinking_options/with_thinking＋AgentSession.select_thinking。云端仅保存model_id，RunCommand/StartRun/ManualMessage/TaskDraft均缺thinking_level。侧栏.detail button规则还覆盖了Conversation已有copyButton。不能只加推理强度UI而不传到实际模型请求。

## 2. 最小实现与接口

- 复用ModelMenu原组件、CSS与现有默认/档位语义；增加可选above位置用于底部弹层，主界面默认位置不变。切模型重置model_default；选强度只改本任务草稿，下一次发送生效，运行中不修改已接管命令/主聊天。候选UI仍使用远端模型目录，不混入本机模型。
- 缩窄CloudTasks通用button CSS到自有控件，Conversation与ModelMenu继承原样式；说明保存草稿的位置，删除/释放按钮行为不改。
- 所有者认证GET /api/cloud/v1/tasks/{task_id}/models返回公开default_model_id/model_ids/thinking_options，来源为Coordinator已解析的同一模型profile，过滤当前Runner已公开model_ids；不加载SDK或提供完整configuration/密钥/上游URL。原profile不匹配拒绝，未配置档位返回真实空能力。
- 本机固定binding下GET /models复用现有认证/project/session/instance与view白名单；新增CloudApi.models。选项读取失败可见并禁止显式档位选择，不借本机能力补假数据。
- ManualMessage、TaskDraft、StartRun、RunCommand新增thinking_level，值为model_default或既有THINKING_LEVELS，默认model_default。沿草稿CAS→固定命令请求/原ID指纹→owner入站校验→持久回执/WS→Runner journal全链路携带；回执核对同时核对thinking_level，草稿匹配/ACK清理包含该值，防旧ACK清掉新档位。
- Coordinator用同一已解析profile校验支持档位，Runner在admit前调用catalog.with_thinking校验，在原_run先select_model后select_thinking再arun；复用现有会话持久化和模型中间件，不能修改共享SDK对象或创建平行模型适配逻辑。显式恢复沿原会话checkpoint，不因草稿菜单变化重写原执行选择。

## 3. 正常与失败链

正常：CloudTaskDetail读取远端公开能力→ModelMenu选择model/level→useCloudDraft按独立版本保存→ManualMessage冻结model/level/text→command_dispatch(StartRun)→RunnerRuns持久RunCommand→RunnerKernel校验/设置会话选择→既有middleware/catalog生成本轮模型视图。实际模型适配器收到所选推理设置，主会话与其他任务不变。

失败：能力读取失败保留草稿并提示；不支持的档位在owner准入前拒绝，不创建运行回执，Runner也独立拒绝且不污染journal；已受理后HTTP丢失只核对原ID/同一model/level。迟到草稿读取/旧ACK不能覆盖用户后来选择的档位。切模型重置强度且同步缓存/服务端；草稿恢复与发送后的版本行为同原CAS合同。

## 4. 变更与验证

影响ModelMenu、CloudTasks CSS/Detail、cloudApi/types/useCloudDraft，本机draft/journal/dispatch/DTO，owner models/runner_runs/routes，Runner kernel及对应测试。无新依赖、无新DB表/迁移、无新锁，沿原短事务和任务gate，不跨IO持锁。

先补红灯：选项认证/过滤与不泄密、不支持档位拒绝、run请求/回执/草稿固定档位、Runner实际会话模型视图、模型切换重置/草稿隔离。浏览器比较主/云复制按钮计算样式，桌面和375px检查同一ModelMenu打开、强度选项、Esc/焦点及底部弹层完整可见。最后后端/前端全量、构建、diff check及必要包源核对；独立架构/安全复核。测试注入沿现有fixture，新增能力时只补真实协议字段，不放宽断言。

## 5. 已完成与验证（2026-11-02）

复制按钮污染已通过缩窄侧栏按钮选择器修正；底部复用主会话ModelMenu，以above定位显示同一模型/推理强度菜单。读取远端公开目录、模型切换重置默认、档位写本机草稿并冻结到run/receipt，真实Runner执行中使用已有会话选择和模型中间件。控制端默认档位也先核对profile及模型白名单；确定拒绝不会变成unknown阻塞后续新消息。草稿位置提示已接线。

验证：

- `.venv/bin/python -m pytest -q tests/cloud/test_task_thinking.py`：9 passed，包含实际Coordinator/Runner/native SDK到临时loopback HTTP的high/default→high/low请求检查；无真实供应商请求。
- `PATH="$PWD/build/spec143-s05-20261102/test-assets/bin:$PATH" .venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/spec143-s064-controls-20261102/backend-final-0.xml`及index1：3255＋3502＝6757 passed、无失败/跳过，原6748保留；两项旧随机UUID参数节点沿143x按源字节核对后归一后缀比较。
- `npm --prefix frontend test -- --run`：584 passed（原583保留，新增菜单/档位草稿一项）；默认/候选构建均退出0。候选固定独立outDir，防浏览器fixture被后续默认构建覆盖；没有修改真实运行配置。
- `node build/spec143-s064-controls-20261102/browser_probe.mjs`：复制样式逐项相同，桌面及375px模型菜单完整可见，选择high/保存/发送/草稿隔离、Esc与返回焦点通过。使用真实本机HTTP、受控owner传输和本机确定性模型，非Docker/公司业务验收。
- 四目标标准wheel/Twine、Mac源外干净安装/产品smoke通过；499个生产Python源逐包字节一致；pip check/compileall/diff check退出0。provider_requests=0，无依赖升级或真实数据操作。

[完整验证](../../build/spec143-s064-controls-20261102/verification.json)、[复核](../../build/spec143-s064-controls-20261102/review.md)、[复制样式](../../build/spec143-s064-controls-20261102/copy-style.json)、[桌面对照](../../build/spec143-s064-controls-20261102/desktop-task-error.png)、[375px菜单](../../build/spec143-s064-controls-20261102/mobile-model-thinking.png)。不保持旧B Runner线协议兼容；尚未部署，本轮端到端源码测试不替代目标镜像重建/实机门禁。Spec142和上轮脏改动保留，本轮未提交。S06.4补齐不新增分片，15单元完成7项、剩余8项，下一片仍为S07.2，无新增用户掌握记录。
