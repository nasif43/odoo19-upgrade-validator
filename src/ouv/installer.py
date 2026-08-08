import time
import re
import subprocess
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class InstallResult(BaseModel):
    module_name: str
    exit_code: int
    duration_ms: int
    log_output: str
    install_error_sig: Optional[str] = None


class InstallExecutor:
    """Runs `odoo-bin -i <module> --stop-after-init --no-http` as a subprocess."""

    # Matches structured Odoo log ERROR/CRITICAL lines and our own [ERROR] tags.
    _ERROR_PATTERN: re.Pattern = re.compile(
        r"(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \d+ (?:ERROR|CRITICAL)\b)"
        r"|(?:\[ERROR\])"
        r"|(?:\bCRITICAL\b)",
        re.IGNORECASE,
    )
    # Captures the message portion of an Odoo structured ERROR/CRITICAL line.
    _ODOO_ERROR_LINE: re.Pattern = re.compile(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \d+ (?:ERROR|CRITICAL) .+: (.+)"
    )

    def __init__(self, odoo_bin_path: str = "odoo-bin", odoo_config_path: Optional[str] = None):
        self.odoo_bin_path = odoo_bin_path
        self.odoo_config_path = odoo_config_path

    def install_module(self, module_name: str, db_name: str, timeout_seconds: int = 300) -> InstallResult:
        cmd = [
            self.odoo_bin_path,
            "-d", db_name,
            "-i", module_name,
            "--stop-after-init",
            "--no-http",
            "--log-level=warn",  # reduce noise; ERROR/CRITICAL still surface
        ]
        if self.odoo_config_path:
            cmd.extend(["-c", self.odoo_config_path])

        start_time = time.time()
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds
            )
            duration_ms = int((time.time() - start_time) * 1000)
            log_output = res.stdout or ""

            if res.returncode != 0:
                # Process-level failure — normal non-zero exit
                error_sig = self._fingerprint_error(log_output)
                exit_code = res.returncode
            elif self.has_log_errors(log_output):
                # odoo-bin exited 0 but wrote ERROR/CRITICAL entries.
                # This happens on view validation failures, missing fields, etc.
                # Treat as a failure with a synthetic exit code so VerdictAggregator
                # can distinguish it from process crashes (-1 timeout, -2 launch error).
                error_sig = self._fingerprint_error(log_output)
                exit_code = -3  # log-detected failure despite exit 0
            else:
                error_sig = None
                exit_code = 0

            return InstallResult(
                module_name=module_name,
                exit_code=exit_code,
                duration_ms=duration_ms,
                log_output=log_output,
                install_error_sig=error_sig
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log_output = (e.stdout or "") + f"\n[ERROR] Command timed out after {timeout_seconds} seconds."
            return InstallResult(
                module_name=module_name,
                exit_code=-1,
                duration_ms=duration_ms,
                log_output=log_output,
                install_error_sig="TIMEOUT"
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return InstallResult(
                module_name=module_name,
                exit_code=-2,
                duration_ms=duration_ms,
                log_output=f"[ERROR] Subprocess launch error: {str(e)}",
                install_error_sig="SUBPROCESS_LAUNCH_ERROR"
            )

    @classmethod
    def has_log_errors(cls, log: str) -> bool:
        """Return True if the log output contains any ERROR or CRITICAL entries.

        Odoo log format: ``YYYY-MM-DD HH:MM:SS,ms PID LEVEL logger: message``
        We match the LEVEL token at a word boundary to avoid false positives on
        module names or log messages that happen to contain the word 'error'.
        """
        return bool(cls._ERROR_PATTERN.search(log))

    @classmethod
    def _fingerprint_error(cls, log: str) -> Optional[str]:
        """Extract a normalized error fingerprint from Odoo traceback or log output.

        Search order:
        1. Last exception line found within a Traceback block
        2. First ERROR/CRITICAL structured Odoo log line
        3. Fallback constant
        """
        lines = log.splitlines()

        # 1. Traceback block — work backwards to find the exception line
        for i, line in enumerate(lines):
            if "Traceback (most recent call last):" in line:
                tb_lines = lines[i:i + 20]
                for tb_line in reversed(tb_lines):
                    if any(tok in tb_line for tok in ["Error:", "Exception:", "ParseError:", "Warning:"]):
                        return tb_line.strip()

        # 2. First structured ERROR/CRITICAL log line
        for line in lines:
            m = cls._ODOO_ERROR_LINE.search(line)
            if m:
                return m.group(1).strip()[:200]

        return "UNKNOWN_INSTALL_ERROR"
