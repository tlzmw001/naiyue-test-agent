# Lesson 33：模型分类配置与运行快照

> 2026-10-10。逐行读model_access/__init__.py的_error_classifiers/_error_lock、第63–86行三个方法；get只复访模型ID校验。SessionService第202行仅定位装配，不记其全部接口已逐行学。

## 数据与调用链

ModelCatalog的_models={A:模型对象,B:模型对象}与_error_classifiers={A:(适配后的函数,...)}职责不同。后者按模型ID存规则，空缺用空tuple转入内置模型分类。不是.env配置修改/DB存储，也不是全局注册表。

配置路径app.configure_error_classifiers(model_id=A,model=[...])→SessionService校验/busy门禁→catalog.configure_error_classifiers；运行开始SessionService构造RuntimeErrorPolicy(...,catalog.failure_describer())，获得并注入本次快照函数；出错时运行层调用该函数(实际model_id,error)。不是每次出错再创建快照。

## 核心行

- configure_error_classifiers：get校验已配置模型ID；normalize先验证适配列表；锁内替换model_id规则元组。是替换而非追加，空列表移除自定义效果但保留内置分类。
- describe_failure：get校验ID；锁内读取当前规则，缺失()；锁外调用describe_model_failure，避免执行可信自定义函数时占着配置锁。直接接口用当前配置。
- failure_describer：锁内dict(...)浅复制规则映射；与self._error_classifiers两个不同字典，值为同一不可变tuple/函数引用。保护的是映射及函数序列，不深拷贝函数/闭包可变状态或模型对象。
- known=frozenset(self._models)取得并固定本次可用ID集合，字典遍历为键；与分类器键集合不同，未配分类器的B仍是合法模型。
- 内部def describe(model_id,error)只是定义函数，不执行分类；先检查known，再按selected.get(model_id,())选规则并调用describe_model_failure。
- return describe返回函数对象，非分类结果；该函数通过闭包保留selected/known，外函数结束后仍可读取本次数据。每次调用failure_describer创建自己的映射与闭包。
- 装配RuntimeErrorPolicy时调用factory()获得函数；之后失败才调用snapshot(model_id,error)。快照含全部模型规则而非只含起始模型A，因此允许同run后续切B并按B规则分类；不冻结会话模型选择。

## 时间例子与边界

t0配置A→old_rule，B无自定义；t1run_one=failure_describer()；t2底层catalog将A替换成new_rule；t3run_two=failure_describer()。run_one(A,error)仍old_rule，run_two(A,error)与catalog.describe_failure(A,error)均new_rule；run_one(B,TimeoutError)用内置timeout。

上述是底层目录探针，不能读成公共App允许活动运行中改规则；App接口在有任何busy会话时拒绝错误配置变更。模型切换与分类器配置变更是不同动作，快照不禁止切换模型。独立catalog有独立映射；显式共享catalog不是自动多租户隔离。

## 验证

无网络真实ModelCatalog+fake模型探针实际输出old run A: old_rule / new run A: new_rule / direct current A: new_rule / old run B: timeout / unknown model: ModelConfigError，断言全部通过，没有模型调用。

```bash
.venv/bin/python -m pytest -q tests/test_layered_failure_contract.py::test_model_classifiers_are_model_scoped_and_snapshotted
```

实际`1 passed in 0.50s`，测试按模型映射与旧快照保留/直接接口新规则。不证明真实供应商、恶意插件隔离或完整并发模型切换；本课仅文档，无产品/测试/环境/依赖修改，不提交push、无全量构建声明。

## 理解与下一步

- 已讲解、理解待反馈；独立实现/追问未评估。
- 检查：run_one快照在A=old_rule时创建，随后底层目录A改成new_rule；run_one(A,error)与catalog.describe_failure(A,error)各用哪套规则？用户答“旧的、新的”，正确区分快照读取与当前目录读取。记录该分支理解，闭包独立复写、并发边界及追问未评估。
- 唯一下一步RuntimeErrorPolicy的普通模型委托/运行错误分类/保护错误优先级，沿Lesson29地图进入Runtime。
