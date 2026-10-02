"""Consistency tests for the repository's release/CI/documentation metadata.

None of this affects runtime behaviour, but each item breaks silently when
it drifts -- a badge shows "no status", a release has no notes, a copied
workflow still points at another integration's package:

- the README badges must point at workflow files that exist, in this
  repository, and match between the English and French READMEs;
- every changelog needs an entry for the version in the manifest, in order;
- workflows must reference paths that exist in this repository;
- `quality_scale.yaml` backs the "Platinum" badge, so it must have no open
  rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "ffbb_tracker"
WORKFLOWS = ROOT / ".github" / "workflows"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
VERSION = MANIFEST["version"]
REPO = MANIFEST["documentation"].removeprefix("https://github.com/")

READMES = ["README.md", "README.fr.md"]
CHANGELOGS = ["CHANGELOG.md", "CHANGELOG.fr.md"]

# Badges expected in both READMEs, by workflow file.
BADGE_WORKFLOWS = [
    "tests.yaml",
    "hacs.yaml",
    "hassfest.yaml",
    "ruff.yaml",
    "mypy.yaml",
]

_BADGE_IMAGE = re.compile(
    r"img\.shields\.io/github/actions/workflow/status/([^/]+/[^/]+)/([\w.-]+\.ya?ml)"
)
_BADGE_LINK = re.compile(
    r"github\.com/([^/]+/[^/]+)/actions/workflows/([\w.-]+\.ya?ml)"
)


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# --- README badges -----------------------------------------------------------


@pytest.mark.parametrize("readme", READMES)
def test_workflow_badges_target_this_repository(readme):
    """A badge copied from another project must not survive."""
    text = _text(readme)

    repos = {repo for repo, _ in _BADGE_IMAGE.findall(text)}
    repos |= {repo for repo, _ in _BADGE_LINK.findall(text)}

    assert repos == {REPO}


@pytest.mark.parametrize("readme", READMES)
def test_workflow_badges_point_at_existing_workflow_files(readme):
    """The image and the link of each badge must name a real workflow file.

    A wrong extension (`.yml` for a `.yaml` file) renders as "no status".
    """
    text = _text(readme)

    files = {name for _, name in _BADGE_IMAGE.findall(text)}
    files |= {name for _, name in _BADGE_LINK.findall(text)}

    assert files, "no workflow badge found"
    for name in files:
        assert (WORKFLOWS / name).is_file(), f"{readme}: no workflow named {name}"


@pytest.mark.parametrize("readme", READMES)
def test_each_badge_image_and_link_use_the_same_workflow(readme):
    text = _text(readme)

    images = sorted(name for _, name in _BADGE_IMAGE.findall(text))
    links = sorted(name for _, name in _BADGE_LINK.findall(text))

    assert images == links


@pytest.mark.parametrize("readme", READMES)
@pytest.mark.parametrize("workflow", BADGE_WORKFLOWS)
def test_readme_has_a_badge_for_each_ci_workflow(readme, workflow):
    assert workflow in {name for _, name in _BADGE_IMAGE.findall(_text(readme))}


def test_both_readmes_show_the_same_workflow_badges():
    english = {name for _, name in _BADGE_IMAGE.findall(_text("README.md"))}
    french = {name for _, name in _BADGE_IMAGE.findall(_text("README.fr.md"))}

    assert english == french


@pytest.mark.parametrize("readme", READMES)
def test_quality_scale_badge_links_to_an_existing_file(readme):
    text = _text(readme)

    match = re.search(
        r"HA%20Quality%20Scale-([A-Za-z]+)[^)]*\)\]\(([^)]+quality_scale\.yaml)\)", text
    )

    assert match, f"{readme}: no quality scale badge"
    assert match.group(2).endswith("custom_components/ffbb_tracker/quality_scale.yaml")
    assert (INTEGRATION / "quality_scale.yaml").is_file()


# --- Changelogs --------------------------------------------------------------


def _versions(changelog: str) -> list[tuple[int, ...]]:
    headings = re.findall(
        r"^##\s+\[?(\d+\.\d+\.\d+)\]?", _text(changelog), flags=re.MULTILINE
    )
    return [tuple(int(part) for part in heading.split(".")) for heading in headings]


@pytest.mark.parametrize("changelog", CHANGELOGS)
def test_changelog_has_an_entry_for_the_manifest_version(changelog):
    current = tuple(int(part) for part in VERSION.split("."))

    assert current in _versions(changelog)


@pytest.mark.parametrize("changelog", CHANGELOGS)
def test_changelog_versions_are_unique_and_newest_first(changelog):
    versions = _versions(changelog)

    assert versions == sorted(set(versions), reverse=True)


def test_manifest_version_is_the_newest_changelog_version():
    """Catches a changelog entry written for a release that was never bumped."""
    newest = max(_versions("CHANGELOG.md"))

    assert ".".join(map(str, newest)) == VERSION


def test_both_changelogs_cover_the_same_versions():
    assert _versions("CHANGELOG.md") == _versions("CHANGELOG.fr.md")


# --- Workflows ---------------------------------------------------------------


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yaml"))


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda path: path.name)
def test_workflow_is_valid_yaml_with_jobs(workflow):
    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))

    assert isinstance(data, dict)
    assert data.get("jobs")


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda path: path.name)
def test_workflow_paths_exist_in_this_repository(workflow):
    """A workflow copied from another integration must not keep its paths."""
    text = workflow.read_text(encoding="utf-8")

    assert "blue_connect" not in text
    for path in re.findall(r"custom_components/[\w/.]+", text):
        assert (ROOT / path).exists(), f"{workflow.name}: {path} does not exist"
    for script in re.findall(r"scripts/[\w./]+\.py", text):
        assert (ROOT / script).is_file(), f"{workflow.name}: {script} does not exist"


def test_there_is_no_leftover_combined_validate_workflow():
    """hassfest and HACS validation have their own workflows (and badges)."""
    assert not (WORKFLOWS / "validate.yaml").exists()
    assert not (WORKFLOWS / "validate.yml").exists()


def test_ci_dependencies_include_every_tool_the_workflows_install():
    """The mypy and tests workflows only `pip install -r requirements_test.txt`."""
    requirements = _text("requirements_test.txt").lower()

    for tool in ("mypy", "pytest-cov", "ruff"):
        assert tool in requirements


def test_pytest_coverage_is_scoped_to_the_integration():
    """`pytest --cov` with no argument relies on this configuration."""
    pyproject = _text("pyproject.toml")

    assert "[tool.coverage.run]" in pyproject
    assert "custom_components/ffbb_tracker" in pyproject


# --- Quality scale -----------------------------------------------------------


def _quality_rules() -> dict[str, object]:
    data = yaml.safe_load((INTEGRATION / "quality_scale.yaml").read_text("utf-8"))
    return data["rules"]


def _status(rule: object) -> str:
    return rule["status"] if isinstance(rule, dict) else str(rule)


def test_quality_scale_has_no_open_rule():
    """The README advertises the Platinum tier: nothing may be left `todo`."""
    open_rules = [
        name
        for name, rule in _quality_rules().items()
        if _status(rule) not in ("done", "exempt")
    ]

    assert open_rules == []


def test_quality_scale_covers_the_platinum_rules():
    rules = _quality_rules()

    for name in ("async-dependency", "inject-websession", "strict-typing"):
        assert _status(rules[name]) == "done"


def test_every_exempt_rule_explains_why():
    unexplained = [
        name
        for name, rule in _quality_rules().items()
        if _status(rule) == "exempt"
        and not (isinstance(rule, dict) and str(rule.get("comment", "")).strip())
    ]

    assert unexplained == []
