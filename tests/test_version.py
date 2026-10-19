"""tests/test_version.py — バージョン 0.2.0 対応テスト

1. deltasim --version が __version__ と一致すること
2. TestResult.to_dict() に deltasim_version が含まれること
3. deltasim_version が __version__ と一致すること
4. schema_version は "2" のまま
5. CLI --json 出力に deltasim_version が含まれること
"""

from __future__ import annotations
import json
import os
from pathlib import Path
import pytest
from click.testing import CliRunner

import deltasim
import deltasim.api as ds
from deltasim.cli import main

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class TestVersionConsistency:
    """各所のバージョンが一致すること"""

    def test_package_version_is_str(self):
        """__version__ が文字列であること"""
        assert isinstance(deltasim.__version__, str)
        assert deltasim.__version__ != ""

    def test_package_version_not_fallback(self):
        """インストール済み環境で fallback 値 (0.0.0.dev) にならないこと"""
        assert deltasim.__version__ != "0.0.0.dev", \
            "pip install -e . が必要です"

    def test_cli_version_matches_package(self):
        """`deltasim --version` の出力が __version__ と一致すること"""
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0, result.output
        # click の出力: "deltasim, version X.Y.Z"
        assert deltasim.__version__ in result.output, \
            f"--version output: {result.output!r}, __version__: {deltasim.__version__!r}"


class TestResultDeltasimVersion:
    """TestResult.to_dict() / CLI JSON に deltasim_version が含まれること"""

    def _run(self):
        return ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )

    def test_deltasim_version_in_to_dict(self):
        """TestResult.to_dict() に deltasim_version キーが含まれること"""
        d = self._run().to_dict()
        assert "deltasim_version" in d, \
            f"deltasim_version がない: {list(d.keys())}"

    def test_deltasim_version_is_string(self):
        """deltasim_version が文字列であること"""
        d = self._run().to_dict()
        assert isinstance(d["deltasim_version"], str)

    def test_deltasim_version_matches_package_version(self):
        """deltasim_version が deltasim.__version__ と一致すること（ハードコードしない）"""
        d = self._run().to_dict()
        assert d["deltasim_version"] == deltasim.__version__, \
            f"mismatch: result={d['deltasim_version']!r}, pkg={deltasim.__version__!r}"

    def test_schema_version_still_2(self):
        """schema_version は '2' のまま変わらないこと"""
        d = self._run().to_dict()
        assert d["schema_version"] == "2", \
            f"schema_version が変わった: {d['schema_version']!r}"

    def test_deltasim_version_in_json_string(self):
        """to_json() の文字列に deltasim_version が含まれること"""
        j = self._run().to_json()
        parsed = json.loads(j)
        assert "deltasim_version" in parsed
        assert parsed["deltasim_version"] == deltasim.__version__

    def test_deltasim_version_in_cli_json_file(self, tmp_path):
        """`deltasim test --json` の出力ファイルに deltasim_version が含まれること"""
        json_path = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_path,
        ])
        assert result.exit_code == 0, result.output
        d = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "deltasim_version" in d, \
            f"deltasim_version がない: {list(d.keys())}"
        assert d["deltasim_version"] == deltasim.__version__
