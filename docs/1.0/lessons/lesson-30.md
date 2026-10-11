# Lesson 30：FailureDescription的数据契约

> 2026-10-10。只读contracts/failures.py的FailureSource第12行与FailureDescription第31–46行。safe_error_type、响应拒绝异常、ErrorClassifier与resolve_failure尚未逐行讲解。

## 所处链路与具体对象

承接Lesson29：原异常→分类过程→构造FailureDescription→内部报告及事件投影。本类只定义描述形状、校验格式；不分类原异常、不重试、不发布事件、不改变run状态。

本例：FailureDescription(source='model', category='transient', reason='timeout', error_type='TimeoutError')。source错误域与stage执行阶段分开，类没有stage；应用投影时补入阶段。未知分类也是合法对象：unknown/unclassified。

## 核心行

- FailureSource=Literal四个字符串值是类型提示，不会自动在运行时拦截非法值。真正检查在__post_init__。
- @dataclass(frozen=True)由标准库生成初始化等方法，构造时接四字段，初始化后调用__post_init__；正常字段重新赋值抛FrozenInstanceError。不是Pydantic自动校验，且冻结不是恶意代码隔离措施。
- 第33–36行四字段分别表示来源、类别、具体原因、原异常类型名，reason不是供应商原始报错正文。
- 第38行__post_init__在生成的__init__写完字段后自动调用；验证抛异常意味着本次构造失败，没有拿到成功返回的description。
- 第39–40行运行时验证source属于四值；非法直接ValueError。
- 第41–43行遍历category/reason，任一非str或不满足正则则拒绝。展开为普通for逐个判断，or短路确保非字符串不会交给正则；any发现True即可停止，不需list化。
- re.fullmatch要求整个字符串匹配；[a-z]首字符小写字母，[a-z0-9_]{0,79}后续0–79个字符，总长1–80。固定码便于日志/评测归类，但该正则只检查格式，不证明语义正确或任意字符串绝不含敏感信息；内置使用固定码，扩展由可信开发代码提供。
- 第44–46行先检查error_type字符串再匹配首字符[A-Za-z_]、后续[A-Za-z0-9_]{0,99}，允许TimeoutError等类名，总长1–100；不允许异常正文/冒号/空格。safe_error_type如何生成另读。

## 本机探针

使用真实类、无网络：正常四字段构造并asdict，四种非法字段分别触发ValueError，正常重新赋值触发FrozenInstanceError，unknown/unclassified构造合法。实际输出valid/model/transient/timeout/TimeoutError；source、category、reason、error_type四项rejected；mutation: FrozenInstanceError；unknown/unclassified: valid。

最小复查命令：

```bash
.venv/bin/python - <<'PY'
from dataclasses import FrozenInstanceError, asdict
from naiyue_test_agent.contracts.failures import FailureDescription
d = FailureDescription('model', 'transient', 'timeout', 'TimeoutError')
assert asdict(d) == dict(source='model', category='transient', reason='timeout', error_type='TimeoutError')
for field, replacement in [('source', 'provider'), ('category', 123), ('reason', 'request timed out'), ('error_type', 'TimeoutError: details')]:
    values = asdict(d)
    values[field] = replacement
    try:
        FailureDescription(**values)
    except ValueError:
        pass
    else:
        raise AssertionError(field)
try:
    d.reason = 'connection_error'
except FrozenInstanceError:
    pass
else:
    raise AssertionError('mutable')
assert FailureDescription('model', 'unknown', 'unclassified', 'TimeoutError').category == 'unknown'
print('FailureDescription probe: PASS')
PY
```

这里只证明数据类的本机行为，不是全链异常、生产安全或全量构建验证。只更新学习文档，不改产品代码/测试/配置/依赖。

## 理解与下一步

- 已讲解，用户理解待反馈；独立实现/面试追问未评估。
- 检查：reason写成'request timed out'会在哪一步被拒绝？此时是否已成功生成description？用户答“检查参数时被拒绝”，正确识别校验拒绝；补充定位为构造中的__post_init__格式检查，构造未成功返回。后半问用户未单独作答，不记全部细节独立掌握。
- 唯一下一步resolve_failure的分类选择，先解释自定义classifier返回None与返回unknown描述的区别，再逐行读实现；分类函数自己的错误如何保留原异常也在该段。
