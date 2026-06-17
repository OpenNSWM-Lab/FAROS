"""
ClaudeCodeAgent — Autonomous research agent powered by Claude Code CLI.

Streaming mode: launches `claude -p "..." --output-format stream-json --print`,
parses JSON events line-by-line, and yields structured events for SSE streaming.

Supports:
- Task configuration with system prompt templates
- Real-time event streaming (thinking, tool use, results)
- Session persistence for --resume continuation
- Budget and timeout controls
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess as _sp
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("CLAUDE_AGENT_MODEL", "claude-sonnet-4-6")
DEFAULT_MAX_BUDGET = float(os.getenv("CLAUDE_AGENT_MAX_BUDGET", "10.0"))
DEFAULT_TIMEOUT = int(os.getenv("CLAUDE_AGENT_TIMEOUT", "900"))
ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"

# Preset research system prompts
RESEARCH_TEMPLATES = {
    "run_experiment": (
        "CRITICAL: You are working INSIDE a single project directory. "
        "The ONLY files you should access are those within the current working directory. "
        "Do NOT try to read files outside this project — they are not relevant.\n\n"
        "You are running a scientific code experiment. "
        "Do NOT search the web. Do NOT ask questions. Just execute.\n\n"
        "WORKFLOW:\n"
        "1. List the project files to understand the structure (use ls or Glob)\n"
        "2. Read the main entry point and understand what it does\n"
        "3. Install any missing dependencies (use Bash: pip install -r requirements.txt)\n"
        "4. Run the experiment (use Bash: python main.py or equivalent)\n"
        "5. If errors occur, fix them by editing files (use Edit)\n"
        "6. If the code runs, collect results and generate plots\n"
        "7. Write an `EXPERIMENT_REPORT.md` in the current directory with your findings\n\n"
        "Remember: ONLY access files in this project. Stay focused."
    ),
    "fix_and_verify": (
        "CRITICAL: Work ONLY within the current project directory. "
        "Do NOT access files outside this directory.\n\n"
        "You are fixing bugs in this code project. Do NOT search the web.\n\n"
        "WORKFLOW:\n"
        "1. Run the main entry point and tests to identify failures (use Bash)\n"
        "2. For each failure, trace the root cause by reading files (use Read)\n"
        "3. Fix bugs by editing files directly (use Edit)\n"
        "4. Re-run to verify each fix (use Bash)\n"
        "5. Continue until all tests pass or the project runs successfully\n"
        "6. Write a `FIX_REPORT.md` listing every issue found and how it was fixed"
    ),
    "analyze_and_plot": (
        "CRITICAL: Work ONLY within the current project directory. "
        "Do NOT access files outside this directory.\n\n"
        "You are analyzing experimental data in this project. Do NOT search the web.\n\n"
        "WORKFLOW:\n"
        "1. Find and load the experimental data files\n"
        "2. Run analysis scripts to process the data\n"
        "3. Generate figures using matplotlib/seaborn with proper labels\n"
        "4. Save all figures to `outputs/figures/` as PNG files\n"
        "5. Write an `ANALYSIS_REPORT.md` with methodology, results, and figure descriptions"
    ),
    "custom": "",
}

SESSION_DIR_NAME = "claude_sessions"


@dataclass
class ClaudeStreamEvent:
    """A single event from Claude Code's stream-json output, ready for SSE."""

    event_type: str  # "thinking", "tool_use", "tool_result", "error", "done"
    content: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_output: str = ""
    step: str = ""  # "planning", "executing", "analyzing", "complete"
    timestamp: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "content": self.content[:2000] if len(self.content) > 2000 else self.content,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input[:500] if len(self.tool_input) > 500 else self.tool_input,
            "tool_output": self.tool_output[:1000] if len(self.tool_output) > 1000 else self.tool_output,
            "step": self.step,
            "timestamp": self.timestamp,
        }


