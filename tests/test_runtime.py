from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from momentum_reversal.runtime import RuntimeConfigError, resolve_runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def test_repository_defaults_when_no_config_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = resolve_runtime_paths(cwd=root, environment={})
            self.assertEqual(paths.source, "repository_default")
            self.assertIsNone(paths.runtime_root)
            self.assertEqual(paths.data_root, (root / "data").resolve())
            self.assertEqual(paths.results_root, (root / "results").resolve())

    def test_local_config_derives_all_runtime_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            runtime = root / "fast-local"
            (config_dir / "runtime.local.toml").write_text(
                f"schema_version = 1\nruntime_root = '{runtime}'\n",
                encoding="utf-8",
            )
            paths = resolve_runtime_paths(cwd=root, environment={})
            self.assertEqual(paths.source, "local_config")
            self.assertEqual(paths.data_root, (runtime / "data").resolve())
            self.assertEqual(paths.results_root, (runtime / "results").resolve())
            self.assertEqual(paths.cache_root, (runtime / "cache").resolve())
            self.assertEqual(paths.log_root, (runtime / "logs").resolve())

    def test_environment_overrides_config_and_specific_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "runtime.local.toml").write_text(
                "schema_version = 1\nruntime_root = 'ignored'\n",
                encoding="utf-8",
            )
            runtime = root / "env-runtime"
            results = root / "special-results"
            paths = resolve_runtime_paths(
                cwd=root,
                environment={
                    "CROSSSECTION_RUNTIME_ROOT": str(runtime),
                    "CROSSSECTION_RESULTS_ROOT": str(results),
                },
            )
            self.assertEqual(paths.source, "environment")
            self.assertEqual(paths.data_root, (runtime / "data").resolve())
            self.assertEqual(paths.results_root, results.resolve())

    def test_explicit_missing_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeConfigError):
                resolve_runtime_paths(
                    cwd=root,
                    environment={
                        "CROSSSECTION_RUNTIME_CONFIG": str(root / "missing.toml")
                    },
                )

    def test_create_only_creates_the_runtime_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = resolve_runtime_paths(
                cwd=root,
                environment={"CROSSSECTION_RUNTIME_ROOT": str(root / "runtime")},
            )
            paths.create()
            for path in (
                paths.data_root,
                paths.results_root,
                paths.cache_root,
                paths.log_root,
            ):
                self.assertTrue(path.is_dir())


if __name__ == "__main__":
    unittest.main()
