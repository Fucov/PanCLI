"""Typer CLI 入口 — 定义所有命令路由。

无参数运行时默认进入 prompt-toolkit 交互式 Shell。
"""

from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console

from .config import load_config, save_config
from .core import (
    __version__,
    abs_path,
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

console = Console()

app = typer.Typer(
    name="pancli",
    help="AnyShare (PanCLI) 现代化命令行工具。\n\n直接运行 `pancli` 将进入沉浸式文件系统。",
    invoke_without_command=True,
    no_args_is_help=False,
)


# ── 全局回调：默认进入 Shell ────────────────────────────────────


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "-v", "--version", help="显示版本号"),
    whoami: bool = typer.Option(False, "--whoami", help="查看本地缓存的鉴权信息"),
    logout: bool = typer.Option(False, "--logout", help="清除本地保存的凭据缓存"),
) -> None:
    """AnyShare (PanCLI) 现代化命令行工具。"""
    if version:
        console.print(f"pancli {__version__}")
        raise typer.Exit()

    if whoami:
        cfg = load_config()
        console.print(f"当前配置 Host: {cfg.host}")
        console.print(f"当前记住账号: {cfg.username if cfg.username else '无'}")
        console.print(f"密码状态: {'已加密保存在本地' if cfg.encrypted else '未保存'}")
        raise typer.Exit()

    if logout:
        cfg = load_config()
        cfg.username = None
        cfg.encrypted = None
        cfg.cached_token.token = ""
        save_config(cfg)
        console.print("✓ 已清除本地登录凭据，下次启动将重新要求登录。")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        # 无子命令 → 默认进入交互式 Shell
        from .shell import PanShell

        asyncio.run(PanShell().run_async())


# ── 辅助：单次命令的 async 桥接 ─────────────────────────────────


def _run(coro):
    """在单次命令模式下运行异步协程。"""
    asyncio.run(coro)


async def _single_cmd(callback, *args, **kwargs):
    """单次命令：登录 → 执行 → 关闭。"""
    cfg = load_config()
    manager = await login(cfg)
    try:
        await callback(manager, *args, **kwargs)
    finally:
        await manager.close()


# ── 文件系统导航命令 ────────────────────────────────────────────


@app.command()
def ls(
    path: str = typer.Argument(".", help="远程路径"),
    human: bool = typer.Option(True, "-h", "--human", help="可读格式显示文件大小"),
) -> None:
    """列出目录内容。"""
    _run(_single_ls(path, human))


async def _single_ls(path: str, human: bool) -> None:
    cfg = load_config()
    manager = await login(cfg)
    try:
        entrydoc = await manager.get_entrydoc()
        home = entrydoc[0]["name"]
        target = abs_path(f"/{home}", path)
        await do_ls(manager, target, human=human)
    finally:
        await manager.close()


@app.command()
def tree(
    path: str = typer.Argument(".", help="远程路径"),
) -> None:
    """树状图展示目录结构。"""
    _run(_single_tree(path))


async def _single_tree(path: str) -> None:
    cfg = load_config()
    manager = await login(cfg)
    try:
        entrydoc = await manager.get_entrydoc()
        home = entrydoc[0]["name"]
        await do_tree(manager, abs_path(f"/{home}", path))
    finally:
        await manager.close()


@app.command()
def stat(
    path: str = typer.Argument(..., help="远程路径"),
) -> None:
    """查看文件/目录的元信息。"""
    _run(_single_stat(path))


async def _single_stat(path: str) -> None:
    cfg = load_config()
    manager = await login(cfg)
    try:
        entrydoc = await manager.get_entrydoc()
        home = entrydoc[0]["name"]
        await do_stat(manager, abs_path(f"/{home}", path))
    finally:
        await manager.close()


@app.command()
def cat(
    path: str = typer.Argument(..., help="远程文件路径"),
) -> None:
    """打印文件全部内容。"""
    _run(_single_cat(path))


async def _single_cat(path: str) -> None:
    cfg = load_config()
    manager = await login(cfg)
    try:
        entrydoc = await manager.get_entrydoc()
        home = entrydoc[0]["name"]
        await do_cat(manager, abs_path(f"/{home}", path))
    finally:
        await manager.close()


@app.command()
def head(
    path: str = typer.Argument(..., help="远程文件路径"),
    n: int = typer.Option(10, "-n", "--lines", help="行数"),
) -> None:
    """打印文件头部 n 行。"""
    _run(_single_head(path, n))


async def _single_head(path: str, n: int) -> None:
    cfg = load_config()
    manager = await login(cfg)
    try:
        entrydoc = await manager.get_entrydoc()
        home = entrydoc[0]["name"]
        await do_head(manager, abs_path(f"/{home}", path), n=n)
    finally:
        await manager.close()


@app.command()
def tail(
    path: str = typer.Argument(..., help="远程文件路径"),
    n: int = typer.Option(10, "-n", "--lines", help="行数"),
) -> None:
    """打印文件末尾 n 行。"""
    _run(_single_tail(path, n))


