#!/usr/bin/env python3
"""
build.py — cross-platform build runner.

The Makefile depends on `rm -rf`, `sha256sum`, and shell substitution, none of
which exist natively on Windows. Since the project is developed on Windows and
verified on Linux CI, a Makefile-only build means the author cannot run the
thing that judges the author's work. That is its own kind of gap.

This is the Makefile's logic in Python. Same targets, same order, no shell.

    python build.py            # everything
    python build.py test       # test suites only
    python build.py verify     # lint + artifacts + verify + ledger
    python build.py docs       # check documented counts against reality
    python build.py dist       # immutable tarball + sdist + wheel release set
    python build.py release-check   # re-derive recorded releases, report drift
    python build.py --list

Exit code is nonzero if any step fails.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

KEY_SRC = "key.yaml"
KEY = "key.repaired.yaml"
LEDGER = os.path.join("ledger", "solomons-key-builder-ledger.jsonl")
HEAD = os.path.join("ledger", "HEAD")

GENERATED_DIRS = ["redcorpus", "schemas", "examples", "runs", "__pycache__"]


class Step:
    def __init__(self, name: str, argv: list[str], allow_fail: bool = False):
        self.name, self.argv, self.allow_fail = name, argv, allow_fail


def run(argv: list[str], capture: bool = False) -> tuple[int, str]:
    p = subprocess.run([PY, *argv], cwd=HERE, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    if not capture:
        sys.stdout.write(out)
    return p.returncode, out


def clean() -> int:
    for d in GENERATED_DIRS:
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)
    print("clean: removed generated directories")
    return 0


def repair() -> int:
    return run(["repair_pass.py", KEY_SRC, KEY])[0]


def ledger() -> int:
    """Seed the demonstration ledger — refusing if that would destroy real history.

    This function used to open with an unconditional rmtree. `sk_ledger seed`
    already refuses to seed over a non-empty ledger; the build deleted the file
    first, which routed around that guard. Every full build silently destroyed
    any entry appended since the last one.

    That is the gate-bypass pattern this toolchain exists to detect, committed
    in the build script by the author, and it is why PASS_19 survived only in a
    worktree nobody rebuilt.
    """
    # Audit lineages live alongside the demo chain and are never regenerated.
    # Preserve them across the rmtree that reseeds the demonstration chain.
    ldir = os.path.join(HERE, "ledger")
    preserved = {}
    if os.path.isdir(ldir):
        for fn in os.listdir(ldir):
            if fn.startswith("audit") or fn == "AUDIT_HEAD":
                preserved[fn] = open(os.path.join(ldir, fn), "rb").read()

    path = os.path.join(HERE, LEDGER)
    if os.path.exists(path):
        keep = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            # An entry is seed-derived if it came from the KEY's pass history or
            # is the demo run this build creates. Anything else was appended by
            # someone recording a real event.
            pid = e.get("pass_id", "")
            if e.get("entry_type") == "genesis" or pid.startswith("PASS_") and pid <= "PASS_17":
                continue
            if pid == "RUN_build_0001":
                continue
            keep.append(pid or e.get("entry_type"))
        if keep:
            print("ledger: REFUSING to reseed — the existing ledger contains entries")
            print("        that reseeding would destroy:")
            for k in keep:
                print(f"          {k}")
            print()
            print("        Reseeding is only safe on a pure demonstration chain.")
            print("        To keep this history, move it aside first:")
            print(f"          mv {LEDGER} ledger/audit-<name>.jsonl")
            print("        Then reconcile the two lineages rather than discarding one.")
            return 1
    shutil.rmtree(ldir, ignore_errors=True)
    os.makedirs(ldir, exist_ok=True)
    for fn, blob in preserved.items():
        with open(os.path.join(ldir, fn), "wb") as fh:
            fh.write(blob)
    if preserved:
        print(f"ledger: preserved {len(preserved)} audit file(s) across reseed")
    rc, _ = run(["sk_ledger.py", "seed", LEDGER, "--from-key", KEY])
    if rc:
        return rc
    rc, _ = run([
        "sk_ledger.py", "append", LEDGER,
        "--pass", "RUN_build_0001", "--name", "Demo governed build run",
        "--actor", "Codex", "--actor-role", "builder",
        "--type", "validation_run", "--result", "pass",
        "--note", "Witnesses the run in runs/good.",
        # Fixed timestamp. A demonstration chain must be REPRODUCIBLE: two
        # clean builds should produce the same head. With a live clock the
        # fixture head moved every build, which cascaded into every example
        # artifact's ledger_ref — meaning a committed tree would show spurious
        # diffs on every build and train everyone to ignore them.
        # Real appends use the real clock; only the seeded demo is pinned.
        "--timestamp", "2026-01-01T00:00:01Z",
    ])
    if rc:
        return rc
    rc, out = run(["sk_ledger.py", "head", LEDGER], capture=True)
    if rc:
        sys.stdout.write(out)
        return rc
    with open(os.path.join(HERE, HEAD), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out.strip() + "\n")
    print(f"ledger: head written to {HEAD}")
    return 0


def ledger_verify() -> int:
    head = open(os.path.join(HERE, HEAD), encoding="utf-8").read().strip()
    return run(["sk_ledger.py", "verify", LEDGER, "--expect-head", head])[0]


def audit_verify() -> int:
    """Verify every audit lineage against its own anchor.

    The demonstration chain is regenerated each build and proves only that the
    mechanism works. An audit lineage is started once and never reseeded; it is
    the only chain whose entries witnessed anything.
    """
    ldir = os.path.join(HERE, "ledger")
    anchors = {"audit-solomons-key.jsonl": "AUDIT_HEAD"}
    rc_all = 0
    for name, anchor in anchors.items():
        p = os.path.join(ldir, name)
        a = os.path.join(ldir, anchor)
        if not os.path.exists(p):
            continue
        head = open(a, encoding="utf-8").read().strip() if os.path.exists(a) else None
        argv = ["sk_ledger.py", "verify", os.path.join("ledger", name)]
        if head:
            argv += ["--expect-head", head]
        rc, _ = run(argv)
        rc_all = rc_all or rc
    return rc_all


RELEASES = "RELEASES.md"

# Every tar member gets this mtime. Any constant works; what matters is that it
# does not come from the clock or from the filesystem, both of which differ
# between two packs of identical content.
SOURCE_EPOCH = 1_700_000_000


def _excluded(arcpath: str) -> bool:
    """Paths that must not ship, by archive-relative path (no leading name/).

    Two of these are exclusions the packer did not previously have, and both are
    load-bearing:

    RELEASES.md records the artifact hash of the tarball. Packing it would put
    the digest inside the file it describes — the fixed point again, and it also
    means two consecutive `dist` runs pack different trees, which fails the
    reproducibility check for a reason that has nothing to do with tar.

    runs/real/ is a real emitted run: fresh run_id, wall-clock timestamps, live
    ledger head. `.gitignore` already excludes it and says why — it SHOULD differ
    every time. Packing it made the tarball differ after every full build while
    two bare `dist` runs still agreed, so the acceptance test passed while the
    real build was irreproducible. Recipients emit their own with `sk_emit run`;
    runs/good and the RUN* corpus still ship.
    """
    parts = arcpath.split("/")
    base = parts[-1]
    if "__pycache__" in parts:
        return True
    # The old packer walked os.listdir and never shipped .git, so this is not a
    # new exclusion so much as one that was previously implicit. It has to be
    # explicit now: .git/index and .git/logs change on every git operation, so
    # packing them would make the tarball irreproducible by itself. .github is a
    # different path and still ships — CI config is part of what is released.
    if ".git" in parts:
        return True
    if base.endswith((".pyc", ".tar.gz", ".whl", ".egg-info")):
        return True
    if any(part in {".venv", ".venv-release", "venv", "build", "dist"} for part in parts):
        return True
    if base == RELEASES:
        return True
    if parts[:2] == ["runs", "real"]:
        return True
    return False


def _normalize(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip everything that varies between machines, users, and clocks."""
    ti.mtime = SOURCE_EPOCH
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o755 if ti.isdir() else 0o644
    return ti


