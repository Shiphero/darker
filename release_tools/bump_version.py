#!/usr/bin/env python

"""Helper script for bumping the version number

None of the existing tools (like `bump2version`) worked for this project out of the box
without modifications. Hence this script.

Usage::

    python release_tools/bump_version.py {major|minor|patch}

"""

import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Tuple

import click
import requests
from packaging.version import Version

if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


VERSION_PY_PATH = "src/darker/version.py"

PATTERNS = {
    VERSION_PY_PATH: {r"^__version__ *= *\"{old_version->new_version}\""},
    "action.yml": {
        (
            r"^    description: \'Python Version specifier \(PEP440\) - e\.g\."
            r' "{old_version->new_version}"'
        ),
        r'^    default: "{old_version->new_version}"',
        (
            r"^      uses: akaihola/darker/.github/actions/commit-range"
            r"@{old_version->new_version}"
        ),
    },
    "README.rst": {
        r"^           rev: {old_version->new_version}",
        r"^       rev: {old_version->new_version}",
        r"^         - uses: akaihola/darker@{old_version->new_version}",
        r'^             version: "{old_version->new_version}"',
        r"label=release%20{new_version->next_version}",
        (
            r"^\.\. \|next-milestone\| image::"
            r" https://img\.shields\.io/github/milestones/progress/akaihola/darker/"
            r"{new_milestone->next_milestone}"
        ),
        (
            r"^\.\. _next-milestone:"
            r" https://github\.com/akaihola/darker/milestone/"
            r"{new_milestone->next_milestone}"
        ),
    },
    ".github/ISSUE_TEMPLATE/bug_report.md": {
        r"^ - Darker version \[e\.g\. {old_version->new_version}\]"
    },
}

CURRENT_VERSION_RE = re.compile(
    next(iter(PATTERNS[VERSION_PY_PATH])).format(
        **{"old_version->new_version": r"([\d\.a-z]+)"}
    ),
    flags=re.MULTILINE,
)


CAPTURE_RE = re.compile(r"\{(\w+)->(\w+)\}")


def get_milestone_numbers() -> Dict[Version, str]:
    """Fetch milestone names and numbers from the GitHub API

    :return: Milestone names as version numbers, and corresponding milestone numbers
    :raises TypeError: Raised on unexpected JSON response

    """
    milestones = requests.get(
        "https://api.github.com/repos/akaihola/darker/milestones"
    ).json()
    if not isinstance(milestones, list):
        raise TypeError(f"Expected a JSON list from GitHub API, got {milestones}")
    return {Version(m["title"]): str(m["number"]) for m in milestones}


def get_next_milestone_version(
    version: Version, milestone_numbers: Dict[Version, str]
) -> Version:
    """Get the next larger version number found among milestone names

    :param version: The version number to search a larger one for
    :param milestone_numbers: Milestone names and numbers from the GitHub API
    :return: The next larger version number found
    :raises RuntimeError: Raised if no larger version number could be found

    """
    for milestone_version in sorted(milestone_numbers):
        if milestone_version > version:
            return milestone_version
    raise RuntimeError(f"No milestone exists for a version later than {version}")


class NoMatch(Exception):
    """Raised if pattern couldn't be found in the content"""


def replace_span(span: Tuple[int, int], replacement: str, content: str) -> str:
    """Replace given span in a string with the desired replacement string

    :param span: The span to replace
    :param replacement: The string to use as the replacement
    :param content: The content to replace the span in
    :return: The result after the replacement

    """
    start, end = span
    before = content[:start]
    after = content[end:]
    return f"{before}{replacement}{after}"


@click.command()
@click.option("-n", "--dry-run", is_flag=True, default=False)
@click.option("-M", "--major", "increment_major", is_flag=True, default=False)
@click.option("-m", "--minor", "increment_minor", is_flag=True, default=False)
def bump_version(dry_run: bool, increment_major: bool, increment_minor: bool) -> None:
    """Bump the version number"""
    (patterns, replacements, new_version) = get_replacements(
        increment_major, increment_minor
    )
    for path_str, pattern_templates in PATTERNS.items():
        path = Path(path_str)
        content = path.read_text(encoding="utf-8")
        for pattern_template in pattern_templates:
            # example: pattern_template == r"darker/{new_milestone->next_milestone}"
            template_match = CAPTURE_RE.search(pattern_template)
            if not template_match:
                raise NoMatch("Can't find `{CAPTURE_RE}` in `{pattern_template}`")
            current_pattern_name, replacement_name = template_match.groups()
            # example: template_match.groups() == ("new_milestone", "next_milestone")
            current_pattern = patterns[current_pattern_name]  # type: ignore[misc]
            # example: current_pattern == "14"
            replacement = replacements[replacement_name]  # type: ignore[misc]
            # example: replacement == "15"
            pattern = replace_span(
                template_match.span(), f"({current_pattern})", pattern_template
            )
            # example: pattern = r"darker/(14)"
            match = re.search(pattern, content, flags=re.MULTILINE)
            if not match:
                raise NoMatch(f"Can't find `{pattern}` in `{path_str}`")
            content = replace_span(match.span(1), replacement, content)
        if dry_run:
            print(f"\n######## {path_str} ########\n")
            print(content)
        else:
            path.write_text(content, encoding="utf-8")
    patch_changelog(new_version, dry_run)


