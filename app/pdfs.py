"""PDF-Erzeugung: Code-Zettel mit QR-Code und Wahlprotokoll."""
import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from .timeutil import fmt_local, utcnow


def logo_for_print(data: bytes) -> bytes:
    """Logo tonersparend: Graustufen auf Weiß. Ein helles Logo für dunklen Grund (weiß auf transparent) wäre auf Papier
    unsichtbar und wird deshalb als schwarze Silhouette gedruckt."""
    from PIL import Image as PILImage

    img = PILImage.open(io.BytesIO(data)).convert("RGBA")
    alpha = img.getchannel("A")
    page = PILImage.new("RGB", img.size, "white")
    page.paste(img.convert("RGB"), mask=alpha)
    gray = page.convert("L")
    if gray.getextrema()[0] > 225 and alpha.getextrema()[1] > 0:  # praktisch nichts zu sehen
        gray = PILImage.new("L", img.size, 255)
        gray.paste(0, mask=alpha)
    out = io.BytesIO()
    gray.save(out, "PNG")
    return out.getvalue()


def _fit(text: str, font: str, size: float, max_width: float) -> str:
    """Kürzt Text mit … so, dass er in die Breite passt."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if stringWidth(text, font, size) <= max_width:
        return text
    while text and stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text + "…"


def _fit_size(text: str, font: str, size: float, max_width: float, min_size: float) -> tuple[str, float]:
    """Verkleinert die Schrift bis min_size, bevor der Text mit … gekürzt wird."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    while size > min_size and stringWidth(text, font, size) > max_width:
        size = max(min_size, round(size - 0.25, 2))
    return _fit(text, font, size, max_width), size


