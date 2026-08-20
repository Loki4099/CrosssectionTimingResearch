"""Build the long-only cross-sectional research database in staged form."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from momentum_reversal.pipelines.cross_sectional_database import (  # noqa: E402
    DatabaseLayout,
    build_factor_stage,
    build_fundamental_stage,
    build_identifier_stage,
    build_market_factor_stage,
)
from momentum_reversal.data.tiingo_provider import (  # noqa: E402
    TiingoCredentialError,
    resolve_tiingo_api_token,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=("identifiers", "fundamentals", "market-factors", "factors"),
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--program", type=Path)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="refetch official SEC URLs instead of reusing captured responses",
    )
    parser.add_argument(
        "--limit-ciks",
        type=int,
        help="bounded smoke build; never writes a full freeze marker",
    )
    parser.add_argument(
        "--allow-incomplete-identifiers",
        action="store_true",
        help="smoke-test fundamentals before the identifier gate passes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = DatabaseLayout.load(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
        program_path=args.program,
    )
    user_agent = ""
    if args.stage in {"identifiers", "fundamentals"}:
        env_name = str(layout.program["sec"]["user_agent_env"])
        user_agent = os.environ.get(env_name, "").strip()
        if not user_agent:
            raise SystemExit(
                f"{env_name} is required and must identify the research project/contact"
            )
    if args.stage == "identifiers":
        token = None
        if bool(
            layout.program["identifier_resolution"].get(
                "use_tiingo_name_fallback", False
            )
        ):
            try:
                token = resolve_tiingo_api_token(project_root=args.project_root)
            except TiingoCredentialError:
                token = None
        result = build_identifier_stage(
            layout,
            sec_user_agent=user_agent,
            tiingo_api_token=token,
            refresh=bool(args.refresh),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.stage == "fundamentals":
        result = build_fundamental_stage(
            layout,
            sec_user_agent=user_agent,
            refresh=bool(args.refresh),
            limit_ciks=args.limit_ciks,
            allow_incomplete_identifiers=bool(args.allow_incomplete_identifiers),
            progress=lambda message: print(message, flush=True),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.stage == "market-factors":
        result = build_market_factor_stage(layout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.stage == "factors":
        result = build_factor_stage(layout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    raise AssertionError(args.stage)


if __name__ == "__main__":
    raise SystemExit(main())
