"""Async ApiManager — 全异步的网盘底层 API 请求层。

不包含任何 UI 打印逻辑，仅负责数据获取与返回。
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator

import httpx

from . import auth, network
from .models import FileMetaData, LinkInfo, ResourceInfo

# ── 异常 ────────────────────────────────────────────────────────


class ApiManagerException(Exception):
    pass


class WrongPasswordException(ApiManagerException):
    pass


class InvalidRootException(ApiManagerException):
    pass


class NeedReviewException(ApiManagerException):
    pass


class MoveToChildDirectoryException(ApiManagerException):
    pass


# ── AsyncApiManager ─────────────────────────────────────────────


class AsyncApiManager:
    """全异步的 AnyShare 业务 API 抽象层。

    所有业务方法均为 ``async def``。
    内部管理 ``httpx.AsyncClient`` 和 access_token 刷新。
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str | None,
        pubkey: str,
        *,
        encrypted: str | None = None,
        cached_token: str | None = None,
        cached_expire: float | None = None,
    ) -> None:
        self.host = host
        self.base_url = f"https://{host}:443/api/efast/v1"
        self._pubkey = pubkey
        self._password = password
        self._username = username
        self._encrypted = encrypted

        self._tokenid: str = ""
        self._expires: float = 0.0
        self._client: httpx.AsyncClient = network.create_async_client()

        assert (password is not None and pubkey is not None) or encrypted is not None

        if cached_token and cached_expire:
            self._tokenid = cached_token
            self._expires = cached_expire

    async def ensure_token(self) -> None:
        """首次或 Token 过期时刷新。公开供外部在登录后调用。"""
        if self._tokenid and time.time() < (self._expires - 60):
            # 尝试验证已有 token
            try:
                await self.get_entrydoc()
                return
            except network.ApiException as e:
                if e.err is not None and e.err.get("code") == 401001001:
                    pass  # token 无效，继续刷新
                else:
                    raise
        self._update_token_sync()

    def _update_token_sync(self) -> None:
        """同步调用 auth 模块获取 token（OAuth2 登录流程本身不适合异步化）。"""
        if self._encrypted is None:
            self._encrypted = auth.rsa_encrypt(self._password, self._pubkey)  # type: ignore[arg-type]
        try:
            access_token = auth.get_access_token(
                f"https://{self.host}:443/",
                self._username,
                self._encrypted,
            )
        except network.ApiException as e:
            if e.err is not None and e.err.get("code") == 401001003:
                raise WrongPasswordException(str(e)) from e
            raise
        self._tokenid = access_token
        self._expires = time.time() + 3600

    async def _check_token(self) -> None:
        if time.time() > (self._expires - 60):
            self._update_token_sync()

    async def close(self) -> None:
        """关闭底层异步连接池。"""
        await self._client.aclose()

    # ── 内部 HTTP 方法 ──────────────────────────────────────────

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    async def _post(self, path: str, body: dict[str, Any]) -> dict | None:
        return await network.async_post_json(
            self._url(path), body, tokenid=self._tokenid, client=self._client
        )

    async def _get(self, path: str) -> dict | None:
        return await network.async_get_json(
            self._url(path), tokenid=self._tokenid, client=self._client
        )

    # ── 资源信息 ────────────────────────────────────────────────

    async def get_resource_id(self, path: str) -> str | None:
        info = await self.get_resource_info_by_path(path)
        return info.docid if info else None

    async def get_resource_info_by_path(self, path: str) -> ResourceInfo | None:
        await self._check_token()
        if not path:
            return None
        try:
            r = await self._post("/file/getinfobypath", {"namepath": path})
        except network.ApiException as e:
            if e.err is not None and e.err.get("code") in (404006, 403024, 404002006):
                return None
            raise
        if r is None:
            return None
        return ResourceInfo.model_validate(r)

    async def get_resource_path(self, docid: str) -> str:
        await self._check_token()
        r = await self._post("/file/convertpath", {"docid": docid})
        return r["namepath"]  # type: ignore[index]

    async def get_entrydoc(self) -> list[dict]:
        await self._check_token()
        r = await self._get("/entry-doc-lib?type=user_doc_lib&sort=doc_lib_name&direction=asc")
        return r  # type: ignore[return-value]

    async def list_root(self) -> list[dict]:
        r = await self._get("/entry-doc-lib?sort=doc_lib_name&direction=asc")
        return r  # type: ignore[return-value]

    # ── 文件元信息 ──────────────────────────────────────────────

    async def get_file_meta(self, file_id: str) -> FileMetaData:
        await self._check_token()
        r = await self._post("/file/metadata", {"docid": file_id})
        return FileMetaData.model_validate(r)

    # ── 文件标签 ────────────────────────────────────────────────

    async def add_file_tag(self, file_id: str, tag: str) -> None:
        await self._check_token()
        await self._post("/file/addtag", {"docid": file_id, "tag": tag})

    async def get_file_tags(self, file_id: str) -> list[str]:
        await self._check_token()
        r = await self._post("/file/attribute", {"docid": file_id})
        return r["tags"]  # type: ignore[index]

    async def delete_file_tag(self, file_id: str, tag: str) -> None:
        await self._check_token()
        await self._post("/file/deletetag", {"docid": file_id, "tag": tag})

    # ── 文件操作 ────────────────────────────────────────────────

    async def delete_file(self, file_id: str) -> None:
        await self._check_token()
        await self._post("/file/delete", {"docid": file_id})

    async def upload_file(
        self,
        parent_dir_id: str,
        name: str,
        content: bytes | Any,
        *,
        check_existence: bool = True,
        stream_len: int | None = None,
    ) -> str:
        """上传文件，返回 docid。"""
        await self._check_token()

        edit_mode = False
        existing_file_id: str | None = None
        if check_existence:
            parent_dir = await self.get_resource_path(parent_dir_id)
            existing_file_id = await self.get_resource_id(parent_dir + "/" + name)
            edit_mode = existing_file_id is not None

        r = await self._post(
            "/file/osbeginupload",
            {
                "docid": existing_file_id if edit_mode else parent_dir_id,
                "length": stream_len if stream_len is not None else len(content),
                "name": None if edit_mode else name,
                "reqmethod": "PUT",
            },
        )

        headers: dict[str, str] = {}
        for header_str in r["authrequest"][2:]:  # type: ignore[index]
            parts = header_str.split(": ", 1)
            if len(parts) == 2:
                headers[parts[0]] = parts[1]

        await network.async_put_file(
            r["authrequest"][1],  # type: ignore[index]
            headers,
            content,
            client=self._client,
        )

        await self._post(
            "/file/osendupload",
            {"docid": r["docid"], "rev": r["rev"]},  # type: ignore[index]
        )
        return r["docid"]  # type: ignore[index]

    async def get_download_url(self, file_id: str) -> str:
        """获取文件下载 URL（供断点续传等场景使用）。"""
        await self._check_token()
        r = await self._post(
            "/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"}
        )
        return r["authrequest"][1]  # type: ignore[index]

    async def download_file(self, file_id: str) -> bytes:
        """下载文件，返回全部 bytes。"""
        url = await self.get_download_url(file_id)
        return await network.async_get_file(url, client=self._client)

    async def download_file_stream(
        self, file_id: str, chunk_size: int = 8192, resume_from: int = 0
    ) -> AsyncIterator[bytes]:
        """流式下载文件，yield 数据块。支持断点续传。"""
        url = await self.get_download_url(file_id)
        async for chunk in network.async_stream_download(
            url, client=self._client, chunk_size=chunk_size, resume_from=resume_from
        ):
            yield chunk

    async def rename_file(
        self, file_id: str, new_name: str, *, rename_on_dup: bool = False
    ) -> str | None:
        await self._check_token()
        r = await self._post(
            "/file/rename",
            {"docid": file_id, "name": new_name, "ondup": 2 if rename_on_dup else 1},
        )
        return r["name"] if rename_on_dup else None  # type: ignore[index]

    async def move_file(
        self,
        file_id: str,
        dest_dir_id: str,
        *,
        rename_on_dup: bool = False,
        overwrite_on_dup: bool = False,
    ) -> str | tuple[str, str]:
        await self._check_token()
        ondup = 2 if rename_on_dup else (3 if overwrite_on_dup else 1)
        try:
            r = await self._post(
                "/file/move",
                {"docid": file_id, "destparent": dest_dir_id, "ondup": ondup},
            )
        except network.ApiException as e:
            if e.err is not None and e.err.get("errcode") == 403019:
                raise MoveToChildDirectoryException() from e
            raise
        if rename_on_dup:
            return r["docid"], r["name"]  # type: ignore[index]
        return r["docid"]  # type: ignore[index]

    async def copy_file(
        self,
        file_id: str,
        dest_dir_id: str,
        *,
        rename_on_dup: bool = False,
        overwrite_on_dup: bool = False,
    ) -> str | tuple[str, str]:
        await self._check_token()
        ondup = 2 if rename_on_dup else (3 if overwrite_on_dup else 1)
        try:
            r = await self._post(
                "/file/copy",
                {"docid": file_id, "destparent": dest_dir_id, "ondup": ondup},
            )
        except network.ApiException as e:
            if e.err is not None and e.err.get("errcode") == 403019:
                raise MoveToChildDirectoryException() from e
            raise
        if rename_on_dup:
            return r["docid"], r["name"]  # type: ignore[index]
        return r["docid"]  # type: ignore[index]

    # ── 目录操作 ────────────────────────────────────────────────

    async def create_dir(self, parent_dir_id: str, name: str) -> str:
        await self._check_token()
        r = await self._post("/dir/create", {"docid": parent_dir_id, "name": name})
        return r["docid"]  # type: ignore[index]

    async def create_dirs(self, parent_dir_id: str, dirs: str) -> str:
        await self._check_token()
        r = await self._post(
            "/dir/createmultileveldir", {"docid": parent_dir_id, "path": dirs}
        )
        return r["docid"]  # type: ignore[index]

    async def create_dirs_by_path(self, dirs: str) -> str:
        await self._check_token()
        sp = dirs.strip("/").split("/")
        root_dir_id = await self.get_resource_id(sp[0])
        if root_dir_id is None:
            raise InvalidRootException("root dir does not exist")
        if len(sp) == 1:
            return root_dir_id
        return await self.create_dirs(root_dir_id, "/".join(sp[1:]))

    async def delete_dir(self, dir_id: str) -> None:
        await self._check_token()
        await self._post("/dir/delete", {"docid": dir_id})

    async def list_dir(
        self,
        dir_id: str,
        *,
        by: str | None = None,
        sort: str | None = None,
        with_attr: bool = False,
    ) -> tuple[list[dict], list[dict]]:
        """返回 (dirs, files)。"""
        await self._check_token()
        d: dict[str, Any] = {"docid": dir_id, "attr": bool(with_attr)}
        if by is not None:
            d["by"] = by
        if sort is not None:
            d["sort"] = sort
        r = await self._post("/dir/list", d)
        return r["dirs"], r["files"]  # type: ignore[index]

    # ── 外链管理 ────────────────────────────────────────────────

    async def get_link(self, docid: str) -> LinkInfo | None:
        await self._check_token()
        r = await self._post("/link/getdetail", {"docid": docid})
        if r is None or r.get("link") == "":
            return None
        return LinkInfo.model_validate(r)

    async def create_link(
        self,
        docid: str,
        end_time: int | None = None,
        limit_times: int = -1,
        *,
        enable_pass: bool = False,
        allow_view: bool = True,
        allow_download: bool = True,
        allow_upload: bool = False,
    ) -> LinkInfo:
        if allow_download:
            allow_view = True
        perm_int = 1 * allow_view + 2 * allow_download + 4 * allow_upload
        await self._check_token()
        d: dict[str, Any] = {
            "docid": docid,
            "open": enable_pass,
            "limittimes": limit_times,
            "perm": perm_int,
        }
        if end_time is not None:
            d["endtime"] = end_time
        r = await self._post("/link/open", d)
        if r is not None and r.get("result") == 0:
            return LinkInfo.model_validate(r)
        raise NeedReviewException()

    async def modify_link(
        self,
        docid: str,
        end_time: int,
        limit_times: int = -1,
        *,
        enable_pass: bool = False,
        allow_view: bool = True,
        allow_download: bool = True,
        allow_upload: bool = False,
    ) -> LinkInfo:
        if allow_download:
            allow_view = True
        perm_int = 1 * allow_view + 2 * allow_download + 4 * allow_upload
        await self._check_token()
        r = await self._post(
            "/link/set",
            {
                "docid": docid,
                "open": enable_pass,
                "limittimes": limit_times,
                "endtime": end_time,
                "perm": perm_int,
            },
        )
        if r is not None and r.get("result") == 0:
            return LinkInfo.model_validate(r)
        raise NeedReviewException()

    async def delete_link(self, docid: str) -> None:
        await self._check_token()
        await self._post("/link/close", {"docid": docid})
