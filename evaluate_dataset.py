#!/usr/bin/env python3
"""CLI runner: joint rule + document evaluation over the 120-row MoTA dataset."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import APPLICANT_DATASET_FILENAME, get_project_root  # noqa: E402
from core.evaluator import ScholarshipEvaluator  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the MoTA ScholarshipEvaluator (rules + document audit) over "
            f"{APPLICANT_DATASET_FILENAME} and write composite_eval_results.json."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Project root containing MoTA CSV/PDF artefacts (auto-detected).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON output path (default: <root>/composite_eval_results.json).",
    )
    parser.add_argument(
        "--also-eval-results",
        action="store_true",
        help="Also write <root>/eval_results.json (Phase-2 filename) for comparison.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the executive summary printed to stdout.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    root = get_project_root(args.root)
    output = args.output or (root / "composite_eval_results.json")

    evaluator = ScholarshipEvaluator(root=str(root))
    results, summary = evaluator.evaluate_dataset(str(root))
    evaluator.export_json(results, output, summary=summary)
    if args.also_eval_results:
        evaluator.export_json(results, root / "eval_results.json", summary=summary)

    if not args.quiet:
        print(summary.format_report())
        print()
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
