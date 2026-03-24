"""全异步交互式 Shell — 使用 prompt-toolkit 的 prompt_async。

由 main.py 的 callback 在无子命令时自动挂载。
所有命令通过 await 调用 core.py 中的共享业务函数。
"""

from __future__ import annotations

import glob
import os
import shlex
import subprocess
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .api import AsyncApiManager
from .config import load_config, save_config
from .core import (
    abs_path,
    console,
    do_cat,
    do_cp,
    do_download,
    do_find,
    do_head,
    do_ls,
    do_mkdir,
    do_mv,
    do_rm,
    do_stat,
    do_tail,
    do_touch,
    do_tree,
    do_upload,
    login,
)
from .models import AppConfig


# ── 智能补全器 ──────────────────────────────────────────────────


class AnyShareCompleter(Completer):
    """上下文感知的智能补全器。

    - upload 第 1 个参数 → 本地文件补全
    - download 第 2 个参数 → 本地文件补全
    - cd/ls/cat/rm/stat/tree/head/tail/... → 远程路径补全
    """

    CMDS = [
        "ls", "cd", "pwd", "tree", "cat", "head", "tail", "touch",
        "stat", "mkdir", "rm", "mv", "cp", "upload", "download", "find",
        "whoami", "logout", "su", "clear", "exit", "quit", "help",
    ]

    # 第一个参数是本地路径的命令
    _LOCAL_ARG1 = {"upload"}
    # 第二个参数是本地路径的命令
    _LOCAL_ARG2 = {"download"}
    # 需要远程路径补全的命令
    _REMOTE_CMDS = {"ls", "cd", "cat", "head", "tail", "stat", "tree", "touch",
                    "mkdir", "rm", "mv", "cp", "upload", "download", "find"}

    def __init__(self, shell: PanShell) -> None:
        self.shell = shell
        self._cache: dict[str, list[str]] = {}

    def _local_completions(self, word: str):
        """本地文件系统补全。"""
        for match in glob.glob(word + "*"):
            display = os.path.basename(match)
            if os.path.isdir(match):
                match += "/"
                display += "/"
            yield Completion(match, start_position=-len(word), display=display)

    def _remote_completions(self, word: str):
        """远程 AnyShare 路径补全。"""
        if "/" in word:
            parent_str, prefix = word.rsplit("/", 1)
            parent_str = parent_str or "/"
        else:
            parent_str, prefix = ".", word

        try:
            import asyncio
            ap = abs_path(self.shell.cwd, parent_str)
            info = asyncio.get_event_loop().run_until_complete(
                self.shell.manager.get_resource_info_by_path(ap.strip("/"))
            )
            if info and info.size == -1:
                if info.docid not in self._cache:
                    dirs, files = asyncio.get_event_loop().run_until_complete(
                        self.shell.manager.list_dir(info.docid, by="name")
                    )
                    self._cache[info.docid] = (
                        [d["name"] + "/" for d in dirs] + [f["name"] for f in files]
                    )
                for name in self._cache[info.docid]:
                    if name.lower().startswith(prefix.lower()):
                        yield Completion(name, start_position=-len(prefix))
        except Exception:
            pass

    def _arg_position(self, text: str, args: list[str]) -> int:
        """计算当前正在编辑的参数位置（从 1 开始，0 = 命令名）。"""
        if text.endswith(" "):
            return len(args)  # 下一个参数
        return len(args) - 1  # 当前正在输入的参数

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        try:
            args = shlex.split(text)
        except ValueError:
            return

        # 补全命令名
        if not text or (len(args) == 1 and not text.endswith(" ")):
            word = args[0] if args else ""
            for cmd in self.CMDS:
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word))
            return

        word = "" if text.endswith(" ") else args[-1]
        cmd = args[0]
        pos = self._arg_position(text, args)

        # 判断是否需要本地补全
        need_local = (
            (cmd in self._LOCAL_ARG1 and pos == 1)
            or (cmd in self._LOCAL_ARG2 and pos == 2)
        )

        if need_local:
            yield from self._local_completions(word)
        elif cmd in self._REMOTE_CMDS:
            yield from self._remote_completions(word)


# ── PanShell ────────────────────────────────────────────────────


