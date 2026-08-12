#!/usr/bin/env python3
"""Accept an already-recorded release set without rebuilding its bytes.

This is the fail-closed recovery gate for an immutable release whose original
artifacts exist but whose pre-normalization wheel cannot be reproduced on a
fresh runner. ``build.py release-check`` first binds every supplied byte to the
recorded release row. Only that exact wheel is then installed and exercised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

import test_sk_acceptance as acceptance


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: test_release_artifact.py RELEASE_ROOT", file=sys.stderr)
        return 2

    source = os.path.abspath(sys.argv[1])
    try:
        version = Path(source, "VERSION").read_text(encoding="utf-8").strip()
        paths = acceptance.artifact_paths(source, version)
        acceptance.check(
            "recorded_release_set_is_present",
            all(os.path.isfile(path) for path in paths.values()),
            f"missing {[kind for kind, path in paths.items() if not os.path.isfile(path)]}",
        )
        executable = acceptance.release_python()
        acceptance.run_ok(
            "recorded_hashes_and_tree_match",
            [executable, "build.py", "verify-drop", "release-check"],
            source,
            env=acceptance.clean_environment(),
        )

        with tempfile.TemporaryDirectory(prefix="sk-recorded-release-") as temp:
            outside = os.path.join(temp, "outside-checkout")
            os.makedirs(outside)
            acceptance.run_ok(
                "twine_check_strict",
                [
                    executable,
                    "-m",
                    "twine",
                    "check",
                    "--strict",
                    paths["sdist"],
                    paths["wheel"],
                ],
                outside,
                env=acceptance.clean_environment(),
            )

            venv = os.path.join(temp, "installed-venv")
            acceptance.run_ok(
                "fresh_virtual_environment_created",
                [executable, "-m", "venv", venv],
                outside,
                env=acceptance.clean_environment(),
            )
            scripts, installed_python = acceptance.venv_layout(venv)
            acceptance.run_ok(
                "recorded_wheel_installs_with_dependencies",
                [
                    installed_python,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    paths["wheel"],
                ],
                outside,
                env=acceptance.clean_environment(scripts=scripts),
                timeout=600,
            )

            commands = acceptance.installed_commands(scripts)
            installed_env = acceptance.clean_environment(scripts=scripts)
            for name in acceptance.COMMANDS:
                acceptance.run_ok(
                    f"{name}_entry_point_runs",
                    [commands[name], "--help"],
                    outside,
                    env=installed_env,
                )

            source_schemas = sorted(
                path.name
                for path in Path(source, "schemas", "artifacts").glob("*.schema.json")
            )
            probe = """\
import importlib
import json
import sys
from pathlib import Path

modules = json.loads(sys.argv[1])
prefix = Path(sys.prefix).resolve()
for name in modules:
    path = Path(importlib.import_module(name).__file__).resolve()
    if not path.is_relative_to(prefix):
        raise SystemExit(f"{name} loaded outside installed environment: {path}")
from sk_resources import default_schema_dir
schema_dir = Path(default_schema_dir()).resolve()
if not schema_dir.is_relative_to(prefix):
    raise SystemExit(f"schemas loaded outside installed environment: {schema_dir}")
print(json.dumps(sorted(path.name for path in schema_dir.glob("*.schema.json"))))
"""
            output = acceptance.run_ok(
                "installed_modules_and_schemas_resolve_outside_checkout",
                [
                    installed_python,
                    "-I",
                    "-c",
                    probe,
                    json.dumps(acceptance.RUNTIME_MODULES),
                ],
                outside,
                env=installed_env,
            )
            installed_schemas = json.loads(output.strip().splitlines()[-1])
            acceptance.check(
                "installed_schema_set_is_complete",
                installed_schemas == source_schemas,
                f"installed={installed_schemas}, source={source_schemas}",
            )

            project = os.path.join(outside, "project")
            os.makedirs(project)
            workflow = os.path.join(project, "ci.yml")
            contract = os.path.join(project, "project.key.yaml")
            trusted = os.path.join(project, "TRUSTED_PROGRAMS.sha256")
            junit = os.path.join(project, "results.xml")
            Path(workflow).write_text(
                acceptance.SAMPLE_WORKFLOW, encoding="utf-8", newline="\n"
            )
            Path(junit).write_text(
                acceptance.SAMPLE_JUNIT, encoding="utf-8", newline="\n"
            )

            acceptance.run_ok(
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
            acceptance.run_ok(
                "installed_sk_lint_accepts_derived_contract",
                [commands["sk-lint"], contract],
                project,
                env=installed_env,
            )

            run_dir = os.path.join(project, "runs", "first")
            manifest = json.loads(Path(run_dir, "run.json").read_text(encoding="utf-8"))
            artifacts = os.path.join(run_dir, "artifacts")
            gate = "unit_tests_gate"
            acceptance.run_ok(
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
                    manifest["run_id"],
                    "--route",
                    manifest["selected_route_id"],
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
            acceptance.run_ok(
                "installed_sk_verify_accepts_adapter_run",
                verify,
                project,
                env=installed_env,
            )

            Path(junit).write_text(
                acceptance.SAMPLE_JUNIT + "\n", encoding="utf-8", newline="\n"
            )
            rc, output = acceptance.run(verify, project, env=installed_env)
            acceptance.check(
                "tampered_adapter_input_fails_closed",
                rc != 0 and "SEM06" in output and "input hash mismatch" in output,
                f"exit {rc}: {output.strip()[-800:]}",
            )
            Path(junit).write_text(
                acceptance.SAMPLE_JUNIT, encoding="utf-8", newline="\n"
            )
            os.remove(os.path.join(artifacts, f"gate_{gate}.json"))
            rc, output = acceptance.run(verify, project, env=installed_env)
            acceptance.check(
                "missing_gate_decision_fails_closed",
                rc != 0 and "RUN06" in output and "gate_bypass_attempt" in output,
                f"exit {rc}: {output.strip()[-800:]}",
            )
    except (
        acceptance.AcceptanceFailure,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        if not acceptance.results or acceptance.results[-1][0] != acceptance.FAIL:
            acceptance.results.append((acceptance.FAIL, "recorded_release_gate", str(exc)))

    acceptance.report()
    return 1 if any(status == acceptance.FAIL for status, _, _ in acceptance.results) else 0


if __name__ == "__main__":
    sys.exit(main())
