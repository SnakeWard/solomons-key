#!/usr/bin/env python3
"""Acceptance gate for synchronized versions and immutable release sets.

The gate never cuts a release in the repository under test. It copies the
working tree to a temporary checkout, then proves that one PEP 440 prerelease
names one reproducible drop tarball, sdist, and wheel.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def run(argv: list[str], cwd: str) -> tuple[int, str]:
    process = subprocess.run([PY, *argv], cwd=cwd, capture_output=True, text=True)
    return process.returncode, (process.stdout or "") + (process.stderr or "")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata_version(path: str) -> str:
    if path.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            text = archive.read(name).decode("utf-8")
    else:
        with tarfile.open(path, "r:gz") as archive:
            member = next(
                m for m in archive.getmembers()
                if m.name.endswith("/PKG-INFO") and m.name.count("/") == 1
            )
            extracted = archive.extractfile(member)
            assert extracted is not None
            text = extracted.read().decode("utf-8")
    match = re.search(r"^Version: (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


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
    root_releases = os.path.join(HERE, "RELEASES.md")
    releases_before = sha256(root_releases)

    with tempfile.TemporaryDirectory(prefix="sk-release-gate-") as temp:
        work = os.path.join(temp, "work")
        shutil.copytree(
            HERE,
            work,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", "*.tar.gz", "*.whl",
                "dist", "build", "*.egg-info",
            ),
        )
        version = open(os.path.join(work, "VERSION"), encoding="utf-8").read().strip()

        with open(os.path.join(work, "pyproject.toml"), "rb") as handle:
            project = tomllib.load(handle)
        check(
            "package_version_is_dynamic",
            project["project"].get("dynamic") == ["version"]
            and "version" not in project["project"],
            "pyproject.toml still carries a second project version",
        )
        check(
            "setuptools_reads_VERSION",
            project.get("tool", {}).get("setuptools", {}).get("dynamic", {})
            .get("version") == {"file": ["VERSION"]},
            "setuptools dynamic version is not sourced from VERSION",
        )
        declared_modules = set(project.get("tool", {}).get("setuptools", {}).get("py-modules", []))
        runtime_modules = {
            os.path.splitext(name)[0]
            for name in os.listdir(work)
            if name.startswith("sk_") and name.endswith(".py")
        }
        check(
            "every_runtime_module_is_packaged",
            declared_modules == runtime_modules,
            f"declared={sorted(declared_modules)}, runtime={sorted(runtime_modules)}",
        )
        expected_scripts = {
            "sk-init": "sk_init:main",
            "sk-adapt": "sk_adapt:main",
            "sk-lint": "sk_lint:main",
            "sk-ledger": "sk_ledger:main",
            "sk-artifacts": "sk_artifacts:main",
            "sk-emit": "sk_emit:main",
            "sk-verify": "sk_verify:main",
            "sk-handoff": "sk_handoff:main",
        }
        check(
            "every_runtime_cli_is_declared",
            project.get("project", {}).get("scripts") == expected_scripts,
            str(project.get("project", {}).get("scripts")),
        )

        parser_probe = (
            "import build; "
            "print('|'.join(build.VERSION_TOKEN_RE.search('v'+v).group(1) "
            "for v in ['0.10.2a1','0.10.2b1','0.10.2rc1','0.10.2']))"
        )
        rc, output = run(["-c", parser_probe], work)
        check(
            "pep440_prereleases_remain_distinct",
            rc == 0 and output.strip() == "0.10.2a1|0.10.2b1|0.10.2rc1|0.10.2",
            output.strip(),
        )

        rc, first_output = run(["build.py", "dist"], work)
        paths = {
            "tarball": os.path.join(work, f"solomons-key-v{version}.tar.gz"),
            "sdist": os.path.join(work, "dist", f"solomons_key-{version}.tar.gz"),
            "wheel": os.path.join(
                work, "dist", f"solomons_key-{version}-py3-none-any.whl"
            ),
        }
        check("dist_builds_release_set", rc == 0 and all(map(os.path.exists, paths.values())),
              first_output.strip()[-300:])
        if rc or not all(map(os.path.exists, paths.values())):
            report()
            return 1

        first_hashes = {kind: sha256(path) for kind, path in paths.items()}
        read_probe = (
            "import build,json; "
            f"print(json.dumps(build.read_releases()[{version!r}],sort_keys=True))"
        )
        rc, output = run(["-c", read_probe], work)
        try:
            record = json.loads(output) if rc == 0 else {}
        except json.JSONDecodeError:
            record = {}
        check("release_set_is_one_parseable_row", bool(record) and not record.get("legacy"),
              output.strip())
        check("recorded_tarball_hash_matches", record.get("tarball") == first_hashes["tarball"])
        check("recorded_sdist_hash_matches", record.get("sdist") == first_hashes["sdist"])
        check("recorded_wheel_hash_matches", record.get("wheel") == first_hashes["wheel"])
        rc, tree_output = run(["sk_handoff.py", "hash"], work)
        current_tree = tree_output.strip().splitlines()[-1] if tree_output.strip() else ""
        check("recorded_tree_hash_matches", rc == 0 and record.get("tree") == current_tree,
              tree_output.strip())
        check("sdist_metadata_matches_VERSION", metadata_version(paths["sdist"]) == version)
        check("wheel_metadata_matches_VERSION", metadata_version(paths["wheel"]) == version)

        with zipfile.ZipFile(paths["wheel"]) as archive:
            wheel_names = set(archive.namelist())
            packaged_modules = {f"{module}.py" for module in declared_modules}
            check(
                "wheel_contains_every_runtime_module",
                packaged_modules <= wheel_names,
                f"missing {sorted(packaged_modules - wheel_names)}",
            )
            source_schemas = {
                name for name in os.listdir(os.path.join(work, "schemas", "artifacts"))
                if name.endswith(".schema.json")
            }
            wheel_schemas = {
                os.path.basename(name) for name in wheel_names
                if name.startswith("solomons_key/_schemas/") and name.endswith(".schema.json")
            }
            check(
                "wheel_contains_complete_schema_set",
                wheel_schemas == source_schemas,
                f"wheel={sorted(wheel_schemas)}, source={sorted(source_schemas)}",
            )
            entry_point_names = [name for name in wheel_names if name.endswith(".dist-info/entry_points.txt")]
            installed_scripts: dict[str, str] = {}
            if len(entry_point_names) == 1:
                parser = configparser.ConfigParser()
                parser.read_string(archive.read(entry_point_names[0]).decode("utf-8"))
                installed_scripts = dict(parser["console_scripts"])
            check(
                "wheel_declares_every_console_script",
                installed_scripts == expected_scripts,
                f"wheel scripts={installed_scripts}",
            )

            unpacked = os.path.join(temp, "unpacked-wheel")
            archive.extractall(unpacked)

        outside = os.path.join(temp, "outside-checkout")
        os.makedirs(outside)
        import_probe = """\
