# Spec126-04：子实例续跑组合验收与标准交付

状态：2026-10-22已完成本机验收；承接01—03，不增加子Agent产品功能、直聊、后台或多层。

当前链：verify_wheel_install在临时venv/受限PATH/源码外目录运行release_smoke；后者沿已存在共享worker调用MCP、存储与后台验收。它尚未验证安装包按ID续跑，父进程也尚未核对该专用报告。现有01—03测试分别覆盖准入、每次计数、审批/异常、崩溃、压缩/退役/备份及界面迟到隔离，本片不复制这些产品实现。

正常链：源内pytest及源码外release_smoke共同调用新scripts/release_subagent_smoke.subagent_scenario，使用调用方固定模型类、临时工作区/真Write/SQLite和正式图。新建→同ID第二次委派→list查找→正常关闭重开（模型/Write/Auto/driver均0）→再次list→同ID第三次委派→省略ID创建全新实例。每次子委派工具调用+最终报告共2次模型调用，角色max_model_calls=2；记录真实图thread/head/计数、历史前缀/报告、Write调用和文件内容。三次续跑同thread不同委派/head，新实例独立历史，旧工具不重放。

失败链：每个必要断言和验证器失败直接向外传播；verify_subagent_report对缺失项、错误ID/thread/报告前缀、重复委派/head、模型/Write次数、恢复副作用和缺失list验证均拒绝。release_smoke调用后验证，verify_wheel_install解析子进程JSON后再次校验，失败不得写成功报告。验证器不改变产品成功/权限/保存语义。测试注入篡改报告、漏子报告和真实worker检查失败，确保门禁有效。

新增模块与消费者：release_subagent_smoke只承载本片共享场景和专用报告检查，源内测试/release_smoke/verify_wheel_install使用；不打入产品wheel、不导入tests，不新建运行服务。benchmark_subagents是一次性CLI，仅临时数据；复用现有distribution和固定模型工具链，分别记录新建/同ID续跑、SQLite元数据读取+完整列表JSON的P50/P95/原始样本。小规模真实图和较多已保存实例的合成元数据明确区分；合成数据不用于证明可执行准入。测量不设机器相关通过阈值。

改动范围：上述两脚本、release_smoke/verify_wheel_install的既有接线、test_release_subagent_smoke与test_subagent_benchmark；如真实缺口要求产品修复，先用失败测试证明并更新本Spec，不顺便重构。README/web-chat、主Spec/文件地图/交接/待办同步完成范围，086/095仅加当前入口说明，课堂/记忆研究/真实配置/用户库/依赖/版本保持。

验证：先确认新增行为红灯，再最小实现/专项；全后端、全前端与TS/Vite、AST/compile/500行/pip/diff；统一前端产物和同一源码四目标标准wheel、Twine/字节/指纹核对，Mac ARM64源码外干净安装含专用worker。真实浏览器补同ID多轮、重开历史、当前停止/审批组合与375px，无真实供应商；临时页签/服务收尾。独立架构/安全深度复核和主审对抗检查；最后保存实际结果与原始样本，更新handoff唯一下一步。本片不提交/push/发布。

## 实际实现、消费者与保留边界

- scripts/release_subagent_smoke.py包含subagent_scenario与verify_subagent_report；模型类由source test或release_smoke提供，不导入tests。正式父调用→subagent工具→会话准入/共享角色图→独立thread的Write→ToolMessage/最终报告→原父事务确认ready，三次均记录真实head/委派/计数与文件。报告前缀与原message ID序列保留；fresh独立。max_model_calls=2下每次实际2次，生命周期共6次仍可完成第三次，未把累计上限引入产品。
- 正常关闭重开只恢复/读取，模型/Write/Auto/driver/待审批均0，档案message ID逐项相等。两次list均得到原ID/可继续候选且子模型增量0；它不是下一次续跑许可。
- release_smoke调用共享worker，verify_wheel_install在写成功结果前重验专用报告。故意缺失/篡改thread、模型调用数或省略场景时，实际installer.main测试均拒绝且output不存在；checker故障也向调用方传播，没有被观察隔离吞掉。
- 本片未增加或删除产品运行入口。之前的纯历史subagent_records现在仍由chat_queries/chat_history/reply_storage的legacy/归档/分支退役消费；它不替代可信实例登记。prepare_subagent_instance已按agent_id与完整许可核对，_finish_subagent_result按本次seed_id计算usage，ToolResult消费ID信封；前端旧parent_batch保留只用于当前审批恢复，新message已重置。共享角色图、必要保存、内部审批Command、同步短IO/锁与SDK线程回退仍有实际调用方，均保留，不因名字含sync删除。
- 新benchmark_subagents仅在临时目录生成数据。实际固定模型graph与owner.alist_subagents原链分开测量；较大规模登记为明确标记的合成failed记录，无child checkpoint，不拿它证明执行准入。默认3次warmup后120次，纯元数据每规模120次，原始样本保存，无性能通过阈值。

