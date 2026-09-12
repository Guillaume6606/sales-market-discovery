"""Bounded image downloads with DNS pinning and per-hop destination validation."""

import asyncio
import hashlib
import http.client
import io
import ipaddress
import socket
import ssl
import time
import warnings
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from PIL import Image

from libs.common.settings import settings

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 20_000_000


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str
    digest: str


def _public_destination(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    allowed = settings.vision_image_hosts.split(",")
    if (
        parts.scheme != "https"
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
        or not any(
            host == h.strip() or host.endswith("." + h.strip()) for h in allowed if h.strip()
        )
    ):
        raise ValueError("Image URL is outside allowed HTTPS hosts")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [row[4][0] for row in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Image destination is not public")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, ips[0], path


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str) -> None:
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        # Numeric destination prevents a second DNS lookup from changing the address.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _download(url: str) -> bytes:
    deadline = time.monotonic() + 30
    for _ in range(4):
        host, address, path = _public_destination(url)
        connection = _PinnedHTTPSConnection(host, address)
        try:
            connection.request("GET", path, headers={"Accept": "image/jpeg,image/png"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect without destination")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError("Image fetch failed")
            length = response.getheader("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("Image exceeds byte limit")
            chunks = []
            received = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Image download deadline exceeded")
                if connection.sock is not None:
                    connection.sock.settimeout(min(10, remaining))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - received))
                if not chunk:
                    return b"".join(chunks)
                received += len(chunk)
                if received > MAX_BYTES:
                    raise ValueError("Image exceeds byte limit")
                chunks.append(chunk)
        finally:
            connection.close()
    raise ValueError("Too many image redirects")


def normalize_image(data: bytes) -> ImageInput:
    if len(data) > MAX_BYTES:
        raise ValueError("Image exceeds byte limit")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG") or image.width * image.height > MAX_PIXELS:
                raise ValueError("Unsupported image or pixel limit exceeded")
            image.load()
            image = image.convert("RGB")
            image.thumbnail((768, 768))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
    encoded = output.getvalue()
    return ImageInput(encoded, "image/jpeg", hashlib.sha256(encoded).hexdigest())


async def prepare_images(urls: list[str]) -> list[ImageInput]:
    images: list[ImageInput] = []
    seen: set[str] = set()
    for url in list(dict.fromkeys(urls))[:6]:
        try:
            image = await asyncio.to_thread(lambda u=url: normalize_image(_download(u)))
        except (
            OSError,
            ValueError,
            http.client.HTTPException,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            continue
        if image.digest not in seen:
            images.append(image)
            seen.add(image.digest)
        if len(images) == 3:
            break
    return images