def _qr(c: canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    """Zeichnet einen QR-Code (schwarz auf weiß) an die angegebene Stelle der PDF-Seite."""
    widget = qr.QrCodeWidget(url, barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x, y)


def codes_pdf(election_name: str, items: list[tuple[str, str]], base_url: str) -> bytes:
    """items = [(Abteilung, Code)]. 3 x 8 Zettel pro A4-Seite zum Ausschneiden."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Wahlcodes – {election_name}")
    page_w, page_h = A4
    cols, rows, margin = 3, 8, 10 * mm
    w = (page_w - 2 * margin) / cols
    h = (page_h - 2 * margin) / rows
    per_page = cols * rows
    pad = 3 * mm
    qr_size = min(h - 2 * pad, 25 * mm)

    for i, (dept, code) in enumerate(items):
        if i and i % per_page == 0:
            c.showPage()
        slot = i % per_page
        x = margin + (slot % cols) * w
        y = page_h - margin - (slot // cols + 1) * h

        c.setStrokeColor(colors.black)  # tonersparend: nur Schwarz auf Weiß
        c.setLineWidth(0.3)
        c.setDash(1.5, 3)
        c.rect(x, y, w, h)
        c.setDash()

        text_w = w - qr_size - 3 * pad
        c.setFillColor(colors.black)
        title, title_size = _fit_size(election_name, "Helvetica", 7, w - 2 * pad, 5.5)
        c.setFont("Helvetica", title_size)
        c.drawString(x + pad, y + h - pad - 6, title)
        label, label_size = _fit_size(dept, "Helvetica-Bold", 11.5, text_w, 7)  # Abteilung gut lesbar, schrumpft nur bei langen Namen
        c.setFont("Helvetica-Bold", label_size)
        c.drawString(x + pad, y + h - pad - 6 - label_size - 3, label)
        c.setFont("Courier-Bold", 17)
        c.drawString(x + pad, y + h / 2 - 12, f"{code[:3]} {code[3:]}")
        c.setFont("Helvetica", 6.5)
        c.drawString(x + pad, y + pad + 9, "Einmal gültig – nicht weitergeben")
        c.drawString(x + pad, y + pad + 1, _fit(base_url, "Helvetica", 6.5, w - 2 * pad))
        _qr(c, f"{base_url}/c/{code}", x + w - qr_size - pad, y + (h - qr_size) / 2 + 3, qr_size)

    c.showPage()
    c.save()
    return buf.getvalue()


def protocol_pdf(election, status_label: str, part: dict, results: list[dict],
                 school_name: str = "", logo: bytes | None = None) -> bytes:
    """Wahlprotokoll als PDF: Beteiligung, Ergebnisse je Wahlgang, Entscheidung zur Stichwahl, Unterschriftenzeilen (schwarz-weiß)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title=f"Wahlprotokoll – {election.name}")
    styles = getSampleStyleSheet()
    story = []
    if logo:
        logo = logo_for_print(logo)
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image as RLImage
        iw, ih = ImageReader(io.BytesIO(logo)).getSize()
        h = 18 * mm
        story.append(RLImage(io.BytesIO(logo), width=h * iw / ih, height=h, hAlign="LEFT"))
    if school_name:
        story.append(Paragraph(escape(school_name), styles["Heading3"]))
    story.append(Paragraph(f"Wahlprotokoll: {escape(election.name)}", styles["Title"]))
    ended = fmt_local(election.closed_at) if election.closed_at and election.closed_at < election.end_at else fmt_local(election.end_at)
    story += [
        Paragraph(f"Wahlzeitraum: {fmt_local(election.start_at)} bis {ended} Uhr ({status_label})", styles["Normal"]),
        Paragraph(f"Protokoll erstellt: {fmt_local(utcnow())} Uhr", styles["Normal"]),
        Spacer(1, 8 * mm),
        Paragraph("Wahlbeteiligung", styles["Heading2"]),
    ]
    table_style = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.black),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ])
    rows = [["Abteilung", "Ausgegebene Codes", "Abgegebene Stimmzettel", "Beteiligung"]]
    for d in part["departments"]:
        rows.append([d["name"], d["total"], d["used"], f'{d["percent"]:.1f} %'])
    rows.append(["Gesamt", part["total"], part["used"], f'{part["percent"]:.1f} %'])
    t = Table(rows, hAlign="LEFT")
    t.setStyle(table_style)
    t.setStyle(TableStyle([("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story += [t, Spacer(1, 6 * mm)]

    for r in results:
        title = r["race"].title + (f" ({r['department']})" if r["department"] else "")
        story.append(Paragraph(escape(title), styles["Heading2"]))
        data = [["Kandidat/in", "Stimmen", "Anteil gültig"]]
        for row in r["rows"]:
            data.append([row["candidate"].name, row["votes"], f'{row["percent"]:.1f} %'])
        data.append(["Gültige Stimmen gesamt", r["valid"], ""])
        data.append(["Ungültige Stimmen", r["invalid"], ""])
        data.append(["Abgegebene Stimmen gesamt", r["total"], ""])
        t = Table(data, hAlign="LEFT", colWidths=[90 * mm, 25 * mm, 30 * mm])
        t.setStyle(table_style)
        story.append(t)
        names = lambda cs: ", ".join(c.name for c in cs)  # noqa: E731
        lines = []
        if r["decision"] == "yes":
            lines.append("Entscheidung der Wahlleitung: Stichwahl wird durchgeführt")
        elif r["decision"] == "no":
            lines.append("Entscheidung der Wahlleitung: keine Stichwahl")
        result_lines = []
        yes_plan = r["decision"] == "yes" and r["plan"]
        tie_suffix = " – ohne Stichwahl entscheidet die Wahlleitung" if r["decision"] == "no" else " – Stichwahl nötig"
        if yes_plan and r["plan_kind"] == "speaker":
            result_lines.append(f"Stichwahl um Platz 1 und die Stellvertretung zwischen: {names(r['plan'])}")
        else:
            if r["winner"]:
                label = "Gewählt als Stellvertreter/in" if r["race"].runoff_kind == "deputy" else "Gewählt"
                result_lines.append(f"{label}: {r['winner'].name}")
            if r["runoff_first"]:
                result_lines.append(f"Stimmengleichheit um Platz 1 zwischen: {names(r['runoff_first'])}{tie_suffix}")
            if r["deputy"]:
                result_lines.append(f"Stellvertreter/in: {r['deputy'].name}")
            elif yes_plan:
                result_lines.append(f"Stichwahl um die Stellvertretung zwischen: {names(r['plan'])}")
            elif r["runoff_second"]:
                result_lines.append(f"Stimmengleichheit um Platz 2 zwischen: {names(r['runoff_second'])}{tie_suffix}")
            elif r["deputy_pending"]:
                result_lines.append("Stellvertretung ergibt sich nach Klärung des Gleichstands.")
        if not result_lines:
            result_lines.append("Kein Ergebnis (keine gültigen Stimmen)")
        lines += result_lines
        for line in lines:
            story.append(Paragraph(f"<b>{escape(line)}</b>", styles["Normal"]))
        if not r["consistent"]:
            story.append(Paragraph(
                f"<b>ACHTUNG: {r['total']} Stimmen, aber {r['expected']} verbrauchte Codes.</b>",
                styles["Normal"]))
        story.append(Spacer(1, 5 * mm))

    story += [Spacer(1, 15 * mm),
              Table([["_______________________________", "_______________________________"],
                     ["Wahlleiter/in", "Schulleitung / Zweite Person"]], hAlign="LEFT")]
    doc.build(story)
    return buf.getvalue()
