# Spec088：Windows文件提交与Shell归档

状态：2026-10-16，用户已批准，已实施接线；本机验证与原机边界见末尾。前置为Spec087；不改权限模式、工具注册/审批、配置、依赖或数据库。

## 现状与目标

Write._write/Edit._copy_to_temp准备同目录临时文件，fchmod复制普通模式位，flush/fsync后共同调用_replace_temp→os.replace。Python3.12的fchmod是Unix API；移除它的真实临时目录探针显示已有Write失败AttributeError、原文不变、临时文件零残留。归档_directory额外拒绝mode&0077，0755的合法owned目录也会被拒；Windows模式位不能作为ACL判断。

## 文件和合同

- tools/write.py：POSIX覆盖继续复制原普通模式位；Windows不复制POSIX位。扩展现有_replace_temp(temporary,target,*,existed)，双方调用明确传入准备时的存在状态，不能根据提交时缺失自动改为新建。Windows已存在目标调用窄函数_windows_replace_file，ctypes调用ReplaceFileW(target,temp,None,0,None,None)，不忽略ACL/属性合并失败、不回退普通替换；新建调用os.rename（Windows目标已出现则拒绝），POSIX沿用os.replace。Windows只读目标在提交前反馈具体ToolException，不清只读/提权。原目标身份/摘要/执行permit复核保持。
- tools/edit.py：_copy_to_temp仅POSIX调用fchmod，明确existed=True提交；原分块、归一化/精确匹配、diff/patch与目标锁不变。
- tools/output.py：删除归档私密位门槛，保留owned标记/.gitignore、普通类型、合法文件名、快照确认清理；目录/标记/归档项显式拒绝symlink及Windows reparse。检查和归档错误有日志，归档降级不改变命令主结果。

正常：审查通过→真实目标复核→写临时文件→POSIX复制模式/Windows不使用fchmod→flush/fsync→身份复核→平台提交→返回结果。Win替换DACL由系统保留；不承诺全部文件系统元数据或掉电事务。
失败：准备失败→清理临时文件→具体工具错误。Windows只读→原目标不修改。提交API失败可能部分完成，沿既定结果未知ToolException反馈并记录，绝不失败后再用os.replace绕过属性保留；临时清理故障不掩盖主异常。

## 验证

先红灯：无fchmod下Win模拟Write覆盖/Edit成功且走共享Windows提交；只读拒绝不调用替换；WinAPI参数/0flags、失败不回退、原结果未知及临时清理；POSIX模式原测试保持；0755 owned归档可写可回读/清理、reparse拒绝、标记/非预期文件/快照检查保留。Win模拟是分支证据，真实ReplaceFileW/DACL和占用/只读实机另验。不改测试基础设施、不新增依赖。

参考：Pi a69bef789生产Write/Edit为fs.writeFile直接写目标，非本项目临时替换模型；AgentX3fcfa0e相关生产工具通过MCP接入，不是本地替换后端。[Python3.12 fchmod](https://docs.python.org/3.12/library/os.html#os.fchmod)、[ReplaceFileW属性及失败合同](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)。

## 实施记录

- 修改write/edit/output三个现有模块，无新增平台模块/依赖。_replace_temp的existed只由Write准备状态或Edit的True传入；提交时目标消失不降级新建。POSIX仍走os.replace及原模式位保存，Windows仅已存在目标走ReplaceFileW；原目标只读不会被改成可写。归档目录普通权限不再阻断，symlink/reparse/标记/普通文件/清理快照仍显式检查。
- 初始测试9失败/1通过后，相关原工具与新分支106通过；后补原生API失败和目标消失测试。WinAPI用mock核对参数/DACL保留选项和故障分支，没有实际在Windows执行，也没有原机ACL保留成功证据。原Mac文件写入、模式位、停止/临时清理与原工具回归通过。
- 实现范围未触及模型配置/存储/权限模式/发布。最终验证结果见handoff。

最终验证（两片合并）：全量`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1707 passed in50.90s**；新增四文件73 passed in1.03s；前端9文件27测试与TypeScript/Vite186模块构建通过。compileall/pip check、283文件AST/105生产文件≤500行及diff检查通过。未改真实配置/数据库/依赖，未提交/push，无真实Windows/Linux或远端模型请求。
