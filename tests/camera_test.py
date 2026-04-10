from __future__ import annotations

import httpx

from camera import MjpegCamera


def _make_camera(transport: httpx.MockTransport) -> MjpegCamera:
    cam = MjpegCamera.__new__(MjpegCamera)
    cam._http = httpx.Client(transport=transport)
    cam._host_snapshot = "http://cam/snapshot"
    return cam


def test_fetch_raw_snapshot_accepts_jpeg_with_charset_parameter() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"jpegbytes", headers={"Content-Type": "image/jpeg; charset=binary"})

    bio = _make_camera(httpx.MockTransport(handler))._fetch_raw_snapshot()
    assert bio.getvalue() == b"jpegbytes"


def test_fetch_raw_snapshot_accepts_image_jpg_alias() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"jpegbytes", headers={"Content-Type": "image/jpg"})

    bio = _make_camera(httpx.MockTransport(handler))._fetch_raw_snapshot()
    assert bio.getvalue() == b"jpegbytes"


def test_fetch_raw_snapshot_handles_missing_content_type() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"irrelevant")

    bio = _make_camera(httpx.MockTransport(handler))._fetch_raw_snapshot()
    assert bio.getvalue() == b""


def test_fetch_raw_snapshot_rejects_non_jpeg_image() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "image/png"})

    bio = _make_camera(httpx.MockTransport(handler))._fetch_raw_snapshot()
    assert bio.getvalue() == b""


def test_fetch_raw_snapshot_rejects_jpeg2000_substring_match() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"jp2bytes", headers={"Content-Type": "image/jpeg2000"})

    bio = _make_camera(httpx.MockTransport(handler))._fetch_raw_snapshot()
    assert bio.getvalue() == b""
