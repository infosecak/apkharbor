"""Small synchronous HTTPS transport. Never include credentials in errors."""

import gzip
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler


class APKHarborError(Exception):
    pass


def https_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise APKHarborError("A valid HTTPS URL without embedded credentials is required.")
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        # Do not forward authentication material across hosts.
        if urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            raise APKHarborError("Refused a redirect to a different server.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def google_download_host(url):
    parsed = urlsplit(https_url(url))
    roots = ("google.com", "googleapis.com", "googleusercontent.com", "gvt1.com", "gvt2.com")
    return parsed.port in (None, 443) and any(
        parsed.hostname == root or parsed.hostname.endswith("." + root) for root in roots
    )


class DownloadRedirect(SafeRedirect):
    """Permit Google's CDN handoff for downloads, without forwarding secrets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        if urlsplit(req.full_url).netloc == urlsplit(newurl).netloc:
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        if req.get_method() != "GET" or not google_download_host(req.full_url) or not google_download_host(newurl):
            raise APKHarborError("Refused a download redirect outside Google's HTTPS download servers.")
        redirected = HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # Start the new origin with only the transfer header. In particular,
            # never forward Cookie, Authorization, X-Api-Key or device headers.
            for key in list(redirected.headers) + list(redirected.unredirected_hdrs):
                redirected.remove_header(key)
            redirected.add_header("Accept-Encoding", "identity")
        return redirected


def open_url(url, *, headers=None, data=None, timeout=30, tls_context=None, download_redirects=False):
    https_url(url)
    try:
        handlers = [DownloadRedirect() if download_redirects else SafeRedirect()]
        if tls_context is not None:
            handlers.append(HTTPSHandler(context=tls_context))
        return build_opener(*handlers).open(
            Request(url, data=data, headers=headers or {}), timeout=timeout
        )
    except HTTPError:
        raise
    except (URLError, OSError, ValueError) as exc:
        raise APKHarborError("Could not reach the server over HTTPS. Check connectivity.") from exc


@dataclass
class Response:
    status_code: int
    content: bytes

    def json(self):
        try:
            return json.loads(self.content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise APKHarborError("The server returned invalid JSON.") from exc


def request(url, *, headers=None, content=None, payload=None, timeout=30, tls_context=None):
    headers = dict(headers or {})
    if payload is not None:
        content = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif isinstance(content, str):
        content = content.encode()
    try:
        response = open_url(url, headers=headers, data=content, timeout=timeout, tls_context=tls_context)
    except HTTPError as response:
        return Response(response.code, b"")
    with response:
        body = response.read(16 * 1024 * 1024 + 1)
        if len(body) > 16 * 1024 * 1024:
            raise APKHarborError("API response exceeded 16 MiB.")
        if response.headers.get("Content-Encoding") == "gzip":
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024:
                raise APKHarborError("Decoded API response exceeded 16 MiB.")
        return Response(response.status, body)


def get(url, **kwargs):
    return request(url, **kwargs)


def post(url, **kwargs):
    return request(url, **kwargs)