class PatternDict(TypedDict):
    """Patterns for old and new version and the milestone number for the new version"""

    old_version: str
    new_version: str
    new_milestone: str


class ReplacementDict(TypedDict):
    """Replacement strings of new and next version and milestone num for next version"""

    new_version: str
    next_version: str
    next_milestone: str


def get_replacements(
    increment_major: bool, increment_minor: bool
) -> Tuple[PatternDict, ReplacementDict, Version]:
    """Return search patterns and replacements for version numbers and milestones

    Gets the current version from `version.py` and the milestone numbers from the GitHub
    API. Based on these, builds the search patterns for the old and new version numbers
    and the milestone number of the new version, as well as replacement strings for the
    new and next version numbers and the milestone number of the next version.

    :param increment_major: `True` to increment the major version number
    :param increment_minor: `True` to increment the minor version number
    :return: Patterns, replacements and the new version number

    """
    old_version = get_current_version()
    new_version = get_next_version(old_version, increment_major, increment_minor)
    milestone_numbers = get_milestone_numbers()
    next_version = get_next_milestone_version(new_version, milestone_numbers)
    patterns: PatternDict = {
        "old_version": re.escape(str(old_version)),
        "new_version": re.escape(str(new_version)),
        "new_milestone": milestone_numbers[new_version],
    }
    replacements: ReplacementDict = {
        "new_version": str(new_version),
        "next_version": str(next_version),
        "next_milestone": milestone_numbers[next_version],
    }
    return patterns, replacements, new_version


def get_current_version() -> Version:
    """Find the current version number from `version.py`

    :return: The current version number
    :raises NoMatch: Raised if `version.py` doesn't match the expected format

    """
    version_py = Path(VERSION_PY_PATH).read_text(encoding="utf-8")
    match = CURRENT_VERSION_RE.search(version_py)
    if not match:
        raise NoMatch("Can't find `{SEARCH_CURRENT_VERSION}` in `{VERSION_PY_PATH}`")
    current_version = match.group(1)
    return Version(current_version)


def get_next_version(
    current_version: Version, increment_major: bool, increment_minor: bool
) -> Version:
    """Return the next version number by incrementing elements as specified

    :param current_version: The version number to increment
    :param increment_major: `True` to increment the major version number
    :param increment_minor: `True` to increment the minor version number
    :return: The new version number

    """
    major, minor, micro = current_version.release
    if increment_major:
        return Version(f"{major + 1}.0.0")
    if increment_minor:
        return Version(f"{major}.{minor + 1}.0")
    if current_version.is_devrelease or current_version.is_prerelease:
        return current_version
    return Version(f"{major}.{minor}.{micro + 1}")


def patch_changelog(next_version: Version, dry_run: bool) -> None:
    """Insert the new version and create a new unreleased section in the change log

    :param next_version: The next version after the new version
    :param dry_run: `True` to just print the result

    """
    path = Path("CHANGES.rst")
    content = path.read_text(encoding="utf-8")
    before_unreleased = "These features will be included in the next release:\n\n"
    insert_point = content.index(before_unreleased) + len(before_unreleased)
    before = content[:insert_point]
    after = content[insert_point:]
    title = f"{next_version}_ - {date.today()}"
    new_content = (
        f"{before}"
        "Added\n"
        "-----\n\n"
        "Fixed\n"
        "-----\n\n\n"
        f"{title}\n"
        f"{len(title) * '='}\n\n"
        f"{after}"
    )
    if dry_run:
        print("######## CHANGES.rst ########")
        print(new_content[:200])
    else:
        path.write_text(new_content, encoding="utf-8")


if __name__ == "__main__":
    bump_version()  # pylint: disable=no-value-for-parameter
