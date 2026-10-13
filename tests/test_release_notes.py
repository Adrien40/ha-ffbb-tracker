"""Tests for scripts/release_notes.py, which backs the Release workflow.

A tag pushed without a matching changelog entry or manifest version must
fail the release rather than publish empty or mismatched notes, so every
failure mode is covered, plus an end-to-end run on the repository's own
files (the exact command `.github/workflows/release.yaml` runs).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "release_notes.py"
MANIFEST = ROOT / "custom_components" / "ffbb_tracker" / "manifest.json"

_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
release_notes = importlib.util.module_from_spec(_spec)
sys.modules["release_notes"] = release_notes
_spec.loader.exec_module(release_notes)

SEPARATOR = "🏀" * 10

CHANGELOG = f"""# Project - Changelog

## 0.9.0

{SEPARATOR}

### ✨ New features
- Newer thing.

{SEPARATOR}

## 0.8.7

{SEPARATOR}

Intro sentence.

### 🐛 Bug fixes
- Fixed thing.

---

{SEPARATOR}

## 0.8.70

### 🐛 Bug fixes
- Must never be picked for 0.8.7.
"""

CHANGELOG_FR = f"""# Projet - Journal

## 0.8.7

{SEPARATOR}

### 🐛 Corrections
- Chose corrigée.

{SEPARATOR}
"""


# --- extract_section ---------------------------------------------------------


def test_extract_section_returns_only_the_requested_version():
    section = release_notes.extract_section(CHANGELOG, "0.8.7")

    assert section is not None
    assert section.startswith("Intro sentence.")
    assert "Fixed thing." in section
    assert "Newer thing." not in section


def test_extract_section_does_not_match_a_longer_version_number():
    """`## 0.8.70` must not be mistaken for `## 0.8.7`, and vice versa."""
    section = release_notes.extract_section(CHANGELOG, "0.8.7")

    assert section is not None
    assert "Must never be picked" not in section
    other = release_notes.extract_section(CHANGELOG, "0.8.70")
    assert other is not None
    assert "Must never be picked" in other


def test_extract_section_strips_separators_and_rules():
    section = release_notes.extract_section(CHANGELOG, "0.8.7")

    assert section is not None
    assert "🏀" not in section
    assert "---" not in section
    assert section == section.strip()


@pytest.mark.parametrize(
    "heading", ["## [0.8.7]", "## [0.8.7] - 2026-10-02", "## 0.8.7 "]
)
def test_extract_section_accepts_keep_a_changelog_headings(heading):
    changelog = f"{heading}\n\n- Item.\n\n## 0.8.6\n\n- Old.\n"

    assert release_notes.extract_section(changelog, "0.8.7") == "- Item."


def test_extract_section_returns_none_when_missing_or_empty():
    assert release_notes.extract_section(CHANGELOG, "9.9.9") is None
    assert (
        release_notes.extract_section("## 0.8.7\n\n🏀🏀\n\n## 0.8.6\n", "0.8.7") is None
    )


# --- base_version ------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.8.7", "0.8.7"), ("1.2.0-rc1", "1.2.0"), ("1.2.0b2", "1.2.0")],
)
def test_base_version_drops_prerelease_suffixes(version, expected):
    assert release_notes.base_version(version) == expected


@pytest.mark.parametrize("version", ["", "v0.8.7", "0.8", "latest"])
def test_base_version_rejects_malformed_versions(version):
    with pytest.raises(release_notes.ReleaseNotesError):
        release_notes.base_version(version)


# --- build_release_notes -----------------------------------------------------


def _write(
    tmp_path: Path, *, english: str = CHANGELOG, french: str | None = CHANGELOG_FR
):
    en = tmp_path / "CHANGELOG.md"
    en.write_text(english, encoding="utf-8")
    fr = tmp_path / "CHANGELOG.fr.md"
    if french is not None:
        fr.write_text(french, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": "0.8.7"}), encoding="utf-8")
    return en, fr, manifest


def test_build_appends_the_french_section_in_a_collapsible_block(tmp_path):
    en, fr, manifest = _write(tmp_path)

    notes = release_notes.build_release_notes("0.8.7", manifest, en, fr)

    assert "Fixed thing." in notes
    assert "<details>" in notes
    assert "Chose corrigée." in notes
    assert notes.index("Fixed thing.") < notes.index("Chose corrigée.")
    assert notes.endswith("</details>\n")


def test_build_is_english_only_when_the_french_changelog_has_no_entry(tmp_path):
    en, fr, manifest = _write(tmp_path, french="# Projet\n\n## 0.8.6\n\n- Ancien.\n")

    notes = release_notes.build_release_notes("0.8.7", manifest, en, fr)

    assert "<details>" not in notes


def test_build_is_english_only_when_the_french_changelog_is_missing(tmp_path):
    en, fr, manifest = _write(tmp_path, french=None)

    notes = release_notes.build_release_notes("0.8.7", manifest, en, fr)

    assert "<details>" not in notes
    assert "Fixed thing." in notes


def test_build_uses_the_base_version_for_prerelease_tags(tmp_path):
    en, fr, manifest = _write(tmp_path)

    notes = release_notes.build_release_notes("0.8.7-rc1", manifest, en, fr)

    assert "Fixed thing." in notes


def test_build_fails_when_the_tag_does_not_match_the_manifest(tmp_path):
    en, fr, manifest = _write(tmp_path)

    with pytest.raises(release_notes.ReleaseNotesError, match="does not match"):
        release_notes.build_release_notes("0.9.0", manifest, en, fr)


def test_build_fails_when_the_changelog_has_no_entry(tmp_path):
    en, fr, manifest = _write(tmp_path, english="# Project\n\n## 0.8.6\n\n- Old.\n")

    with pytest.raises(
        release_notes.ReleaseNotesError, match=r"no '## 0\.8\.7' section"
    ):
        release_notes.build_release_notes("0.8.7", manifest, en, fr)


def test_build_fails_when_the_changelog_file_is_missing(tmp_path):
    with pytest.raises(release_notes.ReleaseNotesError, match="not found"):
        release_notes.build_release_notes(
            "0.8.7", None, tmp_path / "nope.md", tmp_path / "nope.fr.md"
        )


def test_build_without_a_manifest_skips_the_version_check(tmp_path):
    en, fr, _ = _write(tmp_path)

    assert "Fixed thing." in release_notes.build_release_notes("0.8.7", None, en, fr)


# --- command line ------------------------------------------------------------


def test_main_prints_the_notes_and_returns_zero(tmp_path, capsys):
    en, fr, manifest = _write(tmp_path)

    code = release_notes.main(
        [
            "0.8.7",
            "--manifest",
            str(manifest),
            "--changelog",
            str(en),
            "--changelog-fr",
            str(fr),
        ]
    )

    assert code == 0
    assert "Fixed thing." in capsys.readouterr().out


def test_main_reports_errors_on_stderr_and_returns_one(tmp_path, capsys):
    en, fr, manifest = _write(tmp_path)

    code = release_notes.main(
        [
            "0.9.0",
            "--manifest",
            str(manifest),
            "--changelog",
            str(en),
            "--changelog-fr",
            str(fr),
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.startswith("error:")


def test_release_command_works_on_the_repository_files():
    """Run the exact command of release.yaml against this repository.

    Fails as soon as the manifest version is bumped without a matching
    changelog entry -- i.e. before a tag is pushed, not after.
    """
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]

    result = subprocess.run(
        [sys.executable, str(SCRIPT), version, "--manifest", str(MANIFEST)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    assert "🏀🏀🏀" not in result.stdout
