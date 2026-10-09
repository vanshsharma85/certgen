"""Certificate rendering: one fixed A4-landscape PDF template, built with ReportLab."""
import os
import tempfile
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

NAVY = HexColor("#1f2a44")
GOLD = HexColor("#b08d3c")
GREY = HexColor("#555555")


class CertificateGenerator:
    """Renders certificates. Instances are stateless and safe to share across threads."""

    def __init__(self, font_path: str | None = None):
        self.body_font = "Helvetica"
        self.name_font = "Times-BoldItalic"
        self.heading_font = "Times-Bold"
        if font_path:
            pdfmetrics.registerFont(TTFont("CertFont", font_path))
            self.body_font = self.name_font = self.heading_font = "CertFont"

    def render(
        self,
        out_path: Path,
        *,
        recipient_name: str,
        course_name: str,
        issuer_name: str,
        issue_date: date,
        certificate_id: str,
    ) -> None:
        """Write the PDF atomically (temp file + rename) so partial files never appear."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
        os.close(fd)
        try:
            self._draw(tmp, recipient_name, course_name, issuer_name, issue_date, certificate_id)
            os.replace(tmp, out_path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _fit(self, text: str, font: str, size: float, max_width: float, min_size: float = 12) -> float:
        while size > min_size and stringWidth(text, font, size) > max_width:
            size -= 1
        return size

    def _draw(self, path, name, course, issuer, issued, cert_id) -> None:
        w, h = landscape(A4)
        c = canvas.Canvas(path, pagesize=(w, h))
        c.setTitle(f"Certificate - {name}")
        c.setAuthor(issuer)

        # Borders
        c.setStrokeColor(NAVY)
        c.setLineWidth(6)
        c.rect(25, 25, w - 50, h - 50)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        c.rect(38, 38, w - 76, h - 76)

        usable = w - 160
        cx = w / 2

        c.setFillColor(NAVY)
        c.setFont(self.heading_font, 40)
        c.drawCentredString(cx, h - 120, "CERTIFICATE")
        c.setFillColor(GOLD)
        c.setFont(self.heading_font, 18)
        c.drawCentredString(cx, h - 148, "OF COMPLETION")

        c.setFillColor(GREY)
        c.setFont(self.body_font, 13)
        c.drawCentredString(cx, h - 200, "This is to certify that")

        c.setFillColor(NAVY)
        size = self._fit(name, self.name_font, 38, usable)
        c.setFont(self.name_font, size)
        c.drawCentredString(cx, h - 255, name)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.line(cx - usable / 3, h - 266, cx + usable / 3, h - 266)

        c.setFillColor(GREY)
        c.setFont(self.body_font, 13)
        c.drawCentredString(cx, h - 300, "has successfully completed")

        c.setFillColor(NAVY)
        size = self._fit(course, self.heading_font, 24, usable)
        c.setFont(self.heading_font, size)
        c.drawCentredString(cx, h - 335, course)

        # Footer: date left, issuer right
        c.setStrokeColor(GREY)
        c.setLineWidth(0.8)
        c.line(90, 110, 260, 110)
        c.line(w - 260, 110, w - 90, 110)
        c.setFillColor(NAVY)
        c.setFont(self.body_font, 12)
        c.drawCentredString(175, 116, issued.strftime("%d %B %Y"))
        issuer_size = self._fit(issuer, self.body_font, 12, 170, min_size=7)
        c.setFont(self.body_font, issuer_size)
        c.drawCentredString(w - 175, 116, issuer)
        c.setFillColor(GREY)
        c.setFont(self.body_font, 10)
        c.drawCentredString(175, 94, "Date")
        c.drawCentredString(w - 175, 94, "Issued by")

        c.setFont(self.body_font, 8)
        c.drawCentredString(cx, 55, f"Certificate ID: {cert_id}")
        c.showPage()
        c.save()
