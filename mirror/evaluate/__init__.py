"""Independent evaluation: rollout checking, trials and paired comparisons."""
from .checker import CHECKER_ID, advisory_violations, check, spec_hash
from .models import EvalReport, FreezeManifest, PairedTrial, TrialRecord
from .paired import FreezeError, blocked_case, corrected_rules, freeze, paired_eval, verify_manifest
from .trial import execute_trial, run_trial


def write_report(report, out_dir):
    from .report import write_report as _w
    return _w(report, out_dir)


__all__ = ["check", "advisory_violations", "spec_hash", "CHECKER_ID", "run_trial", "execute_trial",
           "paired_eval", "freeze", "verify_manifest", "FreezeError", "corrected_rules",
           "blocked_case", "write_report", "EvalReport", "FreezeManifest", "PairedTrial",
           "TrialRecord"]
