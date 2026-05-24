"""Bildhantering: EXIF-rotation, resize för Claude Vision, thumbnails."""

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings

MAX_CLAUDE_SIDE = 1568  # Anthropics rekommendation för bildstorlek
THUMBNAIL_SIDE = 300


def save_uploaded_cover(content: bytes) -> str:
    """Spara originalbilden och returnera relativ sökväg från IMAGES_PATH."""
    img = Image.open(io.BytesIO(content))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    filename = f"{uuid.uuid4().hex}.jpg"
    full_path = settings.covers_dir / filename
    img.save(full_path, "JPEG", quality=90, optimize=True)

    _save_thumbnail(img, filename)
    return f"covers/{filename}"


def _save_thumbnail(img: Image.Image, filename: str) -> None:
    thumb = img.copy()
    thumb.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE))
    thumb.save(settings.thumbnails_dir / filename, "JPEG", quality=85, optimize=True)


def read_cover_for_ocr(relative_path: str, *, max_side: int = MAX_CLAUDE_SIDE) -> bytes:
    """Läs och resize:a en sparad omslagsbild för OCR. Returnerar JPEG-bytes."""
    full_path = settings.images_path / relative_path
    img = Image.open(full_path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def cover_url_path(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    return f"/images/{relative_path}"


def thumbnail_url_path(relative_path: str | None) -> str | None:
    if not relative_path:
        return None
    filename = Path(relative_path).name
    return f"/images/thumbnails/{filename}"


def rotate_saved_image(relative_path: str, angle: int) -> None:
    """Rotera en sparad bild med 90/180/270 grader och uppdatera thumbnail.

    angle: 90 = medurs, -90/270 = moturs, 180 = upp och ner. Andra värden tillåts
    men ger oväntade resultat.
    """
    full_path = settings.images_path / relative_path
    img = Image.open(full_path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    rotated = img.rotate(-angle, expand=True)  # PIL roterar moturs som positivt
    rotated.save(full_path, "JPEG", quality=90, optimize=True)
    _save_thumbnail(rotated, Path(relative_path).name)


def delete_saved_image(relative_path: str) -> None:
    """Radera bild och tillhörande thumbnail från filsystemet."""
    if not relative_path:
        return
    full_path = settings.images_path / relative_path
    thumb_path = settings.thumbnails_dir / Path(relative_path).name
    for path in (full_path, thumb_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
