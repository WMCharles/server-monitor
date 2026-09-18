from __future__ import annotations

import json
import logging
import shlex
import subprocess
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON transport with datetime preservation
# ---------------------------------------------------------------------------


def _default(obj: Any):
    if isinstance(obj, datetime):
        return {"__datetime__": obj.isoformat()}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def _object_hook(obj: dict):
    if len(obj) == 1 and "__datetime__" in obj:
        try:
            return datetime.fromisoformat(obj["__datetime__"])
        except (TypeError, ValueError):
            return obj["__datetime__"]
    return obj


def dumps(value: Any) -> str:
    return json.dumps(value, default=_default)


def loads(text: str) -> Any:
    return json.loads(text, object_hook=_object_hook)


class RemoteError(RuntimeError):
    """Raised when a remote agent cannot be reached or returns an error."""


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class LocalBackend:
    """Calls collector methods on a local ServerMonitor instance."""

    def __init__(self, monitor):
        self.monitor = monitor

    def call(self, method: str, *args):
        return getattr(self.monitor, method)(*args)


class SshBackend:
    """Runs the agent on a remote host over SSH and parses its JSON output."""

    def __init__(self, target: str, command: str, timeout: int = 60):
        self.target = target
        self.command = command
        self.timeout = timeout

    def call(self, method: str, *args):
        args_json = dumps(list(args))
        remote = (
            f"{self.command} call {shlex.quote(method)} {shlex.quote(args_json)}"
        )
        try:
            proc = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    self.target,
                    remote,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteError(
                f"{self.target}: timed out after {self.timeout}s"
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            message = detail[-1] if detail else f"exit {proc.returncode}"
            raise RemoteError(f"{self.target}: {message}")

        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not line:
            raise RemoteError(f"{self.target}: empty response")
        try:
            return loads(line)
        except json.JSONDecodeError as exc:
            raise RemoteError(f"{self.target}: invalid JSON response") from exc


class ServerProxy:
    """Forwards attribute calls to a backend, mirroring ServerMonitor's API."""

    def __init__(self, name: str, backend):
        self.name = name
        self._backend = backend

    def __getattr__(self, item: str):
        if item.startswith("_"):
            raise AttributeError(item)

        def _invoke(*args, **kwargs):
            return self._backend.call(item, *args)

        return _invoke


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------


class Fleet:
    """Collection of monitored servers addressable by name."""

    def __init__(self, settings, local_monitor):
        self.settings = settings
        self._members: dict[str, Any] = {}
        for name, target, command in settings.servers:
            if target in ("", "local"):
                self._members[name] = local_monitor
            else:
                self._members[name] = ServerProxy(
                    name, SshBackend(target, command)
                )

    def names(self) -> list[str]:
        return [name for name, _, _ in self.settings.servers]

    def first(self) -> str:
        names = self.names()
        if not names:
            raise RemoteError("No servers configured")
        return names[0]

    def get(self, name: str):
        if name not in self._members:
            raise RemoteError(
                f"Unknown server '{name}'. Known: {', '.join(self.names())}"
            )
        return self._members[name]

    def is_local(self, name: str) -> bool:
        for member_name, target, _ in self.settings.servers:
            if member_name == name:
                return target in ("", "local")
        return False

    def active(self, context):
        name = context.chat_data.get("server") or self.first()
        return self.get(name)
