"""Independent entry point for the Naiyue evaluation suite."""
import argparse
from contextlib import chdir
from hashlib import sha256
import logging
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import time_ns

from .collaboration import score_collaboration, score_collaboration_rubric
from .cases import load_cases
from .execution import execute
from .trajectory import assess_trace
from .trajectory_workspace import fixture_files
from .report import write_report
from .scoring import judge, score_rubric

logger = logging.getLogger(__name__)


def run(args):
    # Set before importing Inspect. This command owns its process.
    os.environ["INSPECT_TELEMETRY"] = ""
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

    from inspect_ai import Task, eval as inspect_eval
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import Score, accuracy, scorer
    from inspect_ai.solver import solver
    from naiyue_test_agent import application

    config = args.config.resolve()
    if not config.is_file():
        raise ValueError("the explicit model configuration file does not exist")
    cases = load_cases(args.dataset)
    if not args.judge_model and any(case.collaboration is not None and case.checks.rubric for case in cases):
        raise ValueError("open collaboration cases require --judge-model")
    if not args.user_model and any(case.simulation for case in cases):
        raise ValueError("dynamic dialogue cases require --user-model")
    manifest = {
        "model": args.model, "judge_model": args.judge_model, "user_model": args.user_model, "concurrency": 1,
        "dataset": args.dataset.name, "dataset_sha256": sha256(args.dataset.read_bytes()).hexdigest(),
        "fixtures": {
            case.id: {str(path.relative_to(case.fixture)): sha256(path.read_bytes()).hexdigest()
                      for path in sorted(fixture_files(case.fixture))}
            for case in cases if case.fixture is not None
        },
        "git_revision": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
    }
    by_id = {case.id: case for case in cases}
    rows = []

    @solver
    def run_naiyue():
        async def solve(state, generate):
            case_id, attempt = str(state.sample_id).rsplit(":", 1)
            case = by_id[case_id]
            evidence = {"answers": [], "calls": [], "turn_seconds": [], "first_text_seconds": [],
                        "completed": True, "output_tokens": []}
            row = {"case_id": case.id, "mode": case.mode, "attempt": int(attempt),
                   "attempts": case.attempts, "evidence": evidence}
            try:
                await execute(case, config, args.model, evidence, args.user_model)
            except Exception as error:
                logger.error("eval_execution_error case=%s type=%s", case.id, type(error).__name__)
                stage = "simulator" if evidence.get("dialogue_stop") == "simulator_error" else "execution"
                row.update(status="error", error_stage=stage, error=type(error).__name__)
            if case.simulation:
                row["simulated_dialogue"] = True
            row["agent_seconds"] = sum(evidence["turn_seconds"]) if evidence["turn_seconds"] else None
            row["first_text_seconds"] = next(iter(evidence["first_text_seconds"]), None)
            usage = evidence["output_tokens"]
            row["output_tokens"] = sum(usage) if usage and None not in usage else None
            state.store.set("naiyue_evaluation", row)
            return state
        return solve

    @scorer(metrics=[accuracy()])
    def score_naiyue():
        async def score(state, target):
            row = state.store.get("naiyue_evaluation")
            case = by_id[row["case_id"]]
            evidence = row["evidence"]
            if row.get("status") != "error":
                try:
                    checks = judge(case, evidence) if evidence["completed"] else {"completed": False}
                    row.update(checks=checks, status="pass" if all(checks.values()) else "fail")
                    if case.checks.final_ids is not None:
                        row["end_to_end"] = checks.get("final_ids", False)
                    if case.collaboration is not None:
                        dimensions = score_collaboration(case, evidence)
                        checks.update(dimensions)
                        row["collaboration"] = dimensions
                        row["status"] = "pass" if all(checks.values()) else "fail"
                    if case.trajectory:
                        row.update(assess_trace(case, evidence, checks))
                    if case.checks.rubric:
                        row["rubric"] = None
                        if args.judge_model:
                            catalog = application.load_model_catalog(config)
                            try:
                                catalog.prepare_async_clients()
                                grade = score_collaboration_rubric if case.collaboration is not None else score_rubric
                                verdict = await grade(catalog.get(args.judge_model), case, evidence)
                            finally:
                                await catalog.aclose()
                            row["rubric"] = verdict
                        if case.collaboration is not None:
                            delivery = verdict["final_delivery"]["score"] == 2
                            row["end_to_end"] = evidence["completed"] and delivery
                            for name, result in verdict.items():
                                checks[f"rubric_{name}"] = result["score"] == 2
                            row["status"] = "pass" if all(checks.values()) else "fail"
                        elif row["rubric"] is not None and row["rubric"]["score"] < 2:
                            row["status"] = "fail"
                except Exception as error:
                    logger.error("eval_scoring_error case=%s type=%s", case.id, type(error).__name__)
                    row.update(status="error", error_stage="scoring", error=type(error).__name__)
            rows.append(row)
            return Score(value=1 if row["status"] == "pass" else 0, metadata={"result": row})
        return score

    samples = [Sample(id=f"{case.id}:{attempt}", input=case.turns[0])
               for case in cases for attempt in range(1, case.attempts + 1)]
    task = Task(name="naiyue_agent", dataset=samples, solver=run_naiyue(), scorer=score_naiyue())
    output = args.output.resolve() / f"run-{time_ns()}"
    output.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(prefix="naiyue-inspect-") as directory, chdir(directory):
        logs = inspect_eval(task, model="mockllm/model", log_dir=str(output / "inspect"),
                            max_samples=1, display="plain")
    completed = {(row["case_id"], row["attempt"]) for row in rows}
    for sample in samples:
        case_id, attempt = sample.id.rsplit(":", 1)
        if (case_id, int(attempt)) not in completed:
            case = by_id[case_id]
            rows.append({"case_id": case_id, "mode": case.mode, "attempt": int(attempt),
                         "attempts": case.attempts, "status": "error", "error_stage": "inspect",
                         "simulated_dialogue": case.simulation is not None})
    write_report(output, rows, manifest)
    print(f"Report: {output / 'report.html'}")
    if any(log.status != "success" for log in logs) or any(row["status"] == "error" for row in rows):
        return 2
    return int(any(row["status"] in ("fail", "review") for row in rows))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run")
    command.add_argument("--dataset", type=Path, default=Path("evals/data/smoke.jsonl"))
    command.add_argument("--config", type=Path, required=True, help="Explicit Naiyue model configuration")
    command.add_argument("--model", required=True)
    command.add_argument("--judge-model")
    command.add_argument("--user-model", help="Model that simulates later user turns")
    command.add_argument("--output", type=Path, default=Path("build/evals"))
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ImportError, OSError, ValueError) as error:
        parser.exit(2, f"Evaluation setup failed: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