def _pack(dest: str, name: str) -> None:
    """Write a deterministic tarball of the tree to `dest`.

    tarfile.open(..., "w:gz") stamps the current time into the gzip header, so
    the same bytes gzip differently every second. GzipFile(mtime=0) zeroes it.
    Members are collected and sorted by archive path: os.walk order is not
    guaranteed and differs across filesystems.
    """
    members: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(HERE):
        rel_dir = os.path.relpath(dirpath, HERE).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames
            if not _excluded(f"{rel_dir}/{d}".lstrip("/"))
        )
        for fn in filenames:
            rel = f"{rel_dir}/{fn}".lstrip("/")
            if not _excluded(rel):
                members.append((rel, os.path.join(dirpath, fn)))
        if rel_dir:
            members.append((rel_dir, dirpath))

    with open(dest, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
                for rel, full in sorted(set(members)):
                    ti = tf.gettarinfo(full, arcname=f"{name}/{rel}")
                    _normalize(ti)
                    if ti.isreg():
                        with open(full, "rb") as fh:
                            tf.addfile(ti, fh)
                    else:
                        tf.addfile(ti)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash() -> str:
    rc, out = run(["sk_handoff.py", "hash"], capture=True)
    return out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""


def git_commit() -> str:
    """Current commit, or an em dash outside a repo. Never fails the build."""
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE,
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except OSError:
        pass
    return "—"


HEX64 = re.compile(r"\b([0-9a-f]{64})\b")
PEP440_PRERELEASE = (
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:(?:a|b|rc)(?:0|[1-9]\d*))?"
)
VERSION_RE = re.compile(rf"{PEP440_PRERELEASE}\Z")
VERSION_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9])v?({PEP440_PRERELEASE})(?![A-Za-z0-9.])"
)


