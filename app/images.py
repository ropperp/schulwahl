"""Bild-Upload: prüfen, verkleinern, neu kodieren (entfernt EXIF/GPS und eingebettete Inhalte)."""
import io

from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError

PILImage.MAX_IMAGE_PIXELS = 30_000_000  # Schutz vor Dekompressionsbomben
MAX_PIXELS = 25_000_000  # harte Grenze (Pillow warnt zwischen 1x und 2x des Limits nur)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED = {"PNG", "JPEG", "WEBP"}


class ImageError(Exception):
    """Das hochgeladene Bild ist unbrauchbar; die Meldung ist für Anwender/innen gedacht."""
    pass


def read_upload(upload) -> bytes | None:
    """Liest eine hochgeladene Datei aus dem Formular; None, wenn nichts gewählt wurde."""
    if upload is None or not getattr(upload, "filename", ""):
        return None
    data = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        return None
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError("Das Bild ist größer als 5 MB.")
    return data


def process(data: bytes, kind: str) -> tuple[bytes, str]:
    """kind='candidate': quadratisches JPEG 240x240. kind='logo': PNG, max. 600 px, Transparenz bleibt."""
    try:
        img = PILImage.open(io.BytesIO(data))
        fmt = img.format
        if img.width * img.height > MAX_PIXELS:
            raise ImageError("Das Bild hat zu viele Pixel. Bitte ein kleineres Bild verwenden.")
        img.load()
    except (UnidentifiedImageError, OSError, PILImage.DecompressionBombError):
        raise ImageError("Das ist keine gültige Bilddatei (erlaubt: JPG, PNG, WebP).") from None
    if fmt not in ALLOWED:
        raise ImageError("Bitte JPG, PNG oder WebP hochladen.")
    img = ImageOps.exif_transpose(img)
    out = io.BytesIO()
    if kind == "candidate":
        # Angezeigt wird das Foto nur ca. 56 px groß; 240 px reichen auch für Retina-Displays und sparen Datenvolumen.
        img = ImageOps.fit(img.convert("RGB"), (240, 240), method=PILImage.LANCZOS)
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue(), "image/jpeg"
    img.thumbnail((600, 600), PILImage.LANCZOS)
    img.convert("RGBA").save(out, "PNG", optimize=True)
    return out.getvalue(), "image/png"
