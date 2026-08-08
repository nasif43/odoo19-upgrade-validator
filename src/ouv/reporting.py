import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from ouv.verdict import VerdictState
from ouv.static_checks import RuleMatch
from ouv.installer import InstallResult


class StaticCheckReport(BaseModel):
    rule_id: str
    file_path: str
    line_number: Optional[int] = None
    snippet: Optional[str] = None
    severity: str
    fix_hint: str
    evidence: str
    source: str


class ValidationReport(BaseModel):
    module_name: str
    verdict: VerdictState
    reason: Optional[str] = None
    timestamp: str
    ruleset_version: str
    commit_sha: Optional[str] = None
    static_matches: List[StaticCheckReport] = Field(default_factory=list)
    install_duration_ms: Optional[int] = None
    install_exit_code: Optional[int] = None
    install_error_sig: Optional[str] = None
    log_file_path: str


class ReportGenerator:
    """Generates report artifacts (<module>_validator_report/ containing report.json, install.log, README.md)."""

    @classmethod
    def generate_report(
        cls,
        output_dir: Path,
        module_name: str,
        verdict: VerdictState,
        reason: Optional[str],
        timestamp: str,
        ruleset_version: str,
        commit_sha: Optional[str],
        static_matches: List[RuleMatch],
        install_result: Optional[InstallResult]
    ) -> Path:
        report_folder = output_dir / f"{module_name}_validator_report"
        report_folder.mkdir(parents=True, exist_ok=True)

        log_file = report_folder / "install.log"
        log_content = install_result.log_output if install_result else "No dynamic install attempted."
        log_file.write_text(log_content, encoding="utf-8")

        static_reports = [
            StaticCheckReport(
                rule_id=m.rule_id,
                file_path=str(m.file_path),
                line_number=m.line_number,
                snippet=m.snippet,
                severity=m.severity,
                fix_hint=m.fix_hint,
                evidence=m.evidence,
                source=m.source
            ) for m in static_matches
        ]

        report = ValidationReport(
            module_name=module_name,
            verdict=verdict,
            reason=reason,
            timestamp=timestamp,
            ruleset_version=ruleset_version,
            commit_sha=commit_sha,
            static_matches=static_reports,
            install_duration_ms=install_result.duration_ms if install_result else None,
            install_exit_code=install_result.exit_code if install_result else None,
            install_error_sig=install_result.install_error_sig if install_result else None,
            log_file_path=str(log_file)
        )

        json_file = report_folder / "report.json"
        json_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

        readme_file = report_folder / "README.md"
        readme_file.write_text(cls._generate_readme(report), encoding="utf-8")

        return report_folder

    @staticmethod
    def _generate_readme(report: ValidationReport) -> str:
        lines = [
            f"# Validation Report: `{report.module_name}`",
            "",
            f"**Verdict:** `{report.verdict.value.upper()}`",
            f"**Timestamp:** {report.timestamp}",
            f"**Commit SHA:** `{report.commit_sha or 'N/A'}`",
            f"**Ruleset Version:** `{report.ruleset_version}`",
            ""
        ]
        if report.reason:
            lines.append(f"**Reason:** {report.reason}\n")

        if report.static_matches:
            lines.append("## Static Analysis Violations\n")
            for m in report.static_matches:
                lines.extend([
                    f"### Rule `{m.rule_id}` ({m.severity.upper()})",
                    f"- **File:** `{m.file_path}` (Line {m.line_number or 'N/A'})",
                    f"- **Snippet:** `{m.snippet or ''}`",
                    f"- **Fix Hint:** {m.fix_hint}",
                    f"- **Evidence Tier:** `{m.evidence}` | [Source]({m.source})",
                    ""
                ])

        if report.install_error_sig:
            lines.extend([
                "## Dynamic Install Error Signature",
                "```",
                report.install_error_sig,
                "```",
                ""
            ])

        lines.append(f"See detailed raw output in `{report.log_file_path}`.")
        return "\n".join(lines)