class ReleaseRecordError(ValueError):
    """RELEASES.md contains an ambiguous or partial release identity."""


def read_version() -> str:
    """Read the one canonical version used by archives and package metadata."""
    version = open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError(
            "VERSION must be canonical X.Y.Z, X.Y.ZaN, X.Y.ZbN, or X.Y.ZrcN; "
            f"got {version!r}"
        )
    return version


def read_releases() -> dict[str, dict[str, object]]:
    """Parse legacy two-digest rows and immutable four-digest release sets.

    A release identity is one line so it cannot be observed half-written. Two
    hashes mean a historical tree/tarball row. Four mean tree, drop tarball,
    Python sdist, and wheel. Three hashes are a corrupt partial release set and
    fail closed instead of being silently mistaken for history.
    """
    path = os.path.join(HERE, RELEASES)
    entries: dict[str, dict[str, object]] = {}
    if not os.path.exists(path):
        return entries
    for line_number, line in enumerate(
        open(path, encoding="utf-8").read().splitlines(), start=1
    ):
        match = VERSION_TOKEN_RE.search(line)
        hashes = HEX64.findall(line)
        if not match or len(hashes) < 2:
            continue
        if len(hashes) not in (2, 4):
            raise ReleaseRecordError(
                f"{RELEASES}:{line_number}: release rows require exactly two "
                f"legacy hashes or four release-set hashes; found {len(hashes)}"
            )
        version = match.group(1)
        record: dict[str, object] = {
            "tree": hashes[0],
            "tarball": hashes[1],
            "sdist": hashes[2] if len(hashes) == 4 else None,
            "wheel": hashes[3] if len(hashes) == 4 else None,
            "legacy": len(hashes) == 2,
        }
        if version in entries and entries[version] != record:
            raise ReleaseRecordError(
                f"{RELEASES}:{line_number}: v{version} has more than one identity"
            )
        entries[version] = record
    return entries


