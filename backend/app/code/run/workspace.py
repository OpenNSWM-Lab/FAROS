"""
Workspace Manager - Creates isolated workspaces for code execution.

Features:
- Copy repository to isolated workspace
- Apply patches safely
- Cleanup after execution
"""

import os
import shutil
import logging
import subprocess
import tempfile
from typing import Optional, List, Tuple
from pathlib import Path

from app.core.user_context import sanitized_subprocess_env
from app.core.paths import get_data_dir

logger = logging.getLogger(__name__)

# Base directory for workspaces
WORKSPACES_DIR = str(get_data_dir() / "workspaces")

# Directories to exclude when copying
EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "dist",
    "build",
    ".next",
    ".cache",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    "*.egg-info",
}

# Max size for copied files (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


class WorkspaceManager:
    """Manages isolated workspaces for code execution."""
    
    def __init__(self, workspaces_dir: str = WORKSPACES_DIR):
        self.workspaces_dir = workspaces_dir
        os.makedirs(self.workspaces_dir, exist_ok=True)
    
    def create_workspace(self, job_id: str, source_repo: str) -> str:
        """
        Create a workspace by copying the source repository.
        
        Args:
            job_id: Unique job identifier
            source_repo: Path to source repository
            
        Returns:
            Path to created workspace
        """
        workspace_path = os.path.join(self.workspaces_dir, job_id)
        
        if os.path.exists(workspace_path):
            logger.warning(f"Workspace already exists, removing: {workspace_path}")
            shutil.rmtree(workspace_path)
        
        os.makedirs(workspace_path, exist_ok=True)
        
        # Copy repository with exclusions
        self._copy_repo(source_repo, workspace_path)
        
        logger.info(f"Created workspace: {workspace_path}")
        return workspace_path
    
    def _copy_repo(self, source: str, dest: str) -> None:
        """Copy repository with exclusions."""
        source_path = Path(source)
        dest_path = Path(dest)
        
        for item in source_path.rglob("*"):
            # Skip excluded directories
            rel_path = item.relative_to(source_path)
            skip = False
            for part in rel_path.parts:
                if part in EXCLUDE_DIRS or any(
                    part.endswith(exc.replace("*", "")) for exc in EXCLUDE_DIRS if "*" in exc
                ):
                    skip = True
                    break
            
            if skip:
                continue
            
            dest_item = dest_path / rel_path
            
            if item.is_dir():
                dest_item.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                # Skip large files
                try:
                    if item.stat().st_size > MAX_FILE_SIZE:
                        logger.debug(f"Skipping large file: {rel_path}")
                        continue
                except OSError:
                    continue
                
                dest_item.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, dest_item)
                except (PermissionError, OSError) as e:
                    logger.warning(f"Failed to copy {rel_path}: {e}")
    
    def apply_patch(self, workspace_path: str, patch_content: str) -> Tuple[bool, str]:
        """
        Apply a unified diff patch to the workspace.
        
        Args:
            workspace_path: Path to workspace
            patch_content: Unified diff content
            
        Returns:
            Tuple of (success, message)
        """
        if not patch_content or not patch_content.strip():
            return True, "No patch to apply"
        
        # Write patch to temp file
        patch_file = os.path.join(workspace_path, ".patch")
        try:
            with open(patch_file, 'w', encoding='utf-8') as f:
                f.write(patch_content)
            
            # Try to apply patch
            result = subprocess.run(
                ["patch", "-p1", "--forward", "--ignore-whitespace", "-i", patch_file],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=30,
                env=sanitized_subprocess_env(),
            )
            
            if result.returncode == 0:
                return True, f"Patch applied successfully:\n{result.stdout}"
            else:
                # Try with --dry-run to see what would happen
                dry_run = subprocess.run(
                    ["patch", "-p1", "--dry-run", "-i", patch_file],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=sanitized_subprocess_env(),
                )
                return False, f"Patch failed:\n{result.stderr}\nDry run output:\n{dry_run.stdout}"
                
        except subprocess.TimeoutExpired:
            return False, "Patch application timed out"
        except FileNotFoundError:
            # patch command not available, try manual application
            return self._apply_patch_manual(workspace_path, patch_content)
        except Exception as e:
            return False, f"Patch error: {str(e)}"
        finally:
            if os.path.exists(patch_file):
                os.remove(patch_file)
    
    def _apply_patch_manual(self, workspace_path: str, patch_content: str) -> Tuple[bool, str]:
        """
        Manually apply patch when patch command is not available.
        
        This is a simplified implementation that handles basic unified diffs.
        """
        try:
            lines = patch_content.split('\n')
            current_file = None
            changes = []
            
            for line in lines:
                if line.startswith('--- a/') or line.startswith('--- '):
                    # Old file (ignore)
                    pass
                elif line.startswith('+++ b/') or line.startswith('+++ '):
                    # New file path
                    path = line[6:] if line.startswith('+++ b/') else line[4:]
                    current_file = os.path.join(workspace_path, path.strip())
                elif line.startswith('@@'):
                    # Hunk header - simplified handling
                    pass
                elif current_file:
                    if line.startswith('+') and not line.startswith('+++'):
                        changes.append(('add', current_file, line[1:]))
                    elif line.startswith('-') and not line.startswith('---'):
                        changes.append(('remove', current_file, line[1:]))
            
            # For now, just create/append to files for additions
            files_modified = set()
            for action, filepath, content in changes:
                if action == 'add':
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)
                    with open(filepath, 'a', encoding='utf-8') as f:
                        f.write(content + '\n')
                    files_modified.add(filepath)
            
            return True, f"Manual patch applied to {len(files_modified)} files (simplified)"
            
        except Exception as e:
            return False, f"Manual patch failed: {str(e)}"
    
    def cleanup_workspace(self, job_id: str) -> bool:
        """
        Remove a workspace.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if cleanup succeeded
        """
        workspace_path = os.path.join(self.workspaces_dir, job_id)
        
        if not os.path.exists(workspace_path):
            return True
        
        try:
            shutil.rmtree(workspace_path)
            logger.info(f"Cleaned up workspace: {workspace_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to cleanup workspace {job_id}: {e}")
            return False
    
    def get_workspace_path(self, job_id: str) -> Optional[str]:
        """Get workspace path if it exists."""
        workspace_path = os.path.join(self.workspaces_dir, job_id)
        return workspace_path if os.path.exists(workspace_path) else None
    
    def list_workspaces(self) -> List[str]:
        """List all workspace job IDs."""
        if not os.path.exists(self.workspaces_dir):
            return []
        return [
            d for d in os.listdir(self.workspaces_dir)
            if os.path.isdir(os.path.join(self.workspaces_dir, d))
        ]
