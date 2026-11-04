# 143AM：0.5.7 配置参考

用户要求恢复旧工作树的 settings.full.toml 并补充新配置示例。仅维护参考文件，不修改真实配置、凭证或旧工作树。

当前链：本地 settings → ProfileBook → CloudConnection → SSH/Owner；开发机 CloudSettings → backend/templates/model_proxy；UpdateSettings → 构建预检/发行，RemoteUpdateSettings → SSH 调用开发机。正常示例经现有模型验证；错误字段必须被原加载器拒绝。模板回执由预检产生，不伪造成功回执。

交付：docs/examples/full/settings.full.toml；profiles/connection/cloud/update/remote-update/template-build JSON；逐项说明文档。JSON不支持注释，说明与文件并列；所有凭证和实例身份明确是占位示意，不能直接部署。旧 sandbox/delivery/allow_sandbox 删除。测试验证实际加载模型、默认预算、示例字段及注释覆盖。

发布阻断另沿原测试及结果提交/Runner清理链验证，不混入配置语义变更。
