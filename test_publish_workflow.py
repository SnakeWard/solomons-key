#!/usr/bin/env python3
"""Fail-closed structural gate for the TestPyPI OIDC publishing workflow."""

from __future__ import annotations

import os
import re
import sys

import yaml


HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, ".github", "workflows", "publish-testpypi.yml")
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def main() -> int:
    text = open(WORKFLOW, encoding="utf-8").read()
    # BaseLoader keeps YAML scalars as strings and avoids YAML 1.1 interpreting
    # the key `on` as boolean true.
    doc = yaml.load(text, Loader=yaml.BaseLoader)
    trigger = doc.get("on") or {}
    jobs = doc.get("jobs") or {}
    build = jobs.get("build") or {}
    publish = jobs.get("publish") or {}
    build_steps = build.get("steps") or []
    publish_steps = publish.get("steps") or []

    tags = ((trigger.get("push") or {}).get("tags") or [])
    check(
        "only_prerelease_tags_or_explicit_recovery_publish",
        set(trigger) == {"push", "workflow_dispatch"}
        and len(tags) == 3
        and all(tag.startswith("v[0-9]+.[0-9]+.[0-9]+") for tag in tags),
        f"on={trigger}",
    )
    dispatch_inputs = (trigger.get("workflow_dispatch") or {}).get("inputs") or {}
    release_input = dispatch_inputs.get("release_tag") or {}
    check(
        "manual_recovery_requires_one_existing_tag",
        set(dispatch_inputs) == {"release_tag"}
        and release_input.get("required") == "true"
        and release_input.get("type") == "string",
        f"workflow_dispatch={trigger.get('workflow_dispatch')}",
    )
    check(
        "workflow_has_no_ambient_permissions",
        doc.get("permissions") == {},
        f"permissions={doc.get('permissions')}",
    )
    check(
        "build_job_cannot_request_oidc",
        build.get("permissions") == {"contents": "read"},
        f"permissions={build.get('permissions')}",
    )
    check(
        "publish_job_has_only_oidc_permission",
        publish.get("permissions") == {"id-token": "write"},
        f"permissions={publish.get('permissions')}",
    )
    environment = publish.get("environment") or {}
    check(
        "publisher_is_bound_to_testpypi_environment",
        environment.get("name") == "testpypi",
        f"environment={environment}",
    )
    check(
        "publisher_requires_proven_build",
        publish.get("needs") == "build",
        f"needs={publish.get('needs')}",
    )

    build_env = build.get("env") or {}
    release_tag_expression = str(build_env.get("RELEASE_TAG") or "")
    checkout_configs = [
        step.get("with") or {} for step in build_steps
        if str(step.get("uses") or "").startswith("actions/checkout@")
    ]
    setup_config = next(
        (step.get("with") or {} for step in build_steps
         if str(step.get("uses") or "").startswith("actions/setup-python@")),
        {},
    )
    check(
        "manual_recovery_is_tag_bound_to_recorded_bytes",
        "inputs.release_tag" in release_tag_expression
        and "github.ref_name" in release_tag_expression
        and len(checkout_configs) == 3
        and "env.RELEASE_TAG" in str(checkout_configs[0].get("ref") or "")
        and "github.sha" in str(checkout_configs[1].get("ref") or "")
        and checkout_configs[1].get("path") == ".workflow-source"
        and "release-assets-" in str(checkout_configs[2].get("ref") or "")
        and "env.RELEASE_TAG" in str(checkout_configs[2].get("ref") or "")
        and checkout_configs[2].get("path") == ".release-assets"
        and build.get("runs-on") == "ubuntu-latest"
        and setup_config.get("python-version") == "3.11",
        f"env={build_env}, checkouts={checkout_configs}, setup={setup_config}",
    )

    build_commands = "\n".join(str(step.get("run") or "") for step in build_steps)
    check(
        "tag_must_equal_version",
        "RELEASE_TAG" in build_commands
        and "VERSION" in build_commands
        and "re.fullmatch" in build_commands,
    )
    check(
        "distribution_acceptance_is_mandatory",
        "verify-drop test-release acceptance" in build_commands,
    )
    check(
        "recorded_release_set_is_rebuilt_and_checked",
        "build.py dist release-check" in build_commands,
    )
    check(
        "manual_recovery_verifies_and_installs_exact_recorded_bytes",
        "build.py verify-drop release-check" in build_commands
        and "twine check --strict" in build_commands
        and ".workflow-source/test_release_artifact.py" in build_commands,
    )
    check(
        "only_sdist_and_wheel_are_handed_to_publisher",
        "dist/*.tar.gz dist/*.whl" in build_commands
        and "find publish" in build_commands
        and "-eq 2" in build_commands,
    )

    uses = [
        str(step["uses"])
        for job in (build, publish)
        for step in (job.get("steps") or [])
        if "uses" in step
    ]
    check(
        "every_action_is_pinned_to_full_commit",
        bool(uses)
        and all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", value) for value in uses),
        f"uses={uses}",
    )
    publish_uses = [str(step.get("uses") or "") for step in publish_steps]
    check(
        "official_pypa_publisher_is_used",
        any(value.startswith("pypa/gh-action-pypi-publish@") for value in publish_uses),
        f"uses={publish_uses}",
    )
    publish_config = next(
        (step.get("with") or {} for step in publish_steps
         if str(step.get("uses") or "").startswith("pypa/gh-action-pypi-publish@")),
        {},
    )
    check(
        "publisher_targets_testpypi",
        publish_config.get("repository-url") == "https://test.pypi.org/legacy/",
        f"with={publish_config}",
    )
    check(
        "publisher_runs_no_repository_code",
        all("run" not in step for step in publish_steps),
        f"steps={publish_steps}",
    )
    check(
        "workflow_uses_no_long_lived_secret",
        "secrets." not in text
        and "password:" not in text.lower()
        and "username:" not in text.lower(),
    )

    width = max(len(name) for _, name, _ in results)
    for status, name, detail in results:
        suffix = f"   {detail}" if status == FAIL and detail else ""
        print(f"{status}  {name.ljust(width)}{suffix}")
    failed = sum(status == FAIL for status, _, _ in results)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