def append_release_set(
    version: str,
    tree: str,
    tarball: str,
    sdist: str,
    wheel: str,
    commit: str,
) -> None:
    path = os.path.join(HERE, RELEASES)
    row = (
        f"| v{version} | `{tree}` | `{tarball}` | `{sdist}` | `{wheel}` | "
        f"`{commit}` | released |\n"
    )
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    marker = "<!-- release-sets:end -->"
    if marker not in text:
        raise ReleaseRecordError(f"{RELEASES} is missing {marker}")
    index = text.index(marker)
    text = text[:index] + row + text[index:]
    fd, staged = tempfile.mkstemp(prefix=".RELEASES.", suffix=".tmp", dir=HERE)
    try:
        os.chmod(staged, os.stat(path).st_mode if os.path.exists(path) else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
        staged = ""
    finally:
        if fd >= 0:
            os.close(fd)
        if staged and os.path.exists(staged):
            os.remove(staged)


def _copy_release_source(dest: str) -> None:
    """Copy release inputs with normalized timestamps and permissions."""
    os.makedirs(dest)
    for dirpath, dirnames, filenames in os.walk(HERE):
        rel_dir = os.path.relpath(dirpath, HERE).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames
            if not _excluded(f"{rel_dir}/{d}".lstrip("/"))
        )
        target_dir = os.path.join(dest, *rel_dir.split("/")) if rel_dir else dest
        os.makedirs(target_dir, exist_ok=True)
        for filename in sorted(filenames):
            rel = f"{rel_dir}/{filename}".lstrip("/")
            if _excluded(rel):
                continue
            target = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(dirpath, filename), target)
            os.chmod(target, 0o644)
            os.utime(target, (SOURCE_EPOCH, SOURCE_EPOCH))
    for dirpath, dirnames, _ in os.walk(dest, topdown=False):
        for dirname in dirnames:
            directory = os.path.join(dirpath, dirname)
            os.chmod(directory, 0o755)
            os.utime(directory, (SOURCE_EPOCH, SOURCE_EPOCH))
    os.chmod(dest, 0o755)
    os.utime(dest, (SOURCE_EPOCH, SOURCE_EPOCH))


def _release_requirements() -> dict[str, str]:
    path = os.path.join(HERE, "requirements-release.txt")
    required: dict[str, str] = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if not match:
            raise ValueError(f"{path}: release tools must use exact == pins: {line!r}")
        required[match.group(1).lower()] = match.group(2)
    return required


def _check_release_python(executable: str) -> tuple[bool, str]:
    probe = """\
import importlib.metadata
import json
import sys

bad = []
for package, expected in json.loads(sys.argv[1]).items():
    try:
        actual = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        actual = "not installed"
    if actual != expected:
        bad.append(f"{package}=={expected} (found {actual})")
print("\\n".join(bad))
sys.exit(1 if bad else 0)
"""
    try:
        process = subprocess.run(
            [executable, "-c", probe, json.dumps(_release_requirements(), sort_keys=True)],
            cwd=tempfile.gettempdir(), capture_output=True, text=True,
        )
    except OSError as exc:
        return False, str(exc)
    return process.returncode == 0, (process.stdout or "") + (process.stderr or "")


