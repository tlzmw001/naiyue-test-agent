"""Aggregate per-attempt records and write a self-contained local report."""
import html
import json
from math import ceil
from statistics import mean


GRADE_LABELS = {"exact": "精确", "ordered": "按序", "unordered": "任序",
                "partial": "部分", "divergent": "偏离", "review": "待复核"}


def percentile(values, quantile):
    if not values:
        return None
    return sorted(values)[ceil(len(values) * quantile) - 1]


def summarize(rows):
    groups = {}
    for mode in sorted({row["mode"] for row in rows}):
        group = [row for row in rows if row["mode"] == mode]
        durations = [row["agent_seconds"] for row in group
                     if row.get("agent_seconds") is not None]
        first = [row["first_text_seconds"] for row in group
                 if row.get("first_text_seconds") is not None]
        token_rows = [row for row in group if row.get("output_tokens") is not None
                      and row.get("agent_seconds")]
        graded = [row for row in group if row["status"] != "error"]
        successes = sum(row["status"] == "pass" for row in graded)
        outcomes = [row["end_to_end"] for row in graded if row.get("end_to_end") is not None]
        groups[mode] = {
            "attempts": len(group),
            "passed": successes,
            "assertion_pass_rate": successes / len(graded) if graded else None,
            "end_to_end_samples": len(outcomes),
            "end_to_end_success_rate": mean(outcomes) if outcomes else None,
            "failed": sum(row["status"] == "fail" for row in group),
            "errors": sum(row["status"] == "error" for row in group),
            "latency_samples": len(durations),
            "mean_seconds": mean(durations) if durations else None,
            "p95_seconds": percentile(durations, .95),
            "p99_seconds": percentile(durations, .99),
            "first_text_samples": len(first),
            "token_rate_samples": len(token_rows),
            "output_tokens_per_agent_second": (sum(row["output_tokens"] for row in token_rows)
                / sum(row["agent_seconds"] for row in token_rows) if token_rows else None),
            "mean_first_text_seconds": mean(first) if first else None,
        }
    pass_4 = {}
    for case_id in sorted({row["case_id"] for row in rows if row["attempts"] == 4}):
        statuses = [row["status"] for row in rows if row["case_id"] == case_id]
        pass_4[case_id] = (all(status == "pass" for status in statuses)
                           if len(statuses) == 4 and "error" not in statuses else None)
    summary = {"groups": groups, "pass_4": pass_4}
    dynamic = {row["case_id"] for row in rows if row.get("simulated_dialogue")}
    if dynamic:
        outcomes = [pass_4[case_id] for case_id in sorted(dynamic) if pass_4[case_id] is not None]
        summary["simulated_dialogue"] = {
            "cases": len(dynamic), "graded_cases": len(outcomes), "passed_cases": sum(outcomes),
            "pass_4_rate": mean(outcomes) if outcomes else None,
        }
    collaboration = [row["collaboration"] for row in rows if "collaboration" in row]
    if collaboration:
        names = sorted({name for checks in collaboration for name in checks})
        summary["collaboration"] = {
            name: {"passed": sum(checks.get(name) is True for checks in collaboration),
                   "attempts": sum(name in checks for checks in collaboration)}
            for name in names
        }
    traces = [row["trajectory"] for row in rows if "trajectory" in row]
    if traces:
        scores = [t["score"] for t in traces if t["score"] is not None]
        summary["trajectory"] = {
            "assessed": len(traces), "graded": len(scores),
            "grades": {g: sum(t["grade"] == g for t in traces) for g in GRADE_LABELS},
            "mean_score": mean(scores) if scores else None,
            **{"mean_" + key: mean(t[key] for t in traces) for key in ("precision", "recall", "f1")},
        }
    return summary


