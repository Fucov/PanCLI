"""Async HTTP transport layer built on httpx."""

from __future__ import annotations

import ssl
import time
from typing import Any, AsyncIterator

import asyncio as _asyncio

import httpx

from .config import CERT_FILE, get_data_dir

# ── 异常 ────────────────────────────────────────────────────────


class ApiException(Exception):
    """后端返回非预期 HTTP 状态码时抛出。"""

    def __init__(self, err: dict | None, *args: object) -> None:
        super().__init__(*args)
        self.err = err


# ── SSL 证书补丁 ────────────────────────────────────────────────

_MISSING_CERT_PEM = """\
-----BEGIN CERTIFICATE-----
MIIDXzCCAkegAwIBAgILBAAAAAABIVhTCKIwDQYJKoZIhvcNAQELBQAwTDEgMB4G
A1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjMxEzARBgNVBAoTCkdsb2JhbFNp
Z24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMDkwMzE4MTAwMDAwWhcNMjkwMzE4
MTAwMDAwWjBMMSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEG
A1UEChMKR2xvYmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAMwldpB5BngiFvXAg7aEyiie/QV2EcWtiHL8
RgJDx7KKnQRfJMsuS+FggkbhUqsMgUdwbN1k0ev1LKMPgj0MK66X17YUhhB5uzsT
gHeMCOFJ0mpiLx9e+pZo34knlTifBtc+ycsmWQ1z3rDI6SYOgxXG71uL0gRgykmm
KPZpO/bLyCiR5Z2KYVc3rHQU3HTgOu5yLy6c+9C7v/U9AOEGM+iCK65TpjoWc4zd
QQ4gOsC0p6Hpsk+QLjJg6VfLuQSSaGjlOCZgdbKfd/+RFO+uIEn8rUAVSNECMWEZ
XriX7613t2Saer9fwRPvm2L7DWzgVGkWqQPabumDk3F2xmmFghcCAwEAAaNCMEAw
DgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFI/wS3+o
LkUkrk1Q+mOai97i3Ru8MA0GCSqGSIb3DQEBCwUAA4IBAQBLQNvAUKr+yAzv95ZU
RUm7lgAJQayzE4aGKAczymvmdLm6AC2upArT9fHxD4q/c2dKg8dEe3jgr25sbwMp
jjM5RcOO5LlXbKr8EpbsU8Yt5CRsuZRj+9xTaGdWPoO4zzUhw8lo/s7awlOqzJCK
6fBdRoyV3XpYKBovHd7NADdBj+1EbddTKJd+82cEHhXXipa0095MJ6RMG3NzdvQX
mcIfeg7jLQitChws/zyrVQ4PkX4268NXSb7hLi18YIvDQVETI53O9zJrlAGomecs
Mx86OyXShkDOOyyGeMlhLxS67ttVb9+E7gUJTb0o2HLO02JQZR7rkpeDMdmztcpH
WD9f
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEsDCCA5igAwIBAgIQd70OB0LV2enQSdd00CpvmjANBgkqhkiG9w0BAQsFADBM
MSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEGA1UEChMKR2xv
YmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjAeFw0yMDA3MjgwMDAwMDBaFw0y
OTAzMTgwMDAwMDBaMFMxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWdu
IG52LXNhMSkwJwYDVQQDEyBHbG9iYWxTaWduIEdDQyBSMyBEViBUTFMgQ0EgMjAy
MDCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKxnlJV/de+OpwyvCXAJ
IcxPCqkFPh1lttW2oljS3oUqPKq8qX6m7K0OVKaKG3GXi4CJ4fHVUgZYE6HRdjqj
hhnuHY6EBCBegcUFgPG0scB12Wi8BHm9zKjWxo3Y2bwhO8Fvr8R42pW0eINc6OTb
QXC0VWFCMVzpcqgz6X49KMZowAMFV6XqtItcG0cMS//9dOJs4oBlpuqX9INxMTGp
6EASAF9cnlAGy/RXkVS9nOLCCa7pCYV+WgDKLTF+OK2Vxw3RUJ/p8009lQeUARv2
UCcNNPCifYX1xIspvarkdjzLwzOdLahDdQbJON58zN4V+lMj0msg+c0KnywPIRp3
BMkCAwEAAaOCAYUwggGBMA4GA1UdDwEB/wQEAwIBhjAdBgNVHSUEFjAUBggrBgEF
BQcDAQYIKwYBBQUHAwIwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFgQUDZjA
c3+rvb3ZR0tJrQpKDKw+x3wwHwYDVR0jBBgwFoAUj/BLf6guRSSuTVD6Y5qL3uLd
G7wwewYIKwYBBQUHAQEEbzBtMC4GCCsGAQUFBzABhiJodHRwOi8vb2NzcDIuZ2xv
YmFsc2lnbi5jb20vcm9vdHIzMDsGCCsGAQUFBzAChi9odHRwOi8vc2VjdXJlLmds
b2JhbHNpZ24uY29tL2NhY2VydC9yb290LXIzLmNydDA2BgNVHR8ELzAtMCugKaAn
hiVodHRwOi8vY3JsLmdsb2JhbHNpZ24uY29tL3Jvb3QtcjMuY3JsMEcGA1UdIARA
MD4wPAYEVR0gADA0MDIGCCsGAQUFBwIBFiZodHRwczovL3d3dy5nbG9iYWxzaWdu
LmNvbS9yZXBvc2l0b3J5LzANBgkqhkiG9w0BAQsFAAOCAQEAy8j/c550ea86oCkf
r2W+ptTCYe6iVzvo7H0V1vUEADJOWelTv07Obf+YkEatdN1Jg09ctgSNv2h+LMTk
KRZdAXmsE3N5ve+z1Oa9kuiu7284LjeS09zHJQB4DJJJkvtIbjL/ylMK1fbMHhAW
i0O194TWvH3XWZGXZ6ByxTUIv1+kAIql/Mt29PmKraTT5jrzcVzQ5A9jw16yysuR
XRrLODlkS1hyBjsfyTNZrmL1h117IFgntBA5SQNVl9ckedq5r4RSAU85jV8XK5UL
REjRZt2I6M9Po9QL7guFLu4sPFJpwR1sPJvubS2THeo7SxYoNDtdyBHs7euaGcMa
D/fayQ==
-----END CERTIFICATE-----
"""

