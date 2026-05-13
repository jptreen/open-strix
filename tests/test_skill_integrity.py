from __future__ import annotations

import json

import pytest

from open_strix.skill_integrity import hash_skill_dir, validate_external_skills


def _write_skill(root, name: str, body: str = "# Skill\n") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_hash_skill_dir_ignores_external_origin_metadata(tmp_path):
    _write_skill(tmp_path, "demo")
    before = hash_skill_dir(tmp_path / "demo")

    origin_dir = tmp_path / "demo" / ".clawhub"
    origin_dir.mkdir()
    (origin_dir / "origin.json").write_text('{"slug":"demo"}\n', encoding="utf-8")

    assert hash_skill_dir(tmp_path / "demo") == before


def test_validate_external_skills_rejects_unmanifested_clawhub_skill(tmp_path):
    _write_skill(tmp_path, "untrusted")
    origin_dir = tmp_path / "untrusted" / ".clawhub"
    origin_dir.mkdir()
    (origin_dir / "origin.json").write_text('{"slug":"untrusted"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="absent from manifest"):
        validate_external_skills(tmp_path)


def test_validate_external_skills_rejects_unmanifested_skillflag_skill(tmp_path):
    _write_skill(tmp_path, "untrusted")
    origin_dir = tmp_path / "untrusted" / ".skillflag"
    origin_dir.mkdir()
    (origin_dir / "origin.json").write_text('{"id":"untrusted"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="absent from manifest"):
        validate_external_skills(tmp_path)


def test_validate_external_skills_rejects_nested_external_origin(tmp_path):
    _write_skill(tmp_path, "wrapper")
    nested = tmp_path / "wrapper" / "evil"
    nested.mkdir()
    (nested / "SKILL.md").write_text("# Evil\n", encoding="utf-8")
    origin_dir = nested / ".clawhub"
    origin_dir.mkdir()
    (origin_dir / "origin.json").write_text('{"slug":"evil"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="nested path"):
        validate_external_skills(tmp_path)


def test_validate_external_skills_accepts_manifested_local_skill(tmp_path, monkeypatch):
    _write_skill(tmp_path, "github-poller")
    digest = hash_skill_dir(tmp_path / "github-poller")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "slug": "github-poller",
                        "source": "test",
                        "sha256": digest,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("open_strix.skill_integrity.MANIFEST_PATH", manifest)

    validate_external_skills(tmp_path)


def test_validate_external_skills_rejects_manifest_mismatch(tmp_path, monkeypatch):
    _write_skill(tmp_path, "github-poller")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "slug": "github-poller",
                        "source": "test",
                        "sha256": "0" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("open_strix.skill_integrity.MANIFEST_PATH", manifest)

    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        validate_external_skills(tmp_path)
