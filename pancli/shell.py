"""Interactive shell that reuses the main Typer application."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from typer.main import get_command

from .api import AsyncApiManager
from .main import _login, _normalize_remote_path, app
from .theme import UIOptions, create_console


class PanShell:
    def __init__(self, ui: UIOptions | None = None) -> None:
        self.ui = ui or UIOptions()
        self.console = create_console(self.ui, force_terminal=True)
        self.remote_cwd = "/"
        self.local_cwd = str(Path.cwd())
        self.home_root = "/"
        self.manager: AsyncApiManager | None = None

    async def login(self) -> None:
        self.manager, self.home_root = await _login(self.console)
        self.remote_cwd = self.home_root

    async def close(self) -> None:
        if self.manager is not None:
            await self.manager.close()

    async def run(self) -> None:
        await self.login()
        session = PromptSession(history=InMemoryHistory())
        try:
            while True:
                try:
                    text = await session.prompt_async(f"PanCLI [{self.remote_cwd}] $ ")
                except EOFError:
                    break
                except KeyboardInterrupt:
                    self.console.print()
                    continue
                if not text.strip():
                    continue
                if await self.handle(text):
                    break
        finally:
            await self.close()

    async def handle(self, text: str) -> bool:
        if text in {"exit", "quit"}:
            return True
        if text.startswith("!"):
            completed = subprocess.run(text[1:], cwd=self.local_cwd, shell=True)
            if completed.returncode != 0:
                self.console.print(f"local command failed: {completed.returncode}")
            return False
        argv = shlex.split(text)
        cmd = argv[0]
        if cmd == "pwd":
            self.console.print(self.remote_cwd)
            return False
        if cmd == "cd":
            target = argv[1] if len(argv) > 1 else "."
            candidate = _normalize_remote_path(target, self.home_root)
            info = await self.manager.get_resource_info_by_path(candidate.strip("/")) if self.manager else None
            if info is None or not info.is_dir:
                self.console.print(f"not a directory: {candidate}")
                return False
            self.remote_cwd = candidate
            return False
        if cmd == "lpwd":
            self.console.print(self.local_cwd)
            return False
        if cmd == "lcd":
            target = Path(argv[1] if len(argv) > 1 else self.local_cwd).expanduser().resolve()
            if not target.exists() or not target.is_dir():
                self.console.print(f"not a directory: {target}")
                return False
            self.local_cwd = str(target)
            return False
        if cmd == "lls":
            target = Path(argv[1] if len(argv) > 1 else self.local_cwd).expanduser().resolve()
            if not target.exists():
                self.console.print(f"missing: {target}")
                return False
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                suffix = "/" if item.is_dir() else ""
                self.console.print(item.name + suffix)
            return False
        previous = os.environ.get("PANCLI_REMOTE_CWD")
        os.environ["PANCLI_REMOTE_CWD"] = self.remote_cwd
        try:
            get_command(app).main(args=argv, prog_name="pancli", standalone_mode=False)
        except SystemExit:
            pass
        finally:
            if previous is None:
                os.environ.pop("PANCLI_REMOTE_CWD", None)
            else:
                os.environ["PANCLI_REMOTE_CWD"] = previous
        return False


def run_interactive_shell(ui: UIOptions | None = None) -> None:
    try:
        asyncio.run(PanShell(ui).run())
    except KeyboardInterrupt:
        pass