async def _single_tail(path: str, n: int) -> None:
    cfg = load_config()
    manager = await login(cfg)
    try:
        entrydoc = await manager.get_entrydoc()
        home = entrydoc[0]["name"]
        await do_tail(manager, abs_path(f"/{home}", path), n=n)
    finally:
        await manager.close()


# ── 文件操作命令 ────────────────────────────────────────────────


@app.command()
def mkdir(
    path: str = typer.Argument(..., help="远程目录路径"),
) -> None:
    """创建远程目录。"""
    _run(_single_cmd(do_mkdir, path))


@app.command()
def touch(
    path: str = typer.Argument(..., help="远程文件路径"),
) -> None:
    """创建远程空文件。"""
    _run(_single_cmd(do_touch, path))


@app.command()
def rm(
    path: str = typer.Argument(..., help="远程路径"),
    recurse: bool = typer.Option(False, "-r", "--recurse", help="递归删除目录"),
) -> None:
    """删除远程文件或目录。"""

    async def _rm() -> None:
        cfg = load_config()
        manager = await login(cfg)
        try:
            entrydoc = await manager.get_entrydoc()
            home = entrydoc[0]["name"]
            await do_rm(manager, abs_path(f"/{home}", path), recurse=recurse)
        finally:
            await manager.close()

    _run(_rm())


@app.command()
def mv(
    src: str = typer.Argument(..., help="源路径"),
    dst: str = typer.Argument(..., help="目标路径"),
    force: bool = typer.Option(False, "-f", "--force", help="覆盖同名文件"),
) -> None:
    """移动文件/目录。"""

    async def _mv() -> None:
        cfg = load_config()
        manager = await login(cfg)
        try:
            entrydoc = await manager.get_entrydoc()
            home = entrydoc[0]["name"]
            await do_mv(manager, abs_path(f"/{home}", src), abs_path(f"/{home}", dst), overwrite=force)
        finally:
            await manager.close()

    _run(_mv())


@app.command()
def cp(
    src: str = typer.Argument(..., help="源路径"),
    dst: str = typer.Argument(..., help="目标路径"),
    force: bool = typer.Option(False, "-f", "--force", help="覆盖同名文件"),
) -> None:
    """复制文件/目录。"""

    async def _cp() -> None:
        cfg = load_config()
        manager = await login(cfg)
        try:
            entrydoc = await manager.get_entrydoc()
            home = entrydoc[0]["name"]
            await do_cp(manager, abs_path(f"/{home}", src), abs_path(f"/{home}", dst), overwrite=force)
        finally:
            await manager.close()

    _run(_cp())


# ── 传输命令 ────────────────────────────────────────────────────


@app.command()
def upload(
    local: str = typer.Argument(..., help="本地文件/目录路径"),
    remote: str = typer.Argument(".", help="远程目标目录"),
    recurse: bool = typer.Option(False, "-r", "--recurse", help="递归上传目录"),
    jobs: int = typer.Option(4, "-j", "--jobs", help="并发数"),
) -> None:
    """上传文件（支持并发）。"""

    async def _up() -> None:
        cfg = load_config()
        manager = await login(cfg)
        try:
            entrydoc = await manager.get_entrydoc()
            home = entrydoc[0]["name"]
            await do_upload(manager, local, abs_path(f"/{home}", remote), recurse=recurse, jobs=jobs)
        finally:
            await manager.close()

    _run(_up())


@app.command()
def download(
    remote: str = typer.Argument(..., help="远程文件/目录路径"),
    local: str = typer.Argument(".", help="本地保存路径"),
    recurse: bool = typer.Option(False, "-r", "--recurse", help="递归下载目录"),
    jobs: int = typer.Option(4, "-j", "--jobs", help="并发数"),
) -> None:
    """下载文件（支持并发与断点续传）。"""

    async def _dl() -> None:
        cfg = load_config()
        manager = await login(cfg)
        try:
            entrydoc = await manager.get_entrydoc()
            home = entrydoc[0]["name"]
            await do_download(manager, abs_path(f"/{home}", remote), local, recurse=recurse, jobs=jobs)
        finally:
            await manager.close()

    _run(_dl())


# ── 搜索命令 ────────────────────────────────────────────────────


@app.command()
def find(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    path: str = typer.Option(".", help="搜索起始路径"),
    depth: int = typer.Option(5, "-d", "--depth", help="最大搜索深度"),
) -> None:
    """递归搜索文件名包含关键词的文件。"""

    async def _find() -> None:
        cfg = load_config()
        manager = await login(cfg)
        try:
            entrydoc = await manager.get_entrydoc()
            home = entrydoc[0]["name"]
            await do_find(manager, keyword, abs_path(f"/{home}", path), max_depth=depth)
        finally:
            await manager.close()

    _run(_find())


# ── 入口函数 ────────────────────────────────────────────────────


def cli() -> None:
    app()
