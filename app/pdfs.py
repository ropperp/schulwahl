"""PDF-Erzeugung: Code-Zettel mit QR-Code und Wahlprotokoll."""
import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from .timeutil import fmt_local, utcnow

# Fixe Kontaktdaten der HTL 1 für das Protokoll im Papierform-Stil (wie auf dem bisherigen Papierformular)
FORM_FOOTER = "HTL1 Klagenfurt Lastenstraße 1, 9020 Klagenfurt    Tel: 0463/31605    direktion@htl1-klagenfurt.at"


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


def _schuljahr(dt) -> str:
    """Österreichisches Schuljahr (z. B. "2026/2027") für ein Datum; das Schuljahr beginnt im September."""
    y = dt.year
    return f"{y}/{y + 1}" if dt.month >= 9 else f"{y - 1}/{y}"


def _svg_drawing(svg_bytes: bytes, max_w: float, max_h: float):
    """Wandelt ein SVG-Logo in eine passend skalierte reportlab-Zeichnung um; None bei fehlerhafter Datei."""
    from svglib.svglib import svg2rlg

    try:
        d = svg2rlg(io.BytesIO(svg_bytes))
    except Exception:  # noqa: BLE001 - eine fehlerhafte SVG darf das Protokoll nicht zum Absturz bringen
        return None
    if not d or not d.width or not d.height:
        return None
    s = min(max_w / d.width, max_h / d.height)
    d.width, d.height = d.width * s, d.height * s
    d.scale(s, s)
    return d


def _sign_box(caption: str, box_h: float, font_size: float) -> list:
    """Inhalt einer Unterschriftsbox: leerer Platz zum Unterschreiben, eine Linie, darunter der Name (schwarz, nicht fett)."""
    from reportlab.platypus.flowables import HRFlowable

    cap_style = ParagraphStyle("cap", fontName="Helvetica", fontSize=font_size, textColor=colors.black,
                               leading=font_size + 1.5)
    return [Spacer(1, box_h - 13 * mm), HRFlowable(width="100%", thickness=0.7, color=colors.black, spaceAfter=1.5),
           Paragraph(escape(caption) if caption else "&nbsp;", cap_style)]


# Schriftgrößen (Punkt) und Abstände (mm), wenn in den Protokoll-Einstellungen nichts anderes hinterlegt ist
DEFAULT_FONT_SIZES = {"header": 16, "title": 13, "table": 11, "caption": 9, "footer": 7.5, "invalid": 10}
DEFAULT_SPACING = {"heading": 1.5, "result": 5, "header_gap": 1.5}  # "heading" = nach einer Überschrift (vor ihrer Tabelle)
# "result" = nach einer Person/einem Ergebnis (vor der nächsten Überschrift)
# "header_gap" = nach den Spaltentiteln Name/Klasse/Stimmen (vor den dynamischen Werten darunter)


