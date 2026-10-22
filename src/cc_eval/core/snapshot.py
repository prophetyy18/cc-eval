"""Materialising a framework variant into an isolated workspace.

Two mechanisms are supported:

``path``  a directory snapshot (any working tree, no git required)
``git``   a detached ``git worktree`` at a specific ref

Both produce the same result: a plain directory that is copied into the run
workspace, so the agent can neither modify the framework nor leak state into
the next run.

The materialisation also records a **fingerprint** (a content hash of the
whole tree) and a **file map**.  Comparing the fingerprints of the baseline and
the candidate is what lets cc-eval detect changes that were *not* declared as
the independent variable (ADR-003).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import FrameworkRef, Variant

# Directories that are never copied into a workspace.
DEFAULT_EXCLUDES = (".git/**", "**/__pycache__/**", "**/.venv/**", "**/node_modules/**")


class SnapshotError(RuntimeError):
    """Raised when a framework variant cannot be materialised."""


@dataclass
class MaterialisedVariant:
    """The result of materialising one side of the experiment."""

    variant: str
    root: Path
    fingerprint: str
    file_map: dict[str, str] = field(default_factory=dict)
    source_type: str = "path"
    source_ref: str | None = None
    commit: str | None = None
    dirty: bool = False
    dirty_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_provenance(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "fingerprint": self.fingerprint,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "commit": self.commit,
            "dirty": self.dirty,
            "dirty_files": self.dirty_files,
            "file_count": len(self.file_map),
            "notes": self.notes,
        }


@dataclass
class TreeDiff:
    """The difference between two materialised variants."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    identical: bool = False

    def changed_files(self) -> list[str]:
        return sorted([*self.added, *self.removed, *self.changed])

    def as_dict(self) -> dict[str, object]:
        return {
            "added": sorted(self.added),
            "removed": sorted(self.removed),
            "changed": sorted(self.changed),
            "changed_count": len(self.changed_files()),
            "identical": self.identical,
        }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to a regex.

    Unlike :func:`fnmatch`, ``*`` does **not** cross a path separator and
    ``**/`` matches any number of leading directories (including none), so
    ``**/*`` matches both ``a.txt`` and ``dir/a.txt``.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 3] == "**/":
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern[index : index + 2] == "**":
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def _excluded(rel_path: str, excludes: list[str]) -> bool:
    posix = rel_path.replace("\\", "/")
    for pattern in excludes:
        regex = _glob_to_regex(pattern)
        if regex.match(posix):
            return True
        # "dir/**" should also match "dir/file.txt" and "dir/sub/file.txt"
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if posix == prefix or posix.startswith(prefix + "/"):
                return True
    return False


def _included(rel_path: str, includes: list[str]) -> bool:
    posix = rel_path.replace("\\", "/")
    for pattern in includes:
        if _glob_to_regex(pattern).match(posix):
            return True
        if pattern.endswith("/**") and (posix == pattern[:-3] or posix.startswith(pattern[:-3] + "/")):
            return True
    return False


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_file_map(root: Path, excludes: list[str] | None = None) -> dict[str, str]:
    """Map every file under ``root`` to its content hash."""
    excludes = list(excludes or DEFAULT_EXCLUDES)
    mapping: dict[str, str] = {}
    if not root.exists():
        return mapping
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if _excluded(rel, excludes):
            continue
        mapping[rel] = hash_file(path)
    return mapping


