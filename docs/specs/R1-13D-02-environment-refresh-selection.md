# R1-13D-02：环境热加载与 API / CLI 选择

版本边界：本文记录02完成时点；输入框上的模板选择/刷新和首次发送流程现已由[13D-03](R1-13D-03-environment-composer.md)实施，以下03待办表述为历史范围。

用户“继续吧”授权本片；承接01，不提交/push，不修改真实配置、用户库或Docker资源。03界面、04额度/回收、05真实镜像验收仍未实施。

## 当前链与目标

01已将默认环境选择保存到会话，再准备输入和容器；原请求重试读取保存事实。本片使服务无需重启即可刷新模板，并在首次创建时明确选择。LangChain运行、工具和资源生命周期不变。

正常链：CLI或下一片UI读取项目execution-options → 获得模板ID/名称/指纹 → POST sessions携带profile_id和expected_profile_revision → 历史锁内先查原请求 → 新请求在当前不可变目录中核对指纹 → 保存请求与选择 → 原输入/容器准备链。

失败链：文件无效 → refresh返回env_configuration_invalid，旧目录不变；选择已变 → env_profile_changed，未保存会话、未创建输入/容器；同请求重试 → 比较原模型/输入/显式选择引用，复用原快照；同ID换模板或指纹 → env_operation_conflict。模板删除不破坏原请求重试或已建环境。

## 接口与并发

- `POST /api/v1/environments/refresh`：无正文或空对象；拒绝其他字段，包括路径。服务适配器只传启动时固定的settings_path，读取只解析sandbox节。文件缺失/读失败/TOML或字段无效，安全日志只记异常类型。成功返回 `{default_profile, profiles:[{profile_id,name,revision}]}`，不暴露Docker路径或凭据，不探测Docker、不拉取镜像。
- EnvironmentAPI的refresh锁只串行刷新；线程读取/校验完成后在拥有者事件循环一次替换configuration。取消仍排空线程，不让旧读取晚到覆盖后刷新。不锁住运行、历史或停止操作。
- `GET /api/v1/projects/{id}/execution-options`：增加同样的default_profile和profiles；一次请求捕获一个目录，Git预算、network、是否可选和指纹来自同一快照。列表仅表示配置可选，不证明Docker就绪。
- `POST /api/v1/scopes/{id}/sessions`：增加可选profile_id、expected_profile_revision；两者同时提供且仅用于sandbox，省略则首次使用默认项。显式选择必须匹配指纹；旧客户端省略继续工作。会话setup增加可空requested_profile引用，与selection快照分开；旧记录默认空，无DDL。
- HTTP原认证、服务实例校验、X-Request-ID幂等和错误映射保留。refresh重试同请求ID只重放原结果；每次用户主动刷新应生成新ID。
- CLI `nyt chat --execution sandbox --environment python-tests`：只用于新建持久沙箱会话。CLI先从服务取选中ID的指纹，再创建；目录变化由服务拒绝，不自动重选。省略environment保留默认路径。resume/host/no-session冲突在连接前拒绝。

## 影响范围和边界

扩展sandbox_profiles的局部读取与目录投影、EnvironmentAPI、InputService、会话合同/创建、Web schemas/routes、service刷新路由及app接线、CLI参数/启动、前端纯类型合同、文档和测试。SessionService不扩行，不新增配置框架、依赖、任意路径读接口或资源管理。

前端本片只同步新增响应/请求字段的类型；不做下拉框或按钮。旧settings.sandbox对象是启动快照；新选择唯一使用EnvironmentService.configuration，已建环境唯一使用保存settings。

## 验收

先失败测试后实现。覆盖：只解析sandbox、固定路径/拒绝注入、缺失/无效/权限读取失败保留旧目录、清空目录仅禁用未来新建、并发刷新排队和循环响应、选项单快照、认证/实例/请求幂等、显式选择接到真实保存/准备链、未知和过期无副作用、原请求删模板后及重开后重试、默认改变兼容、CLI冲突及指纹转发。

测试用临时配置、现有OfflineModel/SandboxTransport，不修改conftest或放宽旧断言。完成后跑专项、全量后端、前端与标准构建；真实Docker留05。收尾补实际命令/结果和当前正常/失败入口。

## 当前主链与后续03边界

