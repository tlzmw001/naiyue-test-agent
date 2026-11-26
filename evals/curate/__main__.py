"""Hand-run collection for Agent evaluation datasets."""
import argparse
import asyncio
from collections import Counter
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path
from secrets import token_hex
import shlex
import sqlite3
from tempfile import TemporaryDirectory

from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue

from .history import collect, timestamp
from .selection import parse_rules, select


def _percent(raw):
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("expected percentage between 0 and 100") from None
    if not value.is_finite() or not 0 <= value <= 100 or len(value.as_tuple().digits) > 18 or value.as_tuple().exponent < -18:
        raise argparse.ArgumentTypeError("expected percentage between 0 and 100 with up to 18 digits")
    return value


def scan(args):
    from .batch import batch_writer
    from .material import extract_batch

    if args.resume:
        if any(value is not None for value in (
            args.start, args.end, args.rules, args.all_percent, args.matched_percent, args.unmatched_percent, args.seed
        )) or args.project:
            raise ValueError("--resume uses the frozen project scope, rules and seed")
        batch = args.output.absolute()
        # extract_batch checks source/output custody before the first write.
        from .history import source_projects
        source_projects(args.data_dir.resolve(), batch.resolve())
        with batch_writer(batch):
            result = extract_batch(batch, args.data_dir.resolve())
        print(f"{len(result['candidates'])} selected candidate material record(s); {batch}")
        return 0
    start, end = timestamp(args.start), timestamp(args.end)
    if not start or not end or start >= end:
        raise ValueError("--from and --to must be timezone-aware with --from earlier than --to")
    rules = parse_rules(json.loads(args.rules.read_text(encoding="utf-8"))) if args.rules else []
    if (rules and args.all_percent is not None) or (not rules and any(
            value is not None for value in (args.matched_percent, args.unmatched_percent))):
        raise ValueError("use --all-percent without rules or --matched-percent/--unmatched-percent with rules")
    percentages = {name: default if value is None else value for name, default, value in (
        ("all", Decimal(100), args.all_percent), ("matched", Decimal(100), args.matched_percent),
        ("unmatched", Decimal(0), args.unmatched_percent))}
    output = args.output.resolve()
    if args.output.is_symlink() or output.exists():
        raise ValueError("output must be a new batch directory")
    rows, projects = collect(args.data_dir.resolve(), output, args.project, start, end)
    seed = args.seed if args.seed is not None else token_hex(16)
    result = select(rows, rules, percentages, seed)
    report = {"schema_version": 1, "sampling_algorithm": "sha256-exact-quota-v1", "seed": seed,
              "range": {"from": start.isoformat(), "to": end.isoformat(), "projects": args.project or "all"},
              "projects": projects, "rules": {"any": rules} if rules else None,
              "time_basis_counts": dict(Counter(row["time_basis"] for row in rows)), **result}
    summary = ["# 历史运行采样", "", f"窗口内可识别的主 Run 共 {len(rows)} 条，选中 {len(result['selected'])} 条。", ""]
    labels = {"all": "全部", "matched": "命中", "unmatched": "明确未命中", "unknown": "证据不足"}
    for name, pool in result["pools"].items():
        summary.append(f"- {labels[name]} {pool['population']} 条，按 {pool['percent']}% 选中 {pool['selected']} 条")
    issues = sum("issue" in project for project in projects.values())
    summary.extend(["", f"跳过项目 {issues} 个。逐项目缺口与完整运行索引见 manifest.json。"])
    selected = {tuple(key) for key in result["selected"]}
    if selected:
        summary.extend(["", "## 入选运行", "", "| Run | 样本池 | 终态 | 工具异常 | 命中规则 |",
                        "|---|---|---|---|---|"])
        for row in result["index"]:
            if tuple(row["key"]) not in selected:
                continue
            failures = ", ".join(f"{tool['name'] or '工具未知'} ({tool['outcome']})" for tool in row["tools"]
                                 if tool["outcome"] in ("error_result", "exception", "outcome_unknown"))
            fields = ("/".join(row["key"]), labels[row["pool"]], row["status"] or "证据不足", failures or "—",
                      ", ".join(map(str, row["matching_rules"])) or "—")
            summary.append("| " + " | ".join(escape(field).replace("|", "&#124;").replace("\n", " ")
                                            for field in fields) + " |")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".curation-", dir=output.parent) as staging:
        folder = Path(staging) / "batch"
        folder.mkdir(mode=0o700)
        (folder / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (folder / "report.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
        folder.rename(output)
    with batch_writer(output):
        extract_batch(output, args.data_dir.resolve())
    print(f"{len(result['selected'])}/{len(rows)} selected; {issues} project(s) skipped; {output}")
    return 2 if projects and issues == len(projects) else 0


def review_or_export(args):
    from .batch import batch_writer, read_json
    from .publishing import load_baseline, publish, read_version
    from .review import load_decisions, load_review, render_review, save_decision

    batch = args.batch.absolute()
    with batch_writer(batch):
        drafts = load_review(batch)
        if args.command == "export":
            output, summary, datasets = publish(batch, args.version, drafts, load_decisions(batch), args.previous)
            print(f"{output} · 新增 {summary['added']} · 沿用 {summary['retained']} · 重复 {summary['duplicates']}")
            for direction, entries in datasets.items():
                flags = []
                if any(row["case"]["checks"].get("rubric") for row in entries):
                    flags.append("--judge-model JUDGE_ID")
                if any(row["case"].get("simulation") for row in entries):
                    flags.append("--user-model USER_ID")
                dataset = output / (direction + ".jsonl")
                print(f"python -m evals run --dataset {shlex.quote(str(dataset))} "
                      "--config MODELS_CONFIG --model AGENT_ID" + (" " + " ".join(flags) if flags else ""))
            return 0

        known = load_baseline()
        if args.previous:
            _, entries = read_version(args.previous)
            known.extend(entries)
        if args.decision:
            if not all((args.candidate, args.direction, args.revision)):
                raise ValueError("a decision requires candidate, direction and revision from review.html")
            decisions = save_decision(batch, drafts, candidate=args.candidate, direction=args.direction,
                                      revision=args.revision, decision=args.decision, existing=args.existing,
                                      known=known, examples=read_json(args.examples) if args.examples else None)
        else:
            if any((args.candidate, args.direction, args.revision, args.existing, args.examples)):
                raise ValueError("decision options require --decision")
            decisions = load_decisions(batch)
        print(render_review(batch, drafts, decisions, known))
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("scan")
    command.add_argument("--data-dir", required=True, type=Path)
    command.add_argument("--project", action="append", default=[])
    command.add_argument("--from", dest="start")
    command.add_argument("--to", dest="end")
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--rules", type=Path)
    command.add_argument("--all-percent", type=_percent)
    command.add_argument("--matched-percent", type=_percent)
    command.add_argument("--unmatched-percent", type=_percent)
    command.add_argument("--seed")
    command.add_argument("--resume", action="store_true", help="extract material for an existing frozen batch")
    annotation = commands.add_parser("annotate")
    annotation.add_argument("--batch", type=Path, required=True)
    annotation.add_argument("--candidate", action="append", default=[])
    annotation.add_argument("--config", type=Path)
    annotation.add_argument("--model")
    annotation.add_argument("--draft", type=Path, help="import structured manual proposals for one candidate")
    review = commands.add_parser("review")
    review.add_argument("--batch", type=Path, required=True)
    review.add_argument("--previous", type=Path)
    review.add_argument("--candidate")
    review.add_argument("--direction")
    review.add_argument("--revision")
    review.add_argument("--decision", choices=("accept", "needs-material", "link", "discard"))
    review.add_argument("--existing", help="existing case ID for a link decision")
    review.add_argument("--examples", type=Path, help="positive and negative scorer input JSON")
    export = commands.add_parser("export")
    export.add_argument("--batch", type=Path, required=True)
    export.add_argument("--version", required=True)
    export.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            return scan(args)
        if args.command in ("review", "export"):
            return review_or_export(args)
        from .annotation import annotate_batch
        from .batch import batch_writer, read_json

        if args.draft:
            if args.config or args.model or len(args.candidate) != 1:
                raise ValueError("--draft requires exactly one --candidate and no model configuration")
        elif not args.config or not args.config.is_file() or not args.model:
            raise ValueError("provide an explicit --config and --model, or import --draft")
        with batch_writer(args.batch.absolute()):
            manual = read_json(args.draft) if args.draft else None
            if args.draft and not isinstance(manual, dict):
                raise ValueError("manual annotation must be a JSON object")
            errors = asyncio.run(annotate_batch(args.batch.absolute(), ids=args.candidate, config=args.config,
                                               model_id=args.model, manual=manual))
        print(f"annotation complete; {errors} candidate error(s); {args.batch / 'annotations'}")
        return 2 if errors else 0
    except (OSError, ValueError, sqlite3.Error, CleanupIssue) as error:
        parser.exit(2, f"{args.command} failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
