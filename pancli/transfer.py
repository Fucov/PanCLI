"""Concurrent transfer engine with Rich progress bars and resume support."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .models import TransferStatus, TransferTask

if TYPE_CHECKING:
    from .api import AsyncApiManager

# ── 辅助函数 ────────────────────────────────────────────────────


def _sizeof_fmt(num: float, suffix: str = "") -> str:
    """人类可读的文件大小格式化。"""
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def _ts_fmt(us: int) -> str:
    """微秒时间戳 → 可读日期。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(us / 1_000_000))


# ── 单文件下载任务 ──────────────────────────────────────────────


async def _download_single_file(
    manager: "AsyncApiManager",
    task: TransferTask,
    progress: Progress,
    task_id: TaskID,
    semaphore: asyncio.Semaphore,
) -> None:
    """单文件异步下载任务（支持断点续传）。"""
    async with semaphore:
        progress.update(task_id, description=f"[cyan]⬇ {Path(task.remote_path).name}")
        try:
            url, total_size = await manager.get_download_url(task.docid)

            local_path = Path(task.local_path)
            local_size = 0
            headers = {}

            if local_path.exists():
                local_size = local_path.stat().st_size
                if local_size < total_size:
                    headers["Range"] = f"bytes={local_size}-"
                    progress.update(
                        task_id,
                        description=f"[yellow]⬇ {Path(task.remote_path).name} [resume]",
                    )

            start_time = time.time()
            downloaded = local_size
            mode = "ab" if local_size > 0 else "wb"

            with open(local_path, mode) as f:
                async for chunk in manager._client.stream(
                    "GET", url, headers=headers
                ):
                    f.write(chunk)
                    chunk_len = len(chunk)
                    downloaded += chunk_len
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    progress.update(
                        task_id,
                        completed=downloaded,
                        description=f"[cyan]⬇ {Path(task.remote_path).name} "
                        f"[green]{_sizeof_fmt(speed)}/s",
                    )

            task.transferred = downloaded
            task.status = TransferStatus.COMPLETED
            progress.update(
                task_id,
                completed=total_size,
                description=f"[green]✓ {Path(task.remote_path).name}",
            )

        except Exception as e:
            task.status = TransferStatus.FAILED
            task.error = str(e)
            progress.update(
                task_id,
                description=f"[red]✗ {Path(task.remote_path).name}",
            )


# ── 单文件上传任务 ──────────────────────────────────────────────


async def _upload_single_file(
    manager: "AsyncApiManager",
    task: TransferTask,
    remote_parent_id: str,
    progress: Progress,
    task_id: TaskID,
    semaphore: asyncio.Semaphore,
) -> None:
    """单文件异步上传任务。"""
    async with semaphore:
        local_path = Path(task.local_path)
        progress.update(task_id, description=f"[magenta]⬆ {local_path.name}")
        try:
            file_size = local_path.stat().st_size
            start_time = time.time()
            uploaded = 0

            class ProgressReader:
                def __init__(self, fp, pg, tid, task_ref):
                    self._fp = fp
                    self._pg = pg
                    self._tid = tid
                    self._task_ref = task_ref
                    self._start = start_time

                def read(self, size: int = -1) -> bytes:
                    nonlocal uploaded
                    data = self._fp.read(size)
                    if data:
                        uploaded += len(data)
                        elapsed = time.time() - self._start
                        speed = uploaded / elapsed if elapsed > 0 else 0
                        self._pg.update(
                            self._tid,
                            advance=len(data),
                            description=f"[magenta]⬆ {Path(self._task_ref.local_path).name} "
                            f"[green]{_sizeof_fmt(speed)}/s",
                        )
                    return data

            with open(local_path, "rb") as f:
                reader = ProgressReader(f, progress, task_id, task)
                await manager.upload_file(
                    remote_parent_id,
                    local_path.name,
                    reader,
                    stream_len=file_size,
                )

            task.transferred = file_size
            task.status = TransferStatus.COMPLETED
            progress.update(
                task_id,
                completed=file_size,
                description=f"[green]✓ {local_path.name}",
            )

        except Exception as e:
            task.status = TransferStatus.FAILED
            task.error = str(e)
            progress.update(
                task_id,
                description=f"[red]✗ {local_path.name}",
            )


# ── 并发批量下载 ────────────────────────────────────────────────


