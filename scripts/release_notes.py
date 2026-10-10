#!/usr/bin/env python3
"""Build GitHub release notes from the changelogs.

Used by `.github/workflows/release.yaml` when a version tag is pushed:

    python3 scripts/release_notes.py 0.8.7 \\
        --manifest custom_components/ffbb_tracker/manifest.json > release_notes.md

- The version must match "version" in the manifest (a pre-release suffix such
  as ``0.9.0-rc1`` is compared on its ``0.9.0`` base).
- ``CHANGELOG.md`` must contain a ``## 0.8.7`` section; it is required.
- ``CHANGELOG.fr.md`` is appended, folded in a collapsible block, when it
  has a section for the same version too.

Exit status is 1, with a message on stderr, when a requirement is not met, so
a tag pushed without a changelog entry fails the release instead of
publishing empty notes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = ROOT / "CHANGELOG.md"
DEFAULT_CHANGELOG_FR = ROOT / "CHANGELOG.fr.md"

_BASE_VERSION = re.compile(r"\d+\.\d+\.\d+")
# A line made only of symbols/emoji (the decorative separators around each
# version block, horizontal rules): no letter, digit or whitespace.
_DECORATION = re.compile(r"^[^\w\s]+$")


class ReleaseNotesError(Exception):
    """A requirement for publishing the release is not met."""


def base_version(version: str) -> str:
    """Return the ``X.Y.Z`` part of a version, dropping any pre-release suffix."""
    match = _BASE_VERSION.match(version)
    if match is None:
        raise ReleaseNotesError(f"'{version}' is not a valid X.Y.Z version")
    return match.group(0)


def extract_section(changelog: str, version: str) -> str | None:
    """Return the body of the ``## <version>`` section, or None if absent.

    The heading may be bare (``## 0.8.7``) or Keep-a-Changelog style
    (``## [0.8.7] - 2026-10-02``). The body runs to the next ``## `` heading;
    decorative separator lines and surrounding blank lines are removed.
    """
    heading = re.compile(rf"^##\s+\[?{re.escape(version)}\]?(?:\s.*)?$")
    body: list[str] = []
    inside = False
    for line in changelog.splitlines():
        if inside:
            if line.startswith("## "):
                break
            body.append(line)
        elif heading.match(line):
            inside = True

    if not inside:
        return None

    kept = [line for line in body if not _DECORATION.match(line.strip())]
    text = "\n".join(kept).strip()
    return text or None


def build_release_notes(
    version: str,
    manifest: Path | None = None,
    changelog: Path = DEFAULT_CHANGELOG,
    changelog_fr: Path = DEFAULT_CHANGELOG_FR,
) -> str:
    """Return the markdown body of the release for ``version``."""
    base = base_version(version)

    if manifest is not None:
        manifest_version = json.loads(manifest.read_text(encoding="utf-8")).get(
            "version"
        )
        if manifest_version not in (version, base):
            raise ReleaseNotesError(
                f"tag {version} does not match version {manifest_version!r} "
                f"in {manifest}"
            )

    if not changelog.is_file():
        raise ReleaseNotesError(f"{changelog.name} not found")
    english = extract_section(changelog.read_text(encoding="utf-8"), base)
    if english is None:
        raise ReleaseNotesError(f"{changelog.name} has no '## {base}' section")

    notes = english
    if changelog_fr.is_file():
        french = extract_section(changelog_fr.read_text(encoding="utf-8"), base)
        if french is not None:
            notes += (
                "\n\n---\n\n<details>\n<summary>🇫🇷 Version française</summary>\n\n"
                f"{french}\n\n</details>"
            )

    return notes + "\n"


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("version", help="release version, e.g. 0.8.7 (no leading v)")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="manifest.json whose 'version' must match the release version",
    )
    parser.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument("--changelog-fr", type=Path, default=DEFAULT_CHANGELOG_FR)
    args = parser.parse_args(argv)

    try:
        notes = build_release_notes(
            args.version, args.manifest, args.changelog, args.changelog_fr
        )
    except ReleaseNotesError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