_MAX_RETRIES = 10
_RETRY_BACKOFF = 1


def _ensure_cert() -> str:
    """确保补丁证书文件存在。"""
    get_data_dir()
    if not CERT_FILE.exists():
        CERT_FILE.write_text(_MISSING_CERT_PEM)
    return str(CERT_FILE)


def _build_ssl_context() -> ssl.SSLContext:
    """构建包含补丁证书的 SSL 上下文。"""
    cert_path = _ensure_cert()
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cert_path)
    return ctx


# ── 同步 Client（仅供 auth.py 使用）──────────────────────────────


def create_client(**kwargs: Any) -> httpx.Client:
    """创建同步 httpx.Client（用于 OAuth2 登录流程）。"""
    return httpx.Client(verify=_build_ssl_context(), timeout=120.0, **kwargs)


def post_json(
    url: str,
    json_obj: Any,
    *,
    tokenid: str | None = None,
    client: httpx.Client | None = None,
) -> dict | None:
    """同步 POST JSON（仅用于 auth.py 登录流程）。"""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if tokenid is not None:
        headers["Authorization"] = f"Bearer {tokenid}"

    _client = client or create_client()
    own_client = client is None

    try:
        for retry in range(_MAX_RETRIES):
            try:
                r = _client.post(url, headers=headers, json=json_obj)
                if r.status_code != 503:
                    break
                time.sleep(_RETRY_BACKOFF)
            except httpx.ConnectError:
                time.sleep(_RETRY_BACKOFF)

        if r.status_code not in (200, 201):
            err = None
            try:
                err = r.json()
            except Exception:
                pass
            raise ApiException(err, f"api returned HTTP {r.status_code}\n{r.text}")

        if r.text == "":
            return None
        return r.json()
    finally:
        if own_client:
            _client.close()


