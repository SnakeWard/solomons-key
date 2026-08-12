"""Acceptance gate for the fail-closed derive -> emit -> verify CI wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
FLOW = HERE / ".github" / "scripts" / "run_sk_flow.sh"
WORKFLOW = HERE / ".github" / "workflows" / "github_workflows_solomons-key-ci.yml"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def find_bash() -> str | None:
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


FAKE_PYTHON = r"""#!/usr/bin/env bash
set -u

cmd="${1:-}"
shift || true
echo "[FAKE] $cmd $*"

case "$cmd" in
  sk_init.py)
    rc="${FAKE_INIT_RC:-0}"
    if [ "$rc" -eq 0 ]; then
      out=""
      while [ "$#" -gt 0 ]; do
        if [ "$1" = "--out" ]; then
          out="$2"
          break
        fi
        shift
      done
      [ -n "$out" ] && printf '%s\n' 'lot: {}' > "$out"
    fi
    exit "$rc"
    ;;
  sk_lint.py)
    exit "${FAKE_LINT_RC:-0}"
    ;;
  sk_emit.py)
    rc="${FAKE_EMIT_RC:-0}"
    if [ "$rc" -eq 0 ] && [ "${FAKE_EMIT_WRITES_RUN:-1}" -eq 1 ]; then
      out=""
      while [ "$#" -gt 0 ]; do
        if [ "$1" = "--out" ]; then
          out="$2"
          break
        fi
        shift
      done
      mkdir -p "$out"
      printf '%s\n' '{}' > "$out/run.json"
    fi
    exit "$rc"
    ;;
  sk_verify.py)
    exit "${FAKE_VERIFY_RC:-0}"
    ;;
  *)
    echo "unexpected fake-python command: $cmd" >&2
    exit 97
    ;;
esac
"""


@dataclass
class FlowResult:
    returncode: int
    log: str
    output_sentinel_survived: bool


def run_flow(
    bash: str,
    *,
    lint_rc: int = 0,
    emit_rc: int = 0,
    verify_rc: int = 0,
    emit_writes_run: bool = True,
    include_key: bool = True,
    include_ledger: bool = True,
    preexisting_output: bool = False,
) -> FlowResult:
    with tempfile.TemporaryDirectory() as raw_td:
        td = Path(raw_td)
        script = td / ".github" / "scripts" / "run_sk_flow.sh"
        script.parent.mkdir(parents=True)
        shutil.copyfile(FLOW, script)

        for tool in ("sk_lint.py", "sk_emit.py", "sk_verify.py", "sk_init.py"):
            (td / tool).write_text("# test fixture\n", encoding="utf-8")
        if include_key:
            (td / "key.repaired.yaml").write_text("lot: {}\n", encoding="utf-8")
        if include_ledger:
            ledger = td / "ledger" / "solomons-key-builder-ledger.jsonl"
            ledger.parent.mkdir()
            ledger.write_text("{}\n", encoding="utf-8")
        (td / "TRUSTED_PROGRAMS.sha256").write_text("0" * 64 + "  fake\n", encoding="utf-8")
        (td / "schemas" / "artifacts").mkdir(parents=True)

        sentinel = td / "runs" / "ci_run" / "sentinel.txt"
        if preexisting_output:
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text("do not overwrite\n", encoding="utf-8")

        fake_python = td / "fake-python"
        fake_python.write_text(FAKE_PYTHON, encoding="utf-8", newline="\n")
        fake_python.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "PYTHON": "./fake-python",
                "FAKE_LINT_RC": str(lint_rc),
                "FAKE_EMIT_RC": str(emit_rc),
                "FAKE_VERIFY_RC": str(verify_rc),
                "FAKE_EMIT_WRITES_RUN": "1" if emit_writes_run else "0",
            }
        )
        proc = subprocess.run(
            [bash, ".github/scripts/run_sk_flow.sh"],
            cwd=td,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        log_path = td / "sk_flow.log"
        log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        if proc.stdout or proc.stderr:
            log += "\n[PROCESS OUTPUT]\n" + proc.stdout + proc.stderr
        return FlowResult(proc.returncode, log, sentinel.is_file())


def main() -> int:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    check(
        "workflow_does_not_suppress_flow_failure",
        "continue-on-error" not in workflow,
    )
    check(
        "workflow_uses_governed_inputs",
        "LEDGER_PATH: ledger/solomons-key-builder-ledger.jsonl" in workflow
        and "ROUTE_ID: protocol_build_route" in workflow,
    )
    dependency_stage = workflow.find("python -m pip download")
    isolation = workflow.find("--network none")
    check(
        "workflow_stages_dependencies_before_isolation",
        0 <= dependency_stage < isolation and "--no-index" in workflow,
    )
    check(
        "workflow_preserves_failure_log",
        "if: always()" in workflow and "path: sk_flow.log" in workflow,
    )

    bash = find_bash()
    if bash is None:
        check("bash_is_available", False, "bash is required to test the CI wrapper")
    else:
        success = run_flow(bash)
        check("successful_flow_exits_zero", success.returncode == 0, f"rc={success.returncode}")
        all_zero = all(f"[RESULT] {stage}_RC=0" in success.log for stage in ("LINT", "EMIT", "VERIFY"))
        check(
            "successful_flow_records_all_zero_results",
            all_zero,
            "" if all_zero else success.log[-500:],
        )
        governed_defaults = (
            "--route protocol_build_route" in success.log
            and "--ledger ledger/solomons-key-builder-ledger.jsonl" in success.log
        )
        check(
            "flow_uses_governed_defaults",
            governed_defaults,
            "" if governed_defaults else success.log[-500:],
        )

        lint_failure = run_flow(bash, lint_rc=7)
        check("lint_failure_exits_nonzero", lint_failure.returncode == 7, f"rc={lint_failure.returncode}")
        check("lint_failure_stops_before_emit", "[STEP] sk_emit" not in lint_failure.log)

        emit_failure = run_flow(bash, emit_rc=8)
        check("emit_failure_exits_nonzero", emit_failure.returncode == 8, f"rc={emit_failure.returncode}")
        check("emit_failure_stops_before_verify", "[STEP] sk_verify" not in emit_failure.log)

        verify_failure = run_flow(bash, verify_rc=9)
        check("verify_failure_exits_nonzero", verify_failure.returncode == 9, f"rc={verify_failure.returncode}")

        missing_run = run_flow(bash, emit_writes_run=False)
        check("missing_emitted_run_fails_closed", missing_run.returncode != 0, f"rc={missing_run.returncode}")

        missing_ledger = run_flow(bash, include_ledger=False)
        check("missing_ledger_fails_closed", missing_ledger.returncode != 0, f"rc={missing_ledger.returncode}")
        check("missing_ledger_stops_before_emit", "[STEP] sk_emit" not in missing_ledger.log)

        stale_output = run_flow(bash, preexisting_output=True)
        check("stale_output_is_refused", stale_output.returncode != 0, f"rc={stale_output.returncode}")
        check("stale_output_is_not_deleted", stale_output.output_sentinel_survived)

        derived = run_flow(bash, include_key=False)
        check("missing_key_is_derived", derived.returncode == 0, f"rc={derived.returncode}")
        check("derive_result_is_recorded", "[RESULT] INIT_RC=0" in derived.log)

    width = max((len(name) for _, name, _ in results), default=0)
    for status, name, detail in results:
        print(f"{status:<4}  {name:<{width}}" + (f"  {detail}" if detail else ""))
    passed = sum(status == PASS for status, _, _ in results)
    failed = len(results) - passed
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
