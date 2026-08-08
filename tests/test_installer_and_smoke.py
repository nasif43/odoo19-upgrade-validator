"""Tests for installer.py log-parsing and verdict aggregation changes."""

import pytest
from ouv.installer import InstallResult, InstallExecutor
from ouv.verdict import VerdictAggregator, VerdictState


# ─── has_log_errors ──────────────────────────────────────────────────────────

class TestHasLogErrors:
    def test_clean_log_returns_false(self):
        log = (
            "2024-01-01 12:00:00,001 1234 INFO odoo: Loading module sale\n"
            "2024-01-01 12:00:01,002 1234 INFO odoo: Module sale loaded\n"
        )
        assert InstallExecutor.has_log_errors(log) is False

    def test_odoo_error_line_returns_true(self):
        log = (
            "2024-01-01 12:00:00,001 1234 INFO odoo: Loading module bad_mod\n"
            "2024-01-01 12:00:01,500 1234 ERROR odoo.http: Field 'x_missing' does not exist\n"
        )
        assert InstallExecutor.has_log_errors(log) is True

    def test_odoo_critical_line_returns_true(self):
        log = (
            "2024-01-01 12:00:00,001 1234 CRITICAL odoo.modules: cannot load module bad_mod\n"
        )
        assert InstallExecutor.has_log_errors(log) is True

    def test_our_own_error_tag_returns_true(self):
        log = "[ERROR] Command timed out after 300 seconds."
        assert InstallExecutor.has_log_errors(log) is True

    def test_word_error_in_module_name_does_not_trigger(self):
        # The word "error" in a module name or log message text must not fire
        log = (
            "2024-01-01 12:00:00,001 1234 INFO odoo: Module error_handling_utils loaded\n"
            "2024-01-01 12:00:01,001 1234 INFO odoo: Processed 0 errors found\n"
        )
        assert InstallExecutor.has_log_errors(log) is False

    def test_empty_log_returns_false(self):
        assert InstallExecutor.has_log_errors("") is False


# ─── _fingerprint_error ───────────────────────────────────────────────────────

class TestFingerprintError:
    def test_extracts_exception_from_traceback(self):
        log = (
            "Traceback (most recent call last):\n"
            "  File 'odoo/modules/loading.py', line 42, in load_module\n"
            "    mod.load()\n"
            "ValueError: field 'amount_total' has no column\n"
        )
        sig = InstallExecutor._fingerprint_error(log)
        assert sig == "ValueError: field 'amount_total' has no column"

    def test_extracts_from_odoo_error_log_line_when_no_traceback(self):
        log = (
            "2024-01-01 12:00:01,500 1234 ERROR odoo.http: Field 'x_missing' does not exist\n"
        )
        sig = InstallExecutor._fingerprint_error(log)
        assert "x_missing" in sig

    def test_fallback_when_no_recognizable_error(self):
        sig = InstallExecutor._fingerprint_error("Some random output with no recognizable error.")
        assert sig == "UNKNOWN_INSTALL_ERROR"


# ─── VerdictAggregator — exit code -3 handling ────────────────────────────────

class TestVerdictAggregatorLogError:
    def _make_result(self, exit_code: int, sig=None) -> InstallResult:
        return InstallResult(
            module_name="test_mod",
            exit_code=exit_code,
            duration_ms=500,
            log_output="...",
            install_error_sig=sig,
        )

    def test_exit_0_is_passed(self):
        result = self._make_result(0)
        verdict, reason = VerdictAggregator.aggregate([], result)
        assert verdict == VerdictState.PASSED
        assert reason is None

    def test_exit_minus3_is_failed_not_errored(self):
        result = self._make_result(-3, sig="Field 'x' does not exist")
        verdict, reason = VerdictAggregator.aggregate([], result)
        assert verdict == VerdictState.FAILED
        assert "log" in reason.lower() or "exit 0" in reason.lower()

    def test_exit_minus1_is_errored(self):
        result = self._make_result(-1, sig="TIMEOUT")
        verdict, _ = VerdictAggregator.aggregate([], result)
        assert verdict == VerdictState.ERRORED

    def test_exit_minus2_is_errored(self):
        result = self._make_result(-2, sig="SUBPROCESS_LAUNCH_ERROR")
        verdict, _ = VerdictAggregator.aggregate([], result)
        assert verdict == VerdictState.ERRORED

    def test_nonzero_positive_exit_is_failed(self):
        result = self._make_result(1)
        verdict, _ = VerdictAggregator.aggregate([], result)
        assert verdict == VerdictState.FAILED


