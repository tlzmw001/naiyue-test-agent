# S09.1 Windows输入捕获与云端界面修正复验

> 版本边界：本页保留旧版本/阶段性验收记录。2026-11-03 S09之后的当前入口以[云端任务手册](cloud-s091-windows-and-linux.md)和[Spec143AF](specs/143af-s09-formal-switch-and-cleanup.md)为准；旧A Docker/envd步骤不再是当前产品入口，旧候选附件不包含最新切换改动。

适用：原de5f76a候选在Windows第6节/B12输入捕获报OSError，尚未上传或创建任务。新候选仅修正本机输入/导出文件flush和云端UI，不夹带本机另存的build_proxy_env代理候选；实际身份以candidate.json的原基线、source_identity及补丁SHA为准，不以0.5.6版本字符串判断。

Linux已有Coordinator、Runner镜像及私有Docker wrapper保留。本轮客户端与原云端协议相同，可先只更新Windows验收端；记录两端不同的实际源码/wheel指纹，不把混合版本说成同一提交。完整统一版本升级仍属后续门禁。

## 1. 核对与更新Windows候选

先保留原失败报告、原候选wheel和S09私有配置。只停止当前Windows S09配置对应的本机服务，Linux服务/隧道保持；停止失败先核对，不强杀其他Agent或修改site-packages。新窗口填写实际绝对路径：

```powershell
$Root = (Resolve-Path '实际Windows验收根目录').Path
$Fix = (Resolve-Path '本次修复包解压目录').Path
$Py = Join-Path $Root 'venv\Scripts\python.exe'
$Nyt = Join-Path $Root 'venv\Scripts\nyt.exe'
Set-Location $Root
Get-Content (Join-Path $Fix 'SHA256SUMS') | ForEach-Object {
  $Parts = $_ -split '  ', 2
  $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Fix $Parts[1])).Hash.ToLower()
  if ($Actual -ne $Parts[0]) { throw "SHA mismatch: $($Parts[1])" }
}
# 若原启动路径不同，用原命令中的实际settings/config，不更改文件。
& $Nyt service stop --settings "$Root\private\settings.toml" --config "$Root\private\models-local.json"
if ($LASTEXITCODE -ne 0) { throw '请先核对本轮本机服务停止结果' }
$Wheel = Get-ChildItem "$Fix\wheels\windows-x86_64\*.whl" | Select-Object -ExpandProperty FullName
& $Py -m pip install --force-reinstall --no-deps $Wheel
if ($LASTEXITCODE -ne 0) { throw '候选安装失败' }
& $Py "$Fix\verify-candidate.py" --manifest "$Fix\candidate.json"
if ($LASTEXITCODE -ne 0) { throw '候选身份核验失败' }
```

如果该配置的服务已经正常停止，不必为stop重复启动服务。安装仅替换本轮独立venv内的应用wheel，保留现有依赖版本及用户数据；不运行通用pip -U。

## 2. 独立输入捕获探针

包内提供windows-capture-probe.py。它只在系统临时目录创建无敏感内容的小文件，执行真正capture和归档核验，不调用模型、Coordinator、Docker或业务接口，结束自动清理自己的临时目录：

```powershell
& $Py "$Fix\windows-capture-probe.py"
if ($LASTEXITCODE -ne 0) { throw 'Windows独立输入捕获尚未通过，停在此项' }
```

预期输出status=passed，同时报告gzip/tar两种编码的input_id及最终文件名。源文件原始字节必须保持；不能跳过fsync、忽略EBADF或改输入格式绕过。Mac模拟Windows只读句柄拒绝语义的测试不替代这条原生结果。

## 3. 接续原Web门禁

用原来同一条启动命令重启本机服务，保持原settings/models/workspace路径，打开服务本次实际提供的入口；包含token的完整链接不外传。强制刷新网页，确认新建任务界面没有旧固定说明、表单13px、按钮悬停不褪色；主聊天/模型菜单保持原样。打开和查看不应发起新云端任务。

原截图的捕获在source_handle产生前即失败：UI未冻结create command，修复后可在保留的表单再次准备。若当地实际已有source_handle、binding或command_id，则核对原操作，不创建新ID来绕过unknown。先Git输入做B12，必要时selection作为独立覆盖，不用切模式“修复”旧失败。

保存本机捕获成功、输入上传/提交、云端task ID和原命令回执，随后接原手册第6节剩余项。真正持久化失败仍必须拒绝发布；不手写成功回执。

另在结果具备后复验Windows“导出到新目录”：CRLF/二进制字节正确，已有目录不覆盖，原项目不被修改；该处同类只读flush已修正，并保留对可写句柄和已固定源句柄的身份核对。

## 4. 证据边界

记录Windows/Python、原云端模板/回执、更新前后本机wheel SHA、独立探针输出、B12及UI观察。不要附私密配置、访问token、完整模型请求或业务正文。失败保留现场并停在该项，不修改安装后的代码或降低持久化/权限检查。

本轮不重跑S00，不重建已通过的Linux镜像，不切正式入口或自动推进后续发布。纯文档语法检查、本机模拟、Mac安装与公司Windows原生复验分别记录。