# ── 异步 Client ─────────────────────────────────────────────────


# 超时配置：AnyShare OSS 冷启动可能需要较长时间
_ASYNC_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)


def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """创建异步 httpx.AsyncClient。"""
    return httpx.AsyncClient(verify=_build_ssl_context(), timeout=_ASYNC_TIMEOUT, **kwargs)


async def async_post_json(
    url: str,
    json_obj: Any,
    *,
    tokenid: str | None = None,
    client: httpx.AsyncClient,
) -> dict | None:
    """异步 POST JSON，自动重试 503 和连接错误。"""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if tokenid is not None:
        headers["Authorization"] = f"Bearer {tokenid}"

    r: httpx.Response | None = None
    for retry in range(_MAX_RETRIES):
        try:
            r = await client.post(url, headers=headers, json=json_obj)
            if r.status_code != 503:
                break
            import asyncio
            await asyncio.sleep(_RETRY_BACKOFF)
        except httpx.ConnectError:
            import asyncio
            await asyncio.sleep(_RETRY_BACKOFF)

    assert r is not None
    if r.status_code not in (200, 201):
        err = None
        try:
            err = r.json()
        except Exception:
            pass
        raise ApiException(err, f"api returned HTTP {r.status_code}\n{r.text}")

    if r.text == "":
        return None
    return r.json()


async def async_get_json(
    url: str,
    *,
    tokenid: str | None = None,
    client: httpx.AsyncClient,
) -> dict | None:
    """异步 GET + JSON 解析。"""
    headers: dict[str, str] = {}
    if tokenid is not None:
        headers["Authorization"] = f"Bearer {tokenid}"

    r: httpx.Response | None = None
    for retry in range(_MAX_RETRIES):
        r = await client.get(url, headers=headers)
        if r.status_code != 503:
            break
        import asyncio
        await asyncio.sleep(_RETRY_BACKOFF)

    assert r is not None
    if r.status_code != 200:
        err = None
        try:
            err = r.json()
        except Exception:
            pass
        raise ApiException(err, f"api returned HTTP {r.status_code}\n{r.text}")

    if r.text == "":
        return None
    return r.json()


async def async_put_file(
    url: str,
    headers: dict[str, str],
    content: bytes | Any,
    *,
    client: httpx.AsyncClient,
) -> None:
    """异步 PUT 文件内容（bytes 或流式对象）。"""
    for retry in range(_MAX_RETRIES):
        try:
            await client.put(url, headers=headers, content=content)
            return
        except httpx.ConnectError:
            import asyncio
            await asyncio.sleep(_RETRY_BACKOFF)


async def async_get_file(
    url: str,
    *,
    client: httpx.AsyncClient,
) -> bytes:
    """异步 GET 文件并返回全部 bytes。"""
    r = await client.get(url)
    return r.content


async def async_stream_download(
    url: str,
    *,
    client: httpx.AsyncClient,
    chunk_size: int = 8192,
    resume_from: int = 0,
    max_retries: int = 3,
) -> AsyncIterator[bytes]:
    """异步流式下载，支持断点续传 + 500/502 重试。"""
    headers: dict[str, str] = {}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"

    for attempt in range(max_retries):
        try:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (500, 502, 503):
                    if attempt < max_retries - 1:
                        await _asyncio.sleep(2 * (attempt + 1))
                        continue
                    response.raise_for_status()
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size):
                    yield chunk
                return  # 成功完成
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            if attempt < max_retries - 1:
                await _asyncio.sleep(2 * (attempt + 1))
            else:
                raise exc