# ─── Dynamic Smoke Test Harness ───────────────────────────────────────────────
#
# These tests exercise the full static pipeline against a known-good and a
# known-bad module fixture.  They do NOT require Postgres or odoo-bin — they
# use --dry-run mode (static analysis only), so they run in CI on any machine.
#
# To run FULL dynamic checks against a live Odoo 19 instance:
#   OUV_ODOO_BIN=/path/to/odoo-bin \
#   OUV_ODOO_CONF=/path/to/odoo.conf \
#   pytest tests/test_installer_and_smoke.py -k "dynamic" -v
# ─────────────────────────────────────────────────────────────────────────────

import os
from pathlib import Path
from ouv.discovery import ModuleDiscoverer
from ouv.static_checks import StaticAnalysisEngine
from ouv.verdict import VerdictAggregator


RULESET = Path(__file__).parent.parent / "ruleset" / "odoo_18_19_rules.yaml"


def _make_good_module(tmp_path: Path) -> Path:
    """Creates a minimal Odoo 19-ready module — should produce PASSED on static checks."""
    mod = tmp_path / "good_module"
    mod.mkdir()
    (mod / "__init__.py").write_text("", encoding="utf-8")
    (mod / "__manifest__.py").write_text(
        "{\n"
        "    'name': 'Good Module',\n"
        "    'version': '19.0.1.0.0',\n"
        "    'license': 'LGPL-3',\n"
        "    'depends': ['base'],\n"
        "    'installable': True,\n"
        "}\n",
        encoding="utf-8",
    )
    (mod / "models").mkdir()
    (mod / "models" / "__init__.py").write_text("from . import my_model\n", encoding="utf-8")
    (mod / "models" / "my_model.py").write_text(
        "from odoo import models, fields\n\n"
        "class MyModel(models.Model):\n"
        "    _name = 'good.module.thing'\n"
        "    name = fields.Char()\n",
        encoding="utf-8",
    )
    return mod


def _make_bad_module(tmp_path: Path) -> Path:
    """Creates a module with several known blocking issues — should produce FAILED on static checks."""
    mod = tmp_path / "bad_module"
    mod.mkdir()
    (mod / "__init__.py").write_text("", encoding="utf-8")
    (mod / "__manifest__.py").write_text(
        "{\n"
        "    'name': 'Bad Module',\n"
        "    'version': '18.0.1.0.0',\n"  # ← BLOCKING: wrong version
        "    'depends': ['base'],\n"
        "    'installable': True,\n"
        "}\n",
        encoding="utf-8",
    )
    (mod / "models").mkdir()
    (mod / "models" / "__init__.py").write_text("from . import bad_model\n", encoding="utf-8")
    (mod / "models" / "bad_model.py").write_text(
        "from odoo.osv import expression\n"          # ← BLOCKING: deprecated import
        "from odoo import models, fields, api\n\n"
        "class BadModel(models.Model):\n"
        "    _name = 'bad.module.thing'\n"
        "    name = fields.Char()\n\n"
        "    @api.returns('self')\n"                  # ← BLOCKING: @api.returns removed
        "    def get_records(self):\n"
        "        return self\n\n"
        "    def old_way(self):\n"
        "        cr = self._cr\n"                     # ← WARNING: _cr deprecated
        "        return cr\n",
        encoding="utf-8",
    )
    return mod


