"""Paginated, read-only PDF reports from serialized saved results. No inference or external assets."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

FONT_DIR = Path(reportlab.__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont("Report", str(FONT_DIR / "Vera.ttf")))
pdfmetrics.registerFont(TTFont("ReportBold", str(FONT_DIR / "VeraBd.ttf")))
pdfmetrics.registerFontFamily("Report", normal="Report", bold="ReportBold", italic="Report", boldItalic="ReportBold")
NAVY = colors.HexColor("#0b2233")
TEAL = colors.HexColor("#008780")
MINT = colors.HexColor("#e7f6f4")
STYLES = getSampleStyleSheet()
STYLES.add(ParagraphStyle(name="ReportText", fontName="Report", fontSize=8, leading=11, textColor=NAVY, alignment=TA_LEFT, wordWrap="CJK"))
STYLES.add(ParagraphStyle(name="ReportHeading", fontName="ReportBold", fontSize=12, leading=16, textColor=TEAL, spaceBefore=12, spaceAfter=6))
STYLES.add(ParagraphStyle(name="ReportTitle", fontName="ReportBold", fontSize=20, leading=25, textColor=NAVY, spaceAfter=12))


def label(key):
    return str(key).replace("_", " ").replace(".", " / ").capitalize()


def scalar(value):
    if value is None:
        return "Not recorded"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(scalar(item) for item in value) if value else "None"
    return str(value)


def flatten(value, prefix=""):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from flatten(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, (list, tuple)) and any(isinstance(item, dict) for item in value):
        for index, item in enumerate(value, 1):
            yield from flatten(item, f"{prefix} [{index}]")
    else:
        yield prefix, scalar(value) if value != {} else "None"


def chunks(value, limit=600):
    """Bound each table cell so long evidence cannot overflow a page or be lost during row splitting."""
    text = str(value).replace("\n", " ")
    result = []
    while len(text) > limit:
        end = text.rfind(" ", 0, limit)
        end = end if end > limit // 2 else limit
        result.append(text[:end])
        text = text[end:]
    return result + [text]


class Report:
    def __init__(self, title, wide=False):
        self.output = BytesIO()
        self.doc = SimpleDocTemplate(self.output, pagesize=landscape(A4) if wide else A4,
            leftMargin=32, rightMargin=32, topMargin=36, bottomMargin=36,
            title=f"Northwind Global — {title}", author="Northwind Global")
        self.story = [Paragraph("NORTHWIND GLOBAL", STYLES["ReportHeading"]), Paragraph(escape(title), STYLES["ReportTitle"])]

    def text(self, value):
        self.story.append(Paragraph(escape(str(value)).replace("\n", "<br/>"), STYLES["ReportText"]))

    def table(self, headers, rows, widths=None, highlight_differences=False):
        def cell(value):
            return Paragraph(escape(str(value)).replace("\n", "<br/>"), STYLES["ReportText"])
        data = [[cell(value) for value in headers]]
        highlights = []
        for row in rows:
            parts = [chunks(value) for value in row]
            for index in range(max(map(len, parts))):
                if highlight_differences:
                    highlights.extend(("BACKGROUND", (column, len(data)), (column, len(data)), colors.HexColor("#fff6db")) for column in range(2, len(row)) if row[column] != row[1])
                data.append([cell(part[index] if index < len(part) else (part[0] + " (continued)" if column == 0 else "")) for column, part in enumerate(parts)])
        table = LongTable(data, colWidths=widths, repeatRows=1, splitInRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), MINT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), .6, TEAL), ("LINEBELOW", (0, 1), (-1, -1), .2, colors.HexColor("#dce5e8")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), *highlights]))
        self.story.extend([table, Spacer(1, 6)])

    def section(self, title, value):
        self.story.append(Paragraph(escape(title), STYLES["ReportHeading"]))
        self.table(["Metric / field", "Recorded value"], [(label(key), item) for key, item in flatten(value)], [self.doc.width * .43, self.doc.width * .57])

    def finish(self):
        def footer(canvas, doc):
            canvas.setFont("Report", 7)
            canvas.setFillColor(TEAL)
            canvas.drawString(32, 18, "Northwind Global | Expense Policy Validator | Saved results")
            canvas.drawRightString(doc.pagesize[0] - 32, 18, f"Page {doc.page}")
        self.doc.build(self.story, onFirstPage=footer, onLaterPages=footer)
        return self.output.getvalue()


def validation_pdf(run):
    report = Report("Expense validation report")
    report.text("This report contains the entire saved batch, including receipts outside any screen filters.")
    report.section("Run and policy version", {key: run.get(key) for key in ("run_id", "created_at", "ruleset", "execution_mode", "preview", "context_complete", "classification", "execution")})
    report.section("Disposition summary", run.get("dispositions"))
    for receipt in run["results"]["results"]:
        report.story.append(PageBreak())
        report.section(f"Receipt {receipt['receipt_id']}", {key: receipt.get(key) for key in ("receipt_id", "disposition", "decided", "disposition_reasons", "open_questions", "uncertainty", "metadata", "provenance")})
        for name in ("line_items", "exceptions", "review_signals"):
            report.section(label(name), receipt.get(name, []))
    return report.finish()


def evaluation_pdf(payload):
    report = Report("Evaluation metrics and results")
    report.text("Metrics use the scoring version named below. Failed, cancelled or budget-limited runs may have partial coverage. Exporting makes no provider calls. Incomplete costs are lower bounds; missing values are not zero. Rates use 0–1 and fields name their units (ms, USD).")
    job = payload["evaluation"]
    report.section("Evaluation run", {key: job.get(key) for key in ("evaluation_id", "label", "status", "created_at", "started_at", "completed_at", "execution_mode", "preview", "dataset", "ruleset", "configuration", "progress", "stop_reason", "error")})
    report.section("Metrics and scoring provenance", payload["metrics"])
    report.section("Spending and latency", {key: job.get(key) for key in ("spend", "spend_summary", "execution") if key in job})
    for index, result in enumerate(payload["results"], 1):
        report.section(f"Receipt result {index}", result)
    return report.finish()


def comparison_pdf(comparison):
    report = Report("Evaluation comparison", wide=True)
    report.text("Original stored metrics, matching the comparison screen. Later rescoring is not substituted. The first selected run is the baseline; each group repeats it. Highlighted cells differ from the baseline. Rates use 0–1 and fields name their units.")
    report.section("Comparability and differences", comparison["comparability"])
    runs = comparison["evaluations"]
    baseline = runs[0]
    for offset in range(1, len(runs), 2):
        report.story.append(PageBreak())
        group = [baseline, *runs[offset:offset + 2]]
        values = [dict(flatten(run)) for run in group]
        keys = list(dict.fromkeys(key for value in values for key in value))
        report.table(["Metric / setting", *[run.get("label") or run["evaluation_id"] for run in group]],
            [(label(key), *[value.get(key, "Not recorded") for value in values]) for key in keys],
            [report.doc.width * .28, *[report.doc.width * .72 / len(group)] * len(group)], highlight_differences=True)
    return report.finish()