def write_report(output, rows, manifest):
    summary = summarize(rows)
    (output / "results.json").write_text(json.dumps(
        {"manifest": manifest, "summary": summary, "attempts": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")

    def cell(value):
        return "" if value is None else html.escape(str(value))

    columns = ("attempts", "passed", "failed", "errors", "assertion_pass_rate", "end_to_end_success_rate",
               "end_to_end_samples", "latency_samples", "mean_seconds",
               "p95_seconds", "p99_seconds", "mean_first_text_seconds", "first_text_samples",
               "output_tokens_per_agent_second", "token_rate_samples")
    header = "".join(f"<th>{cell(name)}</th>" for name in columns)
    totals = "".join(
        f"<tr><th>{cell(mode)}</th>" + "".join(f"<td>{cell(group[key])}</td>" for key in columns)
        + "</tr>" for mode, group in summary["groups"].items())
    trajectory_html = ""
    if "trajectory" in summary:
        stats = summary["trajectory"]
        counts = " · ".join(f"{label} {stats['grades'][g]}" for g, label in GRADE_LABELS.items())
        table_rows = []
        for row in rows:
            t = row.get("trajectory")
            if t is None:
                continue
            values = (row["case_id"], row["equivalence"], row["boundary"], "通过" if row["end_to_end"] else "失败",
                      GRADE_LABELS[t["grade"]], f"{t['matches']}/{t['actual']}/{t['expected']}",
                      f"{t['precision']:.2f}", f"{t['recall']:.2f}",
                      round(t["score"], 2) if t["score"] is not None else None)
            table_rows.append("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in values) + "</tr>")
        columns = ("用例", "等价类", "边界", "任务验收", "轨迹档位", "匹配/实际/参考", "精确率", "召回率", "分数")
        average = stats["mean_score"]
        trajectory_html = (f"<h2>轨迹评分</h2><p>{counts}</p>"
                           f"<p>已分档 {stats['graded']}/{stats['assessed']}，平均分 "
                           f"{cell(round(average, 2) if average is not None else None)}。"
                           f"逐题平均精确率 {stats['mean_precision']:.3f}，召回率 {stats['mean_recall']:.3f}。</p>"
                           "<table><tr>" + "".join(f"<th>{c}</th>" for c in columns)
                           + "</tr>" + "".join(table_rows) + "</table>")
    collaboration_html = ""
    if "collaboration" in summary:
        labels = {"routing": "选择角色", "handoff": "交接字段", "child_execution": "子 Agent 实际执行",
                  "child_report": "子报告回传", "no_delegation": "遵守禁止委派"}
        items = "".join(f"<li>{labels[name]} {count['passed']}/{count['attempts']}</li>"
                        for name, count in summary["collaboration"].items())
        collaboration_html = f"<h2>多 Agent 协作</h2><ul>{items}</ul>"
    dialogue_html = ""
    if "simulated_dialogue" in summary:
        stats = summary["simulated_dialogue"]
        dialogue_html = (f"<h2>动态多轮交互</h2><p>四次全通过 {stats['passed_cases']}/{stats['graded_cases']}，"
                         f"可判定 {stats['graded_cases']}/{stats['cases']} 题。</p>")
    attempts = "".join(
        f"<details><summary>{cell(row['case_id'])} · {cell(row['attempt'])} · "
        f"{cell(row['status'])} · {cell(row.get('agent_seconds'))} s</summary>"
        f"<pre>{cell(json.dumps(row, ensure_ascii=False, indent=2))}</pre></details>"
        for row in rows)
    (output / "report.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Naiyue 评测</title>'
        '<style>body{font:16px system-ui;margin:3rem}table{border-collapse:collapse}'
        'th,td{padding:.5rem;border:1px solid #aaa}details{margin:.8rem 0}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
        f'<h1>Naiyue 评测</h1><p>执行模式与耗时汇总（秒）</p>'
        f'<table><tr><th>mode</th>{header}</tr>{totals}</table>'
        f'{trajectory_html}{collaboration_html}{dialogue_html}<h2>多轮 pass^4</h2><pre>{cell(json.dumps(summary["pass_4"], ensure_ascii=False))}</pre>'
        f'<h2>逐次执行</h2>{attempts}</html>', encoding="utf-8")
