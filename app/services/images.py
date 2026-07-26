"""Avatar image preprocessing.

Telegram stores profile photos as squares: whatever a raw MTProto
UploadProfilePhotoRequest sends gets center-cropped server-side, so a tall
promo image silently loses its top and bottom (official clients avoid this
by making the user pick the crop square before uploading). Instead of
cropping, letterbox the image onto a square canvas — a blurred, stretched
copy of itself as the background — so nothing is ever cut off.
"""
from __future__ import annotations

import io

from PIL import Image, ImageFilter, ImageOps

# Telegram downscales avatars to ~640px anyway; cap the upload so a phone
# photo doesn't get re-encoded at full 4000px for nothing.
_MAX_SIDE = 2048


def fit_avatar_to_square(photo_bytes: bytes) -> bytes:
    """Returns JPEG bytes of a square image containing the whole original.
    Already-square input is just normalized (orientation, RGB, JPEG)."""
    img = Image.open(io.BytesIO(photo_bytes))
    img = ImageOps.exif_transpose(img)  # phone photos carry rotation in EXIF
    img = img.convert("RGB")

    width, height = img.size
    side = max(width, height)
    if side > _MAX_SIDE:
        scale = _MAX_SIDE / side
        img = img.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
        width, height = img.size
        side = max(width, height)

    if width != height:
        background = img.resize((side, side), Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(max(8, side // 20))
        )
        background.paste(img, ((side - width) // 2, (side - height) // 2))
        img = background

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