刷新：`service.environments.refresh` 从 `scopes.settings_path` 取得唯一文件 → `EnvironmentAPI.refresh` 持G6 → `run_blocking(load_sandbox_configuration)` 读取、解析、严格校验 → owner loop替换 `_service.configuration` → `catalog()` 只返回ID/名称/指纹。失败在替换前抛 `SandboxError(env_configuration_invalid)`，服务异常映射返回409；旧目录对象保持相同。取消时先排空线程，再释放G6，不发布被取消的候选。

选择：`cli.chat.main` 先拒绝冲突参数 → `chat_startup.prepare_terminal` 向已选项目查询execution-options → 把所选ID/指纹加入POST sessions。Web `NewSession` 校验成对字段及sandbox限定 → `SessionAPI.create_sandbox` → `execution_binding.create_sandbox_session` 再校验引用 → 历史锁内按稳定会话ID查原行。新行从同一configuration选出完整策略，比较指纹后保存 `SandboxSessionSetup(requested_profile,selection)`；原行比较请求身份、直接使用保存selection，不查询当前模板。之后 `prepare_sandbox_session` 使用快照Git预算和EnvironmentService.create的原策略。

例如Node指纹A已被客户端看到，刷新后成为B：新请求带A报env_profile_changed，不写会话、输入或容器；重新确认B并换请求ID后才创建。原来已经用A保存成功的请求仍可按原ID恢复，不要求目录继续包含A。

03准备扩展现有 `ExecutionSelector`、`WebApi.createSelected` 与新会话草稿流程：输入框上沿的执行位置列表显示本机及配置模板，列表内提供刷新按钮；选择阶段保存草稿，首次发送才保存会话并准备环境，之后发送消息。刷新失败保留当前列表；选择内容变化明确要求重新确认；旧会话显示保存的实际模板，重试只核对原创建请求。03本片尚未实施，当前TypeScript仅同步可选合同字段；04容器额度/回收同样不在本片。

## 实际验收（2026-10-26）

| 检查与命令 | 结果 |
| --- | --- |
| 首次新增loader/refresh/selection测试 | 12 failed，分别为新入口/字段未实现及options混读配置；测试空集合类型按现有API的tuple校准，不改变产品断言 |
| HTTP/CLI首次及合法CLI参数首次 | 4 failed/8 passed；合法参数另1 failed（未识别environment），均在实现前确认 |
| 四个新增 `tests/test_sandbox_profile_{refresh,selection,http,cli}.py` | 30 passed；包含取消排空、真实配置变更、业务边界、HTTP固定路径/认证/幂等和CLI转发 |
| `pytest -q tests/test_sandbox*.py tests/test_git_input_service.py tests/test_git_input_install.py` | 138 passed/37.02s |
| 全量collect及 `python build/r1-13d-02/run_shard.py 0` / `1` | 519文件5456项；两组互斥覆盖全部文件，2706 passed/264.29s，2750 passed/350.97s |
| `npm test -- --run` / `npm run build`（frontend目录） | 505 passed；TS/Vite exit0，保留既有大chunk警告 |
| `python -m scripts.build_release --target macos-arm64 --output build/r1-13d-02/dist` | exit0；343 Python、4 Web及README与当前源码逐字节一致 |
| `python -m twine check build/r1-13d-02/dist/*.whl` | PASSED |
| `python -m scripts.verify_wheel_install --wheel-dir build/r1-13d-02/dist --output build/r1-13d-02/install.json` | 初次PyPI文件下载超时；原命令重试exit0，源码目录外全新安装、CLI/API/SQLite/MCP/存储升级备份还原/后台/子实例/记忆smoke通过，0供应商请求 |
| 定向架构/安全审查、diff/行数 | 无剩余阻塞；刷新锁补入G6；所有新增/修改Python文件不超过500行 |

Python为 `.venv/bin/python`；pytest PATH前置 `build/pre-profiles-push/test-assets/bin`，不修改真实PATH配置。完整收集节点、互斥分组、原始日志、包内容与安装结果保存在忽略的 `build/r1-13d-02/`。无真实Docker/公司环境新验收，不把受控transport当成隔离证明。

原请求恢复、显式选择、局部加载及认证已沿生产入口接线；旧入口无新增兼容复制，旧客户端省略选择参数仍可用。01的未提交文件和research原始资料保留，未修改真实配置/用户库/依赖版本，未提交/push。下一步只讲解03，待用户批准。
