"""File storage for PlanPackage artifacts."""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.models.plan_package import PlanPackage


def generate_plan_package_id() -> str:
    return f"ppkg_{uuid.uuid4().hex[:12]}"


class PlanPackageStorage:
    """File-based storage for complete idea+plan deliverables."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "plan_packages"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, package_id: str) -> Path:
        return self.base_path / f"{package_id}.json"

    def _serialize(self, package: PlanPackage) -> dict:
        return package.model_dump(mode="json")

    def _deserialize(self, data: dict) -> PlanPackage:
        if data.get("createdAt") and isinstance(data["createdAt"], str):
            data["createdAt"] = datetime.fromisoformat(data["createdAt"])
        return PlanPackage(**data)

    def create(self, package: PlanPackage) -> PlanPackage:
        path = self._path(package.packageId)
        if path.exists():
            raise ValueError(f"PlanPackage {package.packageId} already exists")
        temp_path = path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self._serialize(package), f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        return package

    def update(self, package: PlanPackage) -> PlanPackage:
        path = self._path(package.packageId)
        temp_path = path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self._serialize(package), f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        return package

    def get(self, package_id: str) -> Optional[PlanPackage]:
        path = self._path(package_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return self._deserialize(json.load(f))

    def list_all(self) -> List[PlanPackage]:
        packages: List[PlanPackage] = []
        for path in self.base_path.glob("ppkg_*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    packages.append(self._deserialize(json.load(f)))
            except Exception:
                continue
        packages.sort(key=lambda p: p.createdAt, reverse=True)
        return packages

    def get_by_idea_session(self, idea_session_id: str) -> Optional[PlanPackage]:
        packages = [
            package
            for package in self.list_all()
            if package.source.ideaSessionId == idea_session_id
        ]
        return packages[0] if packages else None

    def get_by_plan_session(self, plan_session_id: str) -> Optional[PlanPackage]:
        packages = [
            package
            for package in self.list_all()
            if package.source.planSessionId == plan_session_id
        ]
        return packages[0] if packages else None

    def list_by_idea_candidate(self, candidate_id: str) -> List[PlanPackage]:
        return [
            package
            for package in self.list_all()
            if package.source.ideaCandidateId == candidate_id
        ]


_storage_instance: Optional[PlanPackageStorage] = None


def _get_data_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data")


def get_plan_package_storage() -> PlanPackageStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = PlanPackageStorage(_get_data_dir())
    return _storage_instance

