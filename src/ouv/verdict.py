from enum import Enum
from typing import List, Optional
from ouv.static_checks import RuleMatch
from ouv.installer import InstallResult


class VerdictState(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    BLOCKED = "blocked"


class VerdictAggregator:
    """Aggregates static check results and dynamic install result into final state."""

    @staticmethod
    def aggregate(
        static_matches: List[RuleMatch],
        install_result: Optional[InstallResult],
        blocked_reason: Optional[str] = None
    ) -> tuple[VerdictState, Optional[str]]:
        if blocked_reason:
            return VerdictState.BLOCKED, blocked_reason

        # Check for blocking static rule matches
        blocking_matches = [m for m in static_matches if m.severity == "blocking"]
        if blocking_matches:
            rule_ids = ", ".join(m.rule_id for m in blocking_matches)
            return VerdictState.FAILED, f"Blocking static analysis rules failed: {rule_ids}"

        if install_result is None:
            # Dry run / static check only without errors
            return VerdictState.PASSED, None

        if install_result.exit_code == 0:
            return VerdictState.PASSED, None
        elif install_result.exit_code in (-1, -2):
            return VerdictState.ERRORED, f"Infrastructure/Execution error (exit code: {install_result.exit_code})"
        else:
            return VerdictState.FAILED, f"Dynamic installation failed (exit code: {install_result.exit_code})"
