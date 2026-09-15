"""PDF deficiency notice generation for the MoTA verification workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.models import ApplicantProfile, CompositeEvaluationResult

__all__ = ["DeficiencyPDFReport", "ExecutiveBriefingPDFReport"]


class DeficiencyPDFReport:
    """Render a formal, printable deficiency notice from a composite audit."""

    NAVY = colors.HexColor("#0B2545")
    GOLD = colors.HexColor("#FF9933")
    GREEN = colors.HexColor("#138808")
    INK = colors.HexColor("#1F2937")
    MUTED = colors.HexColor("#5C6B7A")
    PALE_BLUE = colors.HexColor("#EEF4FA")
    PALE_GOLD = colors.HexColor("#FFF4E0")
    BORDER = colors.HexColor("#D9E2EC")

    def __init__(self, *, page_size=A4) -> None:
        self.page_size = page_size
        styles = getSampleStyleSheet()
        self.title_style = ParagraphStyle(
            "MotaTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            alignment=TA_CENTER,
            textColor=self.NAVY,
            spaceAfter=2 * mm,
        )
        self.subtitle_style = ParagraphStyle(
            "MotaSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            alignment=TA_CENTER,
            textColor=self.MUTED,
        )
        self.section_style = ParagraphStyle(
            "MotaSection",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=self.NAVY,
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
        )
        self.body_style = ParagraphStyle(
            "MotaBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=self.INK,
            spaceAfter=1.5 * mm,
        )
        self.small_style = ParagraphStyle(
            "MotaSmall",
            parent=self.body_style,
            fontSize=7.5,
            leading=9.5,
            textColor=self.MUTED,
        )
        self.table_style = ParagraphStyle(
            "MotaTable",
            parent=self.body_style,
            fontSize=8,
            leading=10,
        )
        self.table_header_style = ParagraphStyle(
            "MotaTableHeader",
            parent=self.table_style,
            fontName="Helvetica-Bold",
            textColor=colors.white,
        )

    @staticmethod
    def _safe(value: object, fallback: str = "Not available") -> str:
        text = "" if value is None else str(value).strip()
        return escape(text or fallback)

    @staticmethod
    def _audit_date(result: CompositeEvaluationResult) -> str:
        timestamp = getattr(result, "evaluated_at", None)
        if isinstance(timestamp, datetime):
            return timestamp.astimezone(timezone.utc).strftime("%d %B %Y, %H:%M UTC")
        return datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    def _paragraph(self, text: object, style: ParagraphStyle | None = None) -> Paragraph:
        return Paragraph(self._safe(text), style or self.body_style)

    def _header(self) -> list[object]:
        emblem = Table(
            [[Paragraph("GOVERNMENT<br/><font size=7>OF INDIA</font>", self.table_header_style)]],
            colWidths=[25 * mm],
            rowHeights=[15 * mm],
        )
        emblem.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), self.NAVY),
                    ("BOX", (0, 0), (-1, -1), 1, self.GOLD),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ]
            )
        )
        title = [
            Paragraph("MINISTRY OF TRIBAL AFFAIRS", self.title_style),
            Paragraph("SCHOLARSHIP DIVISION", self.title_style),
            Paragraph("AI-Driven Scholarship &amp; Fellowship Verification System | SIH26239", self.subtitle_style),
        ]
        header = Table([[emblem, title]], colWidths=[31 * mm, 145 * mm])
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("LINEBELOW", (0, 0), (-1, -1), 2, self.GOLD),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return [header, Spacer(1, 5 * mm)]

    def _details_table(
        self, applicant: ApplicantProfile, result: CompositeEvaluationResult
    ) -> Table:
        rows = [
            [self._paragraph("Applicant ID", self.table_header_style), self._paragraph(applicant.applicant_id)],
            [self._paragraph("Scheme name", self.table_header_style), self._paragraph(applicant.scheme)],
            [self._paragraph("Date of audit", self.table_header_style), self._paragraph(self._audit_date(result))],
            [self._paragraph("Application reference ID", self.table_header_style), self._paragraph(f"MOTA/{applicant.applicant_id}")],
            [self._paragraph("Composite status", self.table_header_style), self._paragraph(result.composite_status.value)],
        ]
        table = Table(rows, colWidths=[48 * mm, 128 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), self.NAVY),
                    ("BACKGROUND", (1, 0), (1, -1), self.PALE_BLUE),
                    ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    def _missing_documents_table(self, result: CompositeEvaluationResult) -> Table:
        audit = result.document_audit_result
        rows = [[
            self._paragraph("Document", self.table_header_style),
            self._paragraph("Deficiency tier", self.table_header_style),
            self._paragraph("Required action", self.table_header_style),
        ]]
        for name in audit.missing_mandatory_docs:
            rows.append([self._paragraph(name), self._paragraph("Mandatory"), self._paragraph("Submit before disbursal")])
        for name in audit.missing_conditional_docs:
            rows.append([self._paragraph(name), self._paragraph("Conditional"), self._paragraph("Submit where applicable")])
        if len(rows) == 1:
            rows.append([self._paragraph("No missing documents"), self._paragraph("N/A"), self._paragraph("No action required")])
        table = Table(rows, colWidths=[77 * mm, 37 * mm, 62 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.PALE_BLUE]),
                    ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return table

    def _action_plan(self, result: CompositeEvaluationResult) -> list[object]:
        notice = result.deficiency_notice
        days = notice.cure_period_days if notice else 15
        action = notice.action_required if notice else "Submit all missing documents and request a fresh audit."
        flowables: list[object] = [
            Table(
                [[self._paragraph(f"15-DAY COMPLIANCE WINDOW", self.table_header_style), self._paragraph(f"Cure period: {days} calendar days", self.table_header_style)]],
                colWidths=[85 * mm, 91 * mm],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), self.GOLD),
                    ("TEXTCOLOR", (0, 0), (-1, -1), self.NAVY),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]),
            ),
            Spacer(1, 3 * mm),
        ]
        for line in action.splitlines():
            if line.strip():
                flowables.append(self._paragraph(line.replace("**", "")))
        return flowables

    def _footer(self, canvas, document) -> None:
        canvas.saveState()
        width, height = self.page_size
        canvas.setStrokeColor(self.GOLD)
        canvas.setLineWidth(0.6)
        canvas.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(self.MUTED)
        canvas.drawString(18 * mm, 10.5 * mm, "System Generated Audit Report - MoTA Verification Engine")
        canvas.drawRightString(width - 18 * mm, 10.5 * mm, f"Page {document.page}")
        canvas.setFont("Helvetica-Bold", 34)
        canvas.setFillColor(colors.Color(0.05, 0.15, 0.27, alpha=0.045))
        canvas.translate(width / 2, height / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, "MoTA VERIFICATION")
        canvas.restoreState()

    def render(
        self,
        applicant: ApplicantProfile,
        result: CompositeEvaluationResult,
    ) -> bytes:
        """Return a complete PDF document as bytes."""
        if not isinstance(applicant, ApplicantProfile):
            raise TypeError("applicant must be an ApplicantProfile")
        if not isinstance(result, CompositeEvaluationResult):
            raise TypeError("result must be a CompositeEvaluationResult")

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=self.page_size,
            rightMargin=17 * mm,
            leftMargin=17 * mm,
            topMargin=15 * mm,
            bottomMargin=21 * mm,
            title=f"MoTA Deficiency Notice - {applicant.applicant_id}",
            author="MoTA Verification Engine",
        )
        story: list[object] = []
        story.extend(self._header())
        story.append(Paragraph("FORMAL DOCUMENT DEFICIENCY NOTICE", self.section_style))
        story.append(self._details_table(applicant, result))
        story.append(Paragraph("Missing / deficient document checklist", self.section_style))
        story.append(self._missing_documents_table(result))
        story.append(Paragraph("Action plan", self.section_style))
        story.extend(self._action_plan(result))
        story.append(Spacer(1, 3 * mm))
        story.append(KeepTogether([
            Paragraph("Issuing authority", self.section_style),
            self._paragraph("Processing Cell, Ministry of Tribal Affairs", self.body_style),
            self._paragraph("This notice is system-generated from the composite rule and document audit. Retain the acknowledgement number after resubmission.", self.small_style),
        ]))
        document.build(story, onFirstPage=self._footer, onLaterPages=self._footer)
        return buffer.getvalue()

    def write(
        self,
        applicant: ApplicantProfile,
        result: CompositeEvaluationResult,
        output_path: str | Path,
    ) -> Path:
        """Render the notice and persist it to ``output_path``."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.render(applicant, result))
        return path