class TestStaticSmokeGoodModule:
    def test_good_module_passes_static_checks(self, tmp_path):
        mod_path = _make_good_module(tmp_path)
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod_path)
        blocking = [m for m in matches if m.severity == "blocking"]
        assert blocking == [], (
            f"Good module produced unexpected blocking matches: "
            f"{[(m.rule_id, m.snippet) for m in blocking]}"
        )
        verdict, _ = VerdictAggregator.aggregate(matches, None)
        assert verdict == VerdictState.PASSED

    def test_good_module_has_no_model_rename_hits(self, tmp_path):
        mod_path = _make_good_module(tmp_path)
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod_path)
        rename_hits = [m for m in matches if m.rule_id.startswith("MODEL_RENAME_")]
        assert rename_hits == []


class TestStaticSmokeBadModule:
    def test_bad_module_fails_static_checks(self, tmp_path):
        mod_path = _make_bad_module(tmp_path)
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod_path)
        blocking = [m for m in matches if m.severity == "blocking"]
        assert len(blocking) >= 2, (
            f"Bad module should have ≥2 blocking matches, got: "
            f"{[(m.rule_id, m.snippet) for m in blocking]}"
        )
        verdict, reason = VerdictAggregator.aggregate(matches, None)
        assert verdict == VerdictState.FAILED
        assert "MANIFEST_VERSION_STRING" in reason or "ORM_OSV_IMPORT" in reason

    def test_bad_module_flags_osv_import(self, tmp_path):
        mod_path = _make_bad_module(tmp_path)
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod_path)
        rule_ids = {m.rule_id for m in matches}
        assert "ORM_OSV_IMPORT" in rule_ids

    def test_bad_module_flags_version_string(self, tmp_path):
        mod_path = _make_bad_module(tmp_path)
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod_path)
        rule_ids = {m.rule_id for m in matches}
        assert "MANIFEST_VERSION_STRING" in rule_ids


class TestModelRenameRules:
    def test_hr_contract_reference_in_python_is_flagged(self, tmp_path):
        mod = tmp_path / "hr_ext"
        mod.mkdir()
        (mod / "__init__.py").write_text("", encoding="utf-8")
        (mod / "__manifest__.py").write_text(
            "{'name':'HR Ext','version':'19.0.1.0.0','license':'LGPL-3','depends':['hr_contract'],'installable':True}",
            encoding="utf-8"
        )
        (mod / "models").mkdir()
        (mod / "models" / "__init__.py").write_text("", encoding="utf-8")
        py = mod / "models" / "wizard.py"
        py.write_text(
            "from odoo import models\n"
            "class Wiz(models.TransientModel):\n"
            "    _name = 'hr.ext.wiz'\n"
            "    def do_it(self):\n"
            "        contracts = self.env['hr.contract'].search([])\n",
            encoding="utf-8",
        )
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod)
        rule_ids = {m.rule_id for m in matches}
        assert "MODEL_RENAME_HR_CONTRACT" in rule_ids

    def test_procurement_group_reference_in_python_is_flagged(self, tmp_path):
        mod = tmp_path / "stock_ext"
        mod.mkdir()
        (mod / "__init__.py").write_text("", encoding="utf-8")
        (mod / "__manifest__.py").write_text(
            "{'name':'Stock Ext','version':'19.0.1.0.0','license':'LGPL-3','depends':['stock'],'installable':True}",
            encoding="utf-8"
        )
        (mod / "models").mkdir()
        (mod / "models" / "__init__.py").write_text("", encoding="utf-8")
        py = mod / "models" / "route.py"
        py.write_text(
            "from odoo import models\n"
            "class Route(models.Model):\n"
            "    _name = 'stock.ext.route'\n"
            "    def run(self):\n"
            "        pg = self.env['procurement.group']\n",
            encoding="utf-8",
        )
        engine = StaticAnalysisEngine(RULESET)
        matches = engine.analyze_module(mod)
        rule_ids = {m.rule_id for m in matches}
        assert "MODEL_RENAME_PROCUREMENT_GROUP" in rule_ids
