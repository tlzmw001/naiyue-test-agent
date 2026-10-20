# MCP-00 可复查实验

这是选型研究脚本，不是产品MCP实现、公共API或发行入口。范围与正常/失败链见[00 Spec](../../specs/117-00-mcp-client-selection.md)，结果见[选型报告](../mcp-client-selection-2026-10-20.md)。固定SDK 2.3.0、FastMCP 4.1.1、jsonschema 4.26.0；使用本项目现有LangChain 1.4.2/core 1.6.6、Python 3.12。

本机已有隔离依赖，可直接在仓库根目录执行：

```bash
.venv/bin/python -m pytest docs/research/mcp00/test_selection.py -q
```

默认从本机`/tmp/nyt-mcp-compare-deps-path.txt`读取临时依赖位置。另一个环境需要先准备项目既有开发环境，再准备独立研究依赖（以下命令供复现，本轮没有重新安装）：

```bash
export MCP00_DEPS_DIR="$(mktemp -d)"
.venv/bin/python -m pip install --target "$MCP00_DEPS_DIR" \
  'mcp==2.3.0' 'fastmcp-slim[client]==4.1.1' 'jsonschema==4.26.0'
.venv/bin/python -m pytest docs/research/mcp00/test_selection.py -q
```

不把研究依赖装入项目venv；使用系统当前Python启动同一fixture。夹具HTTP端口动态分配，只绑定loopback，测试结束终止子进程。全组0次模型供应商请求、不连接用户业务服务。macOS arm64本机证据不代表Windows/Linux原机验收。

文件职责：

- `fixture.py`：公开SDK服务端覆盖list_tools/call_tool，提供任意schema和可审计故障。
- `clients.py`：同合同SDK/LC候选、专门资源owner与调用借用；结果限制仅转换后限制。
- `boundary.py`：JSON Schema研究分支＋真实prepare_permission_call/ExecutionAuthorizationMiddleware/require_tool_permit＋编译ToolNode。生产ToolRegistry没有修改。
- `http_budget.py`：公开HTTPX2 transport扩展，读取application/json时限制字节。不是完整压缩解码/SSE/stdio内存上限。
- `test_selection.py`：两库、两传输的同一行为矩阵；另用真实AsyncApplication固定Pydantic工具验证共享图、两个会话与跨run借用。研究ExitStack显式关闭资源，不声称现有app.aclose已接MCP。

故障记录：run1中两个失败为异常类型断言漏写Error后缀，追踪真实类型后改正；run2新增读取预算场景暴露RPC Connection closed＋owner退出再次抛底层错误，研究源代码补原始错误上送与精确对象身份去重诊断。新清理错误必须继续抛出，另有故障注入断言。旧日志保留在build，不改生产或既有测试。研究JSON校验适配暂映射Pydantic ValidationError只为调用当前权限函数，不要求01把所有JSON错误包装成Pydantic。
