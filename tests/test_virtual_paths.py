"""Unit tests for ``open_strix.virtual_paths`` (tony-ugg).

These tests lock in the virtual-path remapping behavior for paths the
agent pastes verbatim from its skills-discovery prompt section (e.g.
``/skills/adhd-research/SKILL.md``) into the open-strix filesystem
tools. See ``virtual_paths.py`` docstring for the full incident history.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from open_strix.builtin_skills import BUILTIN_HOME_DIRNAME
from open_strix.virtual_paths import resolve_virtual_path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Simulate an agent home with a skills/ subtree and a builtin
    skills subtree, so ``is_file()`` checks in downstream tools would
    see real files when paths resolve correctly.
    """
    (tmp_path / "skills" / "adhd-research").mkdir(parents=True)
    (tmp_path / "skills" / "adhd-research" / "SKILL.md").write_text("# adhd\n")
    (tmp_path / BUILTIN_HOME_DIRNAME / "memory").mkdir(parents=True)
    (tmp_path / BUILTIN_HOME_DIRNAME / "memory" / "SKILL.md").write_text("# memory\n")
    return tmp_path


def test_user_skill_file_remaps(home: Path):
    resolved = resolve_virtual_path("/skills/adhd-research/SKILL.md", home)
    assert resolved == home / "skills" / "adhd-research" / "SKILL.md"
    assert resolved.is_file()


def test_user_skill_root_remaps(home: Path):
    resolved = resolve_virtual_path("/skills", home)
    assert resolved == home / "skills"
    assert resolved.is_dir()


def test_builtin_skill_file_remaps(home: Path):
    resolved = resolve_virtual_path(
        f"/{BUILTIN_HOME_DIRNAME}/memory/SKILL.md",
        home,
    )
    assert resolved == home / BUILTIN_HOME_DIRNAME / "memory" / "SKILL.md"
    assert resolved.is_file()


def test_builtin_skill_root_remaps(home: Path):
    resolved = resolve_virtual_path(f"/{BUILTIN_HOME_DIRNAME}", home)
    assert resolved == home / BUILTIN_HOME_DIRNAME


def test_real_absolute_path_passes_through(home: Path):
    # A real host path that happens to live under home — should not be
    # double-resolved or re-rooted.
    target = home / "skills" / "adhd-research" / "SKILL.md"
    resolved = resolve_virtual_path(str(target), home)
    assert resolved == target


def test_real_absolute_path_outside_home_passes_through(home: Path):
    # Paths outside home aren't treated as virtual — no "the agent
    # meant something under home" heuristic. /etc/passwd stays
    # /etc/passwd (modulo ``.resolve()``'s symlink canonicalisation,
    # which on macOS turns ``/etc`` into ``/private/etc``). We compare
    # against the plain Path's resolved form, which is what a caller
    # would do anyway.
    resolved = resolve_virtual_path("/etc/passwd", home)
    assert resolved == Path("/etc/passwd").expanduser().resolve()
    # And critically: we did NOT re-root it under home.
    assert home not in resolved.parents


def test_relative_path_resolves_against_cwd(home: Path, monkeypatch, tmp_path: Path):
    # Relative paths keep their historical behaviour: resolved against
    # the current working directory, not the agent home.
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    resolved = resolve_virtual_path("some/relative/path.py", home)
    assert resolved == (workdir / "some" / "relative" / "path.py").resolve()


def test_prefix_collision_not_remapped(home: Path):
    # ``/skills-archive/...`` is NOT under the ``/skills`` virtual
    # root. Segment boundary check prevents false positives.
    resolved = resolve_virtual_path("/skills-archive/old.md", home)
    assert resolved == Path("/skills-archive/old.md")


def test_user_skill_deep_nesting(home: Path):
    # Arbitrary depth under the virtual root should remap correctly.
    resolved = resolve_virtual_path(
        "/skills/foo/bar/baz/helper.py",
        home,
    )
    assert resolved == home / "skills" / "foo" / "bar" / "baz" / "helper.py"


def test_remapped_path_can_be_parent_of_new_file(home: Path):
    # write_file scaffolds new skills: ``resolved.parent.mkdir`` must
    # land under home, not try to mkdir ``/skills`` on host root.
    resolved = resolve_virtual_path(
        "/skills/new-skill-idea/SKILL.md",
        home,
    )
    assert resolved.parent == home / "skills" / "new-skill-idea"
    # Parent doesn't exist yet; we just check the path is correct.
    assert not resolved.parent.exists()
    # Confirm mkdir would land safely under home (not attempt /skills).
    resolved.parent.mkdir(parents=True, exist_ok=True)
    assert resolved.parent.is_dir()
    assert resolved.parent.parent == home / "skills"


def test_expanduser_is_honoured(home: Path, monkeypatch, tmp_path: Path):
    fake_home = tmp_path / "fake-user-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    resolved = resolve_virtual_path("~/file.txt", home)
    assert resolved == fake_home / "file.txt"
