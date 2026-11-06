# 143AV Windows 上传 journal 长路径

Windows 实机同一批三个压缩包复现 3/3。归档发布成功，随后 CreateFileW 创建约 270 字符的 upload-{instance}-{attachment}.json 失败（WinError 3）。目标为本机 Windows 文件操作边界，保留 journal 名称、旧回执和原传输 ID。

Win32 Unicode API 接收长绝对盘符路径或 UNC 路径时添加对应扩展前缀。仅用于实际系统调用和同一个文件的存在性/身份复核以及缓存空间统计，不进入持久记录。继续检查重解析点、单硬链接、原生句柄与命名路径身份；保留文件内容与发布语义。相同原生打开模式同步用于附件来源及目录句柄、产物独占改名。不改注册表、输入协议或 Linux 发布行为。

定向测试注入 CreateFileW 以复现长 journal 参数；覆盖盘符、UNC、UTF-16 长度、已带前缀及短路径。安装烟测构造超过 260 字符的 journal，并验证模拟断连后仍读回同一命令 ID，且缓存容量扫描能统计该 journal。

本机验证：模拟 Win32 调用先红后绿，定向70项，云端及受影响的存储/Windows测试1264项通过。标准 Windows x64 wheel在macOS交叉构建、Twine通过。新增原生长journal安装烟测等待Windows CI或原机验证。
