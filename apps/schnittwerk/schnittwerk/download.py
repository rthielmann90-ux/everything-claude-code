"""Video import from a URL via yt-dlp."""

import ipaddress
import socket
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse


class DownloadError(Exception):
    pass


def validate_url(url: str) -> str:
    """Accept only http(s) URLs whose host resolves to public addresses.

    This blocks the obvious server-side request forgery targets (localhost,
    private networks, cloud metadata endpoints). yt-dlp extractors may still
    follow redirects, so a public deployment should also set SCHNITTWERK_TOKEN.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise DownloadError("Bitte einen vollständigen http(s)-Link angeben.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DownloadError("Die Adresse konnte nicht aufgelöst werden.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise DownloadError("Dieser Link zeigt auf ein internes Netz und ist nicht erlaubt.")
    return url


def download(url: str, target_dir: Path, max_bytes: int, max_duration_s: int,
             on_progress: Callable[[float], None]) -> tuple[Path, str]:
    """Download the best MP4-compatible stream up to 1080p. Returns (path, title)."""
    import yt_dlp
    from yt_dlp.utils import match_filter_func

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(target_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes,
        "match_filter": match_filter_func(f"duration <=? {max_duration_s}"),
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError("Download fehlgeschlagen: " + str(exc).replace("ERROR: ", "")[:300]) from exc
    files = [p for p in target_dir.glob("source.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise DownloadError(
            f"Kein Video geladen. Möglicherweise länger als {max_duration_s // 60} Minuten "
            "oder größer als das Upload-Limit.")
    title = (info or {}).get("title") or "Video"
    return files[0], title
