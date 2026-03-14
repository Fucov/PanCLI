"""BHPAN CLI — 北航网盘现代化命令行工具。

基于 Typer + Rich 构建，提供 ls / upload / download / rm / mkdir / mv / cp / cat / link 等命令。
"""

from __future__ import annotations

import getpass
import os
import sys
import time
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from .api import ApiManager, InvalidRootException, WrongPasswordException
from .auth import rsa_encrypt
from .config import load_config, save_config
from .models import AppConfig
from .network import ApiException

# ── 全局 ────────────────────────────────────────────────────────

__version__ = "2.0.0"

app = typer.Typer(
    name="bhpan",
    help="北航网盘 (BHPAN) 命令行工具 — 现代化重构版",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()

# BHPAN 公钥 (v7, 2023.08+)
_PUBKEY = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4E+eiWRwffhRIPQYvlXU
jf0b3HqCmosiCxbFCYI/gdfDBhrTUzbt3fL3o/gRQQBEPf69vhJMFH2ZMtaJM6oh
E3yQef331liPVM0YvqMOgvoID+zDa1NIZFObSsjOKhvZtv9esO0REeiVEPKNc+Dp
6il3x7TV9VKGEv0+iriNjqv7TGAexo2jVtLm50iVKTju2qmCDG83SnVHzsiNj70M
iviqiLpgz72IxjF+xN4bRw8I5dD0GwwO8kDoJUGWgTds+VckCwdtZA65oui9Osk5
t1a4pg6Xu9+HFcEuqwJTDxATvGAz1/YW0oUisjM0ObKTRDVSfnTYeaBsN6L+M+8g
CwIDAQAB
-----END PUBLIC KEY-----"""

_HOST = "bhpan.buaa.edu.cn"


# ── 辅助函数 ────────────────────────────────────────────────────


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def _ts_fmt(us: int) -> str:
    """微秒时间戳 → 可读日期。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(us / 1_000_000))


def _resolve_home(manager: ApiManager, path: str) -> str:
    """将 ``home`` 别名解析为用户文档库根路径。"""
    alias = "home"
    if path == alias or path.startswith(alias + "/"):
        entrydoc = manager.get_entrydoc()
        if len(entrydoc) < 1:
            console.print("[red]无法获取用户文档库根目录[/red]")
            raise typer.Exit(1)
        path = entrydoc[0]["name"] + path[len(alias) :]
    return path


def _make_progress() -> Progress:
    """构建 Rich 进度条（上传/下载统一样式）。"""
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


# ── 登录 & Manager 工厂 ────────────────────────────────────────


def _login(username_override: str | None = None) -> tuple[ApiManager, AppConfig]:
    """处理登录逻辑，返回 (manager, config)。"""
    cfg = load_config()

    # 用户名
    if username_override:
        username = username_override
    elif cfg.username:
        username = cfg.username
    else:
        username = console.input("[bold cyan]Username:[/bold cyan] ")
        cfg.username = username

    # 密码 / 加密凭据
    store_password = cfg.store_password and username_override is None
    password: str | None = None
    encrypted: str | None = None

    if store_password:
        encrypted = cfg.encrypted
        if encrypted is None:
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, _PUBKEY)
            cfg.encrypted = encrypted
    else:
        password = getpass.getpass()
        encrypted = rsa_encrypt(password, _PUBKEY)

    # 尝试登录（最多 3 次）
    manager: ApiManager | None = None
    for retry in range(3):
        try:
            manager = ApiManager(
                _HOST,
                username,
                password,
                _PUBKEY,
                encrypted=encrypted,
                cached_token=cfg.cached_token.token or None,
                cached_expire=cfg.cached_token.expires or None,
            )
            break
        except WrongPasswordException:
            console.print(f"[yellow]用户名或密码错误，重试 ({retry + 1}/3)[/yellow]")
            time.sleep(1)
            password = getpass.getpass()
            encrypted = rsa_encrypt(password, _PUBKEY)
            cfg.encrypted = encrypted

    if manager is None:
        cfg.username = None
        cfg.encrypted = None
        save_config(cfg)
        console.print("[bold red]登录失败[/bold red]")
        raise typer.Exit(1)

    # 缓存 token
    if manager._expires > 0:
        cfg.cached_token.token = manager._tokenid
        cfg.cached_token.expires = manager._expires
    save_config(cfg)
    return manager, cfg


# 用一个全局回调来处理 --username 选项
_username_override: str | None = None


@app.callback()
def _global_options(
    username: Optional[str] = typer.Option(None, "-u", "--username", help="使用指定用户名登录"),
) -> None:
    global _username_override
    _username_override = username


