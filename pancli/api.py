"""Business API layer — Full AsyncApiManager wrapping all BHPAN REST endpoints."""

from __future__ import annotations

import time
from typing import Any, AsyncIterator

from . import auth, network
from .models import FileMetaData, LinkInfo, ResourceInfo, SearchResult, DirEntry

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


# ── AsyncApiManager ────────────────────────────────────────────────────────────


class AsyncApiManager:
    """北航网盘全异步业务 API 抽象层。

    持有一个 ``httpx.AsyncClient`` 实例用于连接复用，
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
        self._client: network.httpx.AsyncClient = network.create_async_client()

        assert (password is not None and pubkey is not None) or encrypted is not None

        if cached_token and cached_expire:
            self._tokenid = cached_token
            self._expires = cached_expire

    async def initialize(self) -> None:
        """异步初始化：检查 token 并在需要时刷新。"""
        await self._check_token(use_request=True)

    async def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        await self._client.aclose()

    # ── Token 管理 ──────────────────────────────────────────────

    def _encrypt_password(self) -> str:
        """同步加密密码（内部调用，登录时使用）。"""
        if self._encrypted is None and self._password is not None:
            self._encrypted = auth.rsa_encrypt(self._password, self._pubkey)
        return self._encrypted or ""

    async def _update_token(self) -> None:
        """异步更新 access token。"""
        enc_pass = self._encrypt_password()
        try:
            access_token = await self._async_get_access_token(
                f"https://{self.host}:443/",
                self._username,
                enc_pass,
            )
        except network.ApiException as e:
            if e.err is not None and e.err.get("code") == 401001003:
                raise WrongPasswordException(str(e)) from e
            raise
        self._tokenid = access_token
        self._expires = time.time() + 3600

    async def _check_token(self, use_request: bool = False) -> None:
        """检查 token 有效性，必要时自动刷新。"""
        if use_request:
            if time.time() > (self._expires - 60):
                await self._update_token()
            else:
                try:
                    await self.get_entrydoc()
                except network.ApiException as e:
                    if e.err is not None and e.err.get("code") == 401001001:
                        await self._update_token()
                    else:
                        raise
        else:
            if time.time() > (self._expires - 60):
                await self._update_token()

    async def _async_get_access_token(
        self,
        base_url: str,
        username: str,
        encrypted_password: str,
    ) -> str:
        """异步执行 OAuth2 登录流程。"""
        import base64
        import re
        import urllib.parse

        _CLIENT_ID = "0f4bc444-d39a-4945-84a3-023d1f439148"
        _BASIC_AUTH = "Basic MGY0YmM0NDQtZDM5YS00OTQ1LTg0YTMtMDIzZDFmNDM5MTQ4OnVOaVU0V0ZUd1FEfjE4T2JHMkU1M2dqN3ot"

        base_url = base_url.rstrip("/")
        state = urllib.parse.quote(base64.b64encode(b'{"windowId":3}'))

        client = network.create_client(follow_redirects=True)
        try:
            auth_url = (
                f"{base_url}/oauth2/auth?"
                f"audience=&client_id={_CLIENT_ID}"
                f"&redirect_uri=anyshare%3A%2F%2Foauth2%2Flogin%2Fcallback"
                f"&response_type=code&state={state}"
                f"&scope=offline+openid+all&lang=zh-cn"
                f"&udids=00-50-56-C0-00-01"
            )
            r = client.get(auth_url)

            challenge = re.search(r'"challenge":"(.*?)"', r.text)
            csrf = re.search(r'"csrftoken":"(.*?)"', r.text)
            if not challenge or not csrf:
                raise RuntimeError("无法从登录页面提取 challenge / csrftoken")
            challenge_val, csrf_token = challenge.group(1), csrf.group(1)

            signin_body = {
                "_csrf": csrf_token,
                "challenge": challenge_val,
                "account": username,
                "password": encrypted_password,
                "vcode": {"id": "", "content": ""},
                "dualfactorauthinfo": {
                    "validcode": {"vcode": ""},
                    "OTP": {"OTP": ""},
                },
                "remember": False,
                "device": {
                    "name": "RichClient",
                    "description": "RichClient for windows",
                    "client_type": "windows",
                    "udids": ["00-50-56-C0-00-01"],
                },
            }
            signin_resp = network.post_json(
                f"{base_url}/oauth2/signin",
                signin_body,
                client=client,
            )

            redirect_url = signin_resp["redirect"]
            while True:
                resp = client.get(redirect_url, follow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308):
                    new_url = resp.headers.get("Location", "")
                    if "anyshare://" in new_url:
                        location = new_url
                        break
                    redirect_url = new_url
                else:
                    location = resp.headers.get("Location", "")
                    break

            m = re.search(r"code=([^&]+)", location)
            if not m:
                raise RuntimeError(f"无法从回调 URL 中提取 code: {location}")
            code = m.group(1)

            boundary = "----WebKitFormBoundarywPAfbB36kbRTzgzy"
            token_body = (
                f"------WebKitFormBoundarywPAfbB36kbRTzgzy\r\n"
                f'Content-Disposition: form-data; name="grant_type"\r\n\r\n'
                f"authorization_code\r\n"
                f"------WebKitFormBoundarywPAfbB36kbRTzgzy\r\n"
                f'Content-Disposition: form-data; name="code"\r\n\r\n'
                f"{code}\r\n"
                f"------WebKitFormBoundarywPAfbB36kbRTzgzy\r\n"
                f'Content-Disposition: form-data; name="redirect_uri"\r\n\r\n'
                f"anyshare://oauth2/login/callback\r\n"
                f"------WebKitFormBoundarywPAfbB36kbRTzgzy--"
            )
            token_resp = client.post(
                f"{base_url}/oauth2/token",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Authorization": _BASIC_AUTH,
                },
                content=token_body.encode(),
            )
            return token_resp.json()["access_token"]
        finally:
            client.close()

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
        return r["namepath"]

    async def get_entrydoc(self) -> list[dict]:
        await self._check_token()
        r = await self._get("/entry-doc-lib?type=user_doc_lib&sort=doc_lib_name&direction=asc")
        return r or []

    async def list_root(self) -> list[dict]:
        r = await self._get("/entry-doc-lib?sort=doc_lib_name&direction=asc")
        return r or []

    # ── 文件元信息 ──────────────────────────────────────────────

    async def get_file_meta(self, file_id: str) -> FileMetaData:
        await self._check_token()
        r = await self._post("/file/metadata", {"docid": file_id})
        return FileMetaData.model_validate(r)

    # ── 文件标签 ────────────────────────────────────────────────

    async def add_file_tag(self, file_id: str, tag: str) -> None:
        await self._check_token()
        await self._post("/file/addtag", {"docid": file_id, "tag": tag})

    async def add_file_tags(self, file_id: str, tags: list[str]) -> None:
        await self._check_token()
        await self._post("/file/addtags", {"docid": file_id, "tags": tags})

    async def delete_file_tag(self, file_id: str, tag: str) -> None:
        await self._check_token()
        await self._post("/file/deletetag", {"docid": file_id, "tag": tag})

    async def get_file_tags(self, file_id: str) -> list[str]:
        await self._check_token()
        r = await self._post("/file/attribute", {"docid": file_id})
        return r["tags"]

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
        """异步上传文件（≤5 GB），返回 docid。

        传入 ``content=file_stream`` + ``stream_len`` 启用流式上传。
        """
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
        for header_str in r["authrequest"][2:]:
            parts = header_str.split(": ", 1)
            if len(parts) == 2:
                headers[parts[0]] = parts[1]

        await network.async_put_file(
            r["authrequest"][1],
            headers,
            content,
            client=self._client,
        )

        await self._post(
            "/file/osendupload",
            {"docid": r["docid"], "rev": r["rev"]},
        )
        return r["docid"]

    async def download_file(self, file_id: str) -> bytes:
        """异步下载文件，返回全部 bytes。"""
        await self._check_token()
        r = await self._post(
            "/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"}
        )
        url = r["authrequest"][1]
        return await network.async_get_file(url, client=self._client)

    async def get_download_url(self, file_id: str) -> tuple[str, int]:
        """获取下载 URL 和文件大小。"""
        await self._check_token()
        r = await self._post(
            "/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"}
        )
        return r["authrequest"][1], int(r.get("length", 0))

    async def download_file_stream(
        self, file_id: str, *, chunk_size: int = 65536
    ) -> AsyncIterator[bytes]:
        """异步流式下载文件，yield 数据块。"""
        await self._check_token()
        r = await self._post(
            "/file/osdownload", {"docid": file_id, "authtype": "QUERY_STRING"}
        )
        url = r["authrequest"][1]
        async for chunk in network.async_stream_download(
            url, client=self._client, chunk_size=chunk_size
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
        return r["name"] if rename_on_dup else None

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
            return r["docid"], r["name"]
        return r["docid"]

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
            return r["docid"], r["name"]
        return r["docid"]

    # ── 目录操作 ────────────────────────────────────────────────

    async def create_dir(self, parent_dir_id: str, name: str) -> str:
        await self._check_token()
        r = await self._post("/dir/create", {"docid": parent_dir_id, "name": name})
        return r["docid"]

    async def create_dirs(self, parent_dir_id: str, dirs: str) -> str:
        await self._check_token()
        r = await self._post("/dir/createmultileveldir", {"docid": parent_dir_id, "path": dirs})
        return r["docid"]

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
    ) -> tuple[list[DirEntry], list[DirEntry]]:
        """异步列目录，返回 (dirs, files)。"""
        await self._check_token()
        d: dict[str, Any] = {
            "docid": dir_id,
            "attr": bool(with_attr),
        }
        if by is not None:
            d["by"] = by
        if sort is not None:
            d["sort"] = sort
        r = await self._post("/dir/list", d)
        dirs = [DirEntry.from_dict(d, is_dir=True) for d in r.get("dirs", [])]
        files = [DirEntry.from_dict(f, is_dir=False) for f in r.get("files", [])]
        return dirs, files

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

    # ── 搜索功能（新增 find 命令核心）──────────────────────────────

    async def search_recursive(
        self,
        dir_id: str,
        keyword: str,
        *,
        max_depth: int = 3,
        current_depth: int = 0,
    ) -> list[SearchResult]:
        """递归搜索目录中包含关键词的文件/文件夹。

        Parameters
        ----------
        dir_id : str
            要搜索的目录 docid
        keyword : str
            搜索关键词
        max_depth : int
            最大递归深度，防止风控
        current_depth : int
            当前递归深度

        Returns
        -------
        list[SearchResult]
            匹配结果列表
        """
        results: list[SearchResult] = []
        if current_depth >= max_depth:
            return results

        try:
            dirs, files = await self.list_dir(dir_id, by="name")
        except Exception:
            return results

        for d in dirs:
            if keyword.lower() in d.name.lower():
                results.append(
                    SearchResult(
                        path=f"{d.name}/",
                        name=d.name,
                        size=d.size,
                        modified=d.modified,
                        is_dir=True,
                    )
                )
            sub_results = await self.search_recursive(
                d.docid, keyword, max_depth=max_depth, current_depth=current_depth + 1
            )
            results.extend(sub_results)

        for f in files:
            if keyword.lower() in f.name.lower():
                results.append(
                    SearchResult(
                        path=f.name,
                        name=f.name,
                        size=f.size,
                        modified=f.modified,
                        is_dir=False,
                    )
                )

        return results

    async def search(
        self,
        root_path: str = "/",
        keyword: str = "",
        *,
        max_depth: int = 3,
    ) -> list[SearchResult]:
        """在指定路径下搜索文件。

        Parameters
        ----------
        root_path : str
            搜索根路径（如 "home" 或 "/home"）
        keyword : str
            搜索关键词
        max_depth : int
            最大递归深度

        Returns
        -------
        list[SearchResult]
            匹配结果列表
        """
        root_id = await self.get_resource_id(root_path)
        if root_id is None:
            return []
        return await self.search_recursive(root_id, keyword, max_depth=max_depth)
