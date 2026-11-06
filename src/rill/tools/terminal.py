import asyncio
import logging
import os
import signal
import shlex
from pathlib import Path
from typing import Any

from rill.config import Settings
from rill.tools.base import Tool


class TerminalTool(Tool):
    name = "run_terminal_command"
    description = "Execute a shell command in the configured workspace and return stdout, stderr, and exit code."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
            "workdir": {
                "type": "string",
                "description": "Optional working directory relative to the configured terminal workspace.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds.",
                "minimum": 1,
            },
            "confirmed": {
                "type": "boolean",
                "description": "Set to true after user approves the command execution.",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    read_only = False
    risk_level = "high"
    requires_approval = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._logger = logging.getLogger(__name__)
        self.requires_approval = settings.terminal.require_confirmation
        self.timeout_seconds = settings.terminal.timeout_seconds
        self.max_result_bytes = settings.agent.max_result_bytes

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = (arguments.get("command") or "").strip()
        if not command:
            return {"ok": False, "error": "Missing command."}
        if self._is_risky(command) and not self._settings.terminal.allow_risky_commands:
            self._logger.warning("Blocked risky terminal command")
            return {
                "ok": False,
                "error": "Command blocked by safety policy.",
                "requires_confirmation": True,
            }

        workdir = self._resolve_workdir(arguments.get("workdir"))
        timeout = min(int(arguments.get("timeout") or self._settings.terminal.timeout_seconds), self._settings.terminal.timeout_seconds)
        self._logger.info("Executing terminal command bytes=%s timeout=%ss", len(command.encode("utf-8")), timeout)
        process = None
        stdout_task = None
        stderr_task = None
        deadline = asyncio.get_running_loop().time() + timeout
        max_output = max(1024, self._settings.agent.max_result_bytes // 2)
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
            stdout_task = asyncio.create_task(self._read_capped(process.stdout, max_output))
            stderr_task = asyncio.create_task(self._read_capped(process.stderr, max_output))
            await asyncio.wait_for(process.wait(), timeout=max(0.01, deadline - asyncio.get_running_loop().time()))
            stdout_pair, stderr_pair = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
            )
            stdout, stdout_truncated = stdout_pair
            stderr, stderr_truncated = stderr_pair
        except (TimeoutError, asyncio.TimeoutError):
            self._logger.error("Terminal command timed out")
            await self._kill_process(process)
            for task in (locals().get("stdout_task"), locals().get("stderr_task")):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (locals().get("stdout_task"), locals().get("stderr_task")) if task is not None), return_exceptions=True)
            return {"ok": False, "error": f"Command timed out after {timeout} seconds."}
        except asyncio.CancelledError:
            await self._kill_process(process)
            for task in (locals().get("stdout_task"), locals().get("stderr_task")):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (locals().get("stdout_task"), locals().get("stderr_task")) if task is not None), return_exceptions=True)
            raise

        self._logger.info(f"命令完成: exit_code={process.returncode}, stdout_len={len(stdout)}")
        if process.returncode != 0:
            self._logger.warning("Terminal command failed exit_code=%s stderr_bytes=%s", process.returncode, len(stderr))
        return {
            "ok": process.returncode == 0,
            "command": command,
            "workdir": str(workdir),
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }

    async def _read_capped(self, stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        chunks: list[bytes] = []
        kept = 0
        truncated = False
        while chunk := await stream.read(64 * 1024):
            remaining = limit - kept
            if remaining > 0:
                chunks.append(chunk[:remaining])
                kept += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True
        return b"".join(chunks), truncated

    async def _kill_process(self, process: asyncio.subprocess.Process | None) -> None:
        if process is None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        if process.returncode is None:
            await process.wait()

    def _resolve_workdir(self, workdir: str | None) -> Path:
        base = self._settings.terminal.workdir.resolve()
        if not workdir:
            return base
        candidate = (base / workdir).resolve()
        if base == candidate or base in candidate.parents:
            return candidate
        return base

    def _is_risky(self, command: str) -> bool:
        lowered = command.lower()
        risky_terms = (
            "rm -rf /",
            "mkfs",
            "shutdown",
            "reboot",
            "halt",
            "poweroff",
            "dd if=",
            "chmod -r 777 /",
            ":(){:|:&};:",
        )
        if any(term in lowered for term in risky_terms):
            return True
        try:
            tokens = shlex.split(command)
        except ValueError:
            return True
        return any(token in {"sudo", "su"} for token in tokens)