def _get_manager() -> ApiManager:
    """获取已登录的 ApiManager（懒初始化）。"""
    m, cfg = _login(_username_override)
    return m


# ── 命令实现 ────────────────────────────────────────────────────


@app.command()
def ls(
    remote_dir: str = typer.Argument(..., help="远程路径（可用 home 表示文档根目录）"),
    human: bool = typer.Option(False, "-h", "--human", help="以可读格式显示文件大小"),
) -> None:
    """列出远程目录内容。"""
    m = _get_manager()
    try:
        remote_dir = _resolve_home(m, remote_dir)
        dir_info = m.get_resource_info_by_path(remote_dir)
        if dir_info is None:
            console.print(f"[red]路径不存在:[/red] {remote_dir}")
            raise typer.Exit(1)

        if dir_info.size == -1:
            # 目录
            dirs, files = m.list_dir(dir_info.docid, by="name")
            table = Table(
                title=f"📂 {remote_dir}",
                show_header=True,
                header_style="bold magenta",
                border_style="dim",
            )
            table.add_column("创建者", style="cyan", no_wrap=True)
            table.add_column("大小", justify="right", style="green")
            table.add_column("修改时间", style="yellow")
            table.add_column("名称", style="white bold")

            table.add_row("", "", _ts_fmt(dir_info.modified), Text(".", style="dim"))
            for d in dirs:
                table.add_row(
                    d.get("creator", ""),
                    Text("📁", style="blue"),
                    _ts_fmt(d["modified"]),
                    Text(d["name"], style="bold blue"),
                )
            for f in files:
                size_str = _sizeof_fmt(f["size"]) if human else str(f["size"])
                table.add_row(
                    f.get("creator", ""),
                    size_str,
                    _ts_fmt(f["modified"]),
                    f["name"],
                )
            console.print(table)
        else:
            # 文件详情
            meta = m.get_file_meta(dir_info.docid)
            info_table = Table(
                title=f"📄 {remote_dir}",
                show_header=False,
                border_style="dim",
            )
            info_table.add_column("属性", style="cyan bold")
            info_table.add_column("值")
            info_table.add_row("大小", _sizeof_fmt(meta.size) if human else str(meta.size))
            info_table.add_row("DocID", meta.docid)
            info_table.add_row("版本", meta.rev)
            info_table.add_row("编辑者", meta.editor)
            info_table.add_row("修改时间", _ts_fmt(meta.modified))
            info_table.add_row("标签", ", ".join(meta.tags) if meta.tags else "—")
            console.print(info_table)
    finally:
        m.close()


@app.command()
def upload(
    local_path: str = typer.Argument(..., help="本地文件或目录路径"),
    remote_dir: str = typer.Argument(..., help="远程目标目录"),
    rename: Optional[str] = typer.Option(None, help="重命名上传的文件"),
    recurse: bool = typer.Option(False, "-r", "--recurse", help="递归上传目录"),
) -> None:
    """上传本地文件或目录到网盘。"""
    m = _get_manager()
    try:
        remote_dir = _resolve_home(m, remote_dir)
        _upload_impl(m, local_path, remote_dir, rename=rename, allow_recurse=recurse)
    finally:
        m.close()


def _upload_impl(
    m: ApiManager,
    local_path: str,
    remote_dir: str,
    *,
    rename: str | None = None,
    allow_recurse: bool = False,
) -> None:
    remote_dir = remote_dir.strip("/")
    local_path = os.path.normpath(local_path)
    remote_name = rename or os.path.basename(os.path.abspath(local_path))

    if not os.path.exists(local_path):
        console.print(f"[red]本地路径不存在:[/red] {local_path}")
        return

    if os.path.isfile(local_path):
        file_size = os.path.getsize(local_path)
        dir_id = m.create_dirs_by_path(remote_dir)

        if file_size > 1024 * 1024:
            # 大文件 → 流式上传 + 进度条
            with _make_progress() as progress:
                task = progress.add_task(f"⬆  {remote_name}", total=file_size)
                with open(local_path, "rb") as f:
                    # 构造一个回调包装器
                    class _ProgressReader:
                        def __init__(self, fp, pg, tid):
                            self._fp = fp
                            self._pg = pg
                            self._tid = tid

                        def read(self, size: int = -1) -> bytes:
                            data = self._fp.read(size)
                            if data:
                                self._pg.update(self._tid, advance=len(data))
                            return data

                    wrapped = _ProgressReader(f, progress, task)
                    m.upload_file(dir_id, remote_name, wrapped, stream_len=file_size)
        else:
            with open(local_path, "rb") as f:
                content = f.read()
            console.print(f"[dim]上传中...[/dim] {remote_name} ({_sizeof_fmt(file_size)})")
            m.upload_file(dir_id, remote_name, content)

        console.print(f"[green]✓[/green] 上传完成: {remote_name}")
    else:
        if allow_recurse:
            entries = os.listdir(local_path)
            full_remote = remote_dir + "/" + remote_name
            for entry in entries:
                full_local = os.path.join(local_path, entry)
                _upload_impl(m, full_local, full_remote, allow_recurse=True)
            if not entries:
                m.create_dirs_by_path(full_remote)
        else:
            console.print(f"[yellow]{local_path} 是目录，请使用 -r 递归上传[/yellow]")


