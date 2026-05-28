import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class PackageAuditStore:
    """File-backed audit log and backup store for FAROS package lifecycle events."""

    def __init__(self, root: Optional[Path] = None):
        base = Path(__file__).resolve().parents[3] / "data" / "faros" / "packages"
        self.root = root or base
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "audit.json"
        self.backups_root = self.root / "backups"
        self.backups_root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]")

    def list_events(self, package_type: str | None = None, limit: int | None = None) -> List[Dict[str, Any]]:
        events = json.loads(self.path.read_text())
        if package_type:
            events = [event for event in events if event.get("packageType") == package_type]
        if limit is not None:
            events = events[-limit:]
        return list(reversed(events))

    def append_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        events = json.loads(self.path.read_text())
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        events.append(event)
        self.path.write_text(json.dumps(events, indent=2))
        return event

    def backup_package(self, package_type: str, package_id: str, source_dir: Path, version: str | None = None) -> str:
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Package directory '{source_dir}' not found for backup")
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_dir = self.backups_root / package_type / package_id / timestamp
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, backup_dir)
        self.append_event({
            'packageType': package_type,
            'packageId': package_id,
            'action': 'backup',
            'version': version,
            'backupPath': str(backup_dir),
        })
        return str(backup_dir)

    def latest_backup(self, package_type: str, package_id: str) -> Dict[str, Any] | None:
        for event in self.list_events(package_type=package_type):
            if event.get('packageId') == package_id and event.get('action') == 'backup':
                return event
        return None

    def restore_backup(self, package_type: str, package_id: str, target_dir: Path) -> Dict[str, Any]:
        backup = self.latest_backup(package_type, package_id)
        if not backup:
            raise ValueError(f"No backup available for {package_type} '{package_id}'")
        backup_path = Path(backup['backupPath'])
        if not backup_path.is_dir():
            raise FileNotFoundError(f"Backup path '{backup_path}' for {package_type} '{package_id}' not found")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(backup_path, target_dir)
        restored = self.append_event({
            'packageType': package_type,
            'packageId': package_id,
            'action': 'rollback',
            'version': backup.get('version'),
            'backupPath': str(backup_path),
        })
        return restored
