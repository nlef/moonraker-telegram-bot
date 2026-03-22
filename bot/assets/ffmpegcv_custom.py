from __future__ import annotations

import logging
from typing import Any

from ffmpegcv.ffmpeg_reader import FFmpegReader, get_outnumpyshape, get_videofilter_cpu  # type: ignore[import-untyped]
from ffmpegcv.stream_info import get_info  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class FFmpegReaderStreamRTCustom(FFmpegReader):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def VideoReader(  # noqa: N802
        stream_url: str,
        codec: str | None,
        pix_fmt: str,
        crop_xywh: tuple[int, int, int, int] | None,
        resize: tuple[int, int] | None,
        resize_keepratio: bool,
        resize_keepratioalign: str,
        timeout: int | None,
        videoinfo: Any,
    ) -> FFmpegReaderStreamRTCustom:
        vid = FFmpegReaderStreamRTCustom()
        videoinfo = videoinfo or get_info(stream_url, timeout)
        vid.origin_width = videoinfo.width
        vid.origin_height = videoinfo.height
        vid.fps = videoinfo.fps
        vid.codec = codec or videoinfo.codec
        vid.count = videoinfo.count
        vid.duration = videoinfo.duration
        vid.pix_fmt = pix_fmt

        (vid.crop_width, vid.crop_height), (vid.width, vid.height), filteropt = get_videofilter_cpu(
            (vid.origin_width, vid.origin_height), pix_fmt, crop_xywh, resize, resize_keepratio, resize_keepratioalign
        )
        vid.size = (vid.width, vid.height)

        rtsp_opt = "-rtsp_transport tcp " if stream_url.startswith("rtsp://") else ""
        vid.ffmpeg_cmd = (
            f"ffmpeg -loglevel warning "
            f" {rtsp_opt} "
            " -probesize 32 -analyzeduration 0 -fflags discardcorrupt "
            "-fflags nobuffer -flags low_delay -strict experimental "
            f" -vcodec {vid.codec} -i {stream_url}"
            f" {filteropt} -pix_fmt {pix_fmt}  -f rawvideo pipe:"
        )

        vid.out_numpy_shape = get_outnumpyshape(vid.size, pix_fmt)
        return vid


def FFmpegReaderStreamRTCustomInit(  # noqa: N802
    stream_url: str,
    codec: str | None = None,
    pix_fmt: str = "bgr24",
    crop_xywh: tuple[int, int, int, int] | None = None,
    resize: tuple[int, int] | None = None,
    resize_keepratio: bool = True,
    resize_keepratioalign: str = "center",
    timeout: int | None = None,
    videoinfo: Any = None,
) -> FFmpegReaderStreamRTCustom:
    return FFmpegReaderStreamRTCustom.VideoReader(stream_url, codec, pix_fmt, crop_xywh, resize, resize_keepratio, resize_keepratioalign, timeout=timeout, videoinfo=videoinfo)
