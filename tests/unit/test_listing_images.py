import io
from unittest.mock import patch

import pytest
from PIL import Image

from ingestion import listing_images as module


def photo():
    out = io.BytesIO()
    Image.new("RGB", (1000, 500), "red").save(out, "PNG")
    return out.getvalue()


def test_normalize_and_strip_metadata():
    result = module.normalize_image(photo())
    assert result.mime_type == "image/jpeg"
    with Image.open(io.BytesIO(result.data)) as image:
        assert image.size == (768, 384)


@pytest.mark.asyncio
async def test_missing_failed_and_duplicate_photos():
    assert await module.prepare_images([]) == []
    with patch.object(module, "_download", side_effect=OSError):
        assert await module.prepare_images(["https://i.ebayimg.com/a"]) == []
    with patch.object(module, "_download", return_value=photo()):
        assert len(await module.prepare_images(["a", "b"])) == 1


def test_public_ip_required_even_on_allowlisted_host():
    with patch.object(
        module.socket,
        "getaddrinfo",
        return_value=[(None, None, None, None, ("169.254.169.254", 443))],
    ):
        with pytest.raises(ValueError, match="not public"):
            module._public_destination("https://i.ebayimg.com/a")
    with pytest.raises(ValueError):
        module._public_destination("https://i.ebayimg.com.evil.example/a")


def test_pixel_and_byte_limits(monkeypatch):
    monkeypatch.setattr(module, "MAX_PIXELS", 100)
    with pytest.raises(ValueError):
        module.normalize_image(photo())
    monkeypatch.setattr(module, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        module.normalize_image(b"x" * 11)


def test_redirect_revalidates_destination():
    from unittest.mock import MagicMock

    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = 302
    response.getheader.return_value = "https://169.254.169.254/metadata"
    with (
        patch.object(module, "_PinnedHTTPSConnection", return_value=connection),
        patch.object(
            module.socket, "getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]
        ),
    ):
        with pytest.raises(ValueError, match="outside allowed"):
            module._download("https://i.ebayimg.com/a")