@app.command()
def download(
    remote_path: str = typer.Argument(..., help="远程文件或目录路径"),
    local_dir: str = typer.Argument(..., help="本地目标目录"),
    rename: Optional[str] = typer.Option(None, help="重命名下载的文件"),
    recurse: bool = typer.Option(False, "-r", "--recurse", help="递归下载目录"),
) -> None:
    """下载网盘文件或目录到本地。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        _download_impl(m, remote_path, local_dir, rename=rename, allow_recurse=recurse)
    finally:
        m.close()


def _download_impl(
    m: ApiManager,
    remote_path: str,
    local_dir: str,
    *,
    rename: str | None = None,
    allow_recurse: bool = False,
) -> None:
    remote_path = remote_path.strip("/")
    local_name = rename or os.path.basename(remote_path)
    file_info = m.get_resource_info_by_path(remote_path)

    if file_info is None:
        console.print(f"[red]远程路径不存在:[/red] {remote_path}")
        return

    if file_info.size != -1:
        # 文件
        os.makedirs(local_dir, exist_ok=True)
        dest = os.path.join(local_dir, local_name)
        with _make_progress() as progress:
            task = progress.add_task(f"⬇  {local_name}", total=file_info.size)
            with open(dest, "wb") as f:
                for chunk in m.download_file_stream(file_info.docid):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))
        console.print(f"[green]✓[/green] 下载完成: {dest}")
    else:
        if allow_recurse:
            dirs, files = m.list_dir(file_info.docid, by="name")
            full_local = os.path.join(local_dir, local_name)
            for d in dirs:
                _download_impl(
                    m, remote_path + "/" + d["name"], full_local, allow_recurse=True
                )
            for f in files:
                _download_impl(
                    m, remote_path + "/" + f["name"], full_local, allow_recurse=True
                )
        else:
            console.print(f"[yellow]{remote_path} 是目录，请使用 -r 递归下载[/yellow]")


@app.command()
def rm(
    remote_path: str = typer.Argument(..., help="远程文件或目录路径"),
    recurse: bool = typer.Option(False, "-r", "--recurse", help="递归删除目录"),
) -> None:
    """删除远程文件或目录。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        file_info = m.get_resource_info_by_path(remote_path)
        if file_info is None:
            console.print(f"[red]路径不存在:[/red] {remote_path}")
            raise typer.Exit(1)

        if file_info.size != -1:
            m.delete_file(file_info.docid)
            console.print(f"[green]✓[/green] 已删除文件: {remote_path}")
        else:
            if recurse:
                m.delete_dir(file_info.docid)
                console.print(f"[green]✓[/green] 已删除目录: {remote_path}")
            else:
                console.print(f"[yellow]{remote_path} 是目录，请使用 -r 递归删除[/yellow]")
    finally:
        m.close()


