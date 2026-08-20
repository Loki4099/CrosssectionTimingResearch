from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from momentum_reversal.data.research_catalog import (
    ResearchCatalogError,
    rebuild_research_catalog,
)


class ResearchCatalogTests(unittest.TestCase):
    def test_catalog_rebuilds_views_without_mutating_evidence(self) -> None:
        try:
            import duckdb
        except ImportError:
            self.skipTest("duckdb is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "factor_values.parquet"
            pd.DataFrame(
                {"signal_date": ["2020-01-31"], "sid": ["A"], "score": [1.0]}
            ).to_parquet(evidence, index=False)
            digest_before = hashlib.sha256(evidence.read_bytes()).hexdigest()
            registry = root / "registry.csv"
            registry.write_text("factor_id,display_name\nF1,Factor 1\n", encoding="utf-8")
            manifest = root / "bundle.json"
            manifest.write_text(
                json.dumps(
                    {
                        "data_bundle_id": "bundle-v1",
                        "components": [
                            {
                                "component_id": "factor_values",
                                "component_kind": "parquet",
                                "path": evidence.name,
                                "view_name": "v_factor_values",
                                "row_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            catalog = rebuild_research_catalog(
                catalog_path=root / "catalog.duckdb",
                bundle_manifest_path=manifest,
                factor_registry_path=registry,
            )
            self.assertEqual(hashlib.sha256(evidence.read_bytes()).hexdigest(), digest_before)
            with duckdb.connect(str(catalog), read_only=True) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM v_factor_values").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM factor_definition").fetchone()[0],
                    1,
                )

    def test_missing_component_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "registry.csv"
            registry.write_text("factor_id\nF1\n", encoding="utf-8")
            manifest = root / "bundle.json"
            manifest.write_text(
                json.dumps(
                    {
                        "components": [
                            {
                                "component_id": "missing",
                                "component_kind": "parquet",
                                "path": "missing.parquet",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(FileNotFoundError):
                rebuild_research_catalog(
                    catalog_path=root / "catalog.duckdb",
                    bundle_manifest_path=manifest,
                    factor_registry_path=registry,
                )

    def test_invalid_view_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "data.parquet"
            pd.DataFrame({"x": [1]}).to_parquet(evidence)
            registry = root / "registry.csv"
            registry.write_text("factor_id\nF1\n", encoding="utf-8")
            manifest = root / "bundle.json"
            manifest.write_text(
                json.dumps(
                    {
                        "components": [
                            {
                                "component_id": "safe",
                                "component_kind": "parquet",
                                "path": evidence.name,
                                "view_name": "bad-name",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ResearchCatalogError):
                rebuild_research_catalog(
                    catalog_path=root / "catalog.duckdb",
                    bundle_manifest_path=manifest,
                    factor_registry_path=registry,
                )


if __name__ == "__main__":
    unittest.main()

