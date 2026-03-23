"""BHPAN CLI v3 Entry Point — Typer hybrid router with Rich UI."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from . import __version__
from .api import (
    AsyncApiManager,
    InvalidRootException,
    MoveToChildDirectoryException,
    NeedReviewException,
    WrongPasswordException,
)
from .auth import rsa_encrypt
from .config import load_config, save_config
from .models import AppConfig, SearchResult, TransferTask
from .transfer import batch_download, batch_upload, build_download_tasks, build_upload_tasks

__version__ = "3.0.0"

app = typer.Typer(
    name="pancli",
    help="AnyShare (BHPAN) 现代化命令行工具",
    invoke_without_command=True,
    add_completion=False,
)

console = Console()

# ═══════════════════════════════════════════════════════════════════════════════
# Rich Theme 主题系统
# ═══════════════════════════════════════════════════════════════════════════════


DARK_THEME = {
    "repr.number": "cyan",
    "status.spinning": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red bold",
    "info": "blue",
}

LIGHT_THEME = {
    "repr.number": "blue",
    "status.spinning": "blue",
    "success": "green bold",
    "warning": "yellow bold",
    "error": "red bold",
    "info": "blue bold",
}


def _detect_dark_mode() -> bool:
    """简单检测终端是否深色模式（检查环境变量）。"""
    if os.name == "nt":
        try:
            import ctypes
            import struct

            try:
                ctypes.windll.shcore.GetSystemThemeBrush.restype = ctypes.c_void_p
                ref = ctypes.c_int()
                ctypes.windll.shcore.GetSystemThemeBrush(ctypes.byref(ref), 0, 0)
                if ref.value:
                    return False
            except Exception:
                pass
        except Exception:
            pass
    return True


def get_theme(is_dark: bool | None = None) -> dict:
    """获取 Rich 主题配置。"""
    if is_dark is None:
        is_dark = _detect_dark_mode()
    return DARK_THEME if is_dark else LIGHT_THEME


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def _ts_fmt(us: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(us / 1_000_000))


# ═══════════════════════════════════════════════════════════════════════════════
# 登录逻辑
# ═══════════════════════════════════════════════════════════════════════════════


async def _login(cfg: AppConfig) -> AsyncApiManager:
    """异步登录，返回 AsyncApiManager 实例。"""
    username = cfg.username or console.input("[bold cyan]Username:[/bold cyan] ")
    store_password = cfg.store_password
    password: str | None = None
    encrypted: str | None = None

    if store_password:
        encrypted = cfg.encrypted
        if encrypted is None:
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, cfg.pubkey)
            cfg.encrypted = encrypted
    else:
        password = getpass.getpass()
        encrypted = rsa_encrypt(password, cfg.pubkey)

    for retry in range(3):
        try:
            manager = AsyncApiManager(
                cfg.host,
                username,
                password,
                cfg.pubkey,
                encrypted=encrypted,
                cached_token=cfg.cached_token.token or None,
                cached_expire=cfg.cached_token.expires or None,
            )
            await manager.initialize()
            cfg.username = username
            if manager._expires > 0:
                cfg.cached_token.token = manager._tokenid
                cfg.cached_token.expires = manager._expires
            save_config(cfg)
            console.print(f"[green]✓[/green] 已连接: {cfg.host}")
            return manager
        except WrongPasswordException:
            console.print(f"[yellow]密码错误重试 ({retry + 1}/3)[/yellow]")
            time.sleep(1)
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, cfg.pubkey)
            cfg.encrypted = encrypted

    console.print("[bold red]登录失败[/bold red]")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# 路径解析
# ═══════════════════════════════════════════════════════════════════════════════


async def resolve_path(
    manager: AsyncApiManager, path: str, home_name: str
) -> tuple[str, str]:
    """解析路径，返回 (绝对路径, docid)。"""
    if path.startswith("/"):
        p = path
    elif path == ".":
        p = f"/{home_name}"
    else:
        p = path

    parts = []
    for part in p.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)

    abs_path = "/" + "/".join(parts) if parts else "/"
    abs_path_stripped = abs_path.strip("/")

    if abs_path_stripped:
        docid = await manager.get_resource_id(abs_path_stripped)
    else:
        docid = None

    return abs_path, docid


# ═══════════════════════════════════════════════════════════════════════════════
# Typer 命令路由
# ═══════════════════════════════════════════════════════════════════════════════


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    whoami: bool = False,
    logout: bool = False,
    version: bool = False,
) -> None:
    """全局回调：处理 --whoami, --logout, --version 和无子命令默认进入 Shell。"""
    if version:
        console.print(f"[bold cyan]pancli[/bold cyan] v{__version__}")
        raise typer.Exit()

    if whoami:
        cfg = load_config()
        console.print(f"[bold]Host:[/bold] {cfg.host}")
        console.print(f"[bold]Username:[/bold] {cfg.username or '无'}")
        if cfg.encrypted:
            console.print("[bold green]密码:[/bold green] 已加密保存在本地")
        else:
            console.print("[bold yellow]密码:[/bold yellow] 未保存")
        raise typer.Exit()

    if logout:
        cfg = load_config()
        cfg.username = None
        cfg.encrypted = None
        cfg.cached_token.token = ""
        save_config(cfg)
        console.print("[green]✓[/green] 已清除本地登录凭据。")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        from .shell import run_interactive_shell

        run_interactive_shell()


@app.command()
def ls(
    path: str = ".",
    human: bool = False,
) -> None:
    """列出目录内容。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            return
        home_name = entrydoc[0]["name"]

        if path == ".":
            path = f"/{home_name}"

        abs_path, docid = await resolve_path(manager, path, home_name)

        if abs_path == "/":
            for root in entrydoc:
                console.print(f"[blue]📁[/blue] {root['name']}")
            return

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            return

        info = await manager.get_resource_info_by_path(abs_path.strip("/"))
        if info and info.size == -1:
            dirs, files = await manager.list_dir(docid, by="name")
            table = Table(
                title=f"[bold]📂 {abs_path}[/bold]",
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
                size_str = _sizeof_fmt(f.size) if human else str(f.size)
                table.add_row(
                    f.creator or "",
                    size_str,
                    _ts_fmt(f.modified),
                    f.name,
                )
            console.print(table)
        else:
            await manager.close()
            raise typer.Exit(code=1)

        await manager.close()

    asyncio.run(_impl())


@app.command()
def tree(
    path: str = ".",
    depth: int = typer.Option(3, "--depth", "-d", help="递归深度"),
) -> None:
    """显示目录树结构。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            return
        home_name = entrydoc[0]["name"]

        if path == ".":
            path = f"/{home_name}"

        abs_path, docid = await resolve_path(manager, path, home_name)

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            return

        info = await manager.get_resource_info_by_path(abs_path.strip("/"))
        if info and info.size != -1:
            console.print("[red]不是目录[/red]")
            await manager.close()
            return

        async def build_tree(docid: str, node: Tree, current_depth: int) -> None:
            if current_depth >= depth:
                return
            try:
                dirs, files = await manager.list_dir(docid, by="name")
                for d in dirs:
                    sub = node.add(f"[blue]📁 {d.name}[/blue]")
                    await build_tree(d.docid, sub, current_depth + 1)
                for f in files:
                    node.add(f"📄 {f.name} [dim]({_sizeof_fmt(f.size)})[/dim]")
            except Exception:
                pass

        root = Tree(f"[bold blue]📂 {abs_path}[/bold blue]")
        await build_tree(docid, root, 0)
        console.print(root)
        await manager.close()

    asyncio.run(_impl())


@app.command()
def find(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    path: str = typer.Option(".", "--path", "-p", help="搜索根路径"),
    depth: int = typer.Option(3, "--depth", "-d", help="递归深度"),
) -> None:
    """全局搜索文件。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            return
        home_name = entrydoc[0]["name"]

        if path == ".":
            path = f"/{home_name}"

        console.print(f"[cyan]🔍 搜索关键词:[/cyan] [bold]{keyword}[/bold]")
        console.print(f"[cyan]📂 搜索路径:[/cyan] [bold]{path}[/bold]")
        console.print()

        results = await manager.search(path, keyword, max_depth=depth)

        if not results:
            console.print("[yellow]未找到匹配结果[/yellow]")
            await manager.close()
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
        await manager.close()

    asyncio.run(_impl())


@app.command()
def stat(path: str = typer.Argument(..., help="文件或目录路径")) -> None:
    """查看文件/目录元信息。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            return
        home_name = entrydoc[0]["name"]

        abs_path, docid = await resolve_path(manager, path, home_name)

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        info = await manager.get_resource_info_by_path(abs_path.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        meta = await manager.get_file_meta(docid)

        panel_table = Table(
            title=f"📄 {abs_path}",
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
        panel_table.add_row("客户端修改时间", _ts_fmt(meta.client_mtime))
        panel_table.add_row("编辑者", meta.editor or "—")
        panel_table.add_row("标签", ", ".join(meta.tags) if meta.tags else "—")
        panel_table.add_row("版本", meta.rev or "—")

        console.print(panel_table)
        await manager.close()

    asyncio.run(_impl())


@app.command()
def mkdir(
    path: str = typer.Argument(..., help="要创建的目录路径"),
) -> None:
    """创建目录。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        try:
            docid = await manager.create_dirs_by_path(path.strip("/"))
            console.print(f"[green]✓[/green] 创建成功: {path}")
        except InvalidRootException:
            console.print("[red]无效根目录[/red]")
        finally:
            await manager.close()

    asyncio.run(_impl())


@app.command()
def rm(
    path: str = typer.Argument(..., help="要删除的路径"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归删除目录"),
) -> None:
    """删除文件或目录。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        abs_path, docid = await resolve_path(manager, path, home_name)

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        info = await manager.get_resource_info_by_path(abs_path.strip("/"))
        if not info:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        if info.size == -1:
            if not recursive:
                console.print("[yellow]是目录，请使用 -r 递归删除[/yellow]")
                await manager.close()
                return
            await manager.delete_dir(info.docid)
        else:
            await manager.delete_file(info.docid)

        console.print(f"[green]✓[/green] 删除成功: {abs_path}")
        await manager.close()

    asyncio.run(_impl())


@app.command()
def mv(
    src: str = typer.Argument(..., help="源路径"),
    dst: str = typer.Argument(..., help="目标路径"),
    force: bool = typer.Option(False, "--force", "-f", help="覆盖已存在的目标"),
) -> None:
    """移动或重命名文件/目录。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        src_abs, src_docid = await resolve_path(manager, src, home_name)
        dst_abs, dst_docid = await resolve_path(manager, dst, home_name)

        if src_docid is None:
            console.print(f"[red]源路径不存在:[/red] {src_abs}")
            await manager.close()
            return

        src_info = await manager.get_resource_info_by_path(src_abs.strip("/"))
        dst_info = await manager.get_resource_info_by_path(dst_abs.strip("/"))

        if dst_info and dst_info.size == -1:
            if force:
                await manager.move_file(src_info.docid, dst_info.docid, overwrite_on_dup=True)
            else:
                await manager.move_file(src_info.docid, dst_info.docid)
            console.print(f"[green]✓[/green] 移动完成: {src_abs} → {dst_abs}")
        elif dst_info is None:
            dst_parts = dst_abs.strip("/").split("/")
            dst_name = dst_parts[-1]
            dst_parent = "/".join(dst_parts[:-1])
            dst_parent_info = await manager.get_resource_info_by_path(dst_parent)
            if dst_parent_info:
                new_id, new_name = await manager.move_file(
                    src_info.docid, dst_parent_info.docid, rename_on_dup=True
                )
                if new_name != dst_name:
                    await manager.rename_file(new_id, dst_name)
                console.print(f"[green]✓[/green] 重命名完成: {src_abs} → {dst_abs}")
        else:
            if not force:
                console.print(f"[yellow]{dst_abs} 已存在，使用 -f 覆盖[/yellow]")
                await manager.close()
                return
            await manager.delete_file(dst_info.docid)
            dst_parts = dst_abs.strip("/").split("/")
            dst_name = dst_parts[-1]
            dst_parent = "/".join(dst_parts[:-1])
            dst_parent_info = await manager.get_resource_info_by_path(dst_parent)
            if dst_parent_info:
                new_id, new_name = await manager.move_file(
                    src_info.docid, dst_parent_info.docid, rename_on_dup=True
                )
                if new_name != dst_name:
                    await manager.rename_file(new_id, dst_name)
                console.print(f"[green]✓[/green] 移动并覆盖完成: {src_abs} → {dst_abs}")

        await manager.close()

    asyncio.run(_impl())


@app.command()
def cp(
    src: str = typer.Argument(..., help="源路径"),
    dst: str = typer.Argument(..., help="目标路径"),
    force: bool = typer.Option(False, "--force", "-f", help="覆盖已存在的目标"),
) -> None:
    """复制文件/目录。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        src_abs, src_docid = await resolve_path(manager, src, home_name)
        dst_abs, dst_docid = await resolve_path(manager, dst, home_name)

        if src_docid is None:
            console.print(f"[red]源路径不存在:[/red] {src_abs}")
            await manager.close()
            return

        src_info = await manager.get_resource_info_by_path(src_abs.strip("/"))
        dst_info = await manager.get_resource_info_by_path(dst_abs.strip("/"))

        if dst_info and dst_info.size == -1:
            await manager.copy_file(src_info.docid, dst_info.docid, overwrite_on_dup=force)
            console.print(f"[green]✓[/green] 复制完成: {src_abs} → {dst_abs}")
        elif dst_info is None:
            dst_parts = dst_abs.strip("/").split("/")
            dst_name = dst_parts[-1]
            dst_parent = "/".join(dst_parts[:-1])
            dst_parent_info = await manager.get_resource_info_by_path(dst_parent)
            if dst_parent_info:
                new_id, new_name = await manager.copy_file(
                    src_info.docid, dst_parent_info.docid, rename_on_dup=True
                )
                if new_name != dst_name:
                    await manager.rename_file(new_id, dst_name)
                console.print(f"[green]✓[/green] 复制完成: {src_abs} → {dst_abs}")
        else:
            if not force:
                console.print(f"[yellow]{dst_abs} 已存在，使用 -f 覆盖[/yellow]")
                await manager.close()
                return
            await manager.delete_file(dst_info.docid)
            dst_parts = dst_abs.strip("/").split("/")
            dst_name = dst_parts[-1]
            dst_parent = "/".join(dst_parts[:-1])
            dst_parent_info = await manager.get_resource_info_by_path(dst_parent)
            if dst_parent_info:
                new_id, new_name = await manager.copy_file(
                    src_info.docid, dst_parent_info.docid, rename_on_dup=True
                )
                if new_name != dst_name:
                    await manager.rename_file(new_id, dst_name)
                console.print(f"[green]✓[/green] 复制并覆盖完成: {src_abs} → {dst_abs}")

        await manager.close()

    asyncio.run(_impl())


@app.command()
def download(
    remote: str = typer.Argument(..., help="远程路径"),
    local: str = typer.Argument(".", help="本地保存目录"),
    jobs: int = typer.Option(4, "--jobs", "-j", help="并发数"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归下载目录"),
) -> None:
    """下载文件或目录。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        abs_path, docid = await resolve_path(manager, remote, home_name)

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        info = await manager.get_resource_info_by_path(abs_path.strip("/"))

        if info and info.size != -1:
            local_path = Path(local)
            if local_path.is_dir():
                dest = local_path / info.name
            else:
                dest = local_path
            dest.parent.mkdir(parents=True, exist_ok=True)

            local_size = dest.stat().st_size if dest.exists() else 0
            headers = {}
            mode = "ab" if local_size > 0 else "wb"

            if local_size < info.size:
                headers["Range"] = f"bytes={local_size}-"
                console.print(f"[yellow]检测到断点续传，已下载 {local_size} bytes[/yellow]")

            url, _ = await manager.get_download_url(info.docid)
            downloaded = local_size
            start_time = time.time()

            with open(dest, mode) as f:
                from . import network

                async for chunk in network.async_stream_download(
                    url, headers=headers, client=manager._client
                ):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    progress = downloaded / info.size * 100
                    console.print(
                        f"\r[cyan]⬇ {info.name}[/cyan] [{progress:.1f}%] {_sizeof_fmt(speed)}/s",
                        end="",
                    )

            console.print()
            console.print(f"[green]✓[/green] 下载完成: {dest}")
        else:
            if not recursive:
                console.print(f"[yellow]{abs_path} 是目录，请使用 -r 递归下载[/yellow]")
                await manager.close()
                return

            dirs, files = await manager.list_dir(docid, by="name")
            tasks: list[TransferTask] = []
            base_local = Path(local) / info.name
            base_local.mkdir(parents=True, exist_ok=True)

            async def collect_tasks(parent_id: str, parent_remote: str, parent_local: Path) -> None:
                dirs2, files2 = await manager.list_dir(parent_id, by="name")
                for d in dirs2:
                    sub_dir = parent_local / d.name
                    sub_dir.mkdir(exist_ok=True)
                    await collect_tasks(d.docid, f"{parent_remote}/{d.name}", sub_dir)
                for f in files2:
                    file_info = await manager.get_resource_info_by_path(
                        f"{parent_remote}/{f.name}".strip("/")
                    )
                    if file_info:
                        tasks.append(
                            TransferTask(
                                remote_path=f"{parent_remote}/{f.name}",
                                local_path=str(parent_local / f.name),
                                size=file_info.size,
                                docid=file_info.docid,
                            )
                        )

            await collect_tasks(docid, abs_path, base_local)

            if tasks:
                console.print(f"[cyan]准备下载 {len(tasks)} 个文件，并发数 {jobs}[/cyan]")
                await batch_download(manager, tasks, jobs=jobs)

            console.print(f"[green]✓[/green] 目录下载完成: {base_local}")

        await manager.close()

    asyncio.run(_impl())


@app.command()
def upload(
    local: str = typer.Argument(..., help="本地路径"),
    remote: str = typer.Argument(".", help="远程目录"),
    jobs: int = typer.Option(4, "--jobs", "-j", help="并发数"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归上传目录"),
) -> None:
    """上传文件或目录。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        if not os.path.exists(local):
            console.print(f"[red]本地路径不存在:[/red] {local}")
            await manager.close()
            return

        local_path_obj = Path(local)
        local_name = local_path_obj.name

        if remote == ".":
            remote_dir = f"/{home_name}"
        else:
            remote_dir = remote

        remote_dir_stripped = remote_dir.strip("/")

        if os.path.isfile(local):
            dir_id = await manager.create_dirs_by_path(remote_dir_stripped)
            file_size = local_path_obj.stat().st_size
            start_time = time.time()
            uploaded = 0

            with open(local, "rb") as f:
                content = f.read()

            console.print(f"[dim]上传中...[/dim] {local_name} ({_sizeof_fmt(file_size)})")

            class ProgressReader:
                def __init__(self, fp, start):
                    self._fp = fp
                    self._start = start

                def read(self, size: int = -1) -> bytes:
                    nonlocal uploaded
                    data = self._fp.read(size)
                    if data:
                        uploaded += len(data)
                        elapsed = time.time() - self._start
                        speed = uploaded / elapsed if elapsed > 0 else 0
                        progress = uploaded / file_size * 100 if file_size > 0 else 0
                        console.print(
                            f"\r[magenta]⬆ {local_name}[/magenta] [{progress:.1f}%] {_sizeof_fmt(speed)}/s",
                            end="",
                        )
                    return data

            reader = ProgressReader(open(local, "rb"), start_time)
            try:
                await manager.upload_file(dir_id, local_name, reader, stream_len=file_size)
            finally:
                reader._fp.close()

            console.print()
            console.print(f"[green]✓[/green] 上传完成: {remote_dir}/{local_name}")
        else:
            if not recursive:
                console.print(f"[yellow]{local} 是目录，请使用 -r 递归上传[/yellow]")
                await manager.close()
                return

            tasks: list[TransferTask] = []

            async def collect_tasks(parent_local: Path, parent_remote: str) -> None:
                for entry in parent_local.iterdir():
                    rel_remote = f"{parent_remote}/{entry.name}"
                    if entry.is_file():
                        tasks.append(
                            TransferTask(
                                remote_path=rel_remote,
                                local_path=str(entry),
                                size=entry.stat().st_size,
                            )
                        )
                    elif entry.is_dir():
                        sub_dir_id = await manager.create_dirs_by_path(rel_remote.strip("/"))
                        await collect_tasks(entry, rel_remote)

            await collect_tasks(local_path_obj, remote_dir_stripped + "/" + local_name)

            if tasks:
                console.print(f"[cyan]准备上传 {len(tasks)} 个文件，并发数 {jobs}[/cyan]")
                dir_id = await manager.create_dirs_by_path(remote_dir_stripped)
                await batch_upload(manager, tasks, dir_id, jobs=jobs)

            console.print(f"[green]✓[/green] 目录上传完成: {remote_dir}/{local_name}")

        await manager.close()

    asyncio.run(_impl())


@app.command()
def cat(
    path: str = typer.Argument(..., help="文件路径"),
    head: int = typer.Option(0, "--head", help="只显示前 N 行"),
    tail: int = typer.Option(0, "--tail", help="只显示后 N 行"),
) -> None:
    """查看文件内容。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        abs_path, docid = await resolve_path(manager, path, home_name)

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        info = await manager.get_resource_info_by_path(abs_path.strip("/"))
        if not info or info.size == -1:
            console.print(f"[red]不是文件:[/red] {abs_path}")
            await manager.close()
            return

        if head > 0:
            count = 0
            lines_buffer: list[bytes] = []
            async for chunk in manager.download_file_stream(info.docid):
                lines = chunk.split(b"\n")
                for i, line in enumerate(lines):
                    if i < len(lines) - 1:
                        lines_buffer.append(line)
                        count += 1
                        if count >= head:
                            break
                    else:
                        lines_buffer.append(line)
                if count >= head:
                    break
            for line in lines_buffer[:head]:
                sys.stdout.buffer.write(line + b"\n")
        elif tail > 0:
            import collections

            window = collections.deque(maxlen=tail)
            buffer = b""
            async for chunk in manager.download_file_stream(info.docid):
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    window.append(line)
            if buffer:
                window.append(buffer)
            for line in window:
                sys.stdout.buffer.write(line + b"\n")
        else:
            read = 0
            try:
                async for chunk in manager.download_file_stream(info.docid):
                    sys.stdout.buffer.write(chunk)
                    read += len(chunk)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                pass

        await manager.close()

    asyncio.run(_impl())


@app.command()
def touch(
    path: str = typer.Argument(..., help="文件路径"),
) -> None:
    """创建空文件。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        abs_path, _ = await resolve_path(manager, path, home_name)
        parts = abs_path.strip("/").split("/")
        parent = "/".join(parts[:-1]) or f"/{home_name}"
        name = parts[-1]

        parent_info = await manager.get_resource_info_by_path(parent.strip("/"))
        if parent_info:
            pdocid = parent_info.docid
        else:
            pdocid = await manager.create_dirs_by_path(parent.strip("/"))

        await manager.upload_file(pdocid, name, b"")
        console.print(f"[green]✓[/green] 文件建立: {abs_path}")
        await manager.close()

    asyncio.run(_impl())


@app.command()
def link(
    path: str = typer.Argument(..., help="文件路径"),
    create: bool = typer.Option(False, "--create", "-c", help="创建外链"),
    delete: bool = typer.Option(False, "--delete", "-d", help="删除外链"),
    expire: int = typer.Option(0, "--expire", "-e", help="过期时间（Unix 时间戳）"),
    password: bool = typer.Option(False, "--password", "-p", help="启用密码保护"),
) -> None:
    """管理外链。"""

    async def _impl() -> None:
        cfg = load_config()
        manager = await _login(cfg)

        entrydoc = await manager.get_entrydoc()
        if not entrydoc:
            console.print("[red]无法获取文档库根目录[/red]")
            await manager.close()
            return
        home_name = entrydoc[0]["name"]

        abs_path, docid = await resolve_path(manager, path, home_name)

        if docid is None:
            console.print(f"[red]不存在:[/red] {abs_path}")
            await manager.close()
            return

        if create:
            link_info = await manager.create_link(
                docid,
                end_time=expire if expire > 0 else None,
                enable_pass=password,
            )
            console.print(f"[green]✓[/green] 外链创建成功:")
            console.print(f"[cyan]链接:[/cyan] {link_info.link}")
            if link_info.password:
                console.print(f"[cyan]密码:[/cyan] {link_info.password}")
        elif delete:
            await manager.delete_link(docid)
            console.print(f"[green]✓[/green] 外链已删除")
        else:
            link_info = await manager.get_link(docid)
            if link_info:
                console.print(f"[cyan]链接:[/cyan] {link_info.link}")
                if link_info.password:
                    console.print(f"[cyan]密码:[/cyan] {link_info.password}")
                console.print(f"[cyan]权限:[/cyan] {link_info.perm}")
            else:
                console.print("[yellow]该文件没有外链[/yellow]")

        await manager.close()

    asyncio.run(_impl())


@app.command()
def shell() -> None:
    """进入交互式 REPL Shell。"""
    from .shell import run_interactive_shell

    run_interactive_shell()


def cli() -> None:
    """入口函数（兼容旧版本）。"""
    app()


if __name__ == "__main__":
    cli()
