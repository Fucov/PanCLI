from __future__ import annotations

import asyncio
from pathlib import Path

from prompt_toolkit.document import Document

from pansh.main import AppState
from pansh.session import SessionController
from pansh.shell import LocalPathCompleter, PanShell
from pansh.theme import UIOptions


class DummyConsole:
    def print(self, *args, **kwargs) -> None:
        return None


def _state() -> AppState:
    return AppState(
        ui=UIOptions(),
        console=DummyConsole(),
        stderr_console=DummyConsole(),
        settings=None,
        session_controller=SessionController(),
    )


def test_shell_resolve_local_path_uses_shell_cwd() -> None:
    root = Path.cwd()
    nested = root / "src"

    shell = PanShell(_state())
    shell.local_cwd = str(root)

    assert shell._resolve_local_path("src") == nested.resolve()


def test_local_completer_suggests_local_dirs_from_first_token() -> None:
    shell = PanShell(_state())
    shell.local_cwd = str(Path.cwd())
    completer = LocalPathCompleter(shell)

    completions = list(completer.get_completions(Document("D"), None))
    displays = {completion.text for completion in completions}

    assert any(item in {"Doc/", "Doc\\"} for item in displays)


def test_shell_bang_cd_updates_local_cwd() -> None:
    root = Path.cwd()
    nested = root / "src"

    shell = PanShell(_state())
    shell.local_cwd = str(root)

    asyncio.run(shell.handle("!cd src"))

    assert shell.local_cwd == str(nested.resolve())
