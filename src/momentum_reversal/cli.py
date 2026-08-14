"""Command-line entry points for immutable data and registered experiments."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from momentum_reversal.data import convert_ken_french_daily_rf_zip

from momentum_reversal.pipelines import (
    BaselineRunConfig,
    DatasetBuildConfig,
    G00RunConfig,
    G21RunConfig,
    build_yfinance_dataset,
    prepare_experiment_run,
    run_g00,
    run_g21,
    run_frozen_baselines,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="momentum-reversal",
        description="Build PIT datasets and run registered momentum experiments.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_rf = subparsers.add_parser(
        "convert-french-rf",
        help="convert official Kenneth French daily RF ZIP percent data to decimal CSV",
    )
    convert_rf.add_argument("--input-zip", required=True, type=Path)
    convert_rf.add_argument("--output-csv", required=True, type=Path)
    convert_rf.add_argument("--overwrite", action="store_true")
    convert_rf.set_defaults(handler=_handle_convert_french_rf)

    build = subparsers.add_parser(
        "build-data",
        help="download Yahoo data and freeze one audited PIT dataset",
    )
    build.add_argument("--security-master", required=True, type=Path)
    build.add_argument("--membership", required=True, type=Path)
    build.add_argument("--data-root", type=Path, default=Path("data"))
    build.add_argument("--dataset-version", required=True)
    build.add_argument("--snapshot-id", required=True)
    build.add_argument("--price-start", required=True, type=_date)
    build.add_argument("--research-start", required=True, type=_date)
    build.add_argument("--end", required=True, type=_date)
    build.add_argument(
        "--pit-source",
        required=True,
        help="human-readable real source/provenance for the PIT membership file",
    )
    build.add_argument(
        "--pit-date-semantics",
        required=True,
        choices=("effective", "snapshot_asof"),
        help="whether membership dates are effective boundaries or as-of snapshots",
    )
    build.add_argument("--benchmark-symbol", required=True)
    build.add_argument(
        "--benchmark-label",
        required=True,
        help="e.g. SPY_total_return or SP500_total_return_index",
    )
    build.add_argument(
        "--benchmark-kind",
        required=True,
        choices=("investable_proxy", "total_return_index"),
    )
    build.add_argument(
        "--risk-free-csv",
        type=Path,
        help="optional local CSV with date,rf_return daily decimal returns",
    )
    build.add_argument(
        "--risk-free-source",
        help="required provenance label when --risk-free-csv is supplied",
    )
    build.add_argument("--batch-size", type=int, default=50)
    build.add_argument(
        "--max-snapshot-age-days",
        type=int,
        default=0,
        help=(
            "maximum calendar age of latest-asof PIT snapshots; default 0 "
            "requires an exact snapshot on every union signal date, and any "
            "non-zero allowance makes stale dates a review finding"
        ),
    )
    build.add_argument(
        "--calendar-source",
        choices=("XNYS", "observed"),
        default="XNYS",
        help="XNYS is required for formal runs; observed is useful for offline fixtures",
    )
    build.add_argument(
        "--repair",
        action="store_true",
        help="use yfinance heuristic repair in a distinct dataset version",
    )
    build.add_argument(
        "--allow-invalid-data",
        action="store_true",
        help="return success after freezing an invalid_data snapshot (diagnostics only)",
    )
    build.set_defaults(handler=_handle_build_data)

    run = subparsers.add_parser(
        "run-baseline",
        help="verify one curated dataset and export all 18 baseline paths",
    )
    run.add_argument("--data-root", type=Path, default=Path("data"))
    run.add_argument("--dataset-version", required=True)
    run.add_argument("--output-root", type=Path, default=Path("results"))
    run.add_argument("--run-id", required=True)
    run.add_argument(
        "--costs-bps",
        type=_costs,
        default=(0.0, 5.0, 10.0, 20.0),
        help="comma-separated single-side cost scenarios (default: 0,5,10,20)",
    )
    run.add_argument(
        "--allow-review-dataset",
        action="store_true",
        help="run a review dataset after inspecting QA; run remains non-formal",
    )
    run.add_argument(
        "--allow-invalid-dataset",
        action="store_true",
        help="run an invalid_data dataset for diagnostics; manifest remains flagged",
    )
    run.set_defaults(handler=_handle_run_baseline)

    experiment = subparsers.add_parser(
        "run-experiment",
        help="run or dry-validate one registered systematic experiment",
    )
    experiment.add_argument("--spec", required=True, type=Path)
    experiment.add_argument("--data-root", type=Path, default=Path("data"))
    experiment.add_argument("--dataset-version", required=True)
    experiment.add_argument("--output-root", type=Path, default=Path("results"))
    experiment.add_argument("--run-id", required=True)
    experiment.add_argument(
        "--legacy-baseline-root",
        type=Path,
        default=Path("results/g00-long-only-frozen-v3"),
        help="fresh same-dataset 72-scenario long-only run used by the reproduction gate",
    )
    experiment.add_argument(
        "--reuse-long-only-bundle",
        type=Path,
        help=(
            "optional completed G00 bundle whose strictly verified long-only "
            "sleeve is reused while long-short is recomputed"
        ),
    )
    experiment.add_argument(
        "--reference-g00-root",
        type=Path,
        default=Path("results/experiments/G00/runs/g00-frozen-v3-v1"),
        help="completed same-dataset G00 bundle used by G21 incremental comparisons",
    )
    experiment.add_argument(
        "--workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="G21 core-path worker processes (default: up to 4)",
    )
    experiment.add_argument(
        "--allow-review-dataset",
        action="store_true",
        help="explicitly allow a review/prototype dataset; output remains non-formal",
    )
    experiment.add_argument(
        "--dry-run",
        action="store_true",
        help="validate TOML, IDs, matrix size, and output path without writing",
    )
    experiment.set_defaults(handler=_handle_run_experiment)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


def _handle_build_data(args: argparse.Namespace) -> int:
    result = build_yfinance_dataset(
        DatasetBuildConfig(
            data_root=args.data_root,
            dataset_version=args.dataset_version,
            snapshot_id=args.snapshot_id,
            security_master_path=args.security_master,
            membership_path=args.membership,
            research_start=args.research_start,
            price_start=args.price_start,
            end=args.end,
            pit_source=args.pit_source,
            pit_date_semantics=args.pit_date_semantics,
            benchmark_symbol=args.benchmark_symbol,
            benchmark_label=args.benchmark_label,
            benchmark_kind=args.benchmark_kind,
            risk_free_path=args.risk_free_csv,
            risk_free_source=args.risk_free_source,
            batch_size=args.batch_size,
            repair=args.repair,
            calendar_source=args.calendar_source,
            max_snapshot_age_days=args.max_snapshot_age_days,
        )
    )
    print(f"dataset_version={result.dataset_version}")
    print(f"qa_status={result.status}")
    print("research_tier=prototype")
    print("formal_eligible=false")
    print(f"manifest={result.manifest_path}")
    if result.failed_downloads:
        print(f"failed_download_symbols={len(result.failed_downloads)}")
    if result.status == "invalid_data" and not args.allow_invalid_data:
        print(
            "dataset was frozen for diagnosis but is not eligible for a formal run",
            file=sys.stderr,
        )
        return 2
    return 0


def _handle_convert_french_rf(args: argparse.Namespace) -> int:
    destination = convert_ken_french_daily_rf_zip(
        args.input_zip, args.output_csv, overwrite=args.overwrite
    )
    print(f"risk_free_csv={destination.resolve()}")
    print("units=decimal_return_per_exchange_session")
    print("conversion=Kenneth_French_RF_percent_divided_by_100")
    return 0


def _handle_run_baseline(args: argparse.Namespace) -> int:
    result = run_frozen_baselines(
        BaselineRunConfig(
            data_root=args.data_root,
            dataset_version=args.dataset_version,
            output_root=args.output_root,
            run_id=args.run_id,
            costs_bps=args.costs_bps,
            allow_review_dataset=args.allow_review_dataset,
            allow_invalid_dataset=args.allow_invalid_dataset,
        )
    )
    print(f"run_id={result.run_id}")
    print(f"strategy_paths={result.path_count}")
    print(f"scenario_results={result.scenario_count}")
    print(f"formal_run_eligible={str(result.formal_run_eligible).lower()}")
    print(f"manifest={result.manifest_path}")
    return 0


def _handle_run_experiment(args: argparse.Namespace) -> int:
    context = prepare_experiment_run(
        args.spec,
        run_id=args.run_id,
        dataset_version=args.dataset_version,
        data_root=args.data_root,
        output_root=args.output_root,
        require_dataset_manifest=False,
    )
    if not args.dry_run:
        if context.group_id not in {"G00", "G21"}:
            raise RuntimeError(
                f"actual execution is currently implemented only for G00/G21, got "
                f"{context.group_id}"
            )
        if context.group_id == "G00":
            result = run_g00(
                G00RunConfig(
                    context=context,
                    legacy_baseline_root=args.legacy_baseline_root,
                    reuse_long_only_bundle=args.reuse_long_only_bundle,
                    allow_review_dataset=args.allow_review_dataset,
                )
            )
            print(f"run_id={result.run_id}")
            print(f"strategy_paths={result.strategy_count}")
            print(f"main_scenarios={result.scenario_count}")
            print(f"legacy_controls={result.legacy_control_count}")
            print(
                "reused_long_only_scenarios="
                f"{result.reused_long_only_scenario_count}"
            )
            print(
                "computed_long_short_scenarios="
                f"{result.computed_long_short_scenario_count}"
            )
        else:
            result = run_g21(
                G21RunConfig(
                    context=context,
                    reference_g00_root=args.reference_g00_root,
                    allow_review_dataset=args.allow_review_dataset,
                    workers=args.workers,
                )
            )
            print(f"run_id={result.run_id}")
            print(f"strategy_paths={result.strategy_count}")
            print(f"main_scenarios={result.scenario_count}")
            print(f"comparison_rows={result.comparison_count}")
            print(
                "conditional_diagnostic_rows="
                f"{result.conditional_diagnostic_count}"
            )
        print(f"formal_run_eligible={str(result.formal_run_eligible).lower()}")
        print(f"manifest={result.manifest_path}")
        return 0
    print(f"group_id={context.group_id}")
    print(f"spec_id={context.spec_id}")
    print(f"strategy_paths={len(context.strategies)}")
    print("portfolio_modes=long_only,long_short")
    print(f"resolved_spec_sha256={context.group.resolved_sha256}")
    print(f"bundle_dir={context.bundle_dir}")
    print("execution=not_started")
    return 0


def _date(value: str) -> pd.Timestamp:
    try:
        date = pd.Timestamp(value)
    except Exception as error:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from error
    if date.tzinfo is not None:
        date = date.tz_localize(None)
    return date.normalize()


def _costs(value: str) -> tuple[float, ...]:
    try:
        costs = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("costs must be comma-separated numbers") from error
    if not costs or any(cost < 0 for cost in costs):
        raise argparse.ArgumentTypeError("costs must be non-empty and non-negative")
    if len(set(costs)) != len(costs):
        raise argparse.ArgumentTypeError("cost scenarios must be unique")
    return costs


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