class PanShell:
    """全异步交互式 Shell。"""

    def __init__(self) -> None:
        self.cfg: AppConfig = load_config()
        self.manager: AsyncApiManager | None = None
        self.cwd: str = "/"
        self.home_name: str = ""

    async def run_async(self) -> None:
        """主入口：登录 → 交互循环。"""
        self.manager = await login(self.cfg)

        entrydoc = await self.manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            sys.exit(1)
        self.home_name = entrydoc[0]["name"]
        self.cwd = f"/{self.home_name}"

        session: PromptSession = PromptSession(
            history=InMemoryHistory(),
            completer=ThreadedCompleter(AnyShareCompleter(self)),
        )

        try:
            while True:
                try:
                    text = await session.prompt_async(f"PanCLI [{self.cwd}] $ ")
                    stripped = text.strip()
                    if not stripped:
                        continue

                    # ! 本地命令穿透
                    if stripped.startswith("!"):
                        local_cmd = stripped[1:].strip()
                        if not local_cmd:
                            continue
                        if local_cmd.startswith("cd "):
                            target_dir = local_cmd[3:].strip()
                            try:
                                os.chdir(os.path.expanduser(target_dir))
                                console.print(f"[dim]本地路径已切换至: {os.getcwd()}[/dim]")
                            except FileNotFoundError:
                                console.print(f"[red]本地目录不存在: {target_dir}[/red]")
                        else:
                            subprocess.run(local_cmd, shell=True)
                        continue

                    args = shlex.split(stripped)
                    await self.dispatch(args[0], args[1:])
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    break
                except Exception as e:
                    console.print(f"[red]Error:[/red] {e}")
        finally:
            if self.manager:
                await self.manager.close()

    # ── 命令分发 ────────────────────────────────────────────────

    async def dispatch(self, cmd: str, args: list[str]) -> None:
        """将用户输入的命令分发到对应的 core.py 异步函数。"""
        handler = getattr(self, f"cmd_{cmd}", None)
        if handler:
            await handler(args)
        else:
            console.print("[yellow]未知命令，输入 help 查看帮助[/yellow]")

    # ── 环境与基础命令 ──────────────────────────────────────────

    async def cmd_help(self, args: list[str]) -> None:
        console.print("\n[bold]PanCLI 命令参考手册[/bold]\n")
        sections = [
            ("环境与基础", "cyan", [
                ("whoami", "查账户"), ("su [user]", "切账号"),
                ("logout", "清凭证"), ("clear", "清屏"), ("exit/quit", "退出"),
            ]),
            ("导航与属性", "green", [
                ("ls [dir] [-h]", "列表"), ("cd <dir>", "切换目录"),
                ("pwd", "显示当前路径"), ("tree [dir]", "树状图"), ("stat <path>", "查元数据"),
            ]),
            ("文件管理", "yellow", [
                ("cat <file>", "打印全部内容"),
                ("head <file> [-n 行数]", "读头部行"), ("tail <file> [-n 行数]", "读尾部行"),
                ("touch <file>", "建空文件"), ("mkdir <dir>", "建目录"),
                ("rm <path> [-r]", "删除"), ("mv/cp <src> <dst>", "移动或复制"),
            ]),
            ("传输管理", "magenta", [
                ("upload <本地> [远程] [-r] [-j N]", "并发上传"),
                ("download <远程> [本地] [-r] [-j N]", "并发下载（支持断点续传）"),
            ]),
            ("搜索", "blue", [
                ("find <关键词> [-d 深度]", "递归搜索 (支持 * ? 通配符)"),
            ]),
            ("本地穿透", "red", [
                ("!<命令>", "执行本地系统命令 (如 !ls -al)"),
                ("!cd <目录>", "切换本地工作目录"),
            ]),
        ]
        for title, color, cmds in sections:
            t = Table("命令", "描述", box=None, show_header=False)
            for c, d in cmds:
                t.add_row(c, d)
            console.print(Panel(t, title=f"[{color}]{title}[/{color}]", border_style=color))

    async def cmd_exit(self, args: list[str]) -> None:
        raise EOFError

    async def cmd_quit(self, args: list[str]) -> None:
        raise EOFError

    async def cmd_clear(self, args: list[str]) -> None:
        console.clear()

    async def cmd_pwd(self, args: list[str]) -> None:
        console.print(self.cwd)

    async def cmd_whoami(self, args: list[str]) -> None:
        console.print(f"当前用户: [bold cyan]{self.cfg.username}[/bold cyan]")
        console.print(f"网盘 Host: [bold cyan]{self.cfg.host}[/bold cyan]")
        status = "[green]已在本地保存密码[/green]" if self.cfg.encrypted else "[yellow]未保存[/yellow]"
        console.print(f"凭据状态: {status}")

    async def cmd_logout(self, args: list[str]) -> None:
        self.cfg.username = None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print("[green]✓ 已清除本地凭据。退出当前 Shell...[/green]")
        raise EOFError

    async def cmd_su(self, args: list[str]) -> None:
        """切换账号。"""
        self.cfg.username = args[0] if args else None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print("[cyan]准备切换账号...[/cyan]")
        if self.manager:
            await self.manager.close()
        self.cfg = load_config()
        self.manager = await login(self.cfg)
        entrydoc = await self.manager.get_entrydoc()
        self.home_name = entrydoc[0]["name"]
        self.cwd = f"/{self.home_name}"

    # ── 导航命令 ────────────────────────────────────────────────

    async def cmd_cd(self, args: list[str]) -> None:
        target = abs_path(self.cwd, args[0]) if args else f"/{self.home_name}"
        if target == "/":
            self.cwd = "/"
            return
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]无此目录:[/red] {target}")
        elif info.size != -1:
            console.print(f"[red]非目录:[/red] {target}")
        else:
            self.cwd = target

    async def cmd_ls(self, args: list[str]) -> None:
        human = True
        path = "."
        # 简单解析 -h 和 path
        for a in args:
            if a in ("-h", "--human"):
                human = True
            elif not a.startswith("-"):
                path = a
        target = abs_path(self.cwd, path)
        await do_ls(self.manager, target, human=human)

    async def cmd_tree(self, args: list[str]) -> None:
        target = abs_path(self.cwd, args[0] if args else ".")
        await do_tree(self.manager, target)

    async def cmd_stat(self, args: list[str]) -> None:
        if not args:
            return
        await do_stat(self.manager, abs_path(self.cwd, args[0]))

    # ── 文件命令 ────────────────────────────────────────────────

    async def cmd_cat(self, args: list[str]) -> None:
        if not args:
            return
        await do_cat(self.manager, abs_path(self.cwd, args[0]))

    async def cmd_head(self, args: list[str]) -> None:
        path, n = None, 10
        i = 0
        while i < len(args):
            if args[i] in ("-n", "--lines") and i + 1 < len(args):
                n = int(args[i + 1])
                i += 2
            elif not args[i].startswith("-"):
                path = args[i]
                i += 1
            else:
                i += 1
        if not path:
            return
        await do_head(self.manager, abs_path(self.cwd, path), n=n)

    async def cmd_tail(self, args: list[str]) -> None:
        path, n = None, 10
        i = 0
        while i < len(args):
            if args[i] in ("-n", "--lines") and i + 1 < len(args):
                n = int(args[i + 1])
                i += 2
            elif not args[i].startswith("-"):
                path = args[i]
                i += 1
            else:
                i += 1
        if not path:
            return
        await do_tail(self.manager, abs_path(self.cwd, path), n=n)

    async def cmd_touch(self, args: list[str]) -> None:
        if not args:
            return
        await do_touch(self.manager, abs_path(self.cwd, args[0]))

    async def cmd_mkdir(self, args: list[str]) -> None:
        if not args:
            return
        await do_mkdir(self.manager, abs_path(self.cwd, args[0]))

    async def cmd_rm(self, args: list[str]) -> None:
        recurse = "-r" in args or "--recurse" in args
        paths = [a for a in args if not a.startswith("-")]
        if not paths:
            return
        await do_rm(self.manager, abs_path(self.cwd, paths[0]), recurse=recurse)

    async def cmd_mv(self, args: list[str]) -> None:
        force = "-f" in args or "--force" in args
        paths = [a for a in args if not a.startswith("-")]
        if len(paths) < 2:
            console.print("[yellow]用法: mv <src> <dst>[/yellow]")
            return
        await do_mv(self.manager, abs_path(self.cwd, paths[0]), abs_path(self.cwd, paths[1]), overwrite=force)

    async def cmd_cp(self, args: list[str]) -> None:
        force = "-f" in args or "--force" in args
        paths = [a for a in args if not a.startswith("-")]
        if len(paths) < 2:
            console.print("[yellow]用法: cp <src> <dst>[/yellow]")
            return
        await do_cp(self.manager, abs_path(self.cwd, paths[0]), abs_path(self.cwd, paths[1]), overwrite=force)

    # ── 传输命令 ────────────────────────────────────────────────

    async def cmd_upload(self, args: list[str]) -> None:
        recurse = "-r" in args or "--recurse" in args
        jobs = 4
        paths = []
        i = 0
        while i < len(args):
            if args[i] in ("-j", "--jobs") and i + 1 < len(args):
                jobs = int(args[i + 1])
                i += 2
            elif args[i] in ("-r", "--recurse"):
                i += 1
            else:
                paths.append(args[i])
                i += 1
        if not paths:
            console.print("[yellow]用法: upload <本地路径> [远程路径][/yellow]")
            return
        local = paths[0]
        remote = abs_path(self.cwd, paths[1] if len(paths) > 1 else ".")
        await do_upload(self.manager, local, remote, recurse=recurse, jobs=jobs)

    async def cmd_download(self, args: list[str]) -> None:
        recurse = "-r" in args or "--recurse" in args
        jobs = 4
        paths = []
        i = 0
        while i < len(args):
            if args[i] in ("-j", "--jobs") and i + 1 < len(args):
                jobs = int(args[i + 1])
                i += 2
            elif args[i] in ("-r", "--recurse"):
                i += 1
            else:
                paths.append(args[i])
                i += 1
        if not paths:
            console.print("[yellow]用法: download <远程路径> [本地路径][/yellow]")
            return
        remote = abs_path(self.cwd, paths[0])
        local = paths[1] if len(paths) > 1 else "."
        await do_download(self.manager, remote, local, recurse=recurse, jobs=jobs)

    # ── 搜索命令 ────────────────────────────────────────────────

    async def cmd_find(self, args: list[str]) -> None:
        keyword = None
        depth = 5
        i = 0
        while i < len(args):
            if args[i] in ("-d", "--depth") and i + 1 < len(args):
                depth = int(args[i + 1])
                i += 2
            elif not args[i].startswith("-"):
                keyword = args[i]
                i += 1
            else:
                i += 1
        if not keyword:
            console.print("[yellow]用法: find <关键词>[/yellow]")
            return
        await do_find(self.manager, keyword, self.cwd, max_depth=depth)
