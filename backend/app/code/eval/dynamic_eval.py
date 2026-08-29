"""
Dynamic Code Evaluator - Execution-based evaluation.

Tier B evaluation that runs tests and commands.
Inspired by RepoExec's execution framework.
"""

import os
import subprocess
import logging
import tempfile
import shutil
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.core.user_context import sanitized_subprocess_env

logger = logging.getLogger(__name__)

# Default timeout for command execution (seconds)
DEFAULT_TIMEOUT = 60


class ExecutionStatus(str, Enum):
    """Status of dynamic execution."""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class CommandResult:
    """Result of a single command execution."""
    command: str
    status: ExecutionStatus
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:5000] if self.stdout else "",  # Limit output
            "stderr": self.stderr[:5000] if self.stderr else "",
            "duration_ms": self.duration_ms,
        }


@dataclass
class DynamicEvalResult:
    """Result of dynamic evaluation."""
    status: ExecutionStatus
    tests_found: bool
    tests_passed: int
    tests_failed: int
    tests_skipped: int
    commands: List[CommandResult] = field(default_factory=list)
    summary: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "tests_found": self.tests_found,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "commands": [c.to_dict() for c in self.commands],
            "summary": self.summary,
        }


class DynamicEvaluator:
    """
    Dynamic code evaluator using subprocess execution.
    
    Features:
    - Test discovery and execution
    - Custom command execution
    - Timeout handling
    - Output capture
    """
    
    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        """
        Initialize evaluator.
        
        Args:
            timeout: Default timeout in seconds
        """
        self.timeout = timeout
    
    def _run_command(
        self,
        command: str,
        cwd: str,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> CommandResult:
        """
        Run a command and capture output.
        
        Args:
            command: Command to run
            cwd: Working directory
            timeout: Timeout in seconds
            env: Environment variables
            
        Returns:
            CommandResult with output and status
        """
        timeout = timeout or self.timeout
        start_time = datetime.utcnow()
        
        # Prepare environment
        run_env = sanitized_subprocess_env()
        if env:
            run_env.update(env)
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
            )
            
            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)
            
            status = ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.FAILED
            
            return CommandResult(
                command=command,
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
            )
            
        except subprocess.TimeoutExpired as e:
            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)
            
            return CommandResult(
                command=command,
                status=ExecutionStatus.TIMEOUT,
                exit_code=None,
                stdout=e.stdout or "" if hasattr(e, 'stdout') else "",
                stderr=f"Command timed out after {timeout} seconds",
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            end_time = datetime.utcnow()
            duration_ms = int((end_time - start_time).total_seconds() * 1000)
            
            return CommandResult(
                command=command,
                status=ExecutionStatus.ERROR,
                exit_code=None,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
            )
    
    def _detect_test_framework(self, repo_path: str) -> Optional[str]:
        """
        Detect test framework in repository.
        
        Returns:
            Test command or None if no tests found
        """
        # Check for Python tests
        if os.path.exists(os.path.join(repo_path, "pytest.ini")) or \
           os.path.exists(os.path.join(repo_path, "pyproject.toml")) or \
           os.path.exists(os.path.join(repo_path, "setup.py")):
            # Check if pytest is available
            if os.path.exists(os.path.join(repo_path, "tests")) or \
               os.path.exists(os.path.join(repo_path, "test")):
                return "python -m pytest --tb=short -q"
        
        # Check for Node.js tests
        package_json = os.path.join(repo_path, "package.json")
        if os.path.exists(package_json):
            try:
                import json
                with open(package_json) as f:
                    pkg = json.load(f)
                if "scripts" in pkg and "test" in pkg["scripts"]:
                    return "npm test"
            except Exception:
                pass
        
        # Check for Go tests
        if any(f.endswith("_test.go") for f in os.listdir(repo_path) if os.path.isfile(os.path.join(repo_path, f))):
            return "go test ./..."
        
        return None
    
    def _parse_pytest_output(self, stdout: str, stderr: str) -> Dict[str, int]:
        """Parse pytest output for test counts."""
        import re
        
        # Try to parse summary line like "5 passed, 2 failed, 1 skipped"
        combined = stdout + stderr
        
        passed = 0
        failed = 0
        skipped = 0
        
        # Match patterns like "5 passed"
        passed_match = re.search(r'(\d+)\s+passed', combined)
        if passed_match:
            passed = int(passed_match.group(1))
        
        failed_match = re.search(r'(\d+)\s+failed', combined)
        if failed_match:
            failed = int(failed_match.group(1))
        
        skipped_match = re.search(r'(\d+)\s+skipped', combined)
        if skipped_match:
            skipped = int(skipped_match.group(1))
        
        return {"passed": passed, "failed": failed, "skipped": skipped}
    
    def evaluate(
        self,
        repo_path: str,
        commands: Optional[List[str]] = None,
        run_tests: bool = True,
        timeout: Optional[int] = None,
    ) -> DynamicEvalResult:
        """
        Evaluate code dynamically.
        
        Args:
            repo_path: Path to repository
            commands: Custom commands to run
            run_tests: Whether to auto-detect and run tests
            timeout: Timeout per command
            
        Returns:
            DynamicEvalResult with execution results
        """
        if not os.path.isdir(repo_path):
            return DynamicEvalResult(
                status=ExecutionStatus.ERROR,
                tests_found=False,
                tests_passed=0,
                tests_failed=0,
                tests_skipped=0,
                summary=f"Repository path does not exist: {repo_path}",
            )
        
        command_results = []
        tests_found = False
        tests_passed = 0
        tests_failed = 0
        tests_skipped = 0
        
        # Run custom commands first
        if commands:
            for cmd in commands:
                result = self._run_command(cmd, repo_path, timeout)
                command_results.append(result)
        
        # Auto-detect and run tests
        if run_tests:
            test_cmd = self._detect_test_framework(repo_path)
            
            if test_cmd:
                tests_found = True
                result = self._run_command(test_cmd, repo_path, timeout)
                command_results.append(result)
                
                # Parse test results
                if "pytest" in test_cmd:
                    counts = self._parse_pytest_output(result.stdout, result.stderr)
                    tests_passed = counts["passed"]
                    tests_failed = counts["failed"]
                    tests_skipped = counts["skipped"]
                elif result.status == ExecutionStatus.SUCCESS:
                    tests_passed = 1  # At least one test passed
        
        # Determine overall status
        if not command_results:
            overall_status = ExecutionStatus.SKIPPED
            summary = "No commands executed"
        elif any(r.status == ExecutionStatus.TIMEOUT for r in command_results):
            overall_status = ExecutionStatus.TIMEOUT
            summary = "One or more commands timed out"
        elif any(r.status == ExecutionStatus.ERROR for r in command_results):
            overall_status = ExecutionStatus.ERROR
            summary = "One or more commands had errors"
        elif all(r.status == ExecutionStatus.SUCCESS for r in command_results):
            overall_status = ExecutionStatus.SUCCESS
            summary = f"All {len(command_results)} commands succeeded"
        else:
            overall_status = ExecutionStatus.FAILED
            failed_count = sum(1 for r in command_results if r.status == ExecutionStatus.FAILED)
            summary = f"{failed_count} of {len(command_results)} commands failed"
        
        return DynamicEvalResult(
            status=overall_status,
            tests_found=tests_found,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_skipped=tests_skipped,
            commands=command_results,
            summary=summary,
        )
    
    def evaluate_in_sandbox(
        self,
        code: str,
        language: str = "python",
        test_code: Optional[str] = None,
        timeout: int = 30,
    ) -> DynamicEvalResult:
        """
        Evaluate code in a temporary sandbox.
        
        Creates a temp directory, writes code, and executes.
        
        Args:
            code: Code to evaluate
            language: Programming language
            test_code: Optional test code to run
            timeout: Timeout in seconds
            
        Returns:
            DynamicEvalResult
        """
        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix="code_eval_")
        
        try:
            if language == "python":
                # Write main code
                main_file = os.path.join(temp_dir, "main.py")
                with open(main_file, 'w') as f:
                    f.write(code)
                
                commands = []
                
                # Syntax check
                commands.append(f"python -m py_compile {main_file}")
                
                # Run if test code provided
                if test_code:
                    test_file = os.path.join(temp_dir, "test_main.py")
                    with open(test_file, 'w') as f:
                        f.write(test_code)
                    commands.append(f"python -m pytest {test_file} -v")
                
                return self.evaluate(temp_dir, commands=commands, run_tests=False, timeout=timeout)
            
            else:
                return DynamicEvalResult(
                    status=ExecutionStatus.SKIPPED,
                    tests_found=False,
                    tests_passed=0,
                    tests_failed=0,
                    tests_skipped=0,
                    summary=f"Sandbox evaluation not supported for {language}",
                )
        
        finally:
            # Cleanup
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
