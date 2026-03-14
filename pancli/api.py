"""Business API layer — ApiManager wrapping all BHPAN REST endpoints."""

from __future__ import annotations

import time
from typing import Any, Iterator

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


# ── ApiManager ──────────────────────────────────────────────────


class ApiManager:
    """北航网盘业务 API 抽象层。

    持有一个 ``httpx.Client`` 实例用于连接复用，
    内部管理 access_token 刷新逻辑。
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
        self._client = network.create_client()

        assert (password is not None and pubkey is not None) or encrypted is not None

        if cached_token and cached_expire:
            self._tokenid = cached_token
            self._expires = cached_expire
            self._check_token(use_request=True)
        else:
            self._check_token()

    def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        self._client.close()

    # ── Token 管理 ──────────────────────────────────────────────

    def _update_token(self) -> None:
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

    def _check_token(self, use_request: bool = False) -> None:
        if use_request:
            if time.time() > (self._expires - 60):
                self._update_token()
            else:
                try:
                    self.get_entrydoc()
                except network.ApiException as e:
                    if e.err is not None and e.err.get("code") == 401001001:
                        self._update_token()
                    else:
                        raise
        else:
            if time.time() > (self._expires - 60):
                self._update_token()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _post(self, path: str, body: dict[str, Any]) -> dict | None:
        return network.post_json(
            self._url(path), body, tokenid=self._tokenid, client=self._client
        )

    def _get(self, path: str) -> dict | None:
        return network.get_json(
            self._url(path), tokenid=self._tokenid, client=self._client
        )

    # ── 资源信息 ────────────────────────────────────────────────

    def get_resource_id(self, path: str) -> str | None:
        info = self.get_resource_info_by_path(path)
        return info.docid if info else None

    def get_resource_info_by_path(self, path: str) -> ResourceInfo | None:
        self._check_token()
        if not path:
            return None
        try:
            r = self._post("/file/getinfobypath", {"namepath": path})
        except network.ApiException as e:
            if e.err is not None and e.err.get("code") in (404006, 403024, 404002006):
                return None
            raise
        if r is None:
            return None
        return ResourceInfo.model_validate(r)

    def get_resource_path(self, docid: str) -> str:
        self._check_token()
        r = self._post("/file/convertpath", {"docid": docid})
        return r["namepath"]  # type: ignore[index]

    def get_entrydoc(self) -> list[dict]:
        self._check_token()
        r = self._get("/entry-doc-lib?type=user_doc_lib&sort=doc_lib_name&direction=asc")
        return r  # type: ignore[return-value]

    def list_root(self) -> list[dict]:
        r = self._get("/entry-doc-lib?sort=doc_lib_name&direction=asc")
        return r  # type: ignore[return-value]

    # ── 文件元信息 ──────────────────────────────────────────────

    def get_file_meta(self, file_id: str) -> FileMetaData:
        self._check_token()
        r = self._post("/file/metadata", {"docid": file_id})
        return FileMetaData.model_validate(r)

    # ── 文件标签 ────────────────────────────────────────────────

    def add_file_tag(self, file_id: str, tag: str) -> None:
        self._check_token()
        self._post("/file/addtag", {"docid": file_id, "tag": tag})

    def add_file_tags(self, file_id: str, tags: list[str]) -> None:
        self._check_token()
        self._post("/file/addtags", {"docid": file_id, "tags": tags})

    def delete_file_tag(self, file_id: str, tag: str) -> None:
        self._check_token()
        self._post("/file/deletetag", {"docid": file_id, "tag": tag})

    def get_file_tags(self, file_id: str) -> list[str]:
        self._check_token()
        r = self._post("/file/attribute", {"docid": file_id})
        return r["tags"]  # type: ignore[index]

    # ── 文件操作 ────────────────────────────────────────────────

    def delete_file(self, file_id: str) -> None:
        self._check_token()
        self._post("/file/delete", {"docid": file_id})

    def upload_file(
        self,
        parent_dir_id: str,
        name: str,
        content: bytes | Any,
        *,
        check_existence: bool = True,
        stream_len: int | None = None,
    ) -> str:
        """上传文件（≤5 GB），返回 docid。

        传入 ``content=file_stream`` + ``stream_len`` 启用流式上传。
        """
        self._check_token()

        edit_mode = False
        existing_file_id: str | None = None
        if check_existence:
            parent_dir = self.get_resource_path(parent_dir_id)
            existing_file_id = self.get_resource_id(parent_dir + "/" + name)
            edit_mode = existing_file_id is not None

        r = self._post(
            "/file/osbeginupload",
            {
                "docid": existing_file_id if edit_mode else parent_dir_id,
                "length": stream_len if stream_len is not None else len(content),
                "name": None if edit_mode else name,
                "reqmethod": "PUT",
            },
        )

        # 解析上传凭据
        headers: dict[str, str] = {}
        for header_str in r["authrequest"][2:]:  # type: ignore[index]
            parts = header_str.split(": ", 1)
            if len(parts) == 2:
                headers[parts[0]] = parts[1]

        network.put_file(
            r["authrequest"][1],  # type: ignore[index]
            headers,
            content,
            client=self._client,
        )

        # 完成上传
        self._post(
            "/file/osendupload",
            {"docid": r["docid"], "rev": r["rev"]},  # type: ignore[index]
        )
        return r["docid"]  # type: ignore[index]

    def download_file(self, file_id: str) -> bytes:
        """下载文件，返回全部 bytes。"""
        self._check_token()
        r = self._post(
            "/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"}
        )
        url = r["authrequest"][1]  # type: ignore[index]
        return network.get_file(url, client=self._client)

    def download_file_stream(self, file_id: str, chunk_size: int = 1024) -> Iterator[bytes]:
        """流式下载文件，yield 数据块。"""
        self._check_token()
        r = self._post(
            "/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"}
        )
        url = r["authrequest"][1]  # type: ignore[index]
        yield from network.stream_download(url, client=self._client, chunk_size=chunk_size)

    def rename_file(self, file_id: str, new_name: str, *, rename_on_dup: bool = False) -> str | None:
        self._check_token()
        r = self._post(
            "/file/rename",
            {"docid": file_id, "name": new_name, "ondup": 2 if rename_on_dup else 1},
        )
        return r["name"] if rename_on_dup else None  # type: ignore[index]

    def move_file(
        self,
        file_id: str,
        dest_dir_id: str,
        *,
        rename_on_dup: bool = False,
        overwrite_on_dup: bool = False,
    ) -> str | tuple[str, str]:
        self._check_token()
        ondup = 2 if rename_on_dup else (3 if overwrite_on_dup else 1)
        try:
            r = self._post(
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

    def copy_file(
        self,
        file_id: str,
        dest_dir_id: str,
        *,
        rename_on_dup: bool = False,
        overwrite_on_dup: bool = False,
    ) -> str | tuple[str, str]:
        self._check_token()
        ondup = 2 if rename_on_dup else (3 if overwrite_on_dup else 1)
        try:
            r = self._post(
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

    def create_dir(self, parent_dir_id: str, name: str) -> str:
        self._check_token()
        r = self._post("/dir/create", {"docid": parent_dir_id, "name": name})
        return r["docid"]  # type: ignore[index]

    def create_dirs(self, parent_dir_id: str, dirs: str) -> str:
        self._check_token()
        r = self._post("/dir/createmultileveldir", {"docid": parent_dir_id, "path": dirs})
        return r["docid"]  # type: ignore[index]

    def create_dirs_by_path(self, dirs: str) -> str:
        self._check_token()
        sp = dirs.strip("/").split("/")
        root_dir_id = self.get_resource_id(sp[0])
        if root_dir_id is None:
            raise InvalidRootException("root dir does not exist")
        if len(sp) == 1:
            return root_dir_id
        return self.create_dirs(root_dir_id, "/".join(sp[1:]))

    def delete_dir(self, dir_id: str) -> None:
        self._check_token()
        self._post("/dir/delete", {"docid": dir_id})

    def list_dir(
        self,
        dir_id: str,
        *,
        by: str | None = None,
        sort: str | None = None,
        with_attr: bool = False,
    ) -> tuple[list[dict], list[dict]]:
        """返回 (dirs, files)。"""
        self._check_token()
        d: dict[str, Any] = {
            "docid": dir_id,
            "attr": bool(with_attr),
        }
        if by is not None:
            d["by"] = by
        if sort is not None:
            d["sort"] = sort
        r = self._post("/dir/list", d)
        return r["dirs"], r["files"]  # type: ignore[index]

    # ── 外链管理 ────────────────────────────────────────────────

    def get_link(self, docid: str) -> LinkInfo | None:
        self._check_token()
        r = self._post("/link/getdetail", {"docid": docid})
        if r is None or r.get("link") == "":
            return None
        return LinkInfo.model_validate(r)

    def create_link(
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
        self._check_token()
        d: dict[str, Any] = {
            "docid": docid,
            "open": enable_pass,
            "limittimes": limit_times,
            "perm": perm_int,
        }
        if end_time is not None:
            d["endtime"] = end_time
        r = self._post("/link/open", d)
        if r is not None and r.get("result") == 0:
            return LinkInfo.model_validate(r)
        raise NeedReviewException()

    def modify_link(
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
        self._check_token()
        r = self._post(
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

    def delete_link(self, docid: str) -> None:
        self._check_token()
        self._post("/link/close", {"docid": docid})