class ExecutiveBriefingPDFReport:
    """Render a concise ministry-facing batch audit briefing."""

    NAVY = colors.HexColor("#0B2545")
    GOLD = colors.HexColor("#FF9933")
    INK = colors.HexColor("#1F2937")
    MUTED = colors.HexColor("#5C6B7A")
    PALE_BLUE = colors.HexColor("#EEF4FA")
    BORDER = colors.HexColor("#D9E2EC")

    def generate_pdf(self, summary_dict: dict, metrics_df) -> BytesIO:
        """Build an executive summary PDF from summary values and metric rows."""
        summary = summary_dict or {}
        buffer = BytesIO()
        styles = getSampleStyleSheet()
        title = ParagraphStyle("BriefingTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=16, leading=19, alignment=TA_CENTER, textColor=self.NAVY)
        section = ParagraphStyle("BriefingSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=self.NAVY, spaceBefore=4 * mm, spaceAfter=2 * mm)
        body = ParagraphStyle("BriefingBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=self.INK)
        small = ParagraphStyle("BriefingSmall", parent=body, fontSize=7.5, leading=9.5, textColor=self.MUTED)

        def paragraph(value: object, style: ParagraphStyle = body) -> Paragraph:
            return Paragraph(escape(str(value)), style)

        def metric_rows() -> list[tuple[str, object]]:
            if metrics_df is None:
                return []
            if hasattr(metrics_df, "itertuples"):
                columns = list(getattr(metrics_df, "columns", []))
                return [
                    (str(row[0]), row[1])
                    for row in metrics_df.itertuples(index=False, name=None)
                    if len(columns) >= 2 and len(row) >= 2
                ]
            return [(str(row[0]), row[1]) for row in metrics_df if len(row) >= 2]

        document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=17 * mm, leftMargin=17 * mm, topMargin=15 * mm, bottomMargin=21 * mm, title="MoTA Executive Analytics Briefing", author="MoTA Verification Engine")
        total = summary.get("total", summary.get("total_applicants", 0))
        approved = summary.get("approved", summary.get("ready_for_disbursal", 0))
        provisional = summary.get("provisional", 0)
        rejected = summary.get("rejected", 0)
        approval_rate = float(summary.get("approval_rate", 0) or 0)
        allocation = summary.get("allocation_projection", summary.get("allocation", "Not captured"))
        bottlenecks = summary.get("bottlenecks", []) or []
        generated_at = summary.get("generated_at", datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC"))
        story: list[object] = [
            Paragraph("MINISTRY OF TRIBAL AFFAIRS", title),
            Paragraph("EXECUTIVE ANALYTICS BRIEFING", title),
            paragraph(f"AI-Driven Scholarship & Fellowship Verification System | Generated {generated_at}", small),
            Spacer(1, 5 * mm),
        ]
        kpi_rows = [
            [paragraph("Applications processed"), paragraph("Approval rate"), paragraph("Ready for disbursal"), paragraph("Projected allocation")],
            [paragraph(total), paragraph(f"{approval_rate:.1f}%"), paragraph(approved), paragraph(allocation)],
        ]
        kpi_table = Table(kpi_rows, colWidths=[44 * mm, 40 * mm, 44 * mm, 48 * mm])
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, 1), self.PALE_BLUE),
            ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([
            kpi_table,
            Paragraph("Workflow position", section),
            paragraph(f"{approved} ready for disbursal, {provisional} provisional pending document cure, and {rejected} rejected on rule eligibility."),
            Paragraph("Key bottlenecks", section),
        ])
        story.extend(paragraph(f"- {item}") for item in bottlenecks or ["No recurring rule bottleneck identified in this scope."])
        rows = metric_rows()
        if rows:
            story.append(Paragraph("Operational metrics", section))
            metric_table = Table([[paragraph("Metric"), paragraph("Value")]] + [[paragraph(name), paragraph(value)] for name, value in rows], colWidths=[145 * mm, 31 * mm], repeatRows=1)
            metric_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.PALE_BLUE]),
                ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
                ("ALIGN", (1, 0), (1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(metric_table)
        story.extend([Spacer(1, 4 * mm), paragraph("Allocation note: " + str(allocation), small)])

        def footer(canvas, doc) -> None:
            canvas.saveState()
            canvas.setStrokeColor(self.GOLD)
            canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(self.MUTED)
            canvas.drawString(18 * mm, 10.5 * mm, "System Generated Executive Brief - MoTA Verification Engine")
            canvas.drawRightString(A4[0] - 18 * mm, 10.5 * mm, f"Page {doc.page}")
            canvas.restoreState()

        document.build(story, onFirstPage=footer, onLaterPages=footer)
        buffer.seek(0)
        return buffer

    def render(
        self,
        *,
        generated_at: str,
        total: int,
        approved: int,
        provisional: int,
        rejected: int,
        approval_rate: float,
        allocation_projection: str,
        bottlenecks: list[str],
        deficiencies: list[tuple[str, int]],
        scheme_breakdown: dict[str, dict[str, int]] | None = None,
    ) -> bytes:
        buffer = BytesIO()
        styles = getSampleStyleSheet()
        title = ParagraphStyle("BriefTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=16, leading=19, alignment=TA_CENTER, textColor=self.NAVY)
        section = ParagraphStyle("BriefSection", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=self.NAVY, spaceBefore=4 * mm, spaceAfter=2 * mm)
        body = ParagraphStyle("BriefBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, textColor=self.INK)
        small = ParagraphStyle("BriefSmall", parent=body, fontSize=7.5, leading=9.5, textColor=self.MUTED)

        def paragraph(value: object, style: ParagraphStyle = body) -> Paragraph:
            return Paragraph(escape(str(value)), style)

        document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=17 * mm, leftMargin=17 * mm, topMargin=15 * mm, bottomMargin=21 * mm, title="MoTA Executive Analytics Briefing", author="MoTA Verification Engine")
        story: list[object] = [
            Paragraph("MINISTRY OF TRIBAL AFFAIRS", title),
            Paragraph("EXECUTIVE ANALYTICS BRIEFING", title),
            paragraph(f"AI-Driven Scholarship & Fellowship Verification System | Generated {generated_at}", small),
            Spacer(1, 5 * mm),
        ]
        kpi_rows = [
            [paragraph("Applications processed"), paragraph("Approval rate"), paragraph("Ready for disbursal"), paragraph("Projected allocation")],
            [paragraph(total), paragraph(f"{approval_rate:.1f}%"), paragraph(approved), paragraph(allocation_projection)],
        ]
        kpi_table = Table(kpi_rows, colWidths=[44 * mm, 40 * mm, 44 * mm, 48 * mm])
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, 1), self.PALE_BLUE),
            ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([kpi_table, Paragraph("Workflow position", section), paragraph(f"{approved} ready for disbursal, {provisional} provisional pending document cure, and {rejected} rejected on rule eligibility.")])
        story.append(Paragraph("Key bottlenecks", section))
        story.extend(paragraph(f"• {item}") for item in bottlenecks or ["No recurring rule bottleneck identified in this scope."])
        story.append(Paragraph("Top document deficiencies", section))
        deficiency_rows = [[paragraph("Document"), paragraph("Cases")]] + [[paragraph(name), paragraph(count)] for name, count in deficiencies]
        deficiency_table = Table(deficiency_rows, colWidths=[145 * mm, 31 * mm], repeatRows=1)
        deficiency_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.PALE_BLUE]),
            ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([deficiency_table, Paragraph("Scheme breakdown", section)])
        scheme_rows = [[paragraph("Scheme"), paragraph("Processed"), paragraph("Approved"), paragraph("Provisional"), paragraph("Rejected")]]
        for scheme, breakdown in (scheme_breakdown or {}).items():
            scheme_rows.append([
                paragraph(scheme),
                paragraph(breakdown.get("total", 0)),
                paragraph(breakdown.get("approved", 0)),
                paragraph(breakdown.get("provisional", 0)),
                paragraph(breakdown.get("rejected", 0)),
            ])
        if len(scheme_rows) == 1:
            scheme_rows.append([paragraph("No scheme data"), paragraph(0), paragraph(0), paragraph(0), paragraph(0)])
        scheme_table = Table(scheme_rows, colWidths=[76 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm], repeatRows=1)
        scheme_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.PALE_BLUE]),
            ("GRID", (0, 0), (-1, -1), 0.35, self.BORDER),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([scheme_table, Spacer(1, 4 * mm), paragraph("Allocation note: " + allocation_projection, small)])

        def footer(canvas, doc) -> None:
            canvas.saveState()
            canvas.setStrokeColor(self.GOLD)
            canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(self.MUTED)
            canvas.drawString(18 * mm, 10.5 * mm, "System Generated Executive Brief - MoTA Verification Engine")
            canvas.drawRightString(A4[0] - 18 * mm, 10.5 * mm, f"Page {doc.page}")
            canvas.restoreState()

        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return buffer.getvalue()
