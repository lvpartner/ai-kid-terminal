#!/usr/bin/env python3
"""Fail a release when a fact benchmark misses the public performance contract."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--route", default="route_a")
    parser.add_argument(
        "--budget",
        type=Path,
        default=Path("evaluations/performance-budget.json"),
    )
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    budget = json.loads(args.budget.read_text())
    route = report[args.route]
    questions = int(report["question_count"])
    failures: list[str] = []
    if questions < budget["minimum_questions"]:
        failures.append(f"question_count={questions}")
    completed_rate = float(route["completed"]) / questions
    checks = {
        "completed_rate": (completed_rate, ">=", budget["minimum_completed_rate"]),
        "mean_accuracy_0_to_5": (
            route["mean_accuracy_0_to_5"],
            ">=",
            budget["minimum_mean_accuracy"],
        ),
        "unsupported_precise_claims": (
            route["unsupported_precise_claims"],
            "<=",
            budget["maximum_unsupported_precise_claims"],
        ),
        "first_audio_p50_seconds": (
            route["first_audio_p50_seconds"],
            "<=",
            budget["maximum_first_audio_p50_seconds"],
        ),
        "first_audio_p95_seconds": (
            route["first_audio_p95_seconds"],
            "<=",
            budget["maximum_first_audio_p95_seconds"],
        ),
        "total_p95_seconds": (
            route["total_p95_seconds"],
            "<=",
            budget["maximum_total_p95_seconds"],
        ),
    }
    for name, (actual, operator, expected) in checks.items():
        passed = (
            float(actual) >= float(expected)
            if operator == ">="
            else float(actual) <= float(expected)
        )
        if not passed:
            failures.append(f"{name}={actual} expected {operator}{expected}")
    if failures:
        raise SystemExit("performance budget failed: " + "; ".join(failures))
    print(f"performance budget passed: {questions} questions, route={args.route}")


if __name__ == "__main__":
    main()
