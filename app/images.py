"""Bild-Upload: prüfen, verkleinern, neu kodieren (entfernt EXIF/GPS und eingebettete Inhalte)."""
import io

from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError

PILImage.MAX_IMAGE_PIXELS = 30_000_000  # Schutz vor Dekompressionsbomben
MAX_PIXELS = 25_000_000  # harte Grenze (Pillow warnt zwischen 1x und 2x des Limits nur)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SVG_BYTES = 2 * 1024 * 1024
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


def read_svg_upload(upload) -> bytes | None:
    """Liest eine hochgeladene SVG-Datei (farbiges Protokoll-Logo); None, wenn keine ausgewählt wurde."""
    if upload is None or not getattr(upload, "filename", ""):
        return None
    data = upload.file.read(MAX_SVG_BYTES + 1)
    if not data:
        return None
    if len(data) > MAX_SVG_BYTES:
        raise ImageError("Die SVG-Datei ist größer als 2 MB.")
    return data


_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def process_svg(data: bytes) -> bytes:
    """Prüft eine hochgeladene SVG-Datei: gültiges XML, kein DOCTYPE/externe Entities (XXE), keine externen

    `href`/`xlink:href`-Verweise (nur `data:`- oder interne `#`-Verweise erlaubt - sonst könnte eine eingebettete
    `<image>` beim Rendern des Protokolls eine http(s)/file-Adresse laden, SSRF). Die DOCTYPE-Prüfung läuft auf dem
    geparsten Baum (nicht auf den rohen Bytes) - eine andere Kodierung (z. B. UTF-16) kann einen reinen Text-Treffer
    sonst umgehen. Gespeichert wird die vom gehärteten Parser neu serialisierte Fassung, nie die Roh-Bytes.
    """
    from lxml import etree

    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, dtd_validation=False,
                                 load_dtd=False, huge_tree=False)
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError:
        raise ImageError("Das ist keine gültige SVG-Datei.") from None
    if root.getroottree().docinfo.doctype:
        raise ImageError("Diese SVG-Datei wird aus Sicherheitsgründen nicht akzeptiert (DOCTYPE/ENTITY).")
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ImageError("Das ist keine gültige SVG-Datei.")
    for el in root.iter():
        for attr in ("href", _XLINK_HREF):
            href = el.get(attr)
            if href and not href.startswith("#") and not href.startswith("data:"):
                raise ImageError("Diese SVG-Datei enthält einen externen Verweis (href) und wird aus Sicherheitsgründen "
                                 "nicht akzeptiert.")
    return etree.tostring(root)