def fingerprint_of(file_map: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(file_map):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_map[rel].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def diff_file_maps(a: dict[str, str], b: dict[str, str]) -> TreeDiff:
    """Compare two file maps."""
    diff = TreeDiff()
    for rel in sorted(set(a) | set(b)):
        if rel not in a:
            diff.added.append(rel)
        elif rel not in b:
            diff.removed.append(rel)
        elif a[rel] != b[rel]:
            diff.changed.append(rel)
    diff.identical = not diff.changed_files()
    return diff


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - fixed git invocation
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise SnapshotError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def git_available() -> bool:
    return shutil.which("git") is not None


def repo_state(repo: Path) -> tuple[str | None, bool, list[str]]:
    """Return ``(commit, dirty, dirty_files)`` for a git repository."""
    try:
        commit = git(repo, "rev-parse", "HEAD")
    except SnapshotError:
        return None, False, []
    status = git(repo, "status", "--porcelain", check=False)
    dirty_files = [line[3:].strip() for line in status.splitlines() if line.strip()]
    return commit, bool(dirty_files), dirty_files


# --------------------------------------------------------------------------- #
# materialisation
# --------------------------------------------------------------------------- #


def materialise(
    variant: Variant,
    destination: Path,
    *,
    worktree_root: Path | None = None,
    clean: bool = False,
) -> MaterialisedVariant:
    """Materialise ``variant``'s framework into ``destination``.

    By default the framework is copied *into* an existing destination (a run
    workspace already contains ``task/``, ``out/`` and cc-eval's own metadata
    directory).  Pass ``clean=True`` to wipe the destination first - only use
    that for a scratch directory.

    The result is a plain directory copy: the agent can never write back to
    the framework it is being tested against.
    """
    ref = variant.framework
    excludes = list(ref.exclude) or list(DEFAULT_EXCLUDES)
    includes = list(ref.include) or ["**/*"]

    if clean and destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    commit: str | None = None
    dirty = False
    dirty_files: list[str] = []

    if ref.type == "git":
        if ref.repository is None:
            raise SnapshotError(f"variant {variant.name}: git variants need a repository path")
        if not git_available():
            raise SnapshotError("git is not installed but a git variant was requested")
        repo = Path(ref.repository).expanduser().resolve()
        if not (repo / ".git").exists():
            raise SnapshotError(f"{repo} is not a git repository")
        commit, dirty, dirty_files = repo_state(repo)
        if dirty:
            notes.append(
                f"the repository has {len(dirty_files)} uncommitted change(s); "
                "the materialised state is not reproducible from the commit alone"
            )
        if worktree_root is None:
            raise SnapshotError("git variants require a worktree_root")
        worktree_root.mkdir(parents=True, exist_ok=True)
        target = worktree_root / f"{variant.name}-worktree"
        if target.exists():
            git(repo, "worktree", "remove", "--force", str(target), check=False)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
        git(repo, "worktree", "add", "--detach", str(target), ref.ref or "HEAD")
        try:
            file_map = build_file_map(target, excludes)
            _copy_tree(target, destination, excludes, includes)
        finally:
            git(repo, "worktree", "remove", "--force", str(target), check=False)
    elif ref.type == "path":
        if ref.path is None:
            raise SnapshotError(f"variant {variant.name}: path variants need a path")
        source = Path(ref.path).expanduser().resolve()
        if not source.exists():
            raise SnapshotError(f"framework path does not exist: {source}")
        if (source / ".git").exists():
            commit, dirty, dirty_files = repo_state(source)
            if dirty:
                notes.append(
                    f"the source tree has {len(dirty_files)} uncommitted change(s); "
                    "the snapshot is not reproducible from a commit alone"
                )
        file_map = build_file_map(source, excludes)
        _copy_tree(source, destination, excludes, includes)
    else:  # pragma: no cover - pydantic restricts the literal
        raise SnapshotError(f"unsupported framework type: {ref.type}")

    if not file_map:
        notes.append("the materialised framework is empty")

    materialised = MaterialisedVariant(
        variant=variant.name,
        root=destination,
        fingerprint=fingerprint_of(file_map),
        file_map=file_map,
        source_type=ref.type,
        source_ref=ref.ref,
        commit=commit,
        dirty=dirty,
        dirty_files=dirty_files,
        notes=notes,
    )

    if ref.knowledge_snapshot is not None:
        kb_source = Path(ref.knowledge_snapshot).expanduser().resolve()
        if not kb_source.exists():
            raise SnapshotError(f"knowledge snapshot does not exist: {kb_source}")
        kb_target = destination / "knowledge"
        kb_target.mkdir(parents=True, exist_ok=True)
        _copy_tree(kb_source, kb_target, excludes, includes)
        kb_map = build_file_map(kb_source, excludes)
        materialised.file_map.update({f"knowledge/{k}": v for k, v in kb_map.items()})
        materialised.fingerprint = fingerprint_of(materialised.file_map)
        materialised.notes.append(
            f"knowledge base snapshot pinned to {kb_source} (hash {fingerprint_of(kb_map)[:12]})"
        )

    return materialised


def _copy_tree(source: Path, destination: Path, excludes: list[str], includes: list[str]) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            continue
        rel = str(path.relative_to(source)).replace("\\", "/")
        if _excluded(rel, excludes):
            continue
        if not _included(rel, includes):
            continue
        target = destination / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def materialise_to_workspace(
    variant: Variant,
    workspace_root: Path,
    *,
    worktree_root: Path | None = None,
) -> MaterialisedVariant:
    """Materialise a variant directly into a run workspace root."""
    return materialise(variant, workspace_root, worktree_root=worktree_root)
