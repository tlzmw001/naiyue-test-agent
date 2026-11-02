# 云端任务资源维护与容量

S06.3a接通Coordinator资源维护与软磁盘门禁。正式云端侧栏仍按S06.4接入；S06.3b的任务数据删除和本机关联核销见[删除与恢复](cloud-task-deletion.md)。释放环境会保留任务工作区、状态和已确认结果，仍阻止直接删除有关联任务的本机会话；只有核验原删除成功回执后才核销关联。

Coordinator启动后拥有独立资源维护协程，退出时先停止接受新的维护停止命令，等待已经派发的原命令收束，再关闭后端。默认每30秒扫描最多100个任务，按task_id轮转；并非每30秒承诺完成所有任务。只有Runner已报告静止、没有活动run、必要保存/输出/unknown保护及在途控制/环境/产物操作，超过`idle_stop_seconds`，才执行原stop_environment。看进度和下载文件不刷新静止时间。

`GET /api/cloud/v1/capacity`仅所有者私有API可访问，沿用Host、Bearer、协议、实例与Origin校验。它返回真实instance_id，活动、预留、物理状态unknown、未删除保留数，上限，以及磁盘完整性、逻辑字节、可用字节、计量时点和拒绝原因。预留/unknown是可与活动数重叠的子集，不应相加。`can_create`只表示当前容量允许，最终创建仍核对项目、模板、输入和同事务额度；查询不启动、刷新或删除任何容器。

配置通过既有显式Coordinator配置的`resource_limits`字段读取；本片没有修改任何真实环境配置：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| maintenance_interval_seconds | 30.0 | 每轮完成后等待秒数 |
| task_batch_size | 100 | 每轮最多检查任务数 |
| scan_max_entries | 100000 | 元数据扫描条目上限 |
| scan_max_depth | 64 | 相对数据根递归深度上限 |
| scan_timeout_seconds | 2.0 | 扫描检查点截止预算，不能中断内核阻塞的文件系统调用 |
| scan_cache_seconds | 5.0 | 逻辑用量缓存有效期；周期维护强制刷新 |
| min_free_bytes | 268435456 | 新分配后保留的最小可用空间 |
| max_logical_bytes | null | 可选托管目录逻辑字节软上限 |

计量只读目录元数据，不打开任务文件，不读取Runner SQLite，不跟随链接。Linux用已打开FD的mount ID拒绝跨挂载（含同设备bind mount）；其他平台核对设备号。特殊文件只检查元数据，FIFO不会被打开。达到条目/深度/时间预算、权限或挂载检查失败返回unknown，用量留空并拒绝新分配；不会用部分计量的数字冒充完整用量。已知逻辑字节按目录项累加，硬链接可能重复计数，不等于文件系统实际分配块；`free_bytes`每次门禁通过statvfs重新读取。

门禁覆盖新输入transfer、新任务、环境启动/安装、产物捕获/prepare和实际上传；transfer创建后磁盘变满，后续新字节PUT仍受检查。已确认输入段重放不分配新字节。已完整接收后的input/artifact发布、原索引恢复、必要历史保存、停止和下载已固定文件保持可用；真实写盘失败仍按原IO失败返回。已确认`input_ready`的工作区重开不重复计整份输入；未确认首次安装的start/reconcile按manifest完整大小保守预留空间判断。

软门禁不会限制任务命令瞬间产生的大文件，不是卷级硬配额；缓存期间的逻辑字节也不是原子快照。部署需要监控磁盘，真正规定硬配额前须独立验证文件系统和存储驱动。此实现没有修改Docker daemon、分区、挂载或真实防火墙。

确定性本机验证入口：

```bash
.venv/bin/python -m pytest tests/cloud/test_resource_maintenance.py tests/cloud/test_resource_admission.py tests/cloud/test_server_restart.py -q
```

这些测试使用临时目录、受控后端和真实ASGI路由；真实Linux非root/bind mount/容器停止、Windows交互与公司网络仍须目标机验收。
