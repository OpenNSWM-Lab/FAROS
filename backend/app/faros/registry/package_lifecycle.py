import re
from pathlib import Path
from typing import Protocol

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class VersionedSpec(Protocol):
    id: str
    version: str


def parse_semver(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"Invalid semantic version: {version}")
    return tuple(int(part) for part in match.groups())


def compare_semver(left: str, right: str) -> int:
    lval = parse_semver(left)
    rval = parse_semver(right)
    if lval < rval:
        return -1
    if lval > rval:
        return 1
    return 0


def package_dir_exists(root: Path, package_id: str, manifest_name: str) -> bool:
    return (root / package_id / manifest_name).is_file()