import importlib
import json
import os
import sys

layout = os.path.abspath(sys.argv[1])
sys.path.insert(0, layout)
modules = json.loads(sys.argv[2])
for module in modules:
    imported = importlib.import_module(module)
    if not os.path.abspath(imported.__file__).startswith(layout + os.sep):
        raise SystemExit(f"{module} imported from {imported.__file__}, not the wheel layout")
import sk_artifacts
import sk_lint
import sk_resources
schema_dir = os.path.abspath(sk_resources.default_schema_dir())
schemas = sorted(name for name in os.listdir(schema_dir) if name.endswith(".schema.json"))
print(json.dumps({
    "schema_dir": schema_dir,
    "schemas": schemas,
    "artifacts_default": os.path.abspath(sk_artifacts.DEFAULT_SCHEMA_DIR),
    "lint_default": os.path.abspath(sk_lint.SCHEMA_DIR),
}))
"""
        rc, output = run(
            ["-c", import_probe, unpacked, json.dumps(sorted(declared_modules))],
            outside,
        )
        try:
            discovery = json.loads(output) if rc == 0 else {}
        except json.JSONDecodeError:
            discovery = {}
        discovered_dir = discovery.get("schema_dir", "")
        check(
            "unpacked_wheel_discovers_packaged_schemas_outside_checkout",
            rc == 0
            and discovered_dir.startswith(os.path.abspath(unpacked) + os.sep)
            and set(discovery.get("schemas", [])) == source_schemas
            and discovery.get("artifacts_default") == discovered_dir
            and discovery.get("lint_default") == discovered_dir,
            output.strip()[-500:],
        )

        releases_path = os.path.join(work, "RELEASES.md")
        release_text_after_first = open(releases_path, encoding="utf-8").read()
        rc, second_output = run(["build.py", "dist"], work)
        second_hashes = {kind: sha256(path) for kind, path in paths.items()}
        check("unchanged_rebuild_is_allowed", rc == 0, second_output.strip()[-300:])
        check("all_three_artifacts_are_reproducible", first_hashes == second_hashes,
              f"first={first_hashes}, second={second_hashes}")
        check(
            "unchanged_rebuild_does_not_append",
            open(releases_path, encoding="utf-8").read() == release_text_after_first,
        )

        rc, clean_check = run(["build.py", "release-check"], work)
        check("release_check_accepts_complete_set", rc == 0, clean_check.strip()[-300:])

        with open(os.path.join(work, "README.md"), "a", encoding="utf-8") as handle:
            handle.write("\n<!-- changed without a version bump -->\n")
        rc, refusal = run(["build.py", "dist"], work)
        unchanged_files = first_hashes == {kind: sha256(path) for kind, path in paths.items()}
        unchanged_record = open(releases_path, encoding="utf-8").read() == release_text_after_first
        check("changed_tree_same_version_is_refused", rc != 0 and "REFUSING" in refusal,
              refusal.strip()[-300:])
        check("refusal_preserves_entire_release_set", unchanged_files and unchanged_record)

        wheel_bytes = open(paths["wheel"], "rb").read()
        with open(paths["wheel"], "ab") as handle:
            handle.write(b"corrupt")
        rc, corrupt_output = run(["build.py", "release-check"], work)
        check("release_check_rejects_corrupt_member", rc != 0 and "WHEEL MISMATCH" in corrupt_output,
              corrupt_output.strip()[-300:])
        with open(paths["wheel"], "wb") as handle:
            handle.write(wheel_bytes)

        partial = "| v9.9.9 | `" + "1" * 64 + "` | `" + "2" * 64 + "` | `" + "3" * 64 + "` |\n"
        with open(releases_path, "a", encoding="utf-8") as handle:
            handle.write(partial)
        rc, partial_output = run(["build.py", "release-check"], work)
        check("partial_release_set_fails_closed", rc != 0 and "found 3" in partial_output,
              partial_output.strip()[-300:])

    check(
        "gate_does_not_cut_in_repository",
        sha256(root_releases) == releases_before,
        "test_sk_release.py modified the repository's release ledger",
    )
    report()
    return 1 if any(status == FAIL for status, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