def official_protocol_pdf(election, results: list[dict], school_name: str, protocol_logo_svg: bytes | None,
                          signed_by: str, witnesses: list[str] | None = None, location: str = "",
                          font_sizes: dict[str, float] | None = None, spacing: dict[str, float] | None = None) -> bytes:
    """Wahlprotokoll im Stil des bisherigen Papierformulars: ein Blatt je Wahlgang, farbiges Logo, Unterschriftsfelder.

    Anders als `protocol_pdf` (tonersparend, eine Zusammenfassung) ist dieses Protokoll bewusst dem bekannten
    Papierformular nachgebildet - inklusive farbigem Logo. Gedacht zum Unterschreiben/Ablegen, nicht für den
    Massendruck (dafür gibt es weiterhin `protocol_pdf`). `font_sizes`/`spacing` überschreiben einzelne Werte aus
    `DEFAULT_FONT_SIZES`/`DEFAULT_SPACING` (aus den Protokoll-Einstellungen).
    """
    witnesses = [w.strip()[:100] for w in (witnesses or []) if w.strip()][:10]
    fonts = {**DEFAULT_FONT_SIZES, **(font_sizes or {})}
    sp = {**DEFAULT_SPACING, **(spacing or {})}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=16 * mm, bottomMargin=22 * mm, title=f"Wahlprotokoll – {election.name}")
    styles = getSampleStyleSheet()
    center = ParagraphStyle("center", parent=styles["Normal"], alignment=1)
    center_bold = ParagraphStyle("center_bold", parent=center, fontName="Helvetica-Bold", fontSize=fonts["title"],
                                 leading=fonts["title"] + 3)
    center_bold_header = ParagraphStyle("center_bold_header", parent=center, fontName="Helvetica-Bold",
                                        fontSize=fonts["header"])
    section = ParagraphStyle("section", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=fonts["table"],
                             spaceBefore=6, spaceAfter=0)
    invalid_style = ParagraphStyle("invalid", parent=styles["Normal"], fontSize=fonts["invalid"],
                                   leading=fonts["invalid"] + 2)

    def draw_footer(c, _doc):
        """Fußzeile fix am unteren Rand jeder Seite (nicht Teil des fließenden Inhalts)."""
        c.saveState()
        c.setFont("Helvetica", fonts["footer"])
        c.setFillColor(colors.black)
        c.drawCentredString(A4[0] / 2, 10 * mm, FORM_FOOTER)
        c.restoreState()

    logo_drawing = _svg_drawing(protocol_logo_svg, 36 * mm, 16 * mm) if protocol_logo_svg else None
    mini_style = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), fonts["table"]),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, 0), sp["header_gap"] * mm),  # Abstand Spaltentitel -> erste Datenzeile
    ])  # bewusst ohne Rahmen/Linien

    def mini_table(rows: list[list[str]]) -> Table:
        """Name/Klasse/Stimmen-Tabelle ohne Rahmen; ohne Datenzeilen bleiben nur die Spaltentitel übrig."""
        t = Table([["Name", "Klasse", "Stimmen"], *rows], colWidths=[85 * mm, 35 * mm, 25 * mm], hAlign="LEFT")
        t.setStyle(mini_style)
        return t

    def heading_table(label: str, rows: list[list[str]]) -> list:
        """Eine Überschrift + ihre Tabelle, mit konfigurierbarem Abstand davor/danach."""
        return [Paragraph(label, section), Spacer(1, sp["heading"] * mm), mini_table(rows), Spacer(1, sp["result"] * mm)]

    def header_table() -> Table:
        """Die Kopfbox mit Logo und „Wahlen“ (bleibt mit Rahmen)."""
        t = Table([[logo_drawing or "", Paragraph("Wahlen", center_bold_header), ""]],
                  colWidths=[40 * mm, 90 * mm, 30 * mm], rowHeights=[20 * mm])
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ]))
        return t

    def signature_block() -> list:
        """Unterschriftsfelder (Wahlleitung + Wahlzeugen, ab 3 insgesamt 3 nebeneinander) plus Ort/Datum."""
        signers = [f"Wahlleitung: {signed_by}"] + [f"Wahlzeuge: {w}" for w in witnesses]
        cols = 3 if len(signers) >= 3 else max(1, len(signers))
        box_w, box_h = (170 * mm) / cols, 36 * mm
        sign_rows = []
        for j in range(0, len(signers), cols):
            chunk = signers[j:j + cols]
            cells = [_sign_box(caption, box_h, fonts["caption"]) for caption in chunk]
            cells += [""] * (cols - len(cells))
            sign_rows.append(cells)
        t = Table(sign_rows, colWidths=[box_w] * cols, rowHeights=[box_h] * len(sign_rows), hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4)]))  # ohne Rahmen
        date_str = fmt_local(utcnow())[:10]
        return [t, Spacer(1, 4 * mm), Paragraph(escape(f"{location}, {date_str}" if location else date_str), styles["Normal"])]

    def race_block(r: dict) -> list:
        """Titel, Platzierungen und ungültige Stimmen eines Wahlgangs (ohne Kopfbox/Unterschriften)."""
        block = []
        race = r["race"]
        schuljahr = _schuljahr(election.closed_at or election.end_at)
        if race.department_id is None:
            title_text, subtitle_text = f"Ergebnis der Schulsprecherwahl {schuljahr}", "SchulsprecherIn"
        else:
            title_text, subtitle_text = f"Ergebnis der Abteilungssprecherwahl {schuljahr}", r["department"] or ""
        block.append(Paragraph(escape(title_text), center_bold))
        if subtitle_text:
            block.append(Paragraph(escape(subtitle_text), center_bold))
        if race.runoff_kind:
            block.append(Paragraph("(Stichwahl)", center))
        block.append(Spacer(1, 4 * mm))

        votes_by_id = {row["candidate"].id: row["votes"] for row in r["rows"]}
        shown_ids: set[int] = set()
        # Eine Stichwahl geht vor einem bloßen Mehrheitsgewinner: so lange die Wahlleitung "Stichwahl: ja" entschieden
        # hat, gilt niemand als gewählt, auch wenn decide() rein rechnerisch schon eine Führende/einen Führenden kennt.
        pending_runoff = r["decision"] == "yes" and r["plan"] and r["plan_kind"] == "speaker"

        if pending_runoff:
            for c in r["plan"]:
                shown_ids.add(c.id)
            block += heading_table(f"<strike>Als {escape(race.title)} wurde gewählt</strike>: Stichwahl erforderlich zwischen:",
                                   [[c.name, c.info or "", str(votes_by_id[c.id])] for c in r["plan"]])
            slot_labels = []  # Stichwahl noch offen: auch die Stellvertretung ist offen, alles andere -> "Weitere Ergebnisse"
        elif not any(row["votes"] > 0 for row in r["rows"]):
            block += [Paragraph(f"Als {escape(race.title)} wurde gewählt:", section), Spacer(1, sp["heading"] * mm),
                     Paragraph("Keine gültigen Stimmen.", invalid_style), Spacer(1, sp["result"] * mm)]
            slot_labels = []
        else:
            slot_labels = [f"Als {escape(race.title)} wurde gewählt:", "1. Stellvertreter/in", "2. Stellvertreter/in"]

        # Platzierungen richten sich nach der tatsächlichen Anzahl Kandidat/innen mit Stimmen - bei nur zwei
        # Antretenden gibt es z. B. gar keine "2. Stellvertreter/in"; nichts wird durchgestrichen, Plätze ohne
        # Kandidat/in werden einfach weggelassen. Gleichstand innerhalb eines Platzes: alle Betroffenen gemeinsam.
        groups: list[list[dict]] = []
        for row in r["rows"]:
            if row["votes"] <= 0 or row["candidate"].id in shown_ids:
                continue
            if groups and groups[-1][0]["votes"] == row["votes"]:
                groups[-1].append(row)
            else:
                groups.append([row])
        for label in slot_labels:
            if not groups:
                break
            group = groups.pop(0)
            for row in group:
                shown_ids.add(row["candidate"].id)
            block += heading_table(label, [[row["candidate"].name, row["candidate"].info or "", str(row["votes"])] for row in group])

        rest = [row for group in groups for row in group]
        if rest:
            block += heading_table("Weitere Ergebnisse",
                                   [[row["candidate"].name, row["candidate"].info or "", str(row["votes"])] for row in rest])

        block.append(Paragraph(f"<b>ungültige Stimmen:</b> {r['invalid']}", invalid_style))
        return block

    story = []
    for i, r in enumerate(results):
        story += [header_table(), Spacer(1, 5 * mm)] + race_block(r) + [Spacer(1, 6 * mm)] + signature_block()
        if i < len(results) - 1:
            story.append(PageBreak())
    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buf.getvalue()