async def batch_download(
    manager: "AsyncApiManager",
    tasks: list[TransferTask],
    jobs: int = 4,
) -> list[TransferTask]:
    """并发下载多个文件，带 Rich 多行进度条。

    Parameters
    ----------
    manager : AsyncApiManager
        API 管理器实例
    tasks : list[TransferTask]
        下载任务列表
    jobs : int
        最大并发数，默认 4

    Returns
    -------
    list[TransferTask]
        完成状态的任务列表
    """
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(jobs)

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        main_task = progress.add_task(
            f"[bold]批量下载 ({len(tasks)} 文件, 并发 {jobs})",
            total=len(tasks),
        )

        file_progress_tasks: list[tuple[TaskID, int]] = []

        for i, t in enumerate(tasks):
            tid = progress.add_task(f"[cyan]⬇ {Path(t.remote_path).name}", total=t.size)
            file_progress_tasks.append((tid, t.size))
            progress.update(main_task, advance=0)

        async def download_wrapper(idx: int):
            t = tasks[idx]
            tid = file_progress_tasks[idx][0]
            await _download_single_file(manager, t, progress, tid, semaphore)
            progress.update(main_task, advance=1)

        await asyncio.gather(*[download_wrapper(i) for i in range(len(tasks))])

    return tasks


# ── 并发批量上传 ────────────────────────────────────────────────


async def batch_upload(
    manager: "AsyncApiManager",
    tasks: list[TransferTask],
    remote_parent_id: str,
    jobs: int = 4,
) -> list[TransferTask]:
    """并发上传多个文件，带 Rich 多行进度条。

    Parameters
    ----------
    manager : AsyncApiManager
        API 管理器实例
    tasks : list[TransferTask]
        上传任务列表
    remote_parent_id : str
        远程父目录 docid
    jobs : int
        最大并发数，默认 4

    Returns
    -------
    list[TransferTask]
        完成状态的任务列表
    """
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(jobs)

    with Progress(
        TextColumn("[bold magenta]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        main_task = progress.add_task(
            f"[bold]批量上传 ({len(tasks)} 文件, 并发 {jobs})",
            total=len(tasks),
        )

        file_progress_tasks: list[tuple[TaskID, int]] = []

        for i, t in enumerate(tasks):
            size = Path(t.local_path).stat().st_size if Path(t.local_path).exists() else 0
            tid = progress.add_task(f"[magenta]⬆ {Path(t.local_path).name}", total=size)
            file_progress_tasks.append((tid, size))

        async def upload_wrapper(idx: int):
            t = tasks[idx]
            tid = file_progress_tasks[idx][0]
            await _upload_single_file(manager, t, remote_parent_id, progress, tid, semaphore)
            progress.update(main_task, advance=1)

        await asyncio.gather(*[upload_wrapper(i) for i in range(len(tasks))])

    return tasks


# ── 便捷批量构建函数 ────────────────────────────────────────────


def build_download_tasks(
    manager: "AsyncApiManager",
    remote_path: str,
    local_dir: str,
    *,
    allow_recurse: bool = False,
) -> list[TransferTask]:
    """递归构建下载任务列表（同步准备阶段）。"""
    tasks: list[TransferTask] = []

    async def _collect():
        info = await manager.get_resource_info_by_path(remote_path.strip("/"))
        if info is None:
            return

        os.makedirs(local_dir, exist_ok=True)

        if info.size != -1:
            local_name = os.path.basename(remote_path)
            tasks.append(
                TransferTask(
                    remote_path=remote_path,
                    local_path=os.path.join(local_dir, local_name),
                    size=info.size,
                    docid=info.docid,
                )
            )
        elif allow_recurse:
            dirs, files = await manager.list_dir(info.docid, by="name")
            base_local = os.path.join(local_dir, os.path.basename(remote_path.rstrip("/")))
            os.makedirs(base_local, exist_ok=True)
            for d in dirs:
                sub_tasks = build_download_tasks(
                    manager,
                    remote_path + "/" + d.name,
                    base_local,
                    allow_recurse=True,
                )
                tasks.extend(sub_tasks)
            for f in files:
                sub_tasks = build_download_tasks(
                    manager,
                    remote_path + "/" + f.name,
                    base_local,
                    allow_recurse=False,
                )
                tasks.extend(sub_tasks)

    return tasks


def build_upload_tasks(
    local_path: str,
    remote_dir: str,
    *,
    allow_recurse: bool = False,
) -> list[TransferTask]:
    """递归构建上传任务列表（同步准备阶段）。"""
    tasks: list[TransferTask] = []
    local_path = os.path.normpath(local_path)
    remote_name = os.path.basename(os.path.abspath(local_path))
    remote_base = remote_dir.strip("/") + "/" + remote_name

    if os.path.isfile(local_path):
        tasks.append(
            TransferTask(
                remote_path=remote_base,
                local_path=local_path,
                size=os.path.getsize(local_path),
            )
        )
    elif allow_recurse:
        for entry in os.listdir(local_path):
            full_local = os.path.join(local_path, entry)
            full_remote = remote_base + "/" + entry
            if os.path.isfile(full_local):
                tasks.append(
                    TransferTask(
                        remote_path=full_remote,
                        local_path=full_local,
                        size=os.path.getsize(full_local),
                    )
                )
            elif os.path.isdir(full_local):
                tasks.extend(
                    build_upload_tasks(full_local, remote_dir + "/" + remote_name, allow_recurse=True)
                )
    return tasks