## 实际验证

证据：build/spec126-04-gz9tql9c。新增18后端用例。首次红灯为4 failed/11 errors：缺两个模块及installer接受缺失报告；最小实现后专项15通过，再扩展3项installer篡改拒绝用例；最终全量包含全部18项。本片未改测试基础设施、产品配置/依赖/版本/真实库。

- `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests --tb=short`：**4223 passed in293.73s**，backend.log。
- `npm --prefix frontend test -- --run`：**50文件415 passed**，frontend.log；`npm --prefix frontend run build`：TS/Vite成功，保留已有>500kB chunk提示，frontend-build.log。
- Python AST/compileall、182产品+43脚本+123前端源≤500行、pip check、git diff --check通过；产品/前端源相对本片开始未变。
- `.venv/bin/python -m scripts.build_release --target TARGET --frontend-assets build/spec126-04-gz9tql9c/frontend-assets --output build/spec126-04-gz9tql9c/dist`：macos-arm64、linux-x86_64、linux-arm64、windows-x86_64四个目标成功，Twine、inspect_release完整矩阵/同源码及182份Python/共享前端/README字节核对通过；worker不进入产品wheel。application_sha256统一为fab68867c391785a1871c0d13b5edc6c0380fe08c9e171bba0888934b8d1bb8a，详见wheels.jsonl。
- `.venv/bin/python -m scripts.verify_wheel_install --wheel build/spec126-04-gz9tql9c/dist/naiyue_test_agent-0.2.0-py3-none-macosx_11_0_arm64.whl --output build/spec126-04-gz9tql9c/install.json`：源码外干净安装全部门禁及专用子续跑通过，报告root模型12/child模型8、真实Write4，恢复阶段全部0。mac wheel SHA-256为5f7f28d3885a96b0033bf7df8017e761df57f304cf0dc7fedef72028b1e1397c，0供应商。
- 独立架构/安全深度复核无高置信新增发现；主审对抗检查覆盖缺/坏报告、不同thread/ID、重复head/委派、旧前缀、恢复副作用、安装导入边界与合成元数据不执行，未发现需新增产品修复的证据。

真实浏览器沿临时正式Web/SQLite/固定模型：首次子read Ask→用户批准→第二次同ID Ask批准→两报告→关闭重开/查看历史→第三次同ID等待审批→375px“到主会话处理”收起覆盖侧栏→停止当前审批绑定子执行，read未执行、原两报告保留、候选提示已停止。存储核对同一实例，模型root6/child5，tool结果success/success/error。375px scrollWidth=375，正常服务阶段无warn/error；有意关闭旧服务等待重开期间有12条background_task_list_failed诊断，不误报控制台全程0。原端口短时占用后改新临时端口重开，无产品修改。ui-result.json和四张截图保留；页签/服务关闭、端口回收、viewport复位。

## 耗时结果与适用范围

命令：`.venv/bin/python -m scripts.benchmark_subagents --output build/spec126-04-gz9tql9c/benchmark.json`。在全量测试/构建/安装与临时UI结束后单独运行；macOS26.4 ARM64、Python3.12.14，固定模型0供应商；原始样本、实际调用数及环境见benchmark.json。

| 场景 | 样本 | P50 ms | P95 ms | 最大 ms |
| --- | --- | --- | --- | --- |
| 每次新建子实例，完整父→子→父及保存 | 120 | 278.33 | 542.21 | 620.01 |
| 同ID连续续跑，完整父→子→父及保存 | 120 | 337.13 | 659.47 | 731.56 |
| 元数据列表，0条合成记录 | 120 | 0.18 | 0.20 | 1.09 |
| 元数据列表，100条合成failed记录 | 120 | 1.13 | 1.18 | 1.26 |
| 元数据列表，1000条合成failed记录 | 120 | 8.77 | 8.94 | 39.23 |

两种完整路径各3次warmup+120次测量，均实际root246/child123，没有摘要或供应商网络。连续轮次中父历史增长，续跑的子历史也增长，P95之差不能解释为纯准入开销；含原checkpoint/档案/诊断等全部本地运行保存。列表是同一owner.alist_subagents的数据库读取、候选与完整JSON输出，未读子正文，模型增量均0；1000条是无正文的合成失败登记，不代表1000个真实大档案或所有状态。列表仅按需调用，不新增每轮自动扫描。长历史下完整执行耗时增长已记录，本片不据此擅自引入缓存或存储重构，也不作跨机器/真实业务SLA声明。

## 收尾与下一步

Spec126四片完成本机功能与标准交付闭环，当前未提交/push/发布，保留并行学习与记忆研究；不新增用户掌握声明。Linux/Windows本轮为构建证据，其他原机、真实供应商业务效果与远端CI另验。用户后续调整：后台子Agent保留远期候选、不占近期顺位，当前可新建会话并行规划其他测试。长期与跨会话记忆由原会话继续规划；不自动指定下一实施项。用户直聊保持取消，会话分叉独立。
