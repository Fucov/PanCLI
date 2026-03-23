"""Stateful REPL Shell for AnyShare — PanCLI v3 全异步架构."""

from __future__ import annotations

import argparse
import collections
import getpass
import os
import shlex
import sys
import time
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .api import (
    AsyncApiManager,
    InvalidRootException,
    WrongPasswordException,
)
from .auth import rsa_encrypt
from .config import load_config, save_config
from .models import AppConfig, TransferTask

console = Console()

if TYPE_CHECKING:
    from .transfer import batch_download, batch_upload


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def _ts_fmt(us: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(us / 1_000_000))


class AnyShareCompleter(Completer):
    """智能路径补全器 — 同步版，复用缓存减少 API 调用。"""

    def __init__(self, shell: "PanShell"):
        self.shell = shell
        self.cmds = [
            "ls", "cd", "pwd", "tree", "cat", "head", "tail", "touch",
            "stat", "mkdir", "rm", "mv", "cp", "upload", "download",
            "find", "link", "whoami", "logout", "su", "clear", "exit",
            "quit", "help",
        ]
        self._cache: dict[str, list[str]] = {}
        self._path_cache: dict[str, str] = {}

    def _get_info(self, path: str):
        """Bridge: 同步调用 async API。"""
        import asyncio
        try:
            return asyncio.run(self.shell.manager.get_resource_info_by_path(path))
        except Exception:
            return None

    def _list_dir_sync(self, docid: str):
        """Bridge: 同步调用 async API。"""
        import asyncio
        try:
            return asyncio.run(self.shell.manager.list_dir(docid, by="name"))
        except Exception:
            return [], []

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        try:
            args = shlex.split(text)
        except ValueError:
            return

        if not text or (len(args) == 1 and not text.endswith(" ")):
            word = args[0] if args else ""
            for cmd in self.cmds:
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word))
            return

        word = "" if text.endswith(" ") else args[-1]
        cmd = args[0]

        if cmd == "upload" and (len(args) == 2 if not text.endswith(" ") else len(args) == 1):
            import glob
            for match in glob.glob(word + "*"):
                yield Completion(
                    match, start_position=-len(word), display=os.path.basename(match)
                )
            return

        if "/" in word:
            parts = word.rsplit("/", 1)
            parent, prefix = parts[0] or "/", parts[1]
        else:
            parent, prefix = ".", word

        if not self.shell.manager:
            return

        try:
            if parent == "/":
                entrydoc = self.shell._cached_entrydoc
                if entrydoc:
                    for root in entrydoc:
                        if root["name"].startswith(prefix):
                            yield Completion(
                                "/" + root["name"] + "/",
                                start_position=-len(word),
                            )
                return

            abs_parent = self.shell.abs_path(parent)
            docid = self._path_cache.get(abs_parent)
            if not docid:
                info = self._get_info(abs_parent.strip("/"))
                if info and info.size == -1:
                    docid = info.docid
                    self._path_cache[abs_parent] = docid

            if docid:
                if docid not in self._cache:
                    dirs, files = self._list_dir_sync(docid)
                    self._cache[docid] = (
                        [d.name + "/" for d in dirs] + [f.name for f in files]
                    )
                for name in self._cache[docid]:
                    if name.startswith(prefix):
                        yield Completion(name, start_position=-len(prefix))
        except Exception:
            pass


