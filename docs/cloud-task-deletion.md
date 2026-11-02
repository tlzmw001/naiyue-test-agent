# 云端任务删除与恢复

S06.3b提供真实删除API和本机关联核销，候选侧栏已在S06.4接入，正式入口切换归S09。正常释放环境仍保留任务数据，相关说明见[资源维护](cloud-resources.md)。任务删除是所有者确认动作，空闲回收、关闭页面和关闭本机服务都不会自动删除任务数据。

正常流程是先停止云端本轮，等待必要历史、输出和产物保存完成，再预览并确认删除。未静止的健康任务返回`task_delete_requires_stop`，沿已有停止入口处理。发生unknown、断线或未确认创建时，可以明确确认可能丢失未保存现场；这项确认仍不代替后端物理停止证明。

所有者私有API复用既有Host、Bearer、Origin、协议和实例鉴权：

| 接口 | 作用 |
| --- | --- |
| GET /api/cloud/v1/tasks/{task_id}/delete-preview | 返回固定实例、任务、revision/generation、范围及preview_digest |
| POST /api/cloud/v1/tasks/{task_id}/delete | 提交command_id、confirm_task_id、expected_revision/generation、preview_digest和acknowledge_unknown |
| GET /api/cloud/v1/commands/{command_id} | 只读查询原回执，不重新执行 |
| POST /api/cloud/v1/commands/{command_id}/reconcile | 空对象请求，显式从原阶段继续核对/清理 |

task listener无上述管理删除能力。请求不接收目录、容器ID或任意远端路径；预览期间任务状态发生变化会拒绝陈旧确认，需要重新预览。已受理原命令重试使用原ID和固定请求；GET和本机重启都不自动重放删除。

删除阶段为reserved、stopped、cleaning、finished。前者先封新运行、产物、历史建库和下载准入，排空旧环境/文件IO；之后核验原容器身份并确认已消失，再关闭自有历史库并清理任务数据。权限/预算/网络失败保留deleting及unknown回执和阶段；原ID显式reconcile可继续，未完成前不释放保留额度。

未知create没有可信sandbox ID时，RPC失败之后远端仍可能创建，当前查询空列表不能证明它已经终结。此时返回`sandbox_create_unconfirmed`并保持占额；后续发现并核验原实例后，先保存其ID，再停止和核对。多匹配、身份不符或kill后仍存在均不清目录。旧创建若从未出现，需取得可信后端终止证据后另行处理，首版不会以等待一段时间或空列表自动宣告成功。

清理范围是该任务的workspace/state/control、Coordinator历史数据库及附属文件、已保存产物与传输暂存，以及旧命令中的任务正文/结果。访问使用固定根FD、不跟随链接并核对挂载身份，不读取Runner SQLite。共享输入和镜像保留；本机已取回文件和主聊天不删除。旧命令保留ID/digest并脱敏为task_deleted，防原create/run ID被重新执行。该能力属于逻辑数据清理，不承诺介质安全擦除。

关闭任务已有下载会排空当前文件读取；SSE以`task_deleting`终止观察，这个事件表示删除开始。**删除成功以finished持久墓碑和原成功回执为准。** 模型流、迟到断线/回执、历史查询均不能重新创建已删任务。

本机使用已核验binding的以下入口（保留原local_project_id/local_session_id及本机认证要求）：

| 接口 | 作用 |
| --- | --- |
| GET /api/client/v1/cloud-tasks/{binding_id}/delete-preview | 访问该绑定对应实例的真实预览 |
| POST /api/client/v1/cloud-tasks/{binding_id}/delete | 记录固定确认并由既有命令producer派发 |
| GET /api/client/v1/cloud-operations/{command_id} | 核对原回执；离线/404保持保护 |
| POST /api/client/v1/cloud-operations/{command_id}/retry | 显式核对并继续原删除 |

本机仅在同实例、同task、同command、同代际和digest的finished墓碑验证后，在同一项目数据库事务更新操作成功和binding的retained列/JSON。若任务由同owner另一客户端删除，本机读任务后会继续取原delete_command_id回执，核验后再核销。缓存deleted状态不能单独证明成功。

删除确认成功后，本机会话/项目的既有删除guard才允许继续；释放容器、unknown和断网均保持保护。删除回执落库后没有多余远端刷新，不会与紧接着的父会话删除产生回写竞争。

`delete_drain_timeout_seconds`由显式Coordinator配置读取，默认30秒；超时保持删除阶段而不强关仍写数据的worker。目录条目/深度/检查点时间预算沿用resource_limits，失败可原ID重试，不自动扩大权限或修改挂载。真实Linux非root权限、挂载和Windows交互还需目标机验收。

确定性验证入口：

```bash
.venv/bin/python -m pytest tests/cloud/test_task_delete*.py tests/cloud/test_manual_task_delete.py -q
```

测试只使用隔离临时数据、受控后端和真实本机/Coordinator路由，不连接公司开发机或真实供应商。
