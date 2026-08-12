"""Safe registration of a portable Idea+Code sample bundle.

Copying files into ``backend/data`` is insufficient because Code projects are
database-backed and carts are joined by project/package IDs.  This service
validates a bundle, creates the project record and file index, and writes the
cart association expected by existing frontend APIs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from sqlmodel import Session

from app.db import crud
from app.db.engine import _DATA_DIR
from app.models.plan_package import PlanPackage
from app.modules.platform.storage import get_plan_package_storage
from app.services import code_project_service as cps


MAX_ARCHIVE_FILES = 5000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
DEFAULT_IMPORT_ROOT = Path(_DATA_DIR) / "code_imports"
DEFAULT_SAMPLE_ROOT = Path(_DATA_DIR) / "sample_exports"


class BundleImportError(ValueError):
    pass


@dataclass(frozen=True)
class BundleLayout:
    root: Path
    plan_package_path: Path
    cart_path: Path
    project_path: Path


@dataclass(frozen=True)
class BundleImportResult:
    project_id: str
    package_id: str
    cart_id: str
    file_count: int
    total_size_bytes: int
    warnings: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_bundle_source(
    requested_path: str,
    *,
    allowed_roots: Optional[Sequence[Path]] = None,
) -> Path:
    """Resolve a server-local import path under explicitly allowed data roots."""
    roots = tuple(
        path.resolve()
        for path in (allowed_roots or (DEFAULT_IMPORT_ROOT, DEFAULT_SAMPLE_ROOT))
    )
    requested = Path(requested_path)
    candidates = [requested.resolve()] if requested.is_absolute() else [
        (DEFAULT_IMPORT_ROOT / requested).resolve(),
        (DEFAULT_SAMPLE_ROOT / requested).resolve(),
    ]
    for candidate in candidates:
        if candidate.exists() and any(_safe_relative(candidate, root) for root in roots):
            return candidate
    raise BundleImportError(
        "Bundle must exist under backend/data/code_imports or backend/data/sample_exports"
    )


def _extract_zip_safely(archive_path: Path, destination: Path) -> None:
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise BundleImportError(f"Archive has too many entries: {len(infos)}")
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or ".." in Path(normalized).parts:
                raise BundleImportError(f"Unsafe archive path: {info.filename}")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise BundleImportError(f"Archive symlink is not allowed: {info.filename}")
            total_size += info.file_size
            if total_size > MAX_ARCHIVE_BYTES:
                raise BundleImportError("Archive exceeds the uncompressed size limit")

            target = (destination / normalized).resolve()
            if not _safe_relative(target, destination):
                raise BundleImportError(f"Archive path escapes extraction root: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _find_bundle_root(extracted_or_directory: Path) -> Path:
    if (extracted_or_directory / "idea").is_dir() and (extracted_or_directory / "code").is_dir():
        return extracted_or_directory
    children = [path for path in extracted_or_directory.iterdir() if path.is_dir()]
    matches = [path for path in children if (path / "idea").is_dir() and (path / "code").is_dir()]
    if len(matches) != 1:
        raise BundleImportError("Expected exactly one bundle root containing idea/ and code/")
    return matches[0]


def _verify_checksums(root: Path) -> None:
    checksum_file = root / "CHECKSUMS.sha256"
    if not checksum_file.is_file():
        return
    for line_number, line in enumerate(checksum_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise BundleImportError(f"Invalid checksum line {line_number}")
        expected, relative = parts
        target = (root / relative.replace("/", os.sep)).resolve()
        if not _safe_relative(target, root) or not target.is_file():
            raise BundleImportError(f"Checksum target is missing or unsafe: {relative}")
        if _sha256_file(target).lower() != expected.lower():
            raise BundleImportError(f"Checksum mismatch: {relative}")


def inspect_bundle(root: Path) -> tuple[BundleLayout, PlanPackage, dict]:
    bundle_root = _find_bundle_root(root.resolve())
    for path in bundle_root.rglob("*"):
        if path.is_symlink():
            raise BundleImportError(
                f"Bundle symlink is not allowed: {path.relative_to(bundle_root)}"
            )
    _verify_checksums(bundle_root)

    package_paths = sorted((bundle_root / "idea" / "plan_packages").glob("ppkg_*.json"))
    cart_paths = sorted((bundle_root / "code" / "cart_artifacts").glob("cart_*"))
    if len(package_paths) != 1:
        raise BundleImportError("Bundle must contain exactly one PlanPackage")
    if len(cart_paths) != 1 or not cart_paths[0].is_dir():
        raise BundleImportError("Bundle must contain exactly one cart directory")

    try:
        package = PlanPackage.model_validate_json(package_paths[0].read_text(encoding="utf-8"))
    except Exception as exc:
        raise BundleImportError(f"Invalid PlanPackage: {exc}") from exc

    cart_path = cart_paths[0]
    required = [
        cart_path / "data" / "manifest.json",
        cart_path / "event_log.json",
        cart_path / "blueprint_state.json",
        cart_path / "cart_results.json",
    ]
    missing = [str(path.relative_to(bundle_root)) for path in required if not path.is_file()]
    if missing:
        raise BundleImportError(f"Cart is incomplete; missing: {', '.join(missing)}")
    project_path = cart_path / "project"
    if not project_path.is_dir() or not any(path.is_file() for path in project_path.rglob("*")):
        raise BundleImportError("Cart project/ is empty")

    try:
        manifest = json.loads((cart_path / "data" / "manifest.json").read_text(encoding="utf-8"))
        event_log = json.loads((cart_path / "event_log.json").read_text(encoding="utf-8"))
        cart_results = json.loads((cart_path / "cart_results.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleImportError(f"Invalid cart JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BundleImportError("Cart manifest must be a JSON object")
    if not isinstance(event_log, list) or not all(isinstance(event, dict) for event in event_log):
        raise BundleImportError("Cart event_log must be a JSON array of objects")
    if not isinstance(cart_results, dict):
        raise BundleImportError("Cart results must be a JSON object")
    if manifest.get("package_id") != package.packageId:
        raise BundleImportError("Cart manifest package_id does not match PlanPackage")
    if not event_log or event_log[-1].get("event_type") != "cart_complete":
        raise BundleImportError("Cart has no terminal cart_complete event")
    if cart_results.get("package_id") != package.packageId:
        raise BundleImportError("cart_results package_id does not match PlanPackage")

    return BundleLayout(bundle_root, package_paths[0], cart_path, project_path), package, manifest


def _write_json_atomic(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _register_package(package: PlanPackage) -> None:
    storage = get_plan_package_storage()
    existing = storage.get(package.packageId)
    if existing:
        if existing.model_dump(mode="json") != package.model_dump(mode="json"):
            raise BundleImportError(f"PlanPackage ID conflict: {package.packageId}")
        return
    storage.create(package)


def import_bundle(
    source: Path,
    db: Session,
    *,
    title: Optional[str] = None,
) -> BundleImportResult:
    """Import a validated directory or ZIP and register it for existing APIs."""
    temp_parent = Path(_DATA_DIR) / "code_import_tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    project_id: Optional[str] = None
    target_cart: Optional[Path] = None

    with tempfile.TemporaryDirectory(prefix="bundle_", dir=temp_parent) as temp_name:
        inspection_root = source
        if source.is_file():
            if not zipfile.is_zipfile(source):
                raise BundleImportError("Only ZIP archives or extracted bundle directories are supported")
            inspection_root = Path(temp_name)
            _extract_zip_safely(source, inspection_root)

        layout, package, source_manifest = inspect_bundle(inspection_root)
        project_title = title or package.idea.title or package.researchQuestion
        project = cps.create_project(
            db,
            title=project_title,
            description=package.idea.problem or package.background.summary,
            language="python",
            source_idea_session_id=package.source.ideaSessionId,
            source_candidate_id=package.source.ideaCandidateId,
        )
        project_id = project.id
        project_root = Path(cps._get_project_repo_dir(project_id)).resolve()

        try:
            shutil.copytree(layout.project_path, project_root, dirs_exist_ok=True)
            file_count, total_bytes = cps.index_existing_project_files(db, project_id)

            package_short = package.packageId.removeprefix("ppkg_")[:12]
            project_short = project_id.removeprefix("cproj_")[:12]
            cart_id = f"cart_{project_short}_{package_short}_import"
            cart_root = Path(_DATA_DIR) / "cart_artifacts"
            cart_root.mkdir(parents=True, exist_ok=True)
            target_cart = cart_root / cart_id
            if target_cart.exists():
                cart_id = f"{cart_id}_{uuid.uuid4().hex[:6]}"
                target_cart = cart_root / cart_id
            shutil.copytree(layout.cart_path, target_cart)

            manifest_path = target_cart / "data" / "manifest.json"
            manifest = dict(source_manifest)
            manifest.update({
                "cart_id": cart_id,
                "project_id": project_id,
                "package_id": package.packageId,
                "imported_at": datetime.now(timezone.utc).isoformat(),
            })
            _write_json_atomic(manifest_path, manifest)
            cart_results_path = target_cart / "cart_results.json"
            cart_results = json.loads(cart_results_path.read_text(encoding="utf-8"))
            cart_results["cart_id"] = cart_id
            cart_results["project_id"] = project_id
            _write_json_atomic(cart_results_path, cart_results)

            _register_package(package)
        except Exception:
            try:
                crud.delete_project_files(db, project_id)
                crud.delete_project_v2(db, project_id)
            finally:
                cart_base = (Path(_DATA_DIR) / "cart_artifacts").resolve()
                if target_cart is not None and _safe_relative(target_cart, cart_base) and target_cart.exists():
                    shutil.rmtree(target_cart)
                managed_project_root = (Path(_DATA_DIR) / "code_projects" / project_id).resolve()
                expected_parent = (Path(_DATA_DIR) / "code_projects").resolve()
                if _safe_relative(managed_project_root, expected_parent) and managed_project_root.exists():
                    shutil.rmtree(managed_project_root)
            raise

    warnings = (
        "The bundle contains a PlanPackage but not a complete Idea session history; "
        "it appears in PlanPackage/Code views, not as a reconstructed Idea run.",
    )
    return BundleImportResult(
        project_id=project_id,
        package_id=package.packageId,
        cart_id=cart_id,
        file_count=file_count,
        total_size_bytes=total_bytes,
        warnings=warnings,
    )


__all__ = [
    "BundleImportError",
    "BundleImportResult",
    "BundleLayout",
    "import_bundle",
    "inspect_bundle",
    "resolve_bundle_source",
]