class PanShell:
    """交互式 Shell — 全异步架构。

    整个 Shell 运行在单一事件循环中，所有命令处理器直接 await。
    """

    def __init__(self) -> None:
        self.cfg: AppConfig = load_config()
        self.manager: AsyncApiManager | None = None
        self.cwd: str = "/"
        self.home_name: str = ""
        self._cached_entrydoc: list[dict] = []

    async def login(self) -> None:
        """异步交互式登录。"""
        if self.cfg.username:
            username = self.cfg.username
        else:
            username = console.input("[bold cyan]Username:[/bold cyan] ")
            self.cfg.username = username

        store_password = self.cfg.store_password
        password: str | None = None
        encrypted: str | None = None

        if store_password:
            encrypted = self.cfg.encrypted
            if encrypted is None:
                password = getpass.getpass()
                encrypted = rsa_encrypt(password, self.cfg.pubkey)
                self.cfg.encrypted = encrypted
        else:
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, self.cfg.pubkey)

        for retry in range(3):
            try:
                self.manager = AsyncApiManager(
                    self.cfg.host,
                    username,
                    password,
                    self.cfg.pubkey,
                    encrypted=encrypted,
                    cached_token=self.cfg.cached_token.token or None,
                    cached_expire=self.cfg.cached_token.expires or None,
                )
                await self.manager.initialize()
                break
            except WrongPasswordException:
                console.print(f"[yellow]密码错误重试 ({retry + 1}/3)[/yellow]")
                time.sleep(1)
                password = getpass.getpass()
                encrypted = rsa_encrypt(password, self.cfg.pubkey)
                self.cfg.encrypted = encrypted

        if self.manager is None:
            self.cfg.username = None
            self.cfg.encrypted = None
            save_config(self.cfg)
            console.print("[bold red]登录失败[/bold red]")
            sys.exit(1)

        if self.manager._expires > 0:
            self.cfg.cached_token.token = self.manager._tokenid
            self.cfg.cached_token.expires = self.manager._expires
        save_config(self.cfg)

        console.print(f"[green]✓[/green] 已连接: {self.cfg.host}")

        self._cached_entrydoc = await self.manager.get_entrydoc()
        if not self._cached_entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            sys.exit(1)
        self.home_name = self._cached_entrydoc[0]["name"]
        self.cwd = f"/{self.home_name}"

    def abs_path(self, path: str) -> str:
        """解析相对路径为绝对路径。"""
        if path.startswith("/"):
            p = path
        else:
            p = f"{self.cwd}/{path}" if self.cwd != "/" else f"/{path}"

        parts = []
        for part in p.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        return "/" + "/".join(parts) if parts else "/"

    async def run_async(self) -> None:
        """全异步主循环 — 只在外部 asyncio.run() 中调用一次。"""
        await self.login()
        session = PromptSession(
            history=InMemoryHistory(),
            completer=ThreadedCompleter(AnyShareCompleter(self)),
        )

        try:
            while True:
                try:
                    # 关键：使用 prompt_toolkit 的异步提示符
                    text = await session.prompt_async(f"PanCLI [{self.cwd}] $ ")
                    if not text.strip():
                        continue
                    await self.execute_command(text)
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    break
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
        finally:
            if self.manager:
                await self.manager.close()

    async def execute_command(self, text: str) -> None:
        """解析并执行命令。"""
        try:
            args = shlex.split(text)
        except ValueError:
            console.print("[yellow]命令解析错误[/yellow]")
            return

        if not args:
            return

        cmd = args[0]
        handler_name = f"cmd_{cmd}"
        handler = getattr(self, handler_name, None)

        if handler is None:
            self.cmd_unknown(args[1:])
            return

        try:
            await handler(args[1:])
        except TypeError as e:
            console.print(f"[red]命令执行错误:[/red] {e}")

    # ── 命令处理器 ────────────────────────────────────────────────

    async def cmd_unknown(self, args: list[str]) -> None:
        console.print("[yellow]Unknown command. Type 'help'.[/yellow]")

    async def cmd_help(self, args: list[str]) -> None:
        console.print("\n[bold]PanCLI 命令参考手册 v3[/bold]\n")

        t_base = Table("命令", "描述", box=None, show_header=False)
        for c, d in [
            ("whoami", "查账户"),
            ("su [user]", "切账号"),
            ("logout", "清凭证"),
            ("clear", "清屏"),
            ("exit/quit", "退出"),
        ]:
            t_base.add_row(c, d)
        console.print(
            Panel(t_base, title="[cyan]环境与基础[/cyan]", border_style="cyan")
        )

        t_nav = Table("命令", "描述", box=None, show_header=False)
        for c, d in [
            ("ls [dir] [-h]", "列表"),
            ("cd <dir>", "切换目录"),
            ("pwd", "显示当前路径"),
            ("tree [dir]", "树状图"),
            ("stat <path>", "查元数据"),
            ("find <keyword>", "搜索文件"),
        ]:
            t_nav.add_row(c, d)
        console.print(
            Panel(t_nav, title="[green]导航与属性[/green]", border_style="green")
        )

        t_fs = Table("命令", "描述", box=None, show_header=False)
        for c, d in [
            ("cat <file>", "打印全部内容"),
            ("head <file> [-n N]", "读头部行"),
            ("tail <file> [-n N]", "读尾部行"),
            ("touch <file>", "建空文件"),
            ("mkdir <dir>", "建目录"),
            ("rm <path> [-r]", "删除"),
            ("mv / cp", "移动或复制"),
        ]:
            t_fs.add_row(c, d)
        console.print(
            Panel(t_fs, title="[yellow]文件管理[/yellow]", border_style="yellow")
        )

        t_sync = Table("命令", "描述", box=None, show_header=False)
        for c, d in [
            ("upload <本地> [远程] [-r] [-j N]", "批量上传（支持并发）"),
            ("download <远程> [本地] [-r] [-j N]", "批量下载（支持并发）"),
        ]:
            t_sync.add_row(c, d)
        console.print(
            Panel(t_sync, title="[magenta]传输管理[/magenta]", border_style="magenta")
        )

        t_link = Table("命令", "描述", box=None, show_header=False)
        for c, d in [
            ("link <path> [-c/-d]", "查看/创建/删除外链"),
        ]:
            t_link.add_row(c, d)
        console.print(
            Panel(t_link, title="[blue]外链管理[/blue]", border_style="blue")
        )

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
        if self.cfg.encrypted:
            console.print("凭据状态: [green]已在本地保存密码[/green]")
        else:
            console.print("凭据状态: [yellow]未在本地保存密码[/yellow]")

    async def cmd_logout(self, args: list[str]) -> None:
        self.cfg.username = None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print(
            "[green]✓[/green] 已清除本地凭据。将在下次命令或重启时生效。退出当前 Shell..."
        )
        raise EOFError

    async def cmd_su(self, args: list[str]) -> None:
        """切换账号。"""
        self.cfg.username = args[0] if args else None
        self.cfg.encrypted = None
        self.cfg.cached_token.token = ""
        save_config(self.cfg)
        console.print(f"[cyan]准备切换账号，按要求重新登录...[/cyan]")
        if self.manager:
            await self.manager.close()
        self._cached_entrydoc = []
        await self.login()

    async def cmd_cd(self, args: list[str]) -> None:
        target = self.abs_path(args[0]) if args else f"/{self.home_name}"
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
        parser = argparse.ArgumentParser(prog="ls", add_help=False)
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("-h", "--human", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return

        target = self.abs_path(parsed.path)
        if target == "/":
            for root in self._cached_entrydoc:
                console.print(f"[blue]📁[/blue] {root['name']}")
            return

        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {target}")
            return
        if info.size == -1:
            dirs, files = await self.manager.list_dir(info.docid, by="name")
            table = Table(
                title=f"[bold]📂 {target}[/bold]",
                show_header=True,
                border_style="dim",
            )
            table.add_column("创建者", style="cyan")
            table.add_column("大小", justify="right", style="green")
            table.add_column("修改时间", style="yellow")
            table.add_column("名称", style="white bold")

            for d in dirs:
                table.add_row(
                    d.creator or "",
                    Text("📁", style="blue"),
                    _ts_fmt(d.modified),
                    d.name,
                )
            for f in files:
                size_str = _sizeof_fmt(f.size) if parsed.human else str(f.size)
                table.add_row(
                    f.creator or "",
                    size_str,
                    _ts_fmt(f.modified),
                    f.name,
                )
            console.print(table)
        else:
            await self.cmd_stat([target])

    async def cmd_stat(self, args: list[str]) -> None:
        if not args:
            return
        target = self.abs_path(args[0])
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {target}")
            return
        meta = await self.manager.get_file_meta(info.docid)
        panel_table = Table(
            title=f"📄 {target}",
            show_header=False,
            border_style="dim",
            box=None,
        )
        panel_table.add_column("Key", style="cyan bold")
        panel_table.add_column("Value")
        panel_table.add_row("DocID", meta.docid)
        panel_table.add_row("大小", _sizeof_fmt(meta.size))
        panel_table.add_row("类型", "目录" if info.size == -1 else "文件")
        panel_table.add_row("修改时间", _ts_fmt(meta.modified))
        panel_table.add_row("编辑者", meta.editor or "—")
        panel_table.add_row("标签", ", ".join(meta.tags) if meta.tags else "—")
        console.print(panel_table)

    async def cmd_tree(self, args: list[str]) -> None:
        target = self.abs_path(args[0] if args else ".")
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info or info.size != -1:
            console.print("[red]无效目录[/red]")
            return

        async def build_tree_async(docid: str, node: Tree, depth: int = 0) -> None:
            if depth > 10:
                return
            try:
                dirs, files = await self.manager.list_dir(docid, by="name")
                for d in dirs:
                    sub = node.add(f"[blue]📁 {d.name}[/blue]")
                    await build_tree_async(d.docid, sub, depth + 1)
                for f in files:
                    node.add(f"📄 {f.name} [dim]({_sizeof_fmt(f.size)})[/dim]")
            except Exception:
                pass

        tree = Tree(f"[bold blue]📂 {target}[/bold blue]")
        await build_tree_async(info.docid, tree)
        console.print(tree)

    async def cmd_find(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="find", add_help=False)
        parser.add_argument("keyword")
        parser.add_argument("-d", "--depth", type=int, default=3)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return

        console.print(
            f"[cyan]🔍 搜索关键词:[/cyan] [bold]{parsed.keyword}[/bold]"
        )
        console.print()

        results = await self.manager.search(self.cwd, parsed.keyword, max_depth=parsed.depth)

        if not results:
            console.print("[yellow]未找到匹配结果[/yellow]")
            return

        table = Table(
            title=f"[bold]搜索结果 ({len(results)} 项)[/bold]",
            show_header=True,
            border_style="dim",
        )
        table.add_column("类型", width=6, style="cyan")
        table.add_column("名称", style="white bold")
        table.add_column("路径", style="dim")
        table.add_column("大小", justify="right", style="green")
        table.add_column("修改时间", style="yellow")

        for r in results:
            icon = "[blue]📁[/blue]" if r.is_dir else "[white]📄[/white]"
            table.add_row(
                icon,
                r.name,
                r.path,
                _sizeof_fmt(r.size) if not r.is_dir else "—",
                _ts_fmt(r.modified),
            )

        console.print(table)

    async def cmd_mkdir(self, args: list[str]) -> None:
        if not args:
            return
        target = self.abs_path(args[0]).strip("/")
        try:
            await self.manager.create_dirs_by_path(target)
            console.print(f"[green]✓[/green] 创建成功")
        except InvalidRootException:
            console.print("[red]无效根目录[/red]")

    async def cmd_rm(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="rm", add_help=False)
        parser.add_argument("path")
        parser.add_argument("-r", "--recurse", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return

        target = self.abs_path(parsed.path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info:
            console.print("[red]不存在[/red]")
            return
        if info.size != -1:
            await self.manager.delete_file(info.docid)
        else:
            if not parsed.recurse:
                console.print("[yellow]是目录，请加 -r[/yellow]")
                return
            await self.manager.delete_dir(info.docid)
        console.print("[green]✓[/green] 删除成功")

    async def cmd_cat(self, args: list[str]) -> None:
        if not args:
            return
        await self._print_file(args[0])

    async def cmd_head(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="head", add_help=False)
        parser.add_argument("path", nargs="?")
        parser.add_argument("-n", "--lines", type=int, default=10)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        if not parsed.path:
            return

        target = self.abs_path(parsed.path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            return

        count = 0
        lines_buffer: list[bytes] = []
        try:
            async for chunk in self.manager.download_file_stream(info.docid):
                lines = chunk.split(b"\n")
                for i, line in enumerate(lines):
                    if i < len(lines) - 1:
                        lines_buffer.append(line)
                        count += 1
                        if count >= parsed.lines:
                            break
                    else:
                        lines_buffer.append(line)
                if count >= parsed.lines:
                    break

            for line in lines_buffer[: parsed.lines]:
                sys.stdout.buffer.write(line + b"\n")
        except BrokenPipeError:
            pass

    async def cmd_tail(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="tail", add_help=False)
        parser.add_argument("path", nargs="?")
        parser.add_argument("-n", "--lines", type=int, default=10)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return
        if not parsed.path:
            return

        target = self.abs_path(parsed.path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            return

        window = collections.deque(maxlen=parsed.lines)
        buffer = b""
        try:
            async for chunk in self.manager.download_file_stream(info.docid):
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    window.append(line)
            if buffer:
                window.append(buffer)
            for line in window:
                sys.stdout.buffer.write(
                    line + (b"" if line.endswith(b"\r") else b"\n")
                )
        except BrokenPipeError:
            pass

    async def _print_file(self, path: str, limit: int = -1) -> None:
        target = self.abs_path(path).strip("/")
        info = await self.manager.get_resource_info_by_path(target)
        if not info or info.size == -1:
            console.print("[red]文件无效[/red]")
            return
        read = 0
        try:
            async for chunk in self.manager.download_file_stream(info.docid):
                if limit > 0 and read + len(chunk) > limit:
                    sys.stdout.buffer.write(chunk[: limit - read])
                    break
                sys.stdout.buffer.write(chunk)
                read += len(chunk)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            pass
        print()

    async def cmd_touch(self, args: list[str]) -> None:
        if not args:
            return
        target = self.abs_path(args[0]).strip("/")
        parent = "/".join(target.split("/")[:-1])
        name = target.split("/")[-1]
        pinfo = await self.manager.get_resource_info_by_path(parent)
        if not pinfo:
            pdocid = await self.manager.create_dirs_by_path(parent)
        else:
            pdocid = pinfo.docid
        await self.manager.upload_file(pdocid, name, b"")
        console.print("[green]✓[/green] 文件建立")

    async def cmd_mv(self, args: list[str]) -> None:
        await self._do_mv_cp(args, copy=False)

    async def cmd_cp(self, args: list[str]) -> None:
        await self._do_mv_cp(args, copy=True)

    async def _do_mv_cp(self, args: list[str], copy: bool) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("src")
        parser.add_argument("dst")
        parser.add_argument("-f", "--force", action="store_true")
        try:
            p = parser.parse_args(args)
        except SystemExit:
            return

        src = self.abs_path(p.src)
        dst = self.abs_path(p.dst)
        action = "复制" if copy else "移动"

        src_parts = src.strip("/").split("/")
        dst_parts = dst.strip("/").split("/")

        if src_parts == dst_parts:
            console.print("[dim]无需操作[/dim]")
            return

        src_info = await self.manager.get_resource_info_by_path(src.strip("/"))
        if src_info is None:
            console.print(f"[red]源路径不存在:[/red] {src}")
            return

        dst_info = await self.manager.get_resource_info_by_path(dst.strip("/"))

        if dst_info and dst_info.size == -1:
            if src_parts[:-1] == dst_parts:
                console.print("[dim]无需操作[/dim]")
                return
            if src_parts == dst_parts[: len(src_parts)]:
                console.print("[red]不能移动到子目录[/red]")
                return
            if copy:
                await self.manager.copy_file(
                    src_info.docid, dst_info.docid, overwrite_on_dup=p.force
                )
            else:
                await self.manager.move_file(
                    src_info.docid, dst_info.docid, overwrite_on_dup=p.force
                )
            console.print(f"[green]✓[/green] {action}完成")
            return

        if dst_info is None:
            dst_parts_list = dst.strip("/").split("/")
            dst_name = dst_parts_list[-1]
            dst_parent = "/".join(dst_parts_list[:-1])
            dst_parent_info = await self.manager.get_resource_info_by_path(dst_parent)
            if dst_parent_info is None:
                console.print("[red]目标父目录不存在[/red]")
                return
            new_id, new_name = (
                await self.manager.copy_file(
                    src_info.docid, dst_parent_info.docid, rename_on_dup=True
                )
                if copy
                else await self.manager.move_file(
                    src_info.docid, dst_parent_info.docid, rename_on_dup=True
                )
            )
            if new_name != dst_name:
                await self.manager.rename_file(new_id, dst_name)
            console.print(f"[green]✓[/green] {action}完成: {src} → {dst}")
            return

        if src_info.size == -1:
            console.print("[red]不能将目录移动到文件位置[/red]")
            return

        if p.force:
            await self.manager.delete_file(dst_info.docid)
            dst_parts_list = dst.strip("/").split("/")
            dst_name = dst_parts_list[-1]
            dst_parent = "/".join(dst_parts_list[:-1])
            dst_parent_info = await self.manager.get_resource_info_by_path(dst_parent)
            if dst_parent_info:
                new_id, new_name = (
                    await self.manager.copy_file(
                        src_info.docid, dst_parent_info.docid, rename_on_dup=True
                    )
                    if copy
                    else await self.manager.move_file(
                        src_info.docid, dst_parent_info.docid, rename_on_dup=True
                    )
                )
                if new_name != dst_name:
                    await self.manager.rename_file(new_id, dst_name)
            console.print(f"[green]✓[/green] {action}并覆盖完成")
        else:
            console.print(f"[yellow]{dst} 已存在，使用 -f 覆盖[/yellow]")

    async def cmd_upload(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("local")
        parser.add_argument("remote", nargs="?", default=".")
        parser.add_argument("-r", "--recurse", action="store_true")
        parser.add_argument("-j", "--jobs", type=int, default=4)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return

        local = parsed.local
        remote = self.abs_path(parsed.remote)

        if not os.path.exists(local):
            console.print(f"[red]本地路径不存在:[/red] {local}")
            return

        local_path_obj = os.path.normpath(local)
        local_name = os.path.basename(os.path.abspath(local_path_obj))

        if os.path.isfile(local_path_obj):
            remote_dir = remote.strip("/")
            dir_id = await self.manager.create_dirs_by_path(remote_dir)
            file_size = os.path.getsize(local_path_obj)

            console.print(
                f"[dim]上传中...[/dim] {local_name} ({_sizeof_fmt(file_size)})"
            )
            start_time = time.time()

            class ProgressReader:
                def __init__(self, fp, name, total):
                    self._fp = fp
                    self._name = name
                    self._total = total
                    self._start = start_time
                    self._uploaded = 0

                def read(self, size: int = -1) -> bytes:
                    data = self._fp.read(size)
                    if data:
                        self._uploaded += len(data)
                        elapsed = time.time() - self._start
                        speed = self._uploaded / elapsed if elapsed > 0 else 0
                        progress = self._uploaded / self._total * 100
                        console.print(
                            f"\r[magenta]⬆ {self._name}[/magenta] [{progress:.1f}%] {_sizeof_fmt(speed)}/s",
                            end="",
                        )
                    return data

            with open(local_path_obj, "rb") as f:
                reader = ProgressReader(f, local_name, file_size)
                await self.manager.upload_file(
                    dir_id, local_name, reader, stream_len=file_size
                )

            console.print()
            console.print(f"[green]✓[/green] 上传完成: {remote}/{local_name}")
        else:
            if not parsed.recurse:
                console.print(f"[yellow]{local} 是目录，请使用 -r 递归上传[/yellow]")
                return

            tasks: list[TransferTask] = []

            def collect_tasks_sync(parent_local: str, parent_remote: str) -> None:
                for entry in os.listdir(parent_local):
                    full_local = os.path.join(parent_local, entry)
                    rel_remote = f"{parent_remote}/{entry}"
                    if os.path.isfile(full_local):
                        tasks.append(
                            TransferTask(
                                remote_path=rel_remote,
                                local_path=full_local,
                                size=os.path.getsize(full_local),
                            )
                        )
                    elif os.path.isdir(full_local):
                        self.manager.create_dirs_by_path(rel_remote.strip("/"))
                        collect_tasks_sync(full_local, rel_remote)

            base_remote = remote.strip("/") + "/" + local_name
            await self.manager.create_dirs_by_path(base_remote)
            collect_tasks_sync(local_path_obj, base_remote)

            if tasks:
                console.print(
                    f"[cyan]准备上传 {len(tasks)} 个文件，并发数 {parsed.jobs}[/cyan]"
                )
                from .transfer import batch_upload

                dir_id = await self.manager.get_resource_id(base_remote)
                if dir_id:
                    await batch_upload(
                        self.manager, tasks, dir_id, jobs=parsed.jobs
                    )
            console.print(f"[green]✓[/green] 目录上传完成")

    async def cmd_download(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("remote")
        parser.add_argument("local", nargs="?", default=".")
        parser.add_argument("-r", "--recurse", action="store_true")
        parser.add_argument("-j", "--jobs", type=int, default=4)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return

        remote = self.abs_path(parsed.remote)
        local = os.path.normpath(parsed.local)
        os.makedirs(local, exist_ok=True)

        info = await self.manager.get_resource_info_by_path(remote.strip("/"))
        if not info:
            console.print(f"[red]远程路径不存在:[/red] {remote}")
            return

        if info.size != -1:
            local_name = os.path.basename(remote)
            dest = os.path.join(local, local_name)
            local_size = os.path.getsize(dest) if os.path.exists(dest) else 0
            headers = {}
            mode = "ab" if local_size > 0 else "wb"

            if local_size < info.size:
                headers["Range"] = f"bytes={local_size}-"
                console.print(
                    f"[yellow]检测到断点续传，已下载 {local_size} bytes[/yellow]"
                )

            url, _ = await self.manager.get_download_url(info.docid)
            downloaded = local_size
            start_time = time.time()

            from . import network

            with open(dest, mode) as f:
                async for chunk in network.async_stream_download(
                    url, headers=headers, client=self.manager._client
                ):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    progress = downloaded / info.size * 100
                    console.print(
                        f"\r[cyan]⬇ {local_name}[/cyan] [{progress:.1f}%] {_sizeof_fmt(speed)}/s",
                        end="",
                    )

            console.print()
            console.print(f"[green]✓[/green] 下载完成: {dest}")
        else:
            if not parsed.recurse:
                console.print(f"[yellow]{remote} 是目录，请使用 -r 递归下载[/yellow]")
                return

            tasks: list[TransferTask] = []
            base_local = os.path.join(local, os.path.basename(remote.rstrip("/")))
            os.makedirs(base_local, exist_ok=True)

            async def collect_tasks(
                parent_id: str, parent_remote: str, parent_local: str
            ) -> None:
                dirs, files = await self.manager.list_dir(parent_id, by="name")
                for d in dirs:
                    sub_dir = os.path.join(parent_local, d.name)
                    os.makedirs(sub_dir, exist_ok=True)
                    await collect_tasks(d.docid, f"{parent_remote}/{d.name}", sub_dir)
                for f in files:
                    file_info = await self.manager.get_resource_info_by_path(
                        f"{parent_remote}/{f.name}".strip("/")
                    )
                    if file_info:
                        tasks.append(
                            TransferTask(
                                remote_path=f"{parent_remote}/{f.name}",
                                local_path=os.path.join(parent_local, f.name),
                                size=file_info.size,
                                docid=file_info.docid,
                            )
                        )

            await collect_tasks(info.docid, remote, base_local)

            if tasks:
                console.print(
                    f"[cyan]准备下载 {len(tasks)} 个文件，并发数 {parsed.jobs}[/cyan]"
                )
                from .transfer import batch_download

                await batch_download(self.manager, tasks, jobs=parsed.jobs)
            console.print(f"[green]✓[/green] 目录下载完成: {base_local}")

    async def cmd_link(self, args: list[str]) -> None:
        parser = argparse.ArgumentParser(prog="link", add_help=False)
        parser.add_argument("path")
        parser.add_argument("-c", "--create", action="store_true")
        parser.add_argument("-d", "--delete", action="store_true")
        parser.add_argument("-e", "--expire", type=int, default=0)
        parser.add_argument("-p", "--password", action="store_true")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return

        target = self.abs_path(parsed.path)
        info = await self.manager.get_resource_info_by_path(target.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {target}")
            return

        if parsed.create:
            link_info = await self.manager.create_link(
                info.docid,
                end_time=parsed.expire if parsed.expire > 0 else None,
                enable_pass=parsed.password,
            )
            console.print(f"[green]✓[/green] 外链创建成功:")
            console.print(f"[cyan]链接:[/cyan] {link_info.link}")
            if link_info.password:
                console.print(f"[cyan]密码:[/cyan] {link_info.password}")
        elif parsed.delete:
            await self.manager.delete_link(info.docid)
            console.print(f"[green]✓[/green] 外链已删除")
        else:
            link_info = await self.manager.get_link(info.docid)
            if link_info:
                console.print(f"[cyan]链接:[/cyan] {link_info.link}")
                if link_info.password:
                    console.print(f"[cyan]密码:[/cyan] {link_info.password}")
                console.print(f"[cyan]权限:[/cyan] {link_info.perm}")
            else:
                console.print("[yellow]该文件没有外链[/yellow]")


def run_interactive_shell() -> None:
    """对外暴露的唯一同步入口 — 只调用一次 asyncio.run()。"""
    try:
        import asyncio

        asyncio.run(PanShell().run_async())
    except KeyboardInterrupt:
        pass
