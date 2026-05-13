from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


IGNORED_DIRS = {"__pycache__", ".clawhub", ".skillflag"}
IGNORED_FILES = {".DS_Store"}
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "optional-skills" / "manifest.json"


@dataclass(frozen=True)
class SkillManifestEntry:
    slug: str
    source: str
    sha256: str


def _iter_hashed_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(skill_dir).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        if path.name in IGNORED_FILES:
            continue
        files.append(path)
    return files


def hash_skill_dir(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in _iter_hashed_files(skill_dir):
        rel = path.relative_to(skill_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_skill_manifest(path: Path | None = None) -> dict[str, SkillManifestEntry]:
    manifest_path = path or MANIFEST_PATH
    if not manifest_path.exists():
        return {}
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: dict[str, SkillManifestEntry] = {}
    for item in raw.get("skills", []):
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug", "")).strip()
        source = str(item.get("source", "")).strip()
        sha256 = str(item.get("sha256", "")).strip()
        if slug and source and sha256:
            entries[slug] = SkillManifestEntry(slug=slug, source=source, sha256=sha256)
    return entries


def _iter_external_origin_skill_dirs(skills_dir: Path) -> list[Path]:
    skill_dirs: set[Path] = set()
    for origin in sorted(skills_dir.rglob("origin.json")):
        if origin.parent.name not in {".clawhub", ".skillflag"}:
            continue
        skill_dirs.add(origin.parent.parent)
    return sorted(skill_dirs, key=lambda path: path.relative_to(skills_dir).as_posix())


def validate_external_skills(skills_dir: Path) -> None:
    """Fail fast if externally installed skills do not match the repo manifest."""
    if not skills_dir.exists():
        return

    manifest = load_skill_manifest()
    errors: list[str] = []
    top_level_external: set[str] = set()

    for skill_dir in _iter_external_origin_skill_dirs(skills_dir):
        rel = skill_dir.relative_to(skills_dir)
        if len(rel.parts) != 1:
            errors.append(
                f"{rel.as_posix()}: externally installed below nested path; "
                "external skills must be top-level manifest entries"
            )
            continue
        top_level_external.add(skill_dir.name)

    for child in sorted(skills_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        entry = manifest.get(child.name)
        if entry is None:
            if child.name in top_level_external:
                errors.append(f"{child.name}: externally installed but absent from manifest")
            continue

        actual = hash_skill_dir(child)
        if actual != entry.sha256:
            errors.append(
                f"{child.name}: sha256 mismatch for {entry.source} skill "
                f"(expected {entry.sha256}, got {actual})"
            )

    if errors:
        rendered = "; ".join(errors)
        raise RuntimeError(f"skill integrity check failed: {rendered}")