def _metadata_version(path: str) -> str:
    if path.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ValueError(f"{path}: expected one wheel METADATA, found {len(names)}")
            text = archive.read(names[0]).decode("utf-8")
    else:
        with tarfile.open(path, "r:gz") as archive:
            members = [
                member for member in archive.getmembers()
                if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1
            ]
            if len(members) != 1:
                raise ValueError(f"{path}: expected one sdist PKG-INFO, found {len(members)}")
            extracted = archive.extractfile(members[0])
            if extracted is None:
                raise ValueError(f"{path}: PKG-INFO is unreadable")
            text = extracted.read().decode("utf-8")
    match = re.search(r"^Version: (.+)$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"{path}: package metadata has no Version")
    return match.group(1).strip()


def _normalize_sdist(path: str) -> None:
    """Canonicalize backend-created tar/gzip metadata without changing files."""
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for original in source.getmembers():
            member = copy.copy(original)
            extracted = source.extractfile(original) if original.isreg() else None
            members.append((member, extracted.read() if extracted is not None else None))

    normalized = path + ".normalized"
    try:
        with open(normalized, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as archive:
                    for member, data in members:
                        member.pax_headers = {}
                        _normalize(member)
                        if data is None:
                            archive.addfile(member)
                        else:
                            archive.addfile(member, io.BytesIO(data))
        os.replace(normalized, path)
    finally:
        if os.path.exists(normalized):
            os.remove(normalized)


def _build_python_artifacts(output_dir: str, version: str) -> tuple[str, str]:
    executable = os.environ.get("SK_RELEASE_PYTHON", PY)
    ok, detail = _check_release_python(executable)
    if not ok:
        raise RuntimeError(
            "release Python does not match requirements-release.txt:\n"
            + detail.strip()
            + "\nCreate a release venv, install -r requirements-release.txt, then set "
              "SK_RELEASE_PYTHON to that venv's Python."
        )
    with tempfile.TemporaryDirectory(prefix="solomons-key-source-") as temp:
        source = os.path.join(temp, "source")
        _copy_release_source(source)
        env = os.environ.copy()
        env["SOURCE_DATE_EPOCH"] = str(SOURCE_EPOCH)
        env["PYTHONHASHSEED"] = "0"
        process = subprocess.run(
            [executable, "-m", "build", "--sdist", "--wheel", "--no-isolation",
             "--outdir", output_dir, source],
            cwd=temp, env=env, capture_output=True, text=True,
        )
        if process.returncode:
            raise RuntimeError((process.stdout or "") + (process.stderr or ""))
    sdists = sorted(
        os.path.join(output_dir, name) for name in os.listdir(output_dir)
        if name.endswith(".tar.gz")
    )
    wheels = sorted(
        os.path.join(output_dir, name) for name in os.listdir(output_dir)
        if name.endswith(".whl")
    )
    if len(sdists) != 1 or len(wheels) != 1:
        raise RuntimeError(
            f"package build produced {len(sdists)} sdist(s) and {len(wheels)} wheel(s)"
        )
    _normalize_sdist(sdists[0])
    for artifact in (sdists[0], wheels[0]):
        metadata_version = _metadata_version(artifact)
        if metadata_version != version:
            raise RuntimeError(
                f"{os.path.basename(artifact)} records version {metadata_version!r}; "
                f"VERSION records {version!r}"
            )
    return sdists[0], wheels[0]


def _artifact_paths(version: str) -> dict[str, str]:
    package_stem = f"solomons_key-{version}"
    return {
        "tarball": os.path.join(HERE, f"solomons-key-v{version}.tar.gz"),
        "sdist": os.path.join(HERE, "dist", f"{package_stem}.tar.gz"),
        "wheel": os.path.join(HERE, "dist", f"{package_stem}-py3-none-any.whl"),
    }


def dist() -> int:
    try:
        version = read_version()
        entries = read_releases()
    except (OSError, ValueError) as exc:
        print(f"dist: {exc}")
        return 1
    name = f"solomons-key-v{version}"
    final_paths = _artifact_paths(version)

    with tempfile.TemporaryDirectory(prefix=f"{name}-release-") as stage:
        staged_paths = {"tarball": os.path.join(stage, f"{name}.tar.gz")}
        try:
            _pack(staged_paths["tarball"], name)
            package_dir = os.path.join(stage, "dist")
            os.makedirs(package_dir)
            staged_paths["sdist"], staged_paths["wheel"] = _build_python_artifacts(
                package_dir, version
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"dist: package build failed: {exc}")
            return 1

        hashes = {kind: sha256_file(path) for kind, path in staged_paths.items()}
        tree = tree_hash()
        if not re.fullmatch(r"[0-9a-f]{64}", tree):
            print("dist: could not derive a valid governed tree hash; nothing recorded")
            return 1
        recorded = entries.get(version)

        if recorded:
            if recorded["legacy"]:
                print(f"dist: REFUSING to extend legacy v{version} after it was recorded")
                print("      Adding sdist and wheel hashes later would mutate its identity.")
                print("      Bump VERSION and cut one complete release set instead.")
                return 1
            identities = {
                "tree": (recorded["tree"], tree),
                **{kind: (recorded[kind], hashes[kind]) for kind in staged_paths},
            }
            changed = {kind: pair for kind, pair in identities.items() if pair[0] != pair[1]}
            if changed:
                print(f"dist: REFUSING to re-cut v{version} — it is already recorded")
                print(f"      in {RELEASES} with different content.\n")
                for kind, (old, new) in changed.items():
                    print(f"      recorded {kind:<8} {old}")
                    print(f"      current  {kind:<8} {new}")
                print("\n      A version name resolves to exactly one release set,")
                print("      permanently. Some content changed, so VERSION must be bumped.")
                return 1

        os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
        for kind, staged_path in staged_paths.items():
            os.replace(staged_path, final_paths[kind])

        if recorded:
            print(f"dist: v{version} already recorded and unchanged — no-op")
        else:
            try:
                append_release_set(
                    version, tree, hashes["tarball"], hashes["sdist"],
                    hashes["wheel"], git_commit(),
                )
            except (OSError, ValueError) as exc:
                print(f"dist: built artifacts but did not record them: {exc}")
                return 1
            print(f"dist: recorded immutable v{version} release set in {RELEASES}")
        print(f"  tree     {tree}")
        for kind in ("tarball", "sdist", "wheel"):
            print(f"  {kind:<8} {hashes[kind]}  {os.path.relpath(final_paths[kind], HERE)}")
        return 0


def release_check() -> int:
    """Verify every locally present artifact against its recorded release set."""
    try:
        entries = read_releases()
        version = read_version()
    except (OSError, ValueError) as exc:
        print(f"release-check: {exc}")
        return 1
    if not entries:
        print(f"release-check: no releases recorded in {RELEASES} yet")
        return 0

    mismatches = 0
    print(f"release-check: {len(entries)} recorded release(s)")
    for release_version in sorted(entries):
        record = entries[release_version]
        paths = _artifact_paths(release_version)
        kinds = ("tarball",) if record["legacy"] else ("tarball", "sdist", "wheel")
        for kind in kinds:
            path = paths[kind]
            if not os.path.exists(path):
                print(f"  v{release_version}  {kind} not present — not checked")
                continue
            actual = sha256_file(path)
            if actual == record[kind]:
                print(f"  v{release_version}  {kind} matches  {actual[:12]}…")
            else:
                mismatches += 1
                print(f"  v{release_version}  {kind.upper()} MISMATCH")
                print(f"        recorded {record[kind]}")
                print(f"        actual   {actual}")

    if version in entries:
        actual_tree = tree_hash()
        if actual_tree == entries[version]["tree"]:
            print(f"  v{version}  tree matches the working tree")
        else:
            print(f"  v{version}  tree has moved since the release — expected between")
            print("        releases; bump VERSION before cutting again")
            print(f"        recorded {entries[version]['tree']}")
            print(f"        actual   {actual_tree}")
    else:
        print(f"  v{version}  current version not yet released")

    if mismatches:
        print(f"\nrelease-check: {mismatches} published artifact(s) no longer match")
        print("  the immutable release set recorded for them.")
        return 1
    print("release-check: no drift")
    return 0


# --- documentation drift -------------------------------------------------

COUNTS_BEGIN = "<!-- counts:begin -->"
COUNTS_END = "<!-- counts:end -->"
COUNTED_DOCS = ["README_sk-lint.md", "REPORT.md"]


def actual_counts() -> dict[str, int]:
    rc, out = run(["sk_lint.py", "--rules"], capture=True)
    lint = len([l for l in out.splitlines() if l.strip().startswith("SK")])
    verify_src = open(os.path.join(HERE, "sk_verify.py"), encoding="utf-8").read()
    runr = len(set(re.findall(r'add\("(RUN\d+)"', verify_src)))
    art_src = open(os.path.join(HERE, "sk_artifacts.py"), encoding="utf-8").read()
    sem = len(set(re.findall(r'add\("(SEM\d+)"', art_src)))

    tests = 0
    for suite in ("test_sk_lint.py", "test_sk_ledger.py", "test_sk_artifacts.py",
                  "test_sk_verify.py", "test_sk_emit.py", "test_ci_flow.py"):
        if os.path.exists(os.path.join(HERE, suite)):
            _, o = run([suite], capture=True)
            m = re.search(r"(\d+) passed, (\d+) failed", o)
            if m:
                tests += int(m.group(1))
    return {"lint": lint, "run": runr, "semantic": sem, "tests": tests}


def counts_block(c: dict[str, int]) -> str:
    return (
        f"{COUNTS_BEGIN}\n"
        f"**{c['lint']}** lint rules · **{c['run']}** run rules · "
        f"**{c['semantic']}** semantic rules · **{c['tests']}** tests passing\n"
        f"{COUNTS_END}"
    )


def docs(write: bool = False) -> int:
    """Regenerate the counts block in the docs and fail if it had drifted.

    An earlier version of this check scanned prose for phrases like "N lint
    rules". It passed on documents that never used that phrasing — a rule that
    could not fail, which is the exact defect this toolchain exists to catch,
    committed here. The fix is a delimited block that is regenerated and
    compared byte-for-byte, so there is no phrasing for it to miss.
    """
    c = actual_counts()
    block = counts_block(c)
    print(f"docs: actual — {c['lint']} lint, {c['run']} run, {c['semantic']} semantic, {c['tests']} tests")

    drifted, missing = [], []
    for doc in COUNTED_DOCS:
        path = os.path.join(HERE, doc)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        if COUNTS_BEGIN not in text or COUNTS_END not in text:
            missing.append(doc)
            continue
        i = text.index(COUNTS_BEGIN)
        j = text.index(COUNTS_END) + len(COUNTS_END)
        if text[i:j] != block:
            drifted.append((doc, text[i:j]))
            if write:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text[:i] + block + text[j:])

    if missing:
        for d in missing:
            print(f"docs: {d} has no counts block — add {COUNTS_BEGIN} / {COUNTS_END}")
        return 1
    if drifted:
        for d, was in drifted:
            print(f"docs: DRIFT in {d}")
            print("      was: " + was.replace(COUNTS_BEGIN, "").replace(COUNTS_END, "").strip())
            print("      now: " + block.replace(COUNTS_BEGIN, "").replace(COUNTS_END, "").strip())
        if write:
            print("docs: rewritten")
            return 0
        print("\n  Run: python build.py docs-write")
        return 1
    print("docs: counts blocks match the code")
    return 0


def docs_write() -> int:
    return docs(write=True)


TARGETS: dict[str, list] = {
    "clean": [clean],
    "repair": [repair],
    "ledger": [ledger],
    "schemas": [Step("schemas", ["gen_artifact_schemas.py", KEY, "schemas/artifacts"])],
    "examples": [Step("examples", ["gen_artifact_examples.py", KEY, "examples"])],
    "corpus": [Step("corpus", ["gen_redcorpus.py", KEY, "redcorpus"])],
    "runs": [Step("runs", ["gen_runs.py", KEY, LEDGER, "runs"])],
    "emit": [Step("emit-real-run", ["sk_emit.py", "run", "--key", KEY,
                                    "--route", "protocol_build_route",
                                    "--ledger", LEDGER, "--out", "runs/real"])],
    "test": [
        Step("test_sk_lint", ["test_sk_lint.py"]),
        Step("test_sk_ledger", ["test_sk_ledger.py"]),
        Step("test_sk_artifacts", ["test_sk_artifacts.py"]),
        Step("test_sk_verify", ["test_sk_verify.py"]),
        Step("test_ci_flow", ["test_ci_flow.py"]),
    ],
    "test-emit": [Step("test_sk_emit", ["test_sk_emit.py"])],
    "test-release": [Step("test_sk_release", ["test_sk_release.py"])],
    "lint": [Step("lint", ["sk_lint.py", KEY])],
    "artifacts": [Step("artifacts", ["sk_artifacts.py", "validate", "--dir", "examples/valid",
                                     "--key", KEY, "--ledger", LEDGER])],
    "verify": [
        Step("verify-fixture", ["sk_verify.py", "runs/good"]),
        # The real emitted run. Unlike runs/good this is not a shape fixture:
        # every automatic gate's evidence was computed by sk_emit.py and carries
        # that program's runtime hash. This is the record the toolchain exists
        # to produce, and verifying it is the only end-to-end proof.
        Step("verify-real", ["sk_verify.py", "runs/real"]),
    ],
    "ledger-verify": [ledger_verify, audit_verify],
    "docs": [docs],
    "docs-write": [docs_write],
    "pin": [Step("pin", ["sk_handoff.py", "pin"])],
    "verify-drop": [Step("verify-drop", ["sk_handoff.py", "verify-drop"])],
    "dist": [dist],
    "release-check": [release_check],
}

ALL = ["repair", "ledger", "schemas", "examples", "corpus", "runs", "emit",
       "test", "test-emit", "lint", "artifacts", "verify", "ledger-verify",
       "docs", "pin", "release-check"]


def execute(target: str) -> int:
    for item in TARGETS[target]:
        if callable(item):
            rc = item()
        else:
            print(f"--- {item.name}")
            rc, _ = run(item.argv)
        if rc and not (isinstance(item, Step) and item.allow_fail):
            print(f"\nbuild: FAILED at '{target}' (exit {rc})")
            return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="build.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("targets:")
        for t in TARGETS:
            print(f"  {t}")
        print(f"\ndefault (all): {' '.join(ALL)}")
        return 0

    targets = args.targets or ALL
    for t in targets:
        if t == "all":
            for a in ALL:
                if execute(a):
                    return 1
            continue
        if t not in TARGETS:
            sys.stderr.write(f"build: unknown target '{t}' (try --list)\n")
            return 2
        if execute(t):
            return 1
    print("\nbuild: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
