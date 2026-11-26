"""Check standalone boolean asserts against the baseline and single-input mutants."""
import ast


def boolean_expr(node, values):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "eligible":
        if len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if type(value) is int and str(value) in values:
                return values[str(value)]
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not boolean_expr(node.operand, values)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left = boolean_expr(node.left, values)
        right = boolean_expr(node.comparators[0], values)
        if isinstance(node.ops[0], (ast.Eq, ast.Is)):
            return left == right
        if isinstance(node.ops[0], (ast.NotEq, ast.IsNot)):
            return left != right
    raise ValueError("unsupported assertion")


def checks_boundaries(source, values):
    tree = ast.parse(source)
    assertions = []
    imported = False
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "eligibility" and not node.level:
            if len(node.names) == 1 and node.names[0].name == "eligible" and not node.names[0].asname:
                imported = True
                continue
        if (not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_")
                or node.name in names or node.decorator_list or node.type_params
                or node.returns and not (isinstance(node.returns, ast.Constant) and node.returns.value is None)
                or any((node.args.args, node.args.posonlyargs,
                    node.args.kwonlyargs, node.args.kwarg, node.args.vararg))):
            raise ValueError("expected plain pytest test functions")
        names.add(node.name)
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue
            if not isinstance(stmt, ast.Assert):
                raise ValueError("expected independent asserts")
            assertions.append(stmt.test)
    if not imported or not assertions or not all(boolean_expr(a, values) for a in assertions):
        return False
    return all(any(not boolean_expr(a, {**values, key: not value}) for a in assertions)
               for key, value in values.items())
