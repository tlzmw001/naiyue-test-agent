# R1-13D-01：多环境配置与冻结保存合同

版本边界：本文记录01的范围和当时验收。后续固定配置路径热加载、指纹核对及显式API/CLI选择已由[13D-02](R1-13D-02-environment-refresh-selection.md)接入，以下“尚未提供”均指01完成时点。

2026-10-25 用户授权：先提交并 push 当前实现，再实施本片，完成后讲解第二片。本片基线 ce617d0，已 push 到 codex/project-execution-root。真实配置、用户库、Docker 资源及 research 原始资料不修改；本片完成后不自动提交/push。

## 1. 目标和五片边界

可复用环境模板定义镜像；每个沙箱会话仍独占原容器、代码卷及状态卷。本组分片：

1. 本片：配置列表、默认选择、冻结配置保存、旧记录处理，接现有默认创建路径。
2. 热加载接口、配置指纹核对、API/CLI 显式选择；从服务固定的 settings_path 只读重载 sandbox，成功原子替换，失败保留旧配置，不重启服务。
3. 输入框上方的选择/刷新及首次发送准备流程，显示实际绑定，失败保留草稿。
4. 运行/保留数量限制、空闲停止、正常退出停止容器、管理和显式销毁。建议初值 2/10/1800 秒，实施前按本片已确认边界细化；默认不按时间删除用户文件。
5. 两套真实镜像、热更新、并发额度、恢复与交付验收。

SSH14仍暂缓。本片不提供刷新按钮、热加载 HTTP、显式 profile_id 请求、生命周期额度或自动清理，不把基础合同误报为完整功能。

## 2. 当前链与改动原因

AppSettings.sandbox 的单一 image → EnvironmentAPI → EnvironmentService.settings → create 时 inspect 镜像并保存实际 image_id 和扁平 settings。会话准备记录只存模型/输入/绑定 ID。若只增加配置列表，创建重试或尚未建容器的准备会重新读取当前配置，可能换镜像。

新链：

```text
settings.toml 的模板与共用策略
  → SandboxConfiguration（只读列表，校验默认项；加载不访问 Docker）
  → 默认模板选择及 SandboxProfileSelection（指纹＋扁平执行策略）
  → 会话 execution_setup_json.selection，先保存再准备输入
  → InputService 使用该选择的 git 预算
  → EnvironmentService 使用选择的执行策略检查镜像
  → 环境记录保存实际 image_id、执行策略及模板元信息
  → 重试/恢复只读取原快照和原绑定
```

LangChain create_agent 和 run scope 的职责不变；不增加新的工具注入框架。沿用既有 Pydantic 配置、会话 JSON、注册表环境 JSON，无新依赖或 SQL 表。

## 3. 配置接口

```toml
[sandbox]
context = "default"
default_profile = "python-tests"
cpus = 2.0

[sandbox.network]
mode = "external"

[sandbox.profiles.python-tests]
name = "Python 接口测试"
image = "naiyue-python-tests:1.0"

[sandbox.profiles.frontend-tests]
name = "Node 前端测试"
image = "naiyue-frontend-tests:1.0"
```

- 标识为 1..64 个小写字母/数字/横线/下划线，首字符为字母；名称 1..120 字符且非空白、无控制字符。
- 镜像复用既有 SandboxSettings 校验；模板只允许 name/image，不接受任意 Docker 参数。
- 非空列表必须显式指定存在的 default_profile；空列表无默认值时表示仅本机可用。
- 旧 sandbox.image 在配置边界转换成标识 default、名称“默认沙箱”的一项。显式非空 image 与 profiles/default_profile 混用拒绝，不维护两条执行路径。
- 模板解析成 tuple 和冻结对象；序列化回 map，解析/序列化可往返。旧 image 是输入兼容字段，不再作为第二份执行事实。
- SandboxSettings 继续只表示扁平运行策略，环境记录和 envd 不接收模板目录。
- 资源、网络、Docker 目标和 Git 预算暂由模板共用，选择时合成为完整策略快照。

## 4. 保存及恢复合同

SandboxProfileSelection 保存 profile_id、name、revision（规范 JSON 的 SHA-256）和 settings（冻结 SandboxSettings，必须有 image）；加载时校验指纹。SandboxSessionSetup 增加可空 selection：新创建必须写入；空值仅表示旧格式。

SandboxRecord 增加可空 profile_id/profile_name/profile_revision，settings 仍为原扁平格式，image_id 仍为实际检查所得 ID。新会话与注册表记录须匹配同一快照，出现不一致报 env_binding_mismatch。

同 request_id 重试先查原会话，比较模型/输入列表/稳定 ID，复用已保存选择；不读取当前默认模板。修改或删除模板不得影响原会话。镜像标签第一次 inspect 后以实际 image_id 固定，已有环境不重新按标签创建。

