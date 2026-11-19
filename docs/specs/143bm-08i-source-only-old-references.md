# source-only旧来源由程序关联

用户确认实施。真实后台07已正确选择supplement/direct/partial/claim=null，但old_evidence_refs误用记忆O1而非来源O1:S1而失败。该字段无提交消费者；旧ID/版本来自已校验target_old_ref，旧来源从事务当前版本读取。

仅supplement且confirmation_scope为partial/unknown的source-only分支，不验证模型返回的旧诊断引用是否属于可选集合或重复；返回非空时忽略并以固定日志记录field=old_evidence_refs，不记录其内容。schema仍要求有界字符串列表，错误类型/超长不放宽。B提示要求本分支old_evidence_refs=[]，程序继承旧来源；其他关系的旧引用校验保持。

正常链：B选组内O及新E→compiler核对目标/范围/直接支持/新证据→继承旧内容并生成CAS请求→事务检查权限、版本与原始新证据，合并旧来源。旧源不可用也不伪造旧引文。失败链：坏目标/跨范围/新E不存在/推断支持/重复写目标/权限拒绝/CAS冲突/来源容量仍拒绝，失败无补跑。

影响memory_proposals.py、memory_comparison.py及已有program_owned_actions测试，本Spec/handoff。新增局部引用误填测试先红后绿；扩现有真实worker和事务测试携带错误引用，保留缺旧归档/权限/时钟/来源/CAS断言；其他关系坏引用与schema错误仍拒绝。真实比较提示接线验证空旧引用规则。完整后端与标准构建通过后，再决定新完整校准；不重写旧07结果，不补07单例。

实施完成：12项回归在旧实现失败，修复后专项83通过；额外4项补充断言验证其他写关系旧引用及source-only错误schema仍拒绝。全后端7119通过，0失败/错误/跳过；标准Mac构建/Twine/两个模块wheel逐字一致，compileall/pip/diff通过。证据build/spec143bm-source-only-old-refs/verification.json。真实模型0，旧07报告不改，不声称真实批次已补证成功。
