#!/usr/bin/env python3
"""Acceptance gate for the distributable Solomon's Key command suite.

This gate builds two independent release sets, checks their bytes, installs
the wheel into a fresh virtual environment outside either source tree, and
drives the installed CLIs through a real derive -> lint -> adapt -> verify
flow.  It never builds in, publishes from, or writes a release record to the
checkout under test.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []

RUNTIME_MODULES = [
    "sk_init",
    "sk_adapt",
    "sk_lint",
    "sk_ledger",
    "sk_artifacts",
    "sk_emit",
    "sk_verify",
    "sk_handoff",
    "sk_resources",
]
COMMANDS = [
    "sk-init",
    "sk-adapt",
    "sk-lint",
    "sk-ledger",
    "sk-artifacts",
    "sk-emit",
    "sk-verify",
    "sk-handoff",
]

SAMPLE_WORKFLOW = """\
name: distribution acceptance
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Unit tests
        run: python -m unittest
      - name: Manual sign-off
        run: echo reviewed
"""

SAMPLE_JUNIT = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="unittest" tests="2" failures="0" errors="0" skipped="0">
    <testcase classname="test_core" name="test_accepts_valid_input"/>
    <testcase classname="test_core" name="test_rejects_invalid_input"/>
  </testsuite>
</testsuites>
"""


class AcceptanceFailure(RuntimeError):
    """A named acceptance condition failed."""


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    if not ok:
        raise AcceptanceFailure(f"{name}: {detail or 'condition was false'}")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_status() -> bytes | None:
    """Return an exact dirty-tree snapshot, or None outside a Git checkout."""
    try:
        process = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=HERE,
            capture_output=True,
        )
    except OSError:
        return None
    return process.stdout if process.returncode == 0 else None