@app.command()
def mkdir(
    remote_path: str = typer.Argument(..., help="远程目录路径（支持多级创建）"),
) -> None:
    """创建远程目录（支持多级路径）。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        try:
            doc_id = m.create_dirs_by_path(remote_path)
            console.print(f"[green]✓[/green] 目录已创建，docid: [dim]{doc_id}[/dim]")
        except InvalidRootException:
            console.print("[red]无效的根目录[/red]")
            raise typer.Exit(1)
    finally:
        m.close()


@app.command()
def mv(
    src: str = typer.Argument(..., help="源路径"),
    dst: str = typer.Argument(..., help="目标路径"),
    force: bool = typer.Option(False, "-f", "--force", help="覆盖已存在的目标"),
) -> None:
    """移动或重命名远程文件 / 目录。"""
    m = _get_manager()
    try:
        src = _resolve_home(m, src)
        dst = _resolve_home(m, dst)
        _move_or_copy(m, src, dst, overwrite=force, copy=False)
    finally:
        m.close()


@app.command()
def cp(
    src: str = typer.Argument(..., help="源路径"),
    dst: str = typer.Argument(..., help="目标路径"),
    force: bool = typer.Option(False, "-f", "--force", help="覆盖已存在的目标"),
) -> None:
    """复制远程文件 / 目录。"""
    m = _get_manager()
    try:
        src = _resolve_home(m, src)
        dst = _resolve_home(m, dst)
        _move_or_copy(m, src, dst, overwrite=force, copy=True)
    finally:
        m.close()


def _move_or_copy(
    m: ApiManager, src: str, dst: str, *, overwrite: bool = False, copy: bool = False
) -> None:
    """mv / cp 的统一实现逻辑，保留原有 6 种情况矩阵。"""
    action = "复制" if copy else "移动"

    src_parts = src.strip("/").split("/")
    dst_parts = dst.strip("/").split("/")
    src_name = src_parts[-1]
    src_parent = "/".join(src_parts[:-1])
    dst_name = dst_parts[-1]
    dst_parent = "/".join(dst_parts[:-1])

    if src_parts == dst_parts:
        console.print("[red]源路径与目标路径相同[/red]")
        return

    src_info = m.get_resource_info_by_path(src)
    if src_info is None:
        console.print(f"[red]源路径不存在:[/red] {src}")
        return

    dst_info = m.get_resource_info_by_path(dst)

    # case (3)(4): dst 是现有目录
    if dst_info is not None and dst_info.size == -1:
        if src_parts[:-1] == dst_parts:
            console.print("[dim]无需操作[/dim]")
            return
        if src_parts == dst_parts[: len(src_parts)]:
            console.print("[red]不能移动到子目录[/red]")
            return
        console.print(f"[dim]{action}[/dim] {src} → {dst}/")
        if copy:
            m.copy_file(src_info.docid, dst_info.docid, overwrite_on_dup=overwrite)
        else:
            m.move_file(src_info.docid, dst_info.docid, overwrite_on_dup=overwrite)
        console.print(f"[green]✓[/green] {action}完成")
        return

    # case (1)(2): dst 不存在
    if dst_info is None:
        if src_parent == dst_parent:
            if copy:
                dst_parent_info = m.get_resource_info_by_path(dst_parent)
                new_id, _ = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
                m.rename_file(new_id, dst_name)
            else:
                m.rename_file(src_info.docid, dst_name)
            console.print(f"[green]✓[/green] 重命名: {src} → {dst}")
            return
        if src_parts == dst_parts[: len(src_parts)]:
            console.print("[red]不能移动到子目录[/red]")
            return
        dst_parent_info = m.get_resource_info_by_path(dst_parent)
        if dst_parent_info is None:
            console.print("[red]目标父目录不存在[/red]")
            return
        if copy:
            new_id, new_name = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
        else:
            new_id, new_name = m.move_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
        if new_name != dst_name:
            m.rename_file(new_id, dst_name)
        console.print(f"[green]✓[/green] {action}完成: {src} → {dst}")
        return

    # case (5): src 是目录，dst 是文件
    if src_info.size == -1:
        console.print("[red]不能将目录移动到文件位置[/red]")
        return

    # case (6): 两者都是文件
    if overwrite:
        dst_parent_info = m.get_resource_info_by_path(dst_parent)
        assert dst_parent_info is not None
        m.delete_file(dst_info.docid)
        if src_parent == dst_parent:
            if copy:
                new_id, _ = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
                m.rename_file(new_id, dst_name)
            else:
                m.rename_file(src_info.docid, dst_name)
        else:
            if copy:
                new_id, new_name = m.copy_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
            else:
                new_id, new_name = m.move_file(src_info.docid, dst_parent_info.docid, rename_on_dup=True)
            if new_name != dst_name:
                m.rename_file(new_id, dst_name)
        console.print(f"[green]✓[/green] {action}并覆盖完成")
    else:
        console.print(f"[yellow]{dst} 已存在，使用 -f 覆盖[/yellow]")


@app.command()
def cat(
    remote_path: str = typer.Argument(..., help="远程文件路径"),
) -> None:
    """将远程文件内容输出到标准输出（可通过管道使用）。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        file_info = m.get_resource_info_by_path(remote_path)
        if file_info is None:
            console.print(f"[red]路径不存在:[/red] {remote_path}", file=sys.stderr)
            raise typer.Exit(1)
        if file_info.size == -1:
            console.print(f"[yellow]{remote_path} 是目录[/yellow]", file=sys.stderr)
            raise typer.Exit(1)

        if sys.platform == "linux":
            import signal
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)

        try:
            for chunk in m.download_file_stream(file_info.docid):
                sys.stdout.buffer.write(chunk)
        except BrokenPipeError:
            pass
    finally:
        m.close()


