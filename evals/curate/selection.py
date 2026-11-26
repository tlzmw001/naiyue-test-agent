"""Validate scan rules and select reproducible samples from frozen run metadata."""
from decimal import Decimal
from hashlib import sha256
import json

STATUSES = {"completed", "failed", "stopped", "interrupted"}
OUTCOMES = {"success", "error_result", "exception", "not_executed", "outcome_unknown"}
SCOPES = {"root", "child", "both"}
THRESHOLDS = {"min_model_calls", "min_tool_calls", "min_user_turns"}


def _nonempty_list(value):
    return isinstance(value, list) and bool(value)


def _choices(value, allowed):
    return _nonempty_list(value) and all(isinstance(item, str) and item in allowed for item in value)


def parse_rules(document):
    if not isinstance(document, dict) or set(document) != {"any"} or not _nonempty_list(document["any"]):
        raise ValueError("rules must contain a nonempty 'any' list")
    for rule in document["any"]:
        if not isinstance(rule, dict) or set(rule) != {"all"} or not _nonempty_list(rule["all"]):
            raise ValueError("each rule must contain a nonempty 'all' list")
        for condition in rule["all"]:
            if not isinstance(condition, dict) or len(condition) != 1:
                raise ValueError("each condition must have one operator")
            operator, value = next(iter(condition.items()))
            if operator == "run_status" and _choices(value, STATUSES):
                continue
            if operator == "tool_error" and isinstance(value, dict):
                if ({"name", "outcomes"} <= set(value) <= {"name", "outcomes", "scope"}
                        and isinstance(value["name"], str) and value["name"].strip()
                        and _choices(value["outcomes"], OUTCOMES)
                        and value.get("scope", "root") in tuple(SCOPES)):
                    continue
            if operator in THRESHOLDS:
                if operator == "min_tool_calls" and isinstance(value, dict):
                    if set(value) == {"count", "scope"} and value["scope"] in tuple(SCOPES):
                        value = value["count"]
                if type(value) is int and value >= 0:
                    continue
            raise ValueError(f"invalid rule condition: {operator}")
    return document["any"]


def _tools(row, scope):
    return [tool for tool in row["tools"] if scope == "both" or tool["scope"] == scope]


def _evaluate(row, condition):
    operator, value = next(iter(condition.items()))
    if operator == "run_status":
        return row["status"] in value if row["status"] else None
    if operator == "tool_error":
        tools = [tool for tool in _tools(row, value.get("scope", "root"))
                 if tool["name"] in (None, value["name"])]
        if any(tool["name"] == value["name"] and tool["outcome"] in value["outcomes"] for tool in tools):
            return True
        possible = any(tool["outcome"] in (None, "outcome_unknown") or tool["outcome"] in value["outcomes"]
                       for tool in tools)
        return False if row["tools_complete"] and not possible else None
    if operator == "min_tool_calls":
        scope, minimum = (value["scope"], value["count"]) if isinstance(value, dict) else ("root", value)
        count, complete = len(_tools(row, scope)), row["tools_complete"]
    elif operator == "min_model_calls":
        minimum, count, complete = value, row["model_calls"], row["models_complete"]
    else:
        minimum, count, complete = value, row["user_turns"], row["user_turns_complete"]
    if count >= minimum:
        return True
    return False if complete else None


def select(rows, rules, percentages, seed):
    pools = {name: [] for name in (("matched", "unmatched", "unknown") if rules else ("all",))}
    index = []
    for row in sorted(rows, key=lambda item: item["key"]):
        matches, unknown = [], []
        for number, rule in enumerate(rules, 1):
            values = [_evaluate(row, condition) for condition in rule["all"]]
            if all(value is True for value in values):
                matches.append(number)
            elif False not in values:
                unknown.append(number)
        pool = ("matched" if matches else "unknown" if unknown else "unmatched") if rules else "all"
        entry = dict(row, pool=pool, matching_rules=matches, uncertain_rules=unknown)
        index.append(entry)
        pools[pool].append(entry)

    def rank(entry):
        key = entry["key"]
        return sha256(json.dumps([seed, *key], ensure_ascii=True, separators=(",", ":")).encode()).digest(), key

    summary, selected = {}, []
    for pool, entries in pools.items():
        percentage = percentages.get(pool, Decimal(0))
        numerator, denominator = percentage.as_integer_ratio()
        count = len(entries) * numerator // (100 * denominator)
        summary[pool] = {"population": len(entries), "percent": str(percentage), "selected": count}
        selected.extend(entry["key"] for entry in sorted(entries, key=rank)[:count])
    return {"index": index, "pools": summary, "selected": selected}