def clean_environment(*, scripts: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if scripts:
        env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    return env


def run(
    argv: list[str],
    cwd: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> tuple[int, str]:
    process = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return process.returncode, (process.stdout or "") + (process.stderr or "")


def run_ok(
    name: str,
    argv: list[str],
    cwd: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 300,
) -> str:
    rc, output = run(argv, cwd, env=env, timeout=timeout)
    check(name, rc == 0, f"exit {rc}: {output.strip()[-800:]}")
    return output


def copy_source(destination: str) -> None:
    shutil.copytree(
        HERE,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv*",
            "venv",
            "__pycache__",
            "*.pyc",
            "*.tar.gz",
            "*.whl",
            "dist",
            "build",
            "*.egg-info",
        ),
    )


def release_python() -> str:
    requested = os.environ.get("SK_RELEASE_PYTHON", sys.executable)
    resolved = shutil.which(requested)
    if resolved is None and os.path.isfile(os.path.join(HERE, requested)):
        resolved = os.path.join(HERE, requested)
    check(
        "release_python_resolves",
        resolved is not None and os.path.isfile(resolved),
        f"SK_RELEASE_PYTHON={requested!r} is not executable",
    )
    return os.path.abspath(resolved or requested)


def artifact_paths(source: str, version: str) -> dict[str, str]:
    stem = f"solomons_key-{version}"
    return {
        "tarball": os.path.join(source, f"solomons-key-v{version}.tar.gz"),
        "sdist": os.path.join(source, "dist", f"{stem}.tar.gz"),
        "wheel": os.path.join(source, "dist", f"{stem}-py3-none-any.whl"),
    }


def build_release_set(
    label: str,
    source: str,
    executable: str,
) -> tuple[dict[str, str], dict[str, str]]:
    env = clean_environment()
    env["SK_RELEASE_PYTHON"] = executable
    run_ok(
        f"independent_build_{label}",
        [executable, "build.py", "dist"],
        source,
        env=env,
        timeout=600,
    )
    version = Path(source, "VERSION").read_text(encoding="utf-8").strip()
    paths = artifact_paths(source, version)
    check(
        f"release_set_{label}_is_complete",
        all(os.path.isfile(path) for path in paths.values()),
        f"missing {[kind for kind, path in paths.items() if not os.path.isfile(path)]}",
    )
    return paths, {kind: sha256(path) for kind, path in paths.items()}


def venv_layout(venv: str) -> tuple[str, str]:
    if os.name == "nt":
        scripts = os.path.join(venv, "Scripts")
        python = os.path.join(scripts, "python.exe")
    else:
        scripts = os.path.join(venv, "bin")
        python = os.path.join(scripts, "python")
    return scripts, python


def installed_commands(scripts: str) -> dict[str, str]:
    commands: dict[str, str] = {}
    for name in COMMANDS:
        path = shutil.which(name, path=scripts)
        if path:
            commands[name] = os.path.abspath(path)
    check(
        "all_console_scripts_are_installed",
        set(commands) == set(COMMANDS),
        f"missing {sorted(set(COMMANDS) - set(commands))}",
    )
    return commands


def report() -> None:
    width = max((len(name) for _, name, _ in results), default=10)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"   {detail}"
        print(line)
    failed = sum(1 for status, _, _ in results if status == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")


def main() -> int:
    guarded = ["RELEASES.md", "TRUSTED_PROGRAMS.sha256", "TREE.sha256"]
    guard_before = {
        name: sha256(os.path.join(HERE, name))
        for name in guarded
        if os.path.isfile(os.path.join(HERE, name))
    }
    artifacts_before = set(glob.glob(os.path.join(HERE, "*.tar.gz"))) | set(
        glob.glob(os.path.join(HERE, "dist", "*"))
    )
    status_before = git_status()

    try:
        executable = release_python()
        with tempfile.TemporaryDirectory(prefix="sk-distribution-acceptance-") as temp:
            source_a = os.path.join(temp, "source-a")
            source_b = os.path.join(temp, "source-b")
            outside = os.path.join(temp, "outside-checkout")
            os.makedirs(outside)
            copy_source(source_a)
            copy_source(source_b)

            paths_a, hashes_a = build_release_set("a", source_a, executable)
            _, hashes_b = build_release_set("b", source_b, executable)
            check(
                "all_three_artifacts_are_reproducible",
                hashes_a == hashes_b,
                f"build-a={hashes_a}, build-b={hashes_b}",
            )

            run_ok(
                "twine_check_strict",
                [
                    executable,
                    "-m",
                    "twine",
                    "check",
                    "--strict",
                    paths_a["sdist"],
                    paths_a["wheel"],
                ],
                outside,
                env=clean_environment(),
            )

            venv = os.path.join(temp, "installed-venv")
            run_ok(
                "fresh_virtual_environment_created",
                [executable, "-m", "venv", venv],
                outside,
                env=clean_environment(),
            )
            scripts, installed_python = venv_layout(venv)
            run_ok(
                "wheel_installs_with_declared_dependencies",
                [
                    installed_python,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    paths_a["wheel"],
                ],
                outside,
                env=clean_environment(scripts=scripts),
                timeout=600,
            )

            commands = installed_commands(scripts)
            installed_env = clean_environment(scripts=scripts)
            for name in COMMANDS:
                run_ok(
                    f"{name}_entry_point_runs",
                    [commands[name], "--help"],
                    outside,
                    env=installed_env,
                )

            source_schemas = sorted(
                path.name
                for path in Path(source_a, "schemas", "artifacts").glob("*.schema.json")
            )
            probe = """\
import importlib
import json
import os
import sys
from pathlib import Path

modules = json.loads(sys.argv[1])
prefix = Path(sys.prefix).resolve()
locations = {}
for name in modules:
    module = importlib.import_module(name)
    path = Path(module.__file__).resolve()
    if not path.is_relative_to(prefix):
        raise SystemExit(f"{name} loaded outside installed environment: {path}")
    locations[name] = str(path)
from sk_resources import default_schema_dir
schema_dir = Path(default_schema_dir()).resolve()
if not schema_dir.is_relative_to(prefix):
    raise SystemExit(f"schemas loaded outside installed environment: {schema_dir}")
schemas = sorted(path.name for path in schema_dir.glob("*.schema.json"))
print(json.dumps({"locations": locations, "schema_dir": str(schema_dir), "schemas": schemas}))
"""
            probe_output = run_ok(
                "installed_modules_and_schemas_resolve_outside_checkout",
                [installed_python, "-I", "-c", probe, json.dumps(RUNTIME_MODULES)],
                outside,
                env=installed_env,
            )
            discovery = json.loads(probe_output.strip().splitlines()[-1])
            check(
                "installed_schema_set_is_complete",
                discovery.get("schemas") == source_schemas,
                f"installed={discovery.get('schemas')}, source={source_schemas}",
            )

            project = os.path.join(outside, "project")
            os.makedirs(project)
            workflow = os.path.join(project, "ci.yml")
            contract = os.path.join(project, "project.key.yaml")
            trusted = os.path.join(project, "TRUSTED_PROGRAMS.sha256")
            junit = os.path.join(project, "results.xml")
            Path(workflow).write_text(SAMPLE_WORKFLOW, encoding="utf-8", newline="\n")
            Path(junit).write_text(SAMPLE_JUNIT, encoding="utf-8", newline="\n")

            run_ok(
                "installed_sk_init_derives_contract",
                [
                    commands["sk-init"],
                    "--from-ci",
                    workflow,
                    "--out",
                    contract,
                    "--automatic",
                    "Unit tests=python",
                    "--attested",
                    "Manual sign-off",
                ],
                project,
                env=installed_env,
            )
            check(
                "sk_init_writes_contract_trust_and_run",
                os.path.isfile(contract)
                and os.path.isfile(trusted)
                and os.path.isfile(os.path.join(project, "runs", "first", "run.json")),
                "sk-init did not produce the complete adoption starting point",
            )
            run_ok(
                "installed_sk_lint_accepts_derived_contract",
                [commands["sk-lint"], contract],
                project,
                env=installed_env,
            )

            run_dir = os.path.join(project, "runs", "first")
            manifest = json.loads(Path(run_dir, "run.json").read_text(encoding="utf-8"))
            run_id = manifest["run_id"]
            route = manifest["selected_route_id"]
            artifacts = os.path.join(run_dir, "artifacts")
            gate = "unit_tests_gate"
            run_ok(
                "installed_sk_adapt_replaces_gate_evidence",
                [
                    commands["sk-adapt"],
                    "junit",
                    junit,
                    "--gate",
                    gate,
                    "--program",
                    "python",
                    "--run-id",
                    run_id,
                    "--route",
                    route,
                    "--out",
                    artifacts,
                ],
                project,
                env=installed_env,
            )
            verify = [
                commands["sk-verify"],
                run_dir,
                "--key",
                contract,
                "--trusted",
                trusted,
            ]
            run_ok(
                "installed_sk_verify_accepts_adapter_run",
                verify,
                project,
                env=installed_env,
            )

            Path(junit).write_text(SAMPLE_JUNIT + "\n", encoding="utf-8", newline="\n")
            rc, output = run(verify, project, env=installed_env)
            check(
                "tampered_adapter_input_fails_closed",
                rc != 0 and "SEM06" in output and "input hash mismatch" in output,
                f"exit {rc}: {output.strip()[-800:]}",
            )
            Path(junit).write_text(SAMPLE_JUNIT, encoding="utf-8", newline="\n")

            os.remove(os.path.join(artifacts, f"gate_{gate}.json"))
            rc, output = run(verify, project, env=installed_env)
            check(
                "missing_gate_decision_fails_closed",
                rc != 0 and "RUN06" in output and "gate_bypass_attempt" in output,
                f"exit {rc}: {output.strip()[-800:]}",
            )
    except (AcceptanceFailure, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        if not results or results[-1][0] != FAIL:
            results.append((FAIL, "acceptance_gate_completed", str(exc)))

    guard_after = {
        name: sha256(os.path.join(HERE, name))
        for name in guarded
        if os.path.isfile(os.path.join(HERE, name))
    }
    artifacts_after = set(glob.glob(os.path.join(HERE, "*.tar.gz"))) | set(
        glob.glob(os.path.join(HERE, "dist", "*"))
    )
    status_after = git_status()
    try:
        check(
            "live_release_records_and_trust_root_unchanged",
            guard_after == guard_before,
            f"before={guard_before}, after={guard_after}",
        )
        check(
            "live_checkout_has_no_new_release_artifacts",
            artifacts_after == artifacts_before,
            f"new={sorted(artifacts_after - artifacts_before)}",
        )
        check(
            "live_git_worktree_status_unchanged",
            status_before is None or status_after == status_before,
            "acceptance execution changed the live Git worktree",
        )
    except AcceptanceFailure:
        pass

    report()
    return 1 if any(status == FAIL for status, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