class ClaudeCodeAgent:
    """Streaming agent around `claude` CLI.

    Usage:
        agent = ClaudeCodeAgent()
        async for event in agent.stream(workspace="/path", goal="Run experiment"):
            # event is ClaudeStreamEvent
            yield event.to_sse()
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_budget: float = DEFAULT_MAX_BUDGET,
        timeout: int = DEFAULT_TIMEOUT,
        allowed_tools: str = ALLOWED_TOOLS,
    ):
        self.model = model
        self.max_budget = max_budget
        self.timeout = timeout
        self.allowed_tools = allowed_tools

    async def stream(
        self,
        workspace: str,
        goal: str,
        system_prompt: str = "",
        session_id: Optional[str] = None,
    ) -> AsyncIterator[ClaudeStreamEvent]:
        """Stream Claude Code execution events in real-time.

        Args:
            workspace: Absolute path to project directory.
            goal: Research goal / task description.
            system_prompt: Optional system prompt override.
            session_id: Optional previous session ID to resume.

        Yields:
            ClaudeStreamEvent objects.
        """
        claude_bin = shutil.which("claude")
        if not claude_bin:
            yield ClaudeStreamEvent(
                event_type="error",
                content="Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code",
            )
            return

        workspace = os.path.abspath(workspace)
        if not os.path.isdir(workspace):
            yield ClaudeStreamEvent(
                event_type="error",
                content=f"Workspace not found: {workspace}",
            )
            return

        # Build prompt
        prompt = self._build_prompt(goal, workspace, system_prompt)
        # Use default system prompt if none provided
        sp = system_prompt or RESEARCH_TEMPLATES["run_experiment"]

        # Build command
        cmd = [
            claude_bin,
            "-p", prompt,
            "--print",
            "--output-format", "stream-json",
            "--model", self.model,
            "--allowedTools", self.allowed_tools,
            "--add-dir", workspace,
            "--max-budget-usd", str(self.max_budget),
            "--system-prompt", sp,
            "--bare",
            "--allow-dangerously-skip-permissions",  # No permission prompts in automated mode
        ]
        if session_id:
            cmd.extend(["--resume", session_id])

        logger.info("Claude stream start: workspace=%s model=%s", workspace, self.model)

        proc = None
        try:
            proc = _sp.Popen(
                cmd,
                stdout=_sp.PIPE,
                stderr=_sp.PIPE,
                cwd=workspace,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            deadline = time.monotonic() + self.timeout

            yield ClaudeStreamEvent(
                event_type="thinking",
                content="Claude Code agent starting...",
                step="planning",
            )

            loop = asyncio.get_event_loop()

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    proc.wait(timeout=5)
                    yield ClaudeStreamEvent(
                        event_type="error",
                        content=f"Execution timed out after {self.timeout}s",
                    )
                    return

                try:
                    line = await loop.run_in_executor(
                        None, lambda: proc.stdout.readline()
                    )
                except Exception:
                    break

                if not line:
                    if proc.poll() is not None:
                        break
                    await asyncio.sleep(0.05)
                    continue

                line = line.strip()
                if not line:
                    continue

                # Parse JSON event
                event = self._parse_stream_line(line)
                if event:
                    yield event

            # Process done
            proc.wait(timeout=10)
            exit_code = proc.returncode or -1

            if exit_code == 0:
                yield ClaudeStreamEvent(
                    event_type="done",
                    content="Task completed successfully.",
                    step="complete",
                )
            else:
                yield ClaudeStreamEvent(
                    event_type="error",
                    content=f"Claude exited with code {exit_code}. Check logs for details.",
                )

        except FileNotFoundError:
            yield ClaudeStreamEvent(
                event_type="error",
                content="Claude CLI not found in PATH",
            )
        except Exception as exc:
            logger.exception("Claude stream error: %s", exc)
            yield ClaudeStreamEvent(
                event_type="error",
                content=f"Execution error: {exc}",
            )

    # ---- internals ----

    @staticmethod
    def _build_prompt(goal: str, workspace: str, system_prompt: str = "") -> str:
        """Build the user prompt for Claude."""
        # Include project context
        context = ""
        try:
            root = Path(workspace)
            py_files = list(root.rglob("*.py"))
            files_list = []
            for f in sorted(py_files)[:30]:
                rel = str(f.relative_to(root))
                if not any(s in rel for s in (".venv", "venv", "__pycache__", ".git", ".sandbox", "node_modules")):
                    files_list.append(f"  - {rel}")
            if files_list:
                context = "\nProject files:\n" + "\n".join(files_list[:30])
        except Exception:
            pass

        return (
            f"## Task\n{goal}\n\n"
            f"{context}\n\n"
            "IMPORTANT: Work ONLY with files in the current directory. "
            "Do not access external paths or search the web. "
            "Follow the system prompt's workflow step by step. "
            "Report what you accomplished after completing the task."
        )

    @staticmethod
    def _parse_stream_line(line: str) -> Optional[ClaudeStreamEvent]:
        """Parse a single line of stream-json output into a ClaudeStreamEvent."""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return ClaudeStreamEvent(
                event_type="thinking",
                content=line[:500],
                step="executing",
            )

        if not isinstance(data, dict):
            return None

        msg_type = data.get("type", "")

        if msg_type == "assistant":
            content = ""
            message = data.get("message", {})
            if isinstance(message, dict):
                parts = []
                for block in message.get("content", []):
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            # Tool use inside assistant message
                            tool_name = block.get("name", "")
                            tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
                            parts.append(f"[Using tool: {tool_name}]")
                            # Actually, this is better handled as a separate event
                content = "\n".join(parts)
            return ClaudeStreamEvent(
                event_type="thinking",
                content=content,
                step="executing",
            )

        elif msg_type == "tool_use":
            name = data.get("name", "")
            inp = data.get("input", {})
            inp_str = json.dumps(inp, ensure_ascii=False) if isinstance(inp, dict) else str(inp)
            return ClaudeStreamEvent(
                event_type="tool_use",
                tool_name=name,
                tool_input=inp_str[:500],
                step="executing",
            )

        elif msg_type == "tool_result":
            output = data.get("output", data.get("content", ""))
            return ClaudeStreamEvent(
                event_type="tool_result",
                tool_output=str(output)[:1000],
                step="executing",
            )

        elif msg_type in ("error", "system"):
            return ClaudeStreamEvent(
                event_type="error" if msg_type == "error" else "thinking",
                content=str(data.get("message", data.get("error", "")))[:1000],
            )

        elif msg_type == "result":
            return ClaudeStreamEvent(
                event_type="done",
                content=str(data.get("result", data.get("text", "")))[:3000],
                step="complete",
            )

        # Generic: treat as thinking
        content = data.get("text", data.get("content", str(data)[:500]))
        if content and content != str(data):
            return ClaudeStreamEvent(
                event_type="thinking",
                content=str(content)[:1000],
            )

        return None


# ---- Utility ----

def get_session_dir() -> str:
    """Get the session storage directory."""
    from app.db.engine import _DATA_DIR
    d = os.path.join(_DATA_DIR, SESSION_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def save_session(session_id: str, data: dict) -> str:
    """Save a Claude session to disk."""
    path = os.path.join(get_session_dir(), f"{session_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def load_session(session_id: str) -> Optional[dict]:
    """Load a Claude session from disk."""
    path = os.path.join(get_session_dir(), f"{session_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
