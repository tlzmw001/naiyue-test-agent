# S8-2：完整人工评分接线单元

2026-11-17。实现依据为 S8 U02；只扩展评测，不请求模型。

新增报告标识 review_contract=memory-facts-v1。没有标识的历史报告沿原评分入口；新标识必须经过完整评分。两入口均记录事实和输出覆盖，后台另评分比较；前台 comparison_metrics=null，不能冒充后台B已完成。

每个事实评分增加 scope_correct/time_correct（bool）与 duplicate_added（bool）；仅baseline允许time_correct=null，单列已知时间分母，不把未知当错或正确。后台每个事实对应 comparison：attempted、old_recalled（有target_old时bool，否则null）、relation_correct、target_correct（attempted时bool，否则null）、output_index（attempted时必须索引实际model_outputs）。未尝试仍在全部事实分母，关系正确仅在attempted分母；完成率单列。旧项召回分母只含有target_old事实。比较正确由人工基于真实输出审阅，不强迫模型重抄旧正文。

每个case增加output_reviews，覆盖memories中每个(scope,memory_id,version)、每条model_outputs（model_output:数组索引，含根轮最终说明）和存在的followup.answer。每项有id、supported(bool)、reason非空；报告产生的额外记忆也必须审阅。代码只验证覆盖/类型和引用，语义仍由人工审阅，不靠字面相等推断正确。case级旧危险项和原评分保留；新输出unsupported也使安全失败。memories=null不能视为无输出，完整审阅状态为unavailable。

正常：驱动附合同→CLI绑定报告hash的人工材料→原review校验case/fact覆盖→新评分验证比较/输出覆盖→返回额外指标/安全结果。失败：漏事实维度、漏输出、引用越界或类型不符直接拒绝；主流程失败仍保留原工程失败，不被评分救为通过。人工未提供材料不能自动填写“通过”。

影响：新增scripts/memory_review_metrics.py（当前review消费者），扩展review_memory_evaluation、前/后台报告和manifest，新增测试。旧fixture/protocol与报告不重写；新报告标识随源码指纹绑定。U01全部矩阵、U03成本仍另行继续；本单元不宣称S8-2整体完成。