旧格式：
- 已有环境记录：用其原 settings/image_id 和已知模板元信息恢复；无模板元信息时标为“旧版沙箱”，不猜属于当前模板。
- 没有环境记录：仍可打开历史；继续准备明确报 env_legacy_setup_unavailable，不使用当前默认镜像、不创建新容器或输入快照。
- 不批量回写旧库、不新增身份文件、不接管仅有容器而无登记事实的资源。

正常例：默认 Python → 会话先保存 Python 快照 → 输入与容器按该快照准备 → 改默认为 Node → 同请求重试仍用 Python，新请求使用 Node。
失败例：旧会话缺选择且无环境登记 → 准备在任何输入/容器创建前拒绝 → 历史和会话记录保留。

## 5. 影响范围与验收

涉及超过8文件：sandbox_profiles、settings、contracts/session_environment、contracts/sandbox、application/environments/service/api/inputs、application/sessions/execution_binding、配置示例、handoff/本Spec及对应测试。SessionService 已达500行，本片不往该文件增加职责。

测试覆盖配置往返/只读/旧格式/混用/未知默认/非法模板、快照指纹和 SQL JSON 往返、默认创建接线、原请求在默认改变或模板删除后不换绑、服务重开、旧记录有/无登记的两条分支。模型与 Docker 用现有 OfflineModel/SandboxTransport，不修改 conftest 或现有测试断言。

命令：带项目原生工具 PATH 的 pytest 新增及沙箱专项，完整后端按独立文件分组运行并核对全量覆盖；前端 vitest、TS/Vite 与标准 build_release/Twine。真实 Docker 留给05。退出错误、配置与执行能力的证据分开报告。

配置回退只影响未来选择，不能覆盖会话已保存事实；新字段为可空 JSON 兼容字段，无 SQL DDL。旧程序不保证理解新 selection 字段，因此不声明新会话可跨版本降级执行。

## 6. 实际验收（2026-10-25）

当前代码已接到 AppSettings → EnvironmentService 默认选择 → SessionSetup 先保存 → 原Git预算/容器策略 → 环境登记 → check_sandbox 一致性核对；新模板目录不进入envd。原请求在输入准备失败、尚未建容器的情况下也复用原选择。旧image已在配置边界转换，不保留第二条创建链；旧记录只读派生，未添加DDL/批量迁移或猜测当前默认。

| 检查 | 实际结果 |
|---|---|
| `pytest -q tests/test_sandbox_profiles.py` 先红 | 5失败/8通过：新配置字段或选择能力不存在；非法配置原来已拒绝 |
| `pytest -q tests/test_sandbox_profile_sessions.py` 先红 | 5失败：配置尚未接入实际默认创建/恢复链 |
| 新增两文件 | 共20项；包括后补的准备失败重试/Git预算和登记元信息校验 |
| `pytest -q tests/test_sandbox_profiles.py tests/test_sandbox_profile_sessions.py tests/test_sandbox_registry.py tests/test_sandbox_review_cases.py tests/test_git_input_service.py tests/test_git_input_install.py tests/test_sandbox_output_cleanup.py tests/test_sandbox_session_http.py` | 54 passed |
| 全量收集及文件分组 | 515文件，5426项；两组互不重叠，覆盖全部收集节点 |
| `python build/r1-13d-01/run_shard.py 0` / `1` | 2690 passed/264.31s；2736 passed/355.85s，合计5426 |
| 前端 | 本轮提交前60文件505项、TS/Vite通过；本片未改前端源码，不重复运行 |
| `python -m scripts.build_release --target macos-arm64 --output build/r1-13d-01/dist` | exit 0；包内342 Python/4 Web/README与当前源码逐字节一致 |
| `python -m twine check build/r1-13d-01/dist/*.whl` | PASSED |
| `python -m scripts.verify_wheel_install --wheel-dir build/r1-13d-01/dist --output build/r1-13d-01/install.json` | 源码目录外全新安装/产品smoke通过，0供应商请求 |
| 定向架构/安全审阅、`git diff --check`、文件行数 | 无剩余阻塞、无空白错误，新改Python文件均不超500行 |

运行Python使用 `.venv/bin/python`；测试PATH前置 `build/pre-profiles-push/test-assets/bin`，与CI先准备原生工具的方式一致，不改真实PATH配置。日志、精确节点清单、分组启动器、包/安装结果和源码指纹在 `build/r1-13d-01/`，为忽略的本机证据。Docker使用现有受控SandboxTransport，真实Docker/远端仍留在05/14，不据本片宣称隔离或公司环境验证。

当前基线 `ce617d0` 已push；本片未提交。02只接服务端热加载/显式选择及CLI，不提前实施03的界面或04的回收策略。
