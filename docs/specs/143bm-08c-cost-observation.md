# S8-2：评测成本观测

2026-11-17，依据U03。只在临时评测对象观测，不改变生产门禁、检索或模型预算。

新增CaseCosts由后台ProbeCallback和前台Budget实际消费，每case独立。预算准入后记录run_id、stage、单调时钟；结束或异常记录耗时与可得usage。各阶段分别汇总逻辑调用、已完成/失败、token和模型耗时；任一调用usage缺失，则该阶段总token为null，同时报告已知token及未知调用数。零调用阶段为真实0。HTTP attempts未接观测时明确null，不将逻辑次数冒充SDK尝试。

临时MemoryAccess实例的query/comparison_candidates加有界观察包装，保存原方法，case退出前恢复。真实请求/返回不修改，异常原样抛出，只记录动作、耗时和失败计数（具体错误类型继续由原案例报告保留）。统计query/list/read为领域API调用次数，comparison为比较读取组数及计划查询数；不声称SQL操作次数。种子建立后才绑定，排除准备数据。无新增锁，当前每case只有一个独立应用/后台任务；记录追加后汇总，无共享全局计数。

成本报告包含total_ms、各阶段模型调用、领域读取、比较读取；等待/提交/SQL数/预加载token缺乏完整独立观测时保持null，并复用原任务events/usage和followup parts，不猜测。前台总耗时含退出清理，后台为run_case到构造结果前，边界在报告注明；不能直接用两入口总耗时比较算法纯性能。

失败路径保留已准入调用、未知usage与已完成读取；异常不保存正文/URL/凭证。不改变callback原有配对和预算。验证固定时钟、混合usage、错误与恢复、真实前后台接线；冻结最终代码后再启动完整回归。