# ── link 子命令组 ───────────────────────────────────────────────

link_app = typer.Typer(
    name="link",
    help="外链分享管理",
    no_args_is_help=True,
)
app.add_typer(link_app, name="link")


@link_app.command("show")
def link_show(
    remote_path: str = typer.Argument(..., help="远程文件或目录路径"),
) -> None:
    """查看外链信息。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        file_info = m.get_resource_info_by_path(remote_path)
        if file_info is None:
            console.print(f"[red]路径不存在:[/red] {remote_path}")
            raise typer.Exit(1)

        link_info = m.get_link(file_info.docid)
        if link_info is None:
            console.print(f"[dim]{remote_path} 没有已启用的外链[/dim]")
            return

        perm_list = []
        if link_info.perm & 1:
            perm_list.append("预览")
        if link_info.perm & 2:
            perm_list.append("下载")
        if link_info.perm & 4:
            perm_list.append("上传")

        panel_content = (
            f"[bold cyan]链接:[/bold cyan] https://{m.host}/link/{link_info.link}\n"
        )
        if link_info.password:
            panel_content += f"[bold cyan]密码:[/bold cyan] {link_info.password}\n"
        panel_content += f"[bold cyan]权限:[/bold cyan] {', '.join(perm_list)}\n"
        panel_content += f"[bold cyan]过期:[/bold cyan] {_ts_fmt(link_info.endtime)}\n"
        panel_content += f"[bold cyan]限次:[/bold cyan] {link_info.limittimes}"

        console.print(Panel(panel_content, title=f"🔗 {remote_path}", border_style="green"))
    finally:
        m.close()


@link_app.command("create")
def link_create(
    remote_path: str = typer.Argument(..., help="远程文件或目录路径"),
    expires: int = typer.Option(30, "-e", "--expires", help="过期天数"),
    password: bool = typer.Option(False, "-p", "--password", help="启用密码"),
    allow_upload: bool = typer.Option(False, "--allow-upload", help="允许上传"),
    no_download: bool = typer.Option(False, "--no-download", help="禁止下载和预览"),
) -> None:
    """创建或修改外链。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        file_info = m.get_resource_info_by_path(remote_path)
        if file_info is None:
            console.print(f"[red]路径不存在:[/red] {remote_path}")
            raise typer.Exit(1)

        if no_download:
            allow_upload = True
        allow_view = not no_download
        allow_down = not no_download

        expire_time = int(time.time() + 86400 * expires) * 1_000_000

        existing_link = m.get_link(file_info.docid)
        if existing_link is None:
            link = m.create_link(
                file_info.docid,
                expire_time,
                -1,
                enable_pass=password,
                allow_view=allow_view,
                allow_download=allow_down,
                allow_upload=allow_upload,
            )
        else:
            link = m.modify_link(
                file_info.docid,
                expire_time,
                -1,
                enable_pass=password,
                allow_view=allow_view,
                allow_download=allow_down,
                allow_upload=allow_upload,
            )
        console.print(f"[green]✓[/green] https://{m.host}/link/{link.link}")
    finally:
        m.close()


@link_app.command("delete")
def link_delete(
    remote_path: str = typer.Argument(..., help="远程文件或目录路径"),
) -> None:
    """关闭/删除外链。"""
    m = _get_manager()
    try:
        remote_path = _resolve_home(m, remote_path)
        file_info = m.get_resource_info_by_path(remote_path)
        if file_info is None:
            console.print(f"[red]路径不存在:[/red] {remote_path}")
            raise typer.Exit(1)

        link_info = m.get_link(file_info.docid)
        if link_info is None:
            console.print("[dim]该路径没有外链[/dim]")
            return

        m.delete_link(file_info.docid)
        console.print(f"[green]✓[/green] 外链已关闭: {remote_path}")
    finally:
        m.close()


@app.command()
def version() -> None:
    """显示版本号。"""
    console.print(
        Panel(
            f"[bold]bhpan[/bold] v{__version__}\n[dim]北航网盘命令行工具 — 现代化重构版[/dim]",
            border_style="cyan",
        )
    )


# ── 入口 ────────────────────────────────────────────────────────


def cli() -> None:
    """PyInstaller / setup.py console_scripts 入口点。"""
    app()


if __name__ == "__main__":
    cli()
