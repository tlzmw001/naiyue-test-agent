# A提取局部修订：事项对象与局部确认

用户已确认实施。原始后台06的matter没有ruff，B旧项为空；07的A空提议，未执行B。只修改EXTRACT_PROMPT，不增加字段、硬性逐字校验、检索算法、重试或数据库行为。

正常链：_prepare装配A提示和新证据→A提出保留具体对象的稳定matter，以及仅描述本次确认范围的body→bind_extraction→query_plan沿用matter→比较旧记录→B选动作→现有代码门禁提交。matter使用证据中的具体工具/服务/环境名，保持自然语言灵活性；ruff名称放matter，不强制加入identity_terms。用“项目ruff使用约定”同时覆盖试用/正式两种状态，状态放body。

局部确认例：新消息“继续原本地约定，CI不在确认范围”，A可提出该范围，但不能补写旧约定为隔离环境。B只有在检索到对应旧项、且新证据支持时才可作partial source-only；代码保留旧正文/状态/期限并附证。未找到旧项或证据不足时仍按原合同判定，不强制附证/激活。失败跳过不重试。

影响：memory_job_runner.py提示、tests/test_memory_extraction_guidance.py（复用现有准备fixture及工程worker）、本Spec与handoff。测试先确认实际A请求缺失规则的失败，再验证提示接线；固定提议走query_plan及真实worker补证链，检查正文/时钟/来源。固定输出不证明模型真实采纳。工程验证后再冻结新的完整后台calibration批次，后台原24逻辑上限保持，禁止失败子集补跑；本单元先不发真实模型请求。

实施完成：2条实际请求提示接线测试先红后绿；固定流程样本的分号产生两个证据单元，而复用驱动仅引用第一个，改为同义单句输入后保留完整quote断言；该样本修正不当生产修复。专项71、完整后端7108通过，0失败/错误/跳过；标准Mac构建/Twine及wheel实际提示一致，证据build/spec143bm-extraction-guidance/verification.json。真实模型0，不能称06/07真实效果改善。

真实完整校准：build/spec143bm-extraction-calibration/background.json，8例24调用，无补跑，7completed/1partial。06同ID context v1→convention v2，旧项找到；07提出且B判partial补证，但old_evidence_refs误填O1，被old_source校验拒绝，旧v1零新源，不称补证成功。01/05注入，普通召回仍有限。08“根因尚未确定”扩写需人工审阅。完整验证与助手分析见同目录verification.json/comparison.json/analysis.md。下一步讨论source-only旧诊断来源引用是否应由程序关联，保持目标/CAS/新证据门禁；不补07子例，不进入验证/留出。
