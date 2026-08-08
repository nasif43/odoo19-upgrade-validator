import time
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
            "--log-level=info"
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
            error_sig = self._fingerprint_error(log_output) if res.returncode != 0 else None

            return InstallResult(
                module_name=module_name,
                exit_code=res.returncode,
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

    @staticmethod
    def _fingerprint_error(log: str) -> Optional[str]:
        """Extracts normalized error fingerprint from Odoo traceback or log output."""
        lines = log.splitlines()
        for i, line in enumerate(lines):
            if "Traceback (most recent call last):" in line:
                tb_lines = lines[i:i+15]
                for tb_line in reversed(tb_lines):
                    if any(err in tb_line for err in ["Error:", "Exception:", "ParseError:"]):
                        return tb_line.strip()
        return "UNKNOWN_INSTALL_ERROR"
