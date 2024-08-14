import logging

from ffmpegcv.ffmpeg_reader import FFmpegReader, get_outnumpyshape, get_videofilter_cpu  # type: ignore
from ffmpegcv.stream_info import get_info  # type: ignore

logger = logging.getLogger(__name__)


class FFmpegReaderStreamRTCustom(FFmpegReader):
    def __init__(self):
        super().__init__()

    @staticmethod
    def VideoReader(stream_url, codec, pix_fmt, crop_xywh, resize, resize_keepratio, resize_keepratioalign, timeout, videoinfo):
        vid = FFmpegReaderStreamRTCustom()
        videoinfo = videoinfo if videoinfo else get_info(stream_url, timeout)
        vid.origin_width = videoinfo.width
        vid.origin_height = videoinfo.height
        vid.fps = videoinfo.fps
        vid.codec = codec if codec else videoinfo.codec
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


def FFmpegReaderStreamRTCustomInit(
    stream_url, codec=None, pix_fmt="bgr24", crop_xywh=None, resize=None, resize_keepratio=True, resize_keepratioalign="center", timeout=None, videoinfo=None
) -> FFmpegReaderStreamRTCustom:
    return FFmpegReaderStreamRTCustom.VideoReader(stream_url, codec, pix_fmt, crop_xywh, resize, resize_keepratio, resize_keepratioalign, timeout=timeout, videoinfo=videoinfo)
