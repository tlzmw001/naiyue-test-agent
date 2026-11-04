# S09.1 R2：模型目录查询与任务按钮复验

> 版本边界：本页保留旧版本/阶段性验收记录。2026-11-03 S09之后的当前入口以[云端任务手册](cloud-s091-windows-and-linux.md)和[Spec143AF](specs/143af-s09-formal-switch-and-cleanup.md)为准；旧A Docker/envd步骤不再是当前产品入口，旧候选附件不包含最新切换改动。

R1之后输入捕获、上传和任务创建已获用户原机成功报告。当前阻断是Windows本机服务查询模型目录时给Coordinator的models路径附带limit，严格上游返回422；任务本身已经ready。本包累计包含R1文件持久化/UI修正＋R2查询修正和按钮对齐，仍排除未采用的构建代理候选。完整身份以candidate.json.source_identity、source.patch及wheel SHA为准，0.5.6版本号不代表补丁身份。

## 1. 只更新Windows验收端

保留当前本机数据、原配置和已经创建的binding/task/input及回执。Linux Coordinator、Runner镜像和私有Docker wrapper保持；不重装、不重建镜像、不重新导入同一输入来绕过故障。

在新PowerShell填写实际目录。仅停止本轮Windows配置对应服务，不能修改已安装的site-packages文件：

```powershell
$Root = (Resolve-Path '当前Windows验收根目录').Path
$Fix = (Resolve-Path 'R2修复包解压目录').Path
$Py = Join-Path $Root 'venv\Scripts\python.exe'
$Nyt = Join-Path $Root 'venv\Scripts\nyt.exe'
Set-Location $Root
Get-Content (Join-Path $Fix 'SHA256SUMS') | ForEach-Object {
  $Parts = $_ -split '  ', 2
  $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Fix $Parts[1])).Hash.ToLower()
  if ($Actual -ne $Parts[0]) { throw "SHA mismatch: $($Parts[1])" }
}
# 使用原启动命令的实际settings/config路径，不改文件。
& $Nyt service stop --settings "$Root\private\settings.toml" --config "$Root\private\models-local.json"
if ($LASTEXITCODE -ne 0) { throw '先核对本轮服务停止结果' }
$Wheel = Get-ChildItem "$Fix\wheels\windows-x86_64\*.whl" | Select-Object -ExpandProperty FullName
& $Py -m pip install --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw 'R2安装失败' }
& $Py "$Fix\verify-candidate.py" --manifest "$Fix\candidate.json"
if ($LASTEXITCODE -ne 0) { throw 'R2身份核验失败' }
```

服务已正常停止时不必为stop重复启动。安装保留当前依赖版本；不执行通用pip -U。然后使用原来同一条启动命令、相同settings/models/workspace启动本机服务，打开这次实际入口并强制刷新网页。

## 2. 继续原任务验证模型目录与首次发送

1. 打开云端列表并进入原来已ready的任务，不点击新增来代替恢复。
2. 模型目录应成功加载，原“云端模型选项读取失败”消失；模型及推理档位来自实际云端目录。
3. 确认本机模型查询HTTP返回200。若当地AI同时做接口检查，本机路由仍需local_project_id/local_session_id鉴权参数，但转发后的Coordinator models路径应无查询参数。不要放宽Coordinator对limit的拒绝。
4. 在任务输入框选择实际模型、填写目标并发送一次，记录本机command_id、云端run_id与受理回执，再接原S09第6节/B07/B12/B20。结构性查询成功不等于真实模型出站通过，首次真实请求仍须单独登记结果。
5. 回执不明保留原ID并核对，不改配置、换容器或重放未知请求。只交回脱敏结果，不外传访问token或模型凭证。

## 3. 按钮与结果位置

新建任务使用原新建会话的plus图标和14px/9px×12px/无边框样式；删除入口对齐原删除菜单14px/9px×10px，确认删除对齐原确认按钮14px/9px×15px。新建表单提交为自然内容宽。验证浅深主题与窄屏，悬停不褪色，取消删除不产生删除动作。

单文件“下载”由浏览器设置决定目的地；“接收到本机”写入项目数据库相邻的cloud-results受管目录；代码导出到当前授权项目内用户指定的新目录，分支/应用另行确认。R2未改变这些保存规则，详见源码包docs/cloud-result-delivery.md。

## 4. 证据

保存本机R2 wheel SHA/候选核验、原Linux模板与版本指纹、模型目录200、首次消息回执及UI观察。B12前半段已经通过的输入/上传/建任务证据继续保留；无须重跑S00。Mac严格上游路由/客户端测试及本机UI验证不能代替公司Windows＋真实供应商验收。
