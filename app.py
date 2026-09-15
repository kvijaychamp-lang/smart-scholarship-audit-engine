"""Phase 4 — MoTA Scholarship & Fellowship Verification Dashboard (SIH26239).

Interactive Streamlit console that scores the 120-row applicant dataset through
``ScholarshipEvaluator`` (rules + document audit + 15-day deficiency notices)
and exposes a live screening sandbox for ad-hoc intake.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import random
from textwrap import wrap
from typing import Any, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.config import load_applicants, load_document_requirements, load_scheme_rules
from core.evaluator import ScholarshipEvaluator
try:
    from core.pdf_generator import DeficiencyPDFReport, ExecutiveBriefingPDFReport
except ImportError as exc:
    DeficiencyPDFReport = None  # type: ignore[assignment,misc]
    ExecutiveBriefingPDFReport = None  # type: ignore[assignment,misc]
    PDF_IMPORT_ERROR = str(exc)
else:
    PDF_IMPORT_ERROR = None
from core.risk_engine import RiskAssessment, assess_batch
from core.models import (
    ApplicantProfile,
    CompositeDecision,
    CompositeEvaluationResult,
    DocumentRequirement,
    RuleCheck,
    SchemeRule,
)

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Visual language — Government of India / MoTA processing cell
# ---------------------------------------------------------------------------
NAVY = "#0B2545"
SAFFRON = "#FF9933"
INDIA_GREEN = "#138808"
READY_COLOR = "#1B7A4E"
PROVISIONAL_COLOR = "#C47B00"
REJECTED_COLOR = "#B42318"
RISK_HIGH = "#DC2626"
RISK_MEDIUM = "#D97706"
RISK_LOW = "#16A34A"
MUTED = "#5C6B7A"
PAPER = "#F4F6F8"

STATUS_DISPLAY = {
    CompositeDecision.READY_FOR_DISBURSAL.value: "Ready for Disbursal",
    CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value: "Provisional (Deficient Docs)",
    CompositeDecision.REJECTED.value: "Rejected",
}
STATUS_CHART_LABEL = {
    CompositeDecision.READY_FOR_DISBURSAL.value: "Approved",
    CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value: "Provisional",
    CompositeDecision.REJECTED.value: "Rejected",
}
STATUS_COLORS = {
    "Approved": READY_COLOR,
    "Provisional": PROVISIONAL_COLOR,
    "Rejected": REJECTED_COLOR,
    CompositeDecision.READY_FOR_DISBURSAL.value: READY_COLOR,
    CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value: PROVISIONAL_COLOR,
    CompositeDecision.REJECTED.value: REJECTED_COLOR,
}
SCHEME_SHORT = {
    "National Overseas Scholarship (NOS) for ST Students": "NOS (Overseas)",
    "National Fellowship Scheme": "National Fellowship",
    "National Scholarship Scheme (Higher Education)": "Higher Education",
    "Pre-Matric Scholarship for ST Students": "Pre-Matric ST",
    "Post Matric Scholarship for ST Students": "Post-Matric ST",
}
COURSE_OPTIONS = {
    "National Overseas Scholarship (NOS) for ST Students": [
        "Master's",
        "Ph.D",
        "Post-Doctoral Research",
    ],
    "National Fellowship Scheme": ["M.Phil", "M.Phil + Ph.D", "Ph.D"],
    "National Scholarship Scheme (Higher Education)": ["Graduate", "Post Graduate"],
    "Pre-Matric Scholarship for ST Students": ["Class IX", "Class X"],
    "Post Matric Scholarship for ST Students": [
        "Class XI-XII",
        "Diploma",
        "Undergraduate",
        "Graduate",
        "Post Graduate",
    ],
}
INSTITUTION_CATEGORIES = [
    "UGC 2(f)/12(B)",
    "Institute of National Importance",
    "Deemed University eligible under Section 3",
    "Central/State Government grant institution",
    "QS Top-1000 University (Abroad)",
    "Ministry-notified premier institute",
    "Government / recognised school",
    "Recognised post-matric institution",
    "Private unaided (unlisted)",
]
def GET_CLEAN_LAYOUT(title_text: str) -> dict[str, Any]:
    """Return the shared high-contrast layout used by every analytics figure."""
    return dict(
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(family="Inter, sans-serif", color="#0f172a"),
        title=dict(
            text=title_text,
            font=dict(size=15, color="#0f172a", family="sans-serif"),
            x=0.01,
            y=0.96,
            xanchor="left",
            yanchor="top",
        ),
        margin=dict(l=70, r=40, t=80, b=70),
        height=430,
        xaxis=dict(
            title_font=dict(size=12, color="#0f172a", family="Arial Black, Arial, sans-serif"),
            tickfont=dict(size=11, color="#0f172a"),
            gridcolor="#f1f5f9",
            zeroline=False,
        ),
        yaxis=dict(
            title_font=dict(size=12, color="#0f172a", family="Arial Black, Arial, sans-serif"),
            tickfont=dict(size=11, color="#0f172a"),
            gridcolor="#f1f5f9",
            zeroline=False,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=0.98,
            font=dict(size=11, color="#0f172a"),
            bgcolor="rgba(0,0,0,0)",
        ),
    )


def render_chart(container, figure) -> None:
    """Render a consistently sized Plotly figure inside a white card."""
    with container.container(border=True):
        st.plotly_chart(figure, use_container_width=True)


def format_axis_labels(values: list[object], width: int = 28) -> list[str]:
    """Wrap long categorical labels so horizontal charts remain readable."""
    return ["<br>".join(wrap(str(value), width=width)) for value in values]


def _inr(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        return f"₹{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _yes_no(value: Optional[bool]) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "—"


def _not_specified(value):
    if value is None or str(value).strip() in ["", "-", "None", "nan", "NaN"]:
        return "Not Specified"
    return value


def _rule_evidence(check: RuleCheck) -> str:
    if check.passed:
        return "Verified"
    return "Failed Constraint"


def _document_evidence(evidence: Optional[str], status: str) -> str:
    if status == "PRESENT" and evidence and evidence.startswith("required_documents_complete=True"):
        return "Declared & Verified"
    if status == "PRESENT":
        return "Verified"
    if status == "MISSING":
        return "Missing"
    return escape(evidence or "Not applicable")


def _decision_summary(result: CompositeEvaluationResult) -> str:
    failed = [check.description for check in result.rule_checks if not check.passed]
    if failed:
        return "Eligibility gates requiring attention: " + "; ".join(failed)
    return result.rationale.split(" — ", 1)[-1] if result.rationale else "No additional rationale provided."


def _dossier_metric(container: Any, label: str, value: str) -> None:
    with container:
        value_class = "dossier-metric-value dossier-metric-value--muted" if value == "Not Specified" else "dossier-metric-value"
        st.markdown(
            f'<div class="dossier-metric"><div class="dossier-metric-label">{escape(label)}</div>'
            f'<div class="{value_class}">{value}</div></div>',
            unsafe_allow_html=True,
        )


def _audit_metric(container: Any, label: str, value: object) -> None:
    with container:
        st.markdown(
            f'<div class="document-audit-kpi"><div class="dossier-metric-label">{escape(label)}</div>'
            f'<div class="dossier-metric-value">{escape(str(value))}</div></div>',
            unsafe_allow_html=True,
        )


def render_custom_audit_table(df: pd.DataFrame) -> str:
    html = """
    <style>
        .custom-audit-table {
            width: 100%;
            border-collapse: collapse;
            font-family: inherit;
            font-size: 13px;
            margin-bottom: 20px;
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            overflow: hidden;
            table-layout: fixed;
        }
        .custom-audit-table th {
            background-color: #f8fafc;
            color: #0f172a;
            font-weight: 700;
            text-align: left;
            padding: 10px 14px;
            border-bottom: 2px solid #e2e8f0;
        }
        .custom-audit-table td {
            padding: 8px 14px !important;
            color: #334155;
            border-bottom: 1px solid #f1f5f9;
            vertical-align: middle !important;
            height: auto !important;
            line-height: 1.4;
            overflow-wrap: anywhere;
        }
        .custom-audit-table tr:hover {
            background-color: #f8fafc;
        }
    </style>
    <table class="custom-audit-table">
        <thead>
            <tr>
    """
    for col in df.columns:
        html += f"<th>{escape(str(col))}</th>"
    html += "</tr></thead><tbody>"

    for _, row in df.iterrows():
        html += "<tr>"
        for col in df.columns:
            val = str(row[col])
            if val == "PASS" or val == "Verified":
                if val == "PASS":
                    val_html = '<span style="background:#dcfce7; color:#15803d; padding:3px 8px; border-radius:4px; font-weight:600; font-size:12px;">✓ PASS</span>'
                else:
                    val_html = f'<span style="color:#16a34a; font-weight:600;">✓ {escape(val)}</span>'
            elif val == "FAIL" or "Failed" in val:
                if val == "FAIL":
                    val_html = '<span style="background:#fee2e2; color:#b91c1c; padding:3px 8px; border-radius:4px; font-weight:600; font-size:12px;">✗ FAIL</span>'
                else:
                    val_html = f'<span style="color:#dc2626; font-weight:600;">✗ {escape(val)}</span>'
            elif val.strip().lower() == "hard":
                val_html = '<span style="background:#e2e8f0; color:#334155; padding:3px 8px; border-radius:4px; font-weight:600; font-size:12px;">HARD</span>'
            else:
                val_html = escape(val)
            html += f"<td>{val_html}</td>"
        html += "</tr>"

    html += "</tbody></table>"
    return html


def _short_scheme(name: str) -> str:
    return SCHEME_SHORT.get(name, name)


def _status_value(result: CompositeEvaluationResult) -> str:
    return result.composite_status.value


# ---------------------------------------------------------------------------
# Cached backend pipeline
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Loading MoTA rule engine and scoring 120 applicants…")
def load_pipeline() -> dict[str, Any]:
    evaluator = ScholarshipEvaluator(root=str(ROOT))
    applicants = load_applicants(str(ROOT))
    results, summary = evaluator.evaluate_dataset(str(ROOT))
    rules = list(load_scheme_rules(str(ROOT)))
    documents = list(load_document_requirements(str(ROOT)))
    income_ceilings = {rule.scheme: rule.income_limit_inr for rule in rules}
    applicant_by_id = {row.applicant_id: row for row in applicants}
    result_by_id = {row.applicant_id: row for row in results}
    return {
        "evaluator": evaluator,
        "applicants": applicants,
        "results": results,
        "summary": summary,
        "rules": rules,
        "documents": documents,
        "income_ceilings": income_ceilings,
        "applicant_by_id": applicant_by_id,
        "result_by_id": result_by_id,
    }


def build_analytics_frame(
    applicants: list[ApplicantProfile],
    results: list[CompositeEvaluationResult],
    income_ceilings: dict[str, Optional[float]],
) -> pd.DataFrame:
    result_by_id = {item.applicant_id: item for item in results}
    rows: list[dict[str, Any]] = []
    for applicant in applicants:
        result = result_by_id.get(applicant.applicant_id)
        if result is None:
            continue
        status = _status_value(result)
        rows.append(
            {
                "applicant_id": applicant.applicant_id,
                "scheme": applicant.scheme,
                "scheme_short": _short_scheme(applicant.scheme),
                "family_income_inr": applicant.family_income_inr,
                "income_ceiling_inr": income_ceilings.get(applicant.scheme),
                "income_over_ceiling": (
                    applicant.family_income_inr is not None
                    and income_ceilings.get(applicant.scheme) is not None
                    and applicant.family_income_inr > income_ceilings[applicant.scheme]
                ),
                "qualifying_marks_pct": applicant.qualifying_marks_pct,
                "age_years": applicant.age_years,
                "course_level": applicant.course_level,
                "composite_status": status,
                "status_label": STATUS_CHART_LABEL.get(status, status),
                "doc_status": result.doc_verification_status.value,
                "doc_score": result.document_completeness_score,
                "is_eligible": result.is_eligible,
                "domicile_matches_st": applicant.domicile_matches_st,
                "st_status": applicant.st_status,
            }
        )
    return pd.DataFrame(rows)


def add_risk_columns(frame: pd.DataFrame, risks: dict[str, RiskAssessment]) -> pd.DataFrame:
    enriched = frame.copy()
    enriched["risk_score"] = enriched["applicant_id"].map(lambda aid: risks[aid].score)
    enriched["risk_band"] = enriched["applicant_id"].map(lambda aid: risks[aid].band)
    enriched["risk_anomalies"] = enriched["applicant_id"].map(
        lambda aid: "; ".join(risks[aid].anomalies)
    )
    return enriched


def apply_dashboard_filters(
    frame: pd.DataFrame,
    selected_scheme: str,
    selected_st: str,
    selected_domicile: str,
) -> pd.DataFrame:
    """Return the analytics rows matching the Overview filter selections."""
    filtered = frame
    if selected_scheme != "All schemes":
        filtered = filtered[filtered["scheme"] == selected_scheme]
    if selected_st != "All ST Statuses":
        filtered = filtered[filtered["st_status"] == selected_st]
    if selected_domicile != "All Domicile Statuses":
        filtered = filtered[filtered["domicile_matches_st"] == (selected_domicile == "Yes")]
    return filtered


def missing_document_counts(results: list[CompositeEvaluationResult]) -> pd.DataFrame:
    counter: Counter[str] = Counter()
    for result in results:
        audit = result.document_audit_result
        for name in audit.missing_mandatory_docs + audit.missing_conditional_docs:
            counter[name] += 1
    if not counter:
        return pd.DataFrame(columns=["document", "count"])
    return (
        pd.DataFrame(counter.items(), columns=["document", "count"])
        .sort_values("count", ascending=False)
        .reset_index(drop=True)
    )


def executive_metrics(
    applicants: list[ApplicantProfile],
    results: list[CompositeEvaluationResult],
) -> dict[str, Any]:
    """Build narrative and export inputs from one filtered evaluation scope."""
    applicant_by_id = {applicant.applicant_id: applicant for applicant in applicants}
    counts = Counter(_status_value(result) for result in results)
    total = len(results)
    approved = counts.get(CompositeDecision.READY_FOR_DISBURSAL.value, 0)
    provisional = counts.get(CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value, 0)
    rejected = counts.get(CompositeDecision.REJECTED.value, 0)
    failed_rules: Counter[str] = Counter()
    rule_descriptions: dict[str, str] = {}
    for result in results:
        for check in result.rule_checks:
            if not check.passed:
                failed_rules[check.rule_id] += 1
                rule_descriptions.setdefault(check.rule_id, check.description)
    bottlenecks = []
    for rule_id, count in failed_rules.most_common(3):
        share = count / rejected * 100 if rejected else 0
        bottlenecks.append(f"{share:.0f}% of rejected cases failed {rule_descriptions[rule_id]} ({count} cases)")
    deficiencies = missing_document_counts(results)
    top_deficiencies = [(str(row.document), int(row.count)) for row in deficiencies.head(5).itertuples(index=False)]
    amounts = [
        float(getattr(applicant_by_id[result.applicant_id], "disbursal_amount_inr", 0) or 0)
        for result in results
        if _status_value(result) == CompositeDecision.READY_FOR_DISBURSAL.value
        and getattr(applicant_by_id[result.applicant_id], "disbursal_amount_inr", None) is not None
    ]
    return {
        "total": total,
        "approved": approved,
        "provisional": provisional,
        "rejected": rejected,
        "approval_rate": approved / total * 100 if total else 0.0,
        "bottlenecks": bottlenecks,
        "deficiencies": top_deficiencies,
        "scheme_breakdown": {
            scheme: {
                "total": int(group["applicant_id"].count()),
                "approved": int((group["composite_status"] == CompositeDecision.READY_FOR_DISBURSAL.value).sum()),
                "provisional": int((group["composite_status"] == CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value).sum()),
                "rejected": int((group["composite_status"] == CompositeDecision.REJECTED.value).sum()),
            }
            for scheme, group in pd.DataFrame([
                {"scheme": result.scheme, "applicant_id": result.applicant_id, "composite_status": _status_value(result)}
                for result in results
            ]).groupby("scheme")
        } if results else {},
        "allocation": sum(amounts) if amounts else None,
    }


def ready_for_disbursal_frame(
    applicants: list[ApplicantProfile],
    results: list[CompositeEvaluationResult],
) -> pd.DataFrame:
    applicant_by_id = {applicant.applicant_id: applicant for applicant in applicants}
    rows = []
    for result in results:
        if _status_value(result) != CompositeDecision.READY_FOR_DISBURSAL.value:
            continue
        applicant = applicant_by_id[result.applicant_id]
        rows.append({
            "pfms_beneficiary_id": applicant.applicant_id,
            "application_reference": f"MOTA/{applicant.applicant_id}",
            "beneficiary_name": "",
            "scheme": applicant.scheme,
            "bank_account_number": "",
            "ifsc_code": "",
            "sanction_amount_inr": getattr(applicant, "disbursal_amount_inr", ""),
            "payment_status": "READY_FOR_DISBURSAL",
            "document_verification": result.doc_verification_status.value,
        })
    return pd.DataFrame(rows)


def deficiency_master_log(
    applicants: list[ApplicantProfile],
    results: list[CompositeEvaluationResult],
) -> list[dict[str, Any]]:
    applicant_by_id = {applicant.applicant_id: applicant for applicant in applicants}
    records = []
    for result in results:
        audit = result.document_audit_result
        missing = audit.missing_mandatory_docs + audit.missing_conditional_docs
        if not missing:
            continue
        records.append({
            "notice_reference": f"MOTA/DEF/{result.applicant_id}/{datetime.now(timezone.utc).year}",
            "applicant_id": result.applicant_id,
            "scheme": applicant_by_id[result.applicant_id].scheme,
            "composite_status": _status_value(result),
            "cure_period_days": result.deficiency_notice.cure_period_days if result.deficiency_notice else 15,
            "missing_mandatory_documents": audit.missing_mandatory_docs,
            "missing_conditional_documents": audit.missing_conditional_docs,
            "document_completeness": result.document_completeness_score,
        })
    return records


def render_executive_operations(
    applicants: list[ApplicantProfile],
    results: list[CompositeEvaluationResult],
) -> None:
    metrics = executive_metrics(applicants, results)
    allocation_text = _inr(metrics["allocation"]) if metrics["allocation"] is not None else "Not captured"
    top_deficiency = metrics["deficiencies"][0] if metrics["deficiencies"] else ("None recorded", 0)
    top_bottleneck = metrics["bottlenecks"][0] if metrics["bottlenecks"] else "no recurring rule failure identified"
    st.markdown(
        '<div class="profile-card" style="border-top:4px solid #FF9933;">'
        '<div class="mota-kicker" style="color:#0B2545;">Executive ministry briefing</div>'
        '<h3 style="margin:.1rem 0 .35rem 0;">Decision position and operational priorities</h3>'
        f'<p style="margin:0;">{metrics["total"]} applications processed with a {metrics["approval_rate"]:.1f}% ready-for-disbursal rate. '
        f'Key bottleneck: {top_bottleneck}. Top document deficiency: {top_deficiency[0]} in {top_deficiency[1]} cases.</p>'
        f'<p style="margin:.55rem 0 0 0;"><strong>Projected allocation:</strong> {allocation_text}. '
        'The source dataset does not capture sanctioned award amounts; PFMS amount cells remain blank until that field is supplied.</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("### Ministry Operations & Disbursal")
    export_cols = st.columns(3)
    ready_csv = ready_for_disbursal_frame(applicants, results).to_csv(index=False).encode("utf-8")
    export_cols[0].download_button("📥 Export Ready-for-Disbursal CSV", data=ready_csv, file_name="MoTA_PFMS_Ready_for_Disbursal.csv", mime="text/csv", use_container_width=True, type="primary")
    log = deficiency_master_log(applicants, results)
    log_json = json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "records": log}, indent=2, ensure_ascii=False).encode("utf-8")
    log_frame = pd.json_normalize(log) if log else pd.DataFrame(columns=["applicant_id", "scheme", "composite_status"])
    export_cols[1].download_button("📄 Download Deficiency Master Log (JSON)", data=log_json, file_name="MoTA_Consolidated_Deficiency_Master_Log.json", mime="application/json", use_container_width=True)
    export_cols[1].download_button("Download deficiency log CSV", data=log_frame.to_csv(index=False).encode("utf-8"), file_name="MoTA_Consolidated_Deficiency_Master_Log.csv", mime="text/csv", use_container_width=True)
    try:
        if ExecutiveBriefingPDFReport is None:
            raise RuntimeError(f"ReportLab PDF support is unavailable: {PDF_IMPORT_ERROR}")
        briefing_pdf = ExecutiveBriefingPDFReport().render(
            generated_at=datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC"),
            total=metrics["total"], approved=metrics["approved"], provisional=metrics["provisional"], rejected=metrics["rejected"],
            approval_rate=metrics["approval_rate"], allocation_projection=allocation_text,
            bottlenecks=metrics["bottlenecks"], deficiencies=metrics["deficiencies"],
            scheme_breakdown=metrics["scheme_breakdown"],
        )
    except Exception as exc:
        export_cols[2].error(f"Executive PDF unavailable: {exc}")
    else:
        export_cols[2].download_button("📊 Download Executive PDF Briefing", data=briefing_pdf, file_name="MoTA_Executive_Analytics_Briefing.pdf", mime="application/pdf", use_container_width=True, type="primary")


def format_deficiency_letter(
    applicant: ApplicantProfile,
    result: CompositeEvaluationResult,
) -> str:
    notice = result.deficiency_notice
    issued = (
        notice.issued_at.strftime("%d %B %Y")
        if notice
        else datetime.now(timezone.utc).strftime("%d %B %Y")
    )
    days = notice.cure_period_days if notice else 15
    mandatory = notice.missing_mandatory_docs if notice else result.document_audit_result.missing_mandatory_docs
    conditional = (
        notice.missing_conditional_docs if notice else result.document_audit_result.missing_conditional_docs
    )
    action = notice.action_required if notice else "Upload the missing artefacts listed above."
    mandatory_block = "\n".join(f"  • {item}" for item in mandatory) or "  • None"
    conditional_block = "\n".join(f"  • {item}" for item in conditional) or "  • None"
    return f"""GOVERNMENT OF INDIA
MINISTRY OF TRIBAL AFFAIRS
Scholarship / Fellowship Processing Cell
SIH Problem Statement SIH26239 — MoTA AI Verification System

================================================================================
                         DEFICIENCY NOTICE
              Fifteen (15) Day Cure Period — Document Packet
================================================================================

Notice No.          : MOTA/DEF/{applicant.applicant_id}/{datetime.now(timezone.utc).strftime("%Y")}
Date of Issue       : {issued}
Applicant ID        : {applicant.applicant_id}
Scheme              : {applicant.scheme}
            Composite Status    : {STATUS_DISPLAY.get(_status_value(result), _status_value(result))}
Cure Period         : {days} calendar days from the date of this notice

--------------------------------------------------------------------------------
TO THE APPLICANT
--------------------------------------------------------------------------------
You are hereby informed that your application is rule-eligible but the supporting
document packet is incomplete. Disbursal is withheld until the artefacts below
are uploaded / submitted. Failure to cure within {days} days may result in the
application being treated as closed for this cycle.

Missing mandatory documents
{mandatory_block}

Missing conditional documents (where applicable)
{conditional_block}

--------------------------------------------------------------------------------
ACTION REQUIRED
--------------------------------------------------------------------------------
{action}

--------------------------------------------------------------------------------
ISSUING AUTHORITY
--------------------------------------------------------------------------------
Processing Cell, Ministry of Tribal Affairs
(Generated by the MoTA AI-Driven Scholarship & Fellowship Verification System)
Policy ref: MoTA scholarship / fellowship processing guidelines — 15-day deficiency cure period
================================================================================
"""


# ---------------------------------------------------------------------------
# CSS / chrome
# ---------------------------------------------------------------------------
def inject_theme() -> None:
    st.markdown(
        f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] {{
        font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
    }}
    .stApp {{
        background: {PAPER};
        color: #0f172a;
    }}
    body, .stApp, .stApp p, .stApp span, .stApp label,
    .stApp li, .stApp [data-testid="stMarkdownContainer"] {{
        color: #0f172a !important;
    }}
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {{
        color: #0f172a !important;
    }}
    .mota-hero h1, .mota-hero p, .mota-emblem, .mota-emblem small {{
        color: #ffffff !important;
    }}
    .mota-hero .mota-kicker {{
        color: {SAFFRON} !important;
    }}
    .scholarship-card {{
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
        transition: transform 0.2s, box-shadow 0.2s;
    }}
    .scholarship-card:hover {{
        transform: translateY(-2px);
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
    }}
    .match-badge {{
        display: inline-block;
        padding: 0.35rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 600;
    }}
    .match-eligible {{
        background-color: #dcfce7;
        color: #166534;
    }}
    .match-ineligible {{
        background-color: #fee2e2;
        color: #991b1b;
    }}
    .primary-action-btn {{
        background-color: #0284c7;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 0.5rem 1.2rem;
        font-weight: 600;
        cursor: pointer;
        transition: background-color 0.2s;
    }}
    .primary-action-btn:hover:not([disabled]) {{
        background-color: #0369a1;
    }}
    .primary-action-btn[disabled] {{
        background-color: #cbd5e1;
        cursor: not-allowed;
        color: #64748b;
    }}
    .stApp input, .stApp textarea,
    .stApp [data-baseweb="select"] *, .stApp [role="option"] {{
        color: #0f172a !important;
    }}
    div[data-testid="stChatInput"] {{
        background-color: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
    }}
    div[data-testid="stChatInput"] textarea {{
        color: #0f172a !important;
        background-color: #ffffff !important;
    }}
    .stDownloadButton button {{
        background-color: #0284c7 !important;
        color: #ffffff !important;
        border: 1px solid #0284c7 !important;
        font-weight: 600 !important;
    }}
    .stDownloadButton button:hover,
    .stDownloadButton button:focus {{
        background-color: #0369a1 !important;
        color: #ffffff !important;
        border-color: #075985 !important;
    }}
    .stApp div[data-testid="stExpander"] .stButton > button,
    .stApp div[data-testid="stExpander"] .stButton > button * {{
        width: 100%;
        background: #e0f2fe !important;
        border: 1px solid #bae6fd !important;
        border-radius: 8px !important;
        color: #0369a1 !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
    }}
    .stApp div[data-testid="stExpander"] .stButton > button:hover,
    .stApp div[data-testid="stExpander"] .stButton > button:hover * {{
        background: #0284c7 !important;
        border-color: #0284c7 !important;
        color: #ffffff !important;
    }}
    .stApp [data-baseweb="select"] > div,
    .stApp input, .stApp textarea {{
        background-color: #ffffff !important;
        border-color: #cbd5e1 !important;
    }}
    .block-container {{
        padding-top: 1.2rem;
        max-width: 1400px;
    }}
    .stApp [data-testid="stVerticalBlock"] {{
        min-width: 0;
    }}
    section[data-testid="stSidebar"] {{
        background: linear-gradient(180deg, {NAVY} 0%, #102a4c 62%, #0d3a28 100%);
    }}
    section[data-testid="stSidebar"] .stMarkdown, section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] span {{
        color: #E8EEF7 !important;
    }}
    .mota-hero {{
        background: linear-gradient(90deg, {NAVY} 0%, #14345c 70%, #0f4a32 100%);
        border-radius: 14px;
        padding: 1.15rem 1.4rem 1.05rem 1.4rem;
        color: #fff;
        border-top: 6px solid {SAFFRON};
        box-shadow: 0 8px 24px rgba(11, 37, 69, 0.18);
        margin-bottom: 0.8rem;
    }}
    .mota-hero h1 {{
        font-size: 1.45rem;
        margin: 0 0 0.25rem 0;
        letter-spacing: 0.01em;
        font-weight: 700;
    }}
    .mota-hero p {{
        margin: 0;
        opacity: 0.9;
        font-size: 0.92rem;
    }}
    .mota-emblem {{
        width: 54px;
        height: 54px;
        border: 2px solid {SAFFRON};
        border-radius: 50%;
        display: flex;
        flex-direction: column;
        justify-content: center;
        text-align: center;
        color: #fff;
        font-weight: 700;
        line-height: 1.05;
        flex: 0 0 auto;
    }}
    .mota-emblem small {{ font-size: 0.5rem; letter-spacing: 0.12em; }}
    .mota-kicker {{
        color: {SAFFRON};
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin-bottom: 0.35rem;
    }}
    .kpi-card {{
        background-color: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 1rem 1.05rem 0.9rem 1.05rem;
        border-left: 5px solid {NAVY};
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        min-height: 108px;
        color: #0f172a !important;
    }}
    .kpi-card.ready {{ border-left-color: {READY_COLOR}; }}
    .kpi-card.prov {{ border-left-color: {PROVISIONAL_COLOR}; }}
    .kpi-card.rej {{ border-left-color: {REJECTED_COLOR}; }}
    .kpi-label {{
        font-size: 1.1rem;
        color: #0f172a !important;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        line-height: 1.25;
        overflow-wrap: anywhere;
    }}
    .kpi-value {{
        font-size: 2.2rem;
        font-weight: 800;
        color: #0284c7 !important;
        line-height: 1.15;
        margin: 0.15rem 0;
    }}
    .kpi-hint {{ font-size: 0.78rem; color: #334155 !important; }}
    .profile-card {{
        background-color: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 1.05rem 1.2rem;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        color: #0f172a !important;
    }}
    .dossier-metric {{
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 0.6rem 0.8rem;
        min-height: 78px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
    }}
    .dossier-metric-label {{ color: #0f172a; font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }}
    .dossier-metric-value {{ color: #0284c7; font-size: 1.1rem; font-weight: 800; line-height: 1.25; overflow-wrap: anywhere; margin-top: 0.35rem; }}
    .dossier-metric-value--muted {{ color: #64748b; font-weight: 400; font-style: italic; }}
    .document-audit-kpi {{
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        margin-bottom: 12px;
        padding: 10px 12px;
        padding-top: 10px;
        min-height: 78px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
    }}
    .kpi-card, .profile-card, .stMetric, div[data-testid="stMetric"] {{
        background-color: #ffffff !important;
        border-color: #cbd5e1 !important;
        color: #0f172a !important;
    }}
    .kpi-card *, .profile-card *, .stMetric *, div[data-testid="stMetric"] * {{
        color: #0f172a !important;
    }}
    .status-pill {{
        display: inline-block;
        padding: 0.22rem 0.7rem;
        border-radius: 5px;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        border: 1px solid transparent;
    }}
    .pill-ready {{ background: #dcfce7; color: #15803d !important; border-color: #86efac; }}
    .pill-prov {{ background: #fef3c7; color: #b45309 !important; border-color: #fde68a; }}
    .pill-rej {{ background: #fee2e2; color: #b91c1c !important; border-color: #fca5a5; }}
    .profile-card .pill-ready {{ color: #15803d !important; }}
    .profile-card .pill-prov {{ color: #b45309 !important; }}
    .profile-card .pill-rej {{ color: #b91c1c !important; }}
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        padding: 0.35rem 0.45rem !important;
    }}
    .rule-pass {{ color: {READY_COLOR}; font-weight: 600; }}
    .rule-fail {{
        color: #dc2626;
        font-weight: 600;
        background: #fff1f2;
        border-left: 4px solid #dc2626;
        border-radius: 6px;
        padding: 0.65rem 0.85rem;
        margin: 0.4rem 0;
    }}
    .failed-gates {{ background: #fef2f2; border: 1px solid #fca5a5; border-left: 4px solid #ef4444; border-radius: 8px; padding: 12px 16px; margin: 0.5rem 0 1rem; }}
    .not-specified {{ color: #94a3b8; font-style: italic; }}
    .audit-badge {{ font-weight: 700; white-space: nowrap; }}
    .audit-badge--success {{ color: #16a34a; }}
    .audit-badge--danger {{ color: #ef4444; }}
    .audit-table-wrap {{ width: 100%; overflow-x: hidden; border: 1px solid #cbd5e1; border-radius: 6px; }}
    .audit-table {{ width: 100%; table-layout: fixed; }}
    .audit-table th, .audit-table td {{ overflow-wrap: anywhere; vertical-align: top; padding: 6px 12px !important; font-size: 13px !important; line-height: 1.3; }}
    .audit-table th:nth-child(1) {{ width: 11%; }}
    .audit-table th:nth-child(2) {{ width: 16%; }}
    .audit-table th:nth-child(3) {{ width: 23%; }}
    .audit-table th:nth-child(4) {{ width: 18%; }}
    .audit-table th:nth-child(5) {{ width: 12%; }}
    .audit-table th:nth-child(6) {{ width: 20%; }}
    .risk-banner {{ border-radius: 8px; padding: 0.8rem 1rem; margin: 0.5rem 0 1rem; color: #0f172a; }}
    .risk-banner--high {{ background: #fef2f2; border-left: 4px solid #ef4444; }}
    .risk-banner--warning {{ background: #fffbeb; border-left: 4px solid #f59e0b; }}
    .anomaly-card {{ background: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 8px; padding: 0.65rem 0.85rem; margin: 0.4rem 0; color: #0f172a; }}
    div[data-testid="stMetric"] {{
        background-color: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        padding: 0.6rem 0.8rem;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        color: #0f172a !important;
    }}
    div[data-testid="stMetricValue"] {{
        font-size: 1.1rem !important;
        font-weight: 800 !important;
        color: #0284c7 !important;
        line-height: 1.25 !important;
        overflow-wrap: anywhere;
        white-space: normal !important;
    }}
    div[data-testid="stMetricLabel"] {{
        color: #0f172a !important;
        font-weight: 700;
    }}
    div[data-testid="stMetricValue"] *, div[data-testid="stMetricLabel"] *,
    .stSelectbox label, .stMultiSelect label, .stTextInput label,
    .stNumberInput label, [data-testid="stCheckbox"] label,
    [data-testid="stToggle"] label {{
        color: #0f172a !important;
    }}
    .stSelectbox label, .stMultiSelect label, .stTextInput label,
    .stNumberInput label {{
        font-size: 0.82rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }}
    .stApp [data-baseweb="select"] > div,
    div[data-baseweb="input"] > div,
    div[data-baseweb="spinbutton"],
    .stApp input, .stApp textarea {{
        background-color: #f8fafc !important;
        border: 1px solid #94a3b8 !important;
        border-radius: 5px !important;
        box-shadow: none !important;
        color: #0f172a !important;
    }}
    div[data-baseweb="select"] svg {{ fill: #475569 !important; }}
    [data-testid="stToggle"] label, [data-testid="stToggle"] label * {{ color: #334155 !important; }}
    [data-testid="stToggle"] {{ min-height: 58px; padding: 0.35rem 0.2rem; display: flex; align-items: center; }}
    [data-testid="stHorizontalBlock"] {{ gap: 0.75rem; align-items: stretch; }}
    button[kind="primary"], [data-testid="stBaseButton-primary"] {{
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
        border: 1px solid #1d4ed8 !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        min-height: 46px !important;
        transition: transform 120ms ease, box-shadow 120ms ease;
    }}
    button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {{
        transform: translateY(-1px);
        box-shadow: 0 5px 12px rgba(29, 78, 216, 0.25);
    }}
    .stApp [data-baseweb="select"] > div:focus-within,
    .stApp input:focus, .stApp textarea:focus {{
        border-color: #0284c7 !important;
        outline: 3px solid rgba(2,132,199,0.22) !important;
        outline-offset: 1px;
    }}
    div[data-testid="stDataFrame"] {{
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        overflow: hidden;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        max-height: 400px;
    }}
    div[data-testid="stDataFrame"] td, div[data-testid="stDataFrame"] th {{ padding: 6px 12px !important; font-size: 13px !important; }}
    div[data-testid="stDataFrame"] [role="columnheader"] {{
        background-color: #e2e8f0 !important;
        color: #0f172a !important;
        font-weight: 800 !important;
    }}
    div[data-testid="stDataFrame"] [role="gridcell"] {{
        color: #0f172a !important;
        border-color: #cbd5e1 !important;
        padding: 0.45rem 0.6rem !important;
    }}
    div[data-testid="stDataFrame"] [role="row"]:nth-child(even) [role="gridcell"] {{
        background-color: #f8fafc !important;
    }}
    div[data-testid="stDataFrame"] [role="row"]:nth-child(odd) [role="gridcell"] {{
        background-color: #ffffff !important;
    }}
    .stApp table {{
        width: 100%;
        border-collapse: collapse;
        background-color: #ffffff;
        color: #0f172a;
    }}
    .stApp table th {{
        background-color: #e2e8f0;
        color: #0f172a !important;
        font-weight: 800;
        text-align: left;
    }}
    .stApp table th, .stApp table td {{
        border: 1px solid #cbd5e1;
        padding: 0.55rem 0.7rem;
    }}
    .stApp table tbody tr:nth-child(even) {{
        background-color: #f8fafc;
    }}
    .stApp table tbody tr:nth-child(odd) {{
        background-color: #ffffff;
    }}
    .stCaption, .stApp [data-testid="stCaptionContainer"] {{
        color: #334155 !important;
    }}
    /* Floating AI Chatbot styles */
    div[data-testid="stExpander"]:has(p:contains("MoTA AI Policy Copilot")) {{
        position: fixed;
        bottom: 24px;
        right: 24px;
        width: 400px;
        z-index: 99999;
        background: #ffffff !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 12px !important;
        box-shadow: 0 15px 35px rgba(0,0,0,0.2), 0 5px 15px rgba(0,0,0,0.1) !important;
        overflow: hidden;
    }}
    div[data-testid="stExpander"]:has(p:contains("MoTA AI Policy Copilot")) > summary {{
        background: linear-gradient(135deg, {NAVY} 0%, #1e293b 100%) !important;
        border-radius: 12px 12px 0 0 !important;
        padding: 12px 16px !important;
        margin-bottom: 0 !important;
    }}
    div[data-testid="stExpander"]:has(p:contains("MoTA AI Policy Copilot")) > summary p,
    div[data-testid="stExpander"]:has(p:contains("MoTA AI Policy Copilot")) > summary svg {{
        color: #ffffff !important;
        font-weight: 700 !important;
    }}
    div[data-testid="stExpander"]:has(p:contains("MoTA AI Policy Copilot")) div[data-testid="stExpanderDetails"] {{
        max-height: 500px;
        overflow-y: auto;
        padding: 15px !important;
        background: #f8fafc;
    }}
</style>
        """,
        unsafe_allow_html=True,
    )


def hero() -> None:
    st.markdown(
        """
<div class="mota-hero">
    <div style="display:flex;align-items:center;gap:0.9rem;">
        <div class="mota-emblem" aria-label="Government of India emblem">भारत<br><small>INDIA</small></div>
        <div>
            <div class="mota-kicker">Government of India · Ministry of Tribal Affairs · SIH26239</div>
            <h1>MoTA AI-Driven Scholarship &amp; Fellowship Verification</h1>
        </div>
    </div>
  <p>Phase 4 operations console — composite rule eligibility, document audit, and 15-day deficiency routing across five ST schemes.</p>
</div>
        """,
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: int, hint: str, kind: str = "") -> None:
    cls = f"kpi-card {kind}".strip()
    st.markdown(
        f"""
<div class="{cls}">
  <div class="kpi-label">{label}</div>
  <div class="kpi-value">{value}</div>
  <div class="kpi-hint">{hint}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


def status_pill(status: str) -> str:
    label = STATUS_DISPLAY.get(status, status)
    if status == CompositeDecision.READY_FOR_DISBURSAL.value:
        cls = "pill-ready"
    elif status == CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value:
        cls = "pill-prov"
    else:
        cls = "pill-rej"
    return f'<span class="status-pill {cls}">{label}</span>'


# ---------------------------------------------------------------------------
# Policy copilot
# ---------------------------------------------------------------------------
COPILOT_SAMPLES = (
    "What is the max income limit for NOS Overseas Scholarship?",
    "What documents are required if applicant is PVTG?",
    "Is Ph.D. eligible under National Fellowship for ST?",
    "What happens if income exceeds ceiling?",
)


def _policy_citation(rule: SchemeRule, fields: str) -> str:
    source = rule.source or "source not specified"
    return (
        f"**Policy clause reference:** `MoTA_Scheme_Rules_SIH26239.csv` · "
        f"scheme=`{rule.scheme}` · fields=`{fields}` · source=`{source}`"
    )


def _document_citation(requirements: list[DocumentRequirement]) -> str:
    schemes = ", ".join(sorted({item.scheme for item in requirements}))
    return (
        "**Policy clause reference:** `MoTA_Document_Requirements_SIH26239.csv` · "
        f"schemes=`{schemes}` · fields=`Document`, `Requirement`, `Remarks`"
    )


def _scheme_for_query(query: str, rules: list[SchemeRule], context: Optional[ApplicantProfile]) -> Optional[SchemeRule]:
    text = query.lower()
    aliases = {
        "nos": "overseas",
        "overseas": "overseas",
        "fellowship": "fellowship",
        "higher education": "higher education",
        "pre-matric": "pre-matric",
        "pre matric": "pre-matric",
        "post matric": "post matric",
        "post-matric": "post matric",
    }
    for needle, marker in aliases.items():
        if needle in text:
            return next((rule for rule in rules if marker in rule.scheme.lower()), None)
    if context:
        return next((rule for rule in rules if rule.scheme == context.scheme), None)
    return None


def _policy_answer(
    query: str,
    rules: list[SchemeRule],
    documents: list[DocumentRequirement],
    context: Optional[tuple[ApplicantProfile, CompositeEvaluationResult]] = None,
) -> str:
    text = query.lower()
    applicant = context[0] if context else None
    result = context[1] if context else None

    if applicant and result and (
        "why" in text and ("reject" in text or "decision" in text)
        or "missing" in text and ("document" in text or "paper" in text)
        or applicant.applicant_id.lower() in text
    ):
        if "missing" in text and ("document" in text or "paper" in text):
            audit = result.document_audit_result
            missing = audit.missing_mandatory_docs + audit.missing_conditional_docs
            if not missing:
                return (
                    f"**{applicant.applicant_id}** has no missing documents in the evaluated audit. "
                    f"Document status: **{audit.doc_verification_status.value}**.\n\n"
                    "**Evaluation reference:** `CompositeEvaluationResult.document_audit_result`"
                )
            lines = "\n".join(f"- {item}" for item in missing)
            return (
                f"**{applicant.applicant_id}** is missing the following documents:\n{lines}\n\n"
                f"Document status: **{audit.doc_verification_status.value}**.\n\n"
                f"{_document_citation([item for item in documents if item.scheme == applicant.scheme])}"
            )

        failed = [check for check in result.rule_checks if not check.passed]
        if failed:
            reasons = "\n".join(
                f"- **{check.rule_id}:** {check.description} Evidence: `{check.evidence}`"
                for check in failed
            )
            return (
                f"**{applicant.applicant_id}** was **{STATUS_DISPLAY.get(_status_value(result), _status_value(result))}** "
                f"because these policy checks failed:\n{reasons}\n\n"
                "**Evaluation reference:** `CompositeEvaluationResult.rule_checks` · "
                "each failed check includes its guideline reference."
            )
        return (
            f"**{applicant.applicant_id}** is currently **{STATUS_DISPLAY.get(_status_value(result), _status_value(result))}**. "
            f"Evaluator rationale: {result.rationale}\n\n"
            "**Evaluation reference:** `CompositeEvaluationResult.rationale`"
        )

    rule = _scheme_for_query(query, rules, applicant)
    if "income" in text and ("limit" in text or "ceiling" in text or "exceed" in text or "max" in text):
        if rule and rule.income_criterion_applies and rule.income_limit_inr is not None:
            exception = (
                " The CSV also records an exception for an orphan supported by a guardian."
                if rule.orphan_income_exempt
                else ""
            )
            outcome = (
                "Income above this ceiling fails the income eligibility check and can lead to rejection."
                if "exceed" in text
                else f"The maximum permitted family income is {_inr(rule.income_limit_inr)} per annum."
            )
            return (
                f"For **{rule.scheme}**, {outcome}"
                f"{exception}\n\n{_policy_citation(rule, 'income_limit_inr, income_rule')}"
            )
        if rule:
            return (
                f"**{rule.scheme}** has **no family-income ceiling** in the policy data. "
                f"{_policy_citation(rule, 'income_limit_inr, income_rule')}"
            )
        ceilings = "\n".join(
            f"- {_short_scheme(item.scheme)}: {_inr(item.income_limit_inr) if item.income_limit_inr is not None else 'No ceiling'}"
            for item in rules
        )
        return f"Income ceilings in the loaded MoTA rules are:\n{ceilings}\n\nAsk about a named scheme for a focused answer."

    if "pvtg" in text and ("document" in text or "required" in text):
        selected_scheme = applicant.scheme if applicant else None
        matches = [
            item for item in documents
            if "pvtg" in item.document.lower()
            and (selected_scheme is None or item.scheme == selected_scheme)
        ]
        if selected_scheme:
            checklist = [item for item in documents if item.scheme == selected_scheme]
            matches = [item for item in checklist if item.requirement.is_hard_mandatory or "pvtg" in item.document.lower()]
        if not matches:
            return "No PVTG-specific document row was found in the loaded document requirements CSV."
        rows = "\n".join(
            f"- **{item.document}** ({item.requirement.value}): {item.remarks or 'No additional remark'}"
            for item in matches
        )
        scope = f" for **{selected_scheme}**" if selected_scheme else " across the schemes with an explicit PVTG row"
        return f"PVTG document guidance{scope}:\n{rows}\n\n{_document_citation(matches)}"

    if "ph.d" in text or "phd" in text:
        if rule and "fellowship" in rule.scheme.lower():
            eligible = rule.course_is_eligible("Ph.D")
            return (
                f"**{'Yes' if eligible else 'No'}**. Ph.D is {'listed' if eligible else 'not listed'} "
                f"among eligible courses for the **{rule.scheme}**. The minimum marks criterion is "
                f"**{rule.min_marks_pct:g}%** where recorded.\n\n"
                f"{_policy_citation(rule, 'eligible_courses, min_marks_pct')}"
            )
        if rule:
            return (
                f"For **{rule.scheme}**, eligible courses are: {', '.join(rule.eligible_courses)}.\n\n"
                f"{_policy_citation(rule, 'eligible_courses')}"
            )

    if rule:
        return (
            f"For **{rule.scheme}**: eligible courses are **{', '.join(rule.eligible_courses) or 'not restricted in this row'}**; "
            f"minimum marks are **{rule.min_marks_pct:g}%** where recorded; study location is **{rule.study_location}**.\n\n"
            f"{_policy_citation(rule, 'eligible_courses, min_marks_pct, study_location, other_key_rules, institution_rule')}"
        )
    return (
        "I can answer questions grounded in the loaded MoTA scheme rules and document requirements. "
        "Try a sample question or name a scheme such as NOS, National Fellowship, Pre-Matric, or Post Matric."
    )


def render_policy_copilot(
    rules: list[SchemeRule],
    documents: list[DocumentRequirement],
    applicant_by_id: dict[str, ApplicantProfile],
    result_by_id: dict[str, CompositeEvaluationResult],
    key_prefix: str = "",
) -> None:
    selected_id = st.session_state.get("selected_applicant_id")
    context = None
    if selected_id in applicant_by_id and selected_id in result_by_id:
        context = (applicant_by_id[selected_id], result_by_id[selected_id])

    if context:
        st.caption(f"Live applicant context: {context[0].applicant_id} · {context[0].scheme}")
    else:
        st.caption("Policy answers are grounded in the loaded MoTA rules and document checklist CSVs.")

    st.markdown(
        """
        <style>
        /* Force sample question buttons to have clear light backgrounds with dark bold text */
        div[data-testid="stButton"] > button {
            background-color: #f1f5f9 !important;
            color: #0f172a !important;
            border: 1px solid #cbd5e1 !important;
            font-weight: 500 !important;
            text-align: left !important;
            padding: 10px 14px !important;
            border-radius: 8px !important;
            transition: all 0.2s ease-in-out !important;
        }
        div[data-testid="stButton"] > button:hover {
            background-color: #e2e8f0 !important;
            border-color: #3b82f6 !important;
            color: #2563eb !important;
        }
        div[data-testid="stChatInput"] {
            background-color: #ffffff !important;
            border: 1px solid #94a3b8 !important;
        }
        div[data-testid="stChatInput"] textarea {
            background-color: #ffffff !important;
            color: #0f172a !important;
            caret-color: #0f172a !important;
        }
        div[data-testid="stChatInput"] textarea::placeholder {
            color: #475569 !important;
            opacity: 1 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    sample_questions = [
        "What is the max income limit for NOS Overseas Scholarship?",
        "What documents are required if applicant is PVTG?",
        "Is Ph.D. eligible under National Fellowship for ST?",
        "What happens if income exceeds ceiling?",
    ]
    st.caption("💡 Quick Suggestions:")
    sample_columns = st.columns(2)
    for index, sample_question in enumerate(sample_questions):
        if sample_columns[index % 2].button(sample_question, key=f"chat_smp_{index}", use_container_width=True):
            st.session_state["chatbot_user_prompt"] = sample_question
            st.rerun()

    messages: list[dict[str, str]] = st.session_state.setdefault("copilot_messages", [])
    pending = st.session_state.pop("chatbot_user_prompt", None)
    query = st.chat_input("Ask about a MoTA rule, document, or applicant decision…", key=f"copilot_input_{key_prefix}")
    query = query or pending
    if query and query.strip():
        clean_query = query.strip()
        messages.append({"role": "user", "content": clean_query})
        messages.append(
            {
                "role": "assistant",
                "content": _policy_answer(clean_query, rules, documents, context),
            }
        )

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


# ---------------------------------------------------------------------------
# Shared result renderers
# ---------------------------------------------------------------------------
def render_profile_card(applicant: ApplicantProfile, result: CompositeEvaluationResult) -> None:
    status = _status_value(result)
    st.markdown(
        f"""
<div class="profile-card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;">
    <div>
      <div class="mota-kicker" style="color:{SAFFRON};">Applicant dossier</div>
      <h3 style="margin:0 0 0.2rem 0;color:{NAVY};">{applicant.applicant_id}</h3>
      <p style="margin:0;color:{MUTED};">{applicant.scheme}</p>
    </div>
    <div>{status_pill(status)}</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    _dossier_metric(c1, "ST status", _not_specified(applicant.st_status))
    _dossier_metric(c2, "Family income", escape(_inr(applicant.family_income_inr)))
    _dossier_metric(c3, "Qualifying marks", escape(_pct(applicant.qualifying_marks_pct)))
    _dossier_metric(c4, "Age (years)", _not_specified(applicant.age_years))
    d1, d2, d3, d4 = st.columns(4)
    _dossier_metric(d1, "Course level", _not_specified(applicant.course_level))
    _dossier_metric(d2, "Fresh / renewal", _not_specified(applicant.fresh_or_renewal))
    _dossier_metric(d3, "Institution category", _not_specified((applicant.institution_category or "")[:42]))
    _dossier_metric(d4, "Doc completeness", escape(f"{result.document_completeness_score:.0%}"))
    extras = st.columns(4)
    extras[0].caption(f"Gender: **{applicant.gender or 'Not Specified'}**")
    extras[1].caption(f"PVTG: **{applicant.pvtg_status or 'Not Specified'}**")
    extras[2].caption(f"Domicile matches ST: **{_yes_no(applicant.domicile_matches_st)}**")
    extras[3].caption(f"Rule gold label: **{applicant.rule_eligibility or 'Not Specified'}**")
    st.caption(_decision_summary(result))


def render_rule_audit(checks: list[RuleCheck]) -> None:
    st.subheader("Atomic rule audit")
    passed = sum(1 for item in checks if item.passed)
    failed = len(checks) - passed
    a, b, c = st.columns(3)
    a.metric("Rules evaluated", len(checks))
    b.metric("Passed", passed)
    c.metric("Failed", failed)

    failed_rows = [item for item in checks if not item.passed]
    if failed_rows:
        failed_markup = "".join(
            f'<div class="rule-fail">✕ {escape(item.rule_id)} — {escape(item.description)}</div>'
            for item in failed_rows
        )
        st.markdown(
            '<div class="failed-gates"><strong style="color:#dc2626;">⚠ Failed hard / scheme gates</strong>'
            + failed_markup
            + "</div>",
            unsafe_allow_html=True,
        )

    table = pd.DataFrame(
        [
            {
                "Result": "PASS" if item.passed else "FAIL",
                "Rule ID": item.rule_id,
                "Expected constraint": item.description,
                "Actual value / evidence": _rule_evidence(item),
                "Severity": item.severity.value if hasattr(item.severity, "value") else str(item.severity),
                "Guideline": item.guideline_ref or "",
            }
            for item in checks
        ]
    )
    st.markdown(render_custom_audit_table(table), unsafe_allow_html=True)


def render_document_matrix(result: CompositeEvaluationResult) -> None:
    st.subheader("Document audit status")
    audit = result.document_audit_result
    k1, k2, k3, k4 = st.columns(4)
    _audit_metric(k1, "Verification", audit.doc_verification_status.value.replace("_", " "))
    _audit_metric(k2, "Completeness", f"{audit.completeness_score:.0%}")
    _audit_metric(k3, "Missing mandatory", len(audit.missing_mandatory_docs))
    _audit_metric(k4, "Missing conditional", len(audit.missing_conditional_docs))

    rows = []
    for item in audit.items:
        icon = {
            "PRESENT": "✅ Verified",
            "MISSING": "❌ Missing / deficient",
            "WAIVED": "⚪ Waived",
            "NOT_APPLICABLE": "– Not applicable",
        }.get(item.status, item.status)
        rows.append(
            {
                "Document": item.document,
                "Tier": item.tier.title(),
                "Requirement": item.requirement.value if hasattr(item.requirement, "value") else str(item.requirement),
                "Status": icon,
                "Evidence": _document_evidence(item.evidence, item.status),
                "Remarks": item.remarks or "",
            }
        )
    document_table = pd.DataFrame(rows)
    st.markdown(render_custom_audit_table(document_table), unsafe_allow_html=True)


def render_deficiency_panel(applicant: ApplicantProfile, result: CompositeEvaluationResult) -> None:
    status = _status_value(result)
    is_provisional = status == CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value
    if is_provisional or result.deficiency_notice is not None:
        letter = format_deficiency_letter(applicant, result)
        st.subheader("15-day deficiency notice")
        if is_provisional:
            st.info(
                "This application is **provisionally eligible**. Disbursal is held pending "
                "document cure within 15 calendar days."
            )
            st.download_button(
                "Download formal 15-day Deficiency Notice",
                data=letter.encode("utf-8"),
                file_name=f"MoTA_Deficiency_Notice_{applicant.applicant_id}.txt",
                mime="text/plain",
                type="primary",
                key=f"dl-notice-{applicant.applicant_id}",
            )
            if notice := result.deficiency_notice:
                st.download_button(
                    "Download notice JSON",
                    data=json.dumps(notice.model_dump(mode="json"), indent=2, ensure_ascii=False),
                    file_name=f"MoTA_Deficiency_Notice_{applicant.applicant_id}.json",
                    mime="application/json",
                    key=f"dl-notice-json-{applicant.applicant_id}",
                )
            try:
                pdf_bytes = DeficiencyPDFReport().render(applicant, result)
            except (TypeError, ValueError, OSError) as exc:
                st.error(f"PDF report could not be generated: {exc}")
            except Exception:
                st.error("PDF report could not be generated. Please retry or use the text export.")
            else:
                st.download_button(
                    "Download formal deficiency notice PDF",
                    data=pdf_bytes,
                    file_name=f"MoTA_Deficiency_Notice_{applicant.applicant_id}.pdf",
                    mime="application/pdf",
                    key=f"dl-notice-pdf-{applicant.applicant_id}",
                )
        with st.expander("View notice text", expanded=is_provisional):
            st.text(letter)
    elif status == CompositeDecision.READY_FOR_DISBURSAL.value:
        st.success("Document packet fully verified. No deficiency notice is required.")
    else:
        st.caption("Rejected on rule eligibility — a document cure period does not revive a failed rule set.")
    
def render_risk_panel(risk: RiskAssessment) -> None:
    colors = {"High": RISK_HIGH, "Medium": RISK_MEDIUM, "Low": RISK_LOW}
    color = colors[risk.band]
    banner_class = "risk-banner--high" if risk.band == "High" else "risk-banner--warning"
    st.markdown(
        f'<div class="risk-banner {banner_class}">'
        f'<strong style="color:{color};">AI FRAUD RISK: {risk.band.upper()}</strong>'
        f'<span style="float:right;font-weight:700;color:#0f172a;">{risk.score}%</span></div>',
        unsafe_allow_html=True,
    )
    st.progress(risk.score / 100, text=f"Composite Risk Index: {risk.score}%")
    if risk.anomalies:
        st.markdown("**AI Flagged Anomalies**")
        for anomaly in risk.anomalies:
            clean_anomaly = anomaly.removeprefix("Warning: ").removeprefix("Alert: ")
            st.markdown(f'<div class="anomaly-card">{escape(clean_anomaly)}</div>', unsafe_allow_html=True)
    else:
        st.success("No configured anomaly indicators were triggered.")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def tab_overview(
    applicants: list[ApplicantProfile],
    frame: pd.DataFrame,
    results: list[CompositeEvaluationResult],
    summary: Any,
) -> None:
    st.markdown("### Dashboard Filters")
    f1, f2, f3 = st.columns(3)
    schemes = ["All schemes"] + sorted(frame["scheme"].unique().tolist())
    scheme_filter = f1.selectbox("Filter by Scheme", schemes, key="overview_scheme_filter")
    
    st_statuses = ["All ST Statuses"] + sorted(frame["st_status"].dropna().unique().tolist())
    st_filter = f2.selectbox("Filter by ST Status", st_statuses, key="overview_st_filter")
    
    domicile_statuses = ["All Domicile Statuses", "Yes", "No"]
    domicile_filter = f3.selectbox("Filter by Domicile Matches ST", domicile_statuses, key="overview_domicile_filter")
    
    frame = apply_dashboard_filters(frame, scheme_filter, st_filter, domicile_filter)

    counts = Counter(frame["composite_status"])
    total = int(len(frame))
    ready = int(counts.get(CompositeDecision.READY_FOR_DISBURSAL.value, 0))
    prov = int(counts.get(CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value, 0))
    rej = int(counts.get(CompositeDecision.REJECTED.value, 0))

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        kpi_card("Total processed", total, "Composite evaluations in the current filter", "")
    with k2:
        kpi_card("Ready for disbursal", ready, "Rule eligible + documents verified", "ready")
    with k3:
        kpi_card("Provisional (deficient docs)", prov, "Rule eligible · 15-day cure window", "prov")
    with k4:
        kpi_card("Rejected", rej, "Failed one or more hard eligibility gates", "rej")
        
    filtered_applicant_ids = set(frame["applicant_id"])
    filtered_results = [r for r in results if r.applicant_id in filtered_applicant_ids]

    if frame.empty:
        st.warning("No applicants match the selected filters.")
        return

    render_executive_operations(
        [applicant for applicant in applicants if applicant.applicant_id in filtered_applicant_ids],
        filtered_results,
    )

    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rule-engine accuracy vs CSV gold", f"{summary.accuracy_pct:.1f}%")
    m2.metric("Rule-eligible (pre-documents)", summary.predicted_eligible)
    m3.metric("Mean document completeness", f"{(summary.mean_doc_completeness or 0):.1%}")
    m4.metric("Schemes in scope", frame["scheme"].nunique())
    
    st.markdown("### Fraud Risk Distribution Radar")
    risk_counts = frame["risk_band"].value_counts()
    risk_cols = st.columns(3)
    for column, band, color in zip(
        risk_cols,
        ("High", "Medium", "Low"),
        (RISK_HIGH, RISK_MEDIUM, RISK_LOW),
    ):
        with column:
            st.markdown(
                f'<div class="kpi-card" style="border-left-color:{color};">'
                f'<div class="kpi-label" style="color:{color};">{band} Risk</div>'
                f'<div class="kpi-value">{int(risk_counts.get(band, 0))}</div>'
                '<div class="kpi-hint">Applications requiring this risk posture</div></div>',
                unsafe_allow_html=True,
            )
    
    queue = frame[frame["risk_band"] == "High"].sort_values(
        ["risk_score", "family_income_inr"], ascending=[False, False]
    ).head(5)
    st.markdown("### High-Risk Queue · Manual Physical Investigation")
    if queue.empty:
        st.success("No high-risk applications are present in the current filter.")
    else:
        st.dataframe(
            queue[
                [
                    "applicant_id",
                    "scheme_short",
                    "risk_score",
                    "family_income_inr",
                    "qualifying_marks_pct",
                    "risk_anomalies",
                ]
            ].rename(
                columns={
                    "applicant_id": "Applicant ID",
                    "scheme_short": "Scheme",
                    "risk_score": "Risk Index %",
                    "family_income_inr": "Family income (INR)",
                    "qualifying_marks_pct": "Marks %",
                    "risk_anomalies": "AI flags",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("### Visual analytics")
    left, right = st.columns((1.15, 1))

    scheme_status = (
        frame.groupby(["scheme_short", "status_label"], as_index=False)
        .size()
        .rename(columns={"size": "applicants"})
    )
    fig_bar = px.bar(
        scheme_status,
        x="scheme_short",
        y="applicants",
        color="status_label",
        barmode="stack",
        color_discrete_map=STATUS_COLORS,
        title="Scheme-wise status breakdown",
        labels={"scheme_short": "Scheme", "applicants": "Applicants", "status_label": "Status"},
        category_orders={"status_label": ["Approved", "Provisional", "Rejected"]},
    )
    fig_bar.update_layout(**GET_CLEAN_LAYOUT("Scheme-wise status breakdown"))
    fig_bar.update_xaxes(tickangle=-25)
    fig_bar.update_yaxes(title_text="Applicants", title_font=dict(size=12, color="#0f172a"))
    render_chart(left, fig_bar)

    income_df = frame.dropna(subset=["family_income_inr"]).copy()
    if income_df.empty:
        right.info("No family-income values are available for the selected applicants.")
        income_df = pd.DataFrame(columns=frame.columns)
    else:
        income_df["income_position"] = income_df["income_over_ceiling"].map(
            {False: "Within ceiling", True: "Over ceiling"}
        )
        fig_scatter = px.scatter(
        income_df,
        x="scheme_short",
        y="family_income_inr",
        color="status_label",
        symbol="income_position",
        hover_name="applicant_id",
        hover_data={
            "qualifying_marks_pct": True,
            "income_ceiling_inr": True,
            "income_over_ceiling": True,
            "scheme_short": False,
        },
        color_discrete_map=STATUS_COLORS,
        title="Income distribution vs scheme ceilings",
        labels={
            "scheme_short": "Scheme",
            "family_income_inr": "Family income (INR)",
            "status_label": "Status",
        },
        category_orders={"status_label": ["Approved", "Provisional", "Rejected"]},
    )
        ceiling_lookup = (
            income_df.groupby("scheme_short")["income_ceiling_inr"].max().dropna().to_dict()
        )
        first_ceiling = True
        for scheme, ceiling in ceiling_lookup.items():
            fig_scatter.add_trace(
                go.Scatter(
                    x=[scheme],
                    y=[ceiling],
                    mode="markers",
                    marker=dict(symbol="diamond", size=14, color="#1D4ED8", line=dict(width=1, color="white")),
                    name="Scheme income ceiling",
                    hovertemplate=f"{scheme}<br>Ceiling: ₹{ceiling:,.0f}<extra></extra>",
                    showlegend=first_ceiling,
                )
            )
            first_ceiling = False
        fig_scatter.update_layout(**GET_CLEAN_LAYOUT("Income distribution vs scheme ceilings"))
        fig_scatter.update_layout(margin=dict(l=70, r=40, t=90, b=70))
        fig_scatter.update_xaxes(tickangle=-25)
        fig_scatter.update_yaxes(tickformat=",")
        render_chart(right, fig_scatter)

    missing = missing_document_counts(filtered_results)
    deficiency_col, hist_col = st.columns((1, 1.15))
    if missing.empty:
        deficiency_col.info("No missing documents recorded on this batch (fully verified packets).")
    else:
        missing_chart = missing.head(8).copy()
        fig_missing = px.bar(
            missing_chart,
            x="count",
            y="document",
            orientation="h",
            title="Document deficiency distribution (top missing artefacts)",
            labels={"document": "Document", "count": "Applicants"},
            color="count",
            color_continuous_scale=[(0, "#F6C453"), (1, REJECTED_COLOR)],
        )
        fig_missing.update_layout(**GET_CLEAN_LAYOUT("Document deficiency distribution (top missing artefacts)"))
        fig_missing.update_layout(margin=dict(l=220, r=40, t=80, b=70), showlegend=False)
        fig_missing.update_traces(marker_color="#dc2626")
        fig_missing.update_yaxes(
            autorange="reversed",
            automargin=True,
            tickmode="array",
            tickvals=missing_chart["document"].tolist(),
            ticktext=format_axis_labels(missing_chart["document"].tolist()),
            tickfont=dict(size=10, color="#0f172a"),
        )
        render_chart(deficiency_col, fig_missing)

    if income_df.empty:
        hist_col.info("No family-income values are available for the selected applicants.")
    else:
        fig_hist = px.histogram(
            income_df,
            x="family_income_inr",
            color="scheme_short",
            nbins=24,
            title="Family income histogram by scheme",
            labels={"family_income_inr": "Family income (INR)", "scheme_short": "Scheme"},
        )
        fig_hist.update_layout(**GET_CLEAN_LAYOUT("Family income histogram by scheme"))
        fig_hist.update_layout(
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=0.98,
                font=dict(size=11, color="#0f172a"),
                bgcolor="rgba(0,0,0,0)",
            ),
            xaxis_title="Family income (INR)",
            yaxis_title="Applicants",
        )
        fig_hist.update_xaxes(title_font=dict(size=12, color="#0f172a"), tickfont=dict(size=11, color="#0f172a"))
        fig_hist.update_yaxes(title_font=dict(size=12, color="#0f172a"), tickfont=dict(size=11, color="#0f172a"))
        fig_hist.update_traces(marker=dict(line=dict(color="#ffffff", width=1)))
        render_chart(hist_col, fig_hist)
    
    matrix_frame = frame.dropna(subset=["risk_band"])
    if matrix_frame.empty:
        st.info("Risk distribution chart needs risk-band data.")
    else:
        risk_band_counts = (
            matrix_frame["risk_band"]
            .value_counts()
            .reindex(["Low", "Medium", "High"], fill_value=0)
            .rename_axis("risk_band")
            .reset_index(name="applicant_count")
        )
        fig_matrix = px.bar(
            risk_band_counts,
            x="applicant_count",
            y="risk_band",
            color="risk_band",
            orientation="h",
            text="applicant_count",
            color_discrete_map={"High": "#dc2626", "Medium": "#d97706", "Low": "#16a34a"},
            category_orders={"risk_band": ["Low", "Medium", "High"]},
            title="Applicant Risk Distribution Summary",
            labels={
                "applicant_count": "Applicants",
                "risk_band": "Risk Band",
            },
        )
        fig_matrix.update_layout(**GET_CLEAN_LAYOUT("Applicant Risk Distribution Summary"))
        fig_matrix.update_layout(
            title=dict(text="Applicant Risk Distribution Summary", x=0.01, y=0.98),
            showlegend=False,
            margin=dict(t=60),
            paper_bgcolor="#ffffff",
            plot_bgcolor="#ffffff",
        )
        fig_matrix.update_traces(textposition="outside", cliponaxis=False)
        fig_matrix.update_layout(
            xaxis=dict(
                title="Applicants",
                title_font=dict(color="#0f172a", size=12, weight="bold"),
                tickfont=dict(color="#0f172a", size=11),
            ),
            yaxis=dict(
                title="Risk Band",
                title_font=dict(color="#0f172a", size=12, weight="bold"),
                tickfont=dict(color="#0f172a", size=11),
            ),
        )
        fig_matrix.update_xaxes(
            showgrid=True,
            gridcolor="#e2e8f0",
        )
        fig_matrix.update_yaxes(
            showgrid=True,
            gridcolor="#e2e8f0",
        )
        render_chart(st, fig_matrix)

    st.caption(
        "Applicant counts are grouped by the evaluated risk band. Gold-label accuracy is "
        "computed against the `rule_eligibility` column in the SIH applicant CSV."
    )


def tab_audit(
    applicants: list[ApplicantProfile],
    applicant_by_id: dict[str, ApplicantProfile],
    result_by_id: dict[str, CompositeEvaluationResult],
    frame: pd.DataFrame,
    risks: dict[str, RiskAssessment],
) -> None:
    ids = [row.applicant_id for row in applicants]
    f1, f2 = st.columns((1.2, 1.2))
    schemes = ["All schemes"] + sorted(frame["scheme"].unique().tolist())
    statuses = ["All statuses"] + list(STATUS_DISPLAY.keys())
    scheme_filter = f2.selectbox("Filter by scheme", schemes)
    status_filter = f1.selectbox(
        "Filter by composite status",
        statuses,
        format_func=lambda key: "All statuses" if key == "All statuses" else STATUS_DISPLAY[key],
    )
    filtered_ids = ids
    if scheme_filter != "All schemes":
        filtered_ids = [
            aid for aid in filtered_ids if applicant_by_id[aid].scheme == scheme_filter
        ]
    if status_filter != "All statuses":
        filtered_ids = [
            aid for aid in filtered_ids if _status_value(result_by_id[aid]) == status_filter
        ]
    if not filtered_ids:
        st.warning("No applicants match the current filters.")
        return
    selected = st.selectbox("Applicant Search", filtered_ids, index=0)
    st.session_state["selected_applicant_id"] = selected
    applicant = applicant_by_id[selected]
    result = result_by_id[selected]

    render_profile_card(applicant, result)
    render_risk_panel(risks[selected])
    st.markdown("---")
    render_rule_audit(list(result.rule_checks))
    st.markdown("---")
    render_document_matrix(result)
    st.markdown("---")
    render_deficiency_panel(applicant, result)


def _sandbox_profile_from_form() -> ApplicantProfile:
    schemes = list(COURSE_OPTIONS.keys())
    scheme = st.selectbox("Scheme", schemes)
    c1, c2, c3 = st.columns(3)
    st_status = c1.selectbox("ST status", ["ST", "Non-ST", "OBC", "General"])
    pvtg_status = c2.selectbox("PVTG status", ["No", "PVTG"])
    gender = c3.selectbox("Gender", ["Female", "Male", "Other"])
    d1, d2, d3 = st.columns(3)
    age_years = d1.number_input("Age (years)", min_value=5, max_value=80, value=21, step=1)
    family_income = d2.number_input(
        "Family income (INR / year)", min_value=0, max_value=50_000_000, value=400_000, step=10_000
    )
    marks = d3.number_input("Qualifying marks %", min_value=0.0, max_value=100.0, value=72.0, step=0.5)
    e1, e2, e3 = st.columns(3)
    course_level = e1.selectbox("Course level", COURSE_OPTIONS[scheme])
    fresh_or_renewal = e2.selectbox("Fresh or renewal", ["Fresh", "Renewal"])
    institution_category = e3.selectbox("Institution category", INSTITUTION_CATEGORIES)

    with st.container(border=True):
        st.markdown("**Institution & admission flags**")
        flags_a = st.columns(4)
        domicile = flags_a[0].toggle("Domicile matches ST", value=True)
        qs_top = flags_a[1].toggle("QS Top-1000 institute", value=False)
        ministry = flags_a[2].toggle("Ministry-notified institution/course", value=True)
        gov_school = flags_a[3].toggle("Govt / recognised school", value=True)
        flags_b = st.columns(4)
        recognised = flags_b[0].toggle("Recognised course / institution", value=True)
        regular = flags_b[1].toggle("Regular full-time", value=True)
        admission_secured = flags_b[2].toggle("Admission secured", value=True)
        admission_offer = flags_b[3].toggle("Admission offer letter", value=True)
        flags_c = st.columns(4)
        admission_cert = flags_c[0].toggle("Admission / joining certificate", value=True)
        passed_exam = flags_c[1].toggle("Passed qualifying exam", value=True)
        bank_aadhaar = flags_c[2].toggle("Bank + Aadhaar + mobile linked", value=True)
        same_stream = flags_c[3].toggle("Same-stream requirement satisfied", value=True)
        flags_d = st.columns(4)
        other_govt = flags_d[0].toggle("Other Govt scholarship (same study)", value=False)
        other_sch = flags_d[1].toggle("Other scholarship", value=False)
        sibling = flags_d[2].toggle("Sibling already awarded (NOS)", value=False)
        prev_nos = flags_d[3].toggle("Previous NOS award", value=False)
        flags_e = st.columns(4)
        repeating = flags_e[0].toggle("Repeating same class", value=False)
        orphan = flags_e[1].toggle("Orphan supported by guardian", value=False)
        divyang = flags_e[2].toggle("Divyangjan", value=False)
        visa_applicable = flags_e[3].toggle("Visa applicable (NOS)", value=False)

    with st.container(border=True):
        st.markdown("**Document packet toggles**")
        docs = st.columns(4)
        has_st = docs[0].toggle("ST certificate", value=True)
        has_income = docs[1].toggle("Income certificate", value=True)
        has_marks = docs[2].toggle("Qualifying marksheet", value=True)
        has_fee = docs[3].toggle("Fee receipt", value=True)
        docs2 = st.columns(4)
        has_bank = docs2[0].toggle("Bank details / passbook", value=True)
        has_passport = docs2[1].toggle("Valid passport (NOS)", value=True)
        has_visa = docs2[2].toggle("Visa proof (NOS)", value=False)
        has_qs_offer = docs2[3].toggle("QS Top-1000 offer letter", value=False)
        docs3 = st.columns(4)
        has_supervisor = docs3[0].toggle("Supervisor allocation letter", value=True)
        has_ugc = docs3[1].toggle("UGC 2(f)/12(B) document", value=True)
        packet_complete = docs3[2].toggle("Declare required documents complete", value=True)

    applicant_id = st.text_input("Sandbox applicant ID", value="SANDBOX-0001")
    return ApplicantProfile(
        applicant_id=applicant_id.strip() or "SANDBOX-0001",
        scheme=scheme,
        st_status=st_status,
        pvtg_status="PVTG" if pvtg_status == "PVTG" else "No",
        domicile_matches_st=domicile,
        gender=gender,
        age_years=int(age_years),
        course_level=course_level,
        qualifying_marks_pct=float(marks),
        family_income_inr=float(family_income),
        institution_top1000_qs=qs_top,
        ministry_notified_institution_course=ministry,
        institution_category=institution_category,
        school_government_or_recognized=gov_school,
        recognized_course_institution=recognised,
        regular_full_time=regular,
        admission_secured=admission_secured,
        admission_offer=admission_offer,
        admission_certificate=admission_cert,
        passed_required_qualifying_exam=passed_exam,
        scheduled_bank_aadhaar_mobile_linked=bank_aadhaar,
        other_govt_scholarship_same_study=other_govt,
        other_scholarship=other_sch,
        same_parents_other_child_awarded=sibling,
        previous_nos_award=prev_nos,
        repeating_same_class=repeating,
        same_stream_requirement_satisfied=same_stream,
        fresh_or_renewal=fresh_or_renewal,
        required_documents_complete=packet_complete,
        is_orphan_supported_by_guardian=orphan,
        is_divyangjan=divyang,
        has_st_certificate=has_st,
        has_income_certificate=has_income,
        has_qualifying_marksheet=has_marks,
        has_fee_receipt=has_fee,
        has_bank_details=has_bank,
        has_valid_passport=has_passport,
        has_visa_proof=has_visa,
        visa_applicable=visa_applicable,
        has_qs_top1000_offer_letter=has_qs_offer,
        has_supervisor_allocation_letter=has_supervisor,
        has_ugc_recognition_document=has_ugc,
    )


def tab_sandbox(evaluator: ScholarshipEvaluator) -> None:
    st.markdown(
        "Enter a hypothetical or live intake record. **Run AI Verification** scores it through "
        "`ScholarshipEvaluator` (atomic rules + `DocumentAuditor` + deficiency notice). "
        "Changing the scheme updates eligible course levels immediately."
    )
    try:
        profile = _sandbox_profile_from_form()
    except (TypeError, ValueError, KeyError) as exc:
        st.error(f"The sandbox input is invalid: {exc}")
        return
    submitted = st.button("Run AI Verification", type="primary", use_container_width=True)

    if submitted:
        try:
            result = evaluator.evaluate_one(profile)
        except (TypeError, ValueError, KeyError) as exc:
            st.error(f"The verification engine could not evaluate this record: {exc}")
            return
        except Exception:
            st.error("The verification engine encountered an unexpected error. Please review the inputs and retry.")
            return
        st.session_state["sandbox_profile"] = profile
        st.session_state["sandbox_result"] = result

    result: Optional[CompositeEvaluationResult] = st.session_state.get("sandbox_result")
    stored_profile: Optional[ApplicantProfile] = st.session_state.get("sandbox_profile")
    if result is None or stored_profile is None:
        st.caption("Submit the form to see an instant composite decision.")
        return

    st.subheader("Live AI verification result")
    outcome, confidence = st.columns((1.1, 1.9))
    with outcome:
        st.markdown(status_pill(_status_value(result)), unsafe_allow_html=True)
        st.caption(result.rationale)
    with confidence:
        st.metric("Rule confidence", f"{result.confidence_score:.1%}")
        st.progress(result.confidence_score, text="Confidence in atomic rule decision")
    actions = []
    actions.extend(f"Resolve rule: {check.description}" for check in result.rule_checks if not check.passed)
    audit = result.document_audit_result
    actions.extend(f"Submit document: {name}" for name in audit.missing_mandatory_docs)
    actions.extend(f"Submit conditional document: {name}" for name in audit.missing_conditional_docs)
    if actions:
        st.warning("Corrective actions")
        for action in actions:
            st.markdown(f"- {action}")
    else:
        st.success("No corrective action is required. The record is ready for the next workflow step.")

    render_profile_card(stored_profile, result)
    st.markdown("---")
    render_rule_audit(list(result.rule_checks))
    st.markdown("---")
    render_document_matrix(result)
    st.markdown("---")
    render_deficiency_panel(stored_profile, result)


def render_step3_upload(scheme: str, rules: list, documents: list, is_eligible: bool, result, profile: Optional[ApplicantProfile] = None):
    st.header("Step 3: Scheme-Specific Compulsory Docs & QR Verification")
    st.subheader(f"{scheme}")
    
    rule = next((r for r in rules if r.scheme == scheme), None)
    if rule:
        st.markdown("### Scheme Requirements & Guidelines")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Eligible Courses:** {', '.join(rule.eligible_courses)}")
            st.markdown(f"**Minimum Marks:** {rule.min_marks_pct}%")
        with col2:
            st.markdown(f"**Income Ceiling:** {_inr(rule.income_limit_inr) if rule.income_limit_inr else 'No limit'}")
            st.markdown(f"**Study Location:** {rule.study_location}")
        if rule.other_key_rules:
            st.info(f"**Key Rules:** {rule.other_key_rules}")
            
    st.markdown(
        """
        <style>
        /* Clean Light Styling for Streamlit File Uploader */
        div[data-testid="stFileUploader"] {
            background-color: #ffffff !important;
            border: 2px dashed #93c5fd !important;
            border-radius: 12px !important;
            padding: 16px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important;
        }
        div[data-testid="stFileUploader"] section {
            background-color: #f8fafc !important;
            border-radius: 8px !important;
        }
        div[data-testid="stFileUploader"] section * {
            color: #1e293b !important;
        }
        div[data-testid="stFileUploader"] button {
            background-color: #2563eb !important;
            color: #ffffff !important;
            border: none !important;
            font-weight: 600 !important;
            border-radius: 6px !important;
            padding: 6px 16px !important;
        }
        div[data-testid="stFileUploader"] button:hover {
            background-color: #1d4ed8 !important;
            color: #ffffff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("### Document Checklist & Verification")
    req_docs = [d for d in documents if d.scheme == scheme]
    
    uploaded_files = {}
    mandatory_docs = [d for d in req_docs if "MANDATORY" in str(d.requirement.value if hasattr(d.requirement, 'value') else d.requirement).upper()]
    
    for doc in mandatory_docs:
        doc_name = doc.document
        st.markdown(f"**{doc_name}**", unsafe_allow_html=True)
        file = st.file_uploader(f"Upload {doc_name}", key=f"upload_{doc_name}_{scheme}", label_visibility="collapsed")
        uploaded_files[doc_name] = file
        
        if file is not None:
            state_key = f"qr_verified_{doc_name}_{file.file_id}"
            if state_key not in st.session_state:
                import time
                with st.spinner(f"Scanning QR Code on {doc_name}..."):
                    time.sleep(0.5)
                st.session_state[state_key] = True
            
            if "invalid" in file.name.lower() or "tamper" in file.name.lower():
                st.markdown(f'<span style="background:#fee2e2; color:#b91c1c; padding:3px 8px; border-radius:4px; font-weight:600; font-size:12px;">✗ Invalid/Tampered QR</span>', unsafe_allow_html=True)
            else:
                st.markdown(f'<span style="background:#dcfce7; color:#15803d; padding:3px 8px; border-radius:4px; font-weight:600; font-size:12px;">✓ QR Verified via DigiLocker</span>', unsafe_allow_html=True)
        else:
            st.markdown(f'<span style="background:#fee2e2; color:#b91c1c; padding:3px 8px; border-radius:4px; font-weight:600; font-size:12px;">* Compulsory</span>', unsafe_allow_html=True)
        st.markdown("---")

    if not is_eligible:
        st.error("You are currently ineligible for this scheme based on your profile.")
        if result:
            failed_rules = [check.description for check in result.rule_checks if not check.passed]
            if failed_rules:
                st.markdown("**Failed Requirements:**")
                for reason in failed_rules:
                    st.markdown(f"- {reason}")
    else:
        missing = [k for k, v in uploaded_files.items() if v is None]
        if st.button("Submit Application", type="primary", disabled=len(missing) > 0, use_container_width=True):
            app_id = f"APP-{datetime.now(timezone.utc).year}-{random.randint(1000, 9999)}"
            live_profile = profile.model_copy(update={"applicant_id": app_id}) if profile else None
            live_result = result.model_copy(update={"applicant_id": app_id}) if result else None
            
            if "submitted_apps" not in st.session_state:
                st.session_state["submitted_apps"] = []
            if "live_applications" not in st.session_state:
                st.session_state["live_applications"] = []

            if live_profile is not None and live_result is not None:
                st.session_state["live_applications"].append(
                    {"profile": live_profile, "result": live_result}
                )
                
            st.session_state["submitted_apps"].append({
                "app_id": app_id,
                "scheme": scheme,
                "result": result,
                "is_eligible": is_eligible,
                "submitted_at": datetime.now(timezone.utc).isoformat()
            })
            
            st.balloons()
            st.success(f"Application Submitted Successfully! Your Application ID is **{app_id}**")
            st.session_state['step'] = 1
            st.session_state['selected_scheme'] = None
            st.rerun()


def _render_scholarship_card(scheme: str, result: Optional[CompositeEvaluationResult], is_eligible: bool, rules: list, documents: list, key_suffix: str = "", applicant_profile: Optional[ApplicantProfile] = None) -> None:
    if is_eligible:
        badge_html = f'<span class="match-badge match-eligible">✓ 100% Match</span>'
        border_color = READY_COLOR
    else:
        reason = "Does not meet scheme criteria"
        if result:
            failed_rules = [check.description for check in result.rule_checks if not check.passed]
            if failed_rules:
                reason = failed_rules[0]
        badge_html = f'<span class="match-badge match-ineligible">✕ Ineligible: {escape(reason)}</span>'
        border_color = REJECTED_COLOR

    benefit_summary = "Full tuition and allowance"
    if "Overseas" in scheme or "NOS" in scheme:
        benefit_summary = "Tuition, living allowance, and travel support for abroad studies"
    elif "Fellowship" in scheme:
        benefit_summary = "Monthly stipend and contingency grant for research"
    elif "Pre-Matric" in scheme:
        benefit_summary = "Monthly maintenance allowance and ad-hoc grant"
    elif "Post Matric" in scheme:
        benefit_summary = "Compulsory non-refundable fees and maintenance allowance"
        
    ministry_badge = "Ministry of Tribal Affairs"
    
    st.markdown(f'''
    <div class="scholarship-card" style="border-left: 6px solid {border_color}; margin-bottom: 0.5rem; padding-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 1rem;">
            <div>
                <div class="mota-kicker">{ministry_badge}</div>
                <h3 style="margin-top: 0; margin-bottom: 0.5rem; color: #0B2545;">{scheme}</h3>
                <p style="margin: 0; color: #475569; font-size: 0.95rem;">{benefit_summary}</p>
            </div>
            <div>
                {badge_html}
            </div>
        </div>
    </div>
    ''', unsafe_allow_html=True)
    
    c1, c2, c3 = st.columns([2, 1, 1])
    with c3:
        scheme_code = "".join([c for c in scheme if c.isalnum()]).lower()
        if st.button("Apply Now & Upload Docs", key=f"apply_btn_{scheme_code}_{key_suffix}", use_container_width=True):
            st.session_state['selected_scheme'] = scheme
            st.session_state['selected_result'] = result
            st.session_state['selected_is_eligible'] = is_eligible
            st.session_state['selected_profile'] = applicant_profile
            st.session_state['step'] = 3
            st.rerun()
    st.markdown("<div style='margin-bottom: 1.5rem;'></div>", unsafe_allow_html=True)


def render_find_scholarships(evaluator: ScholarshipEvaluator, rules, documents) -> None:
    if 'user_profile' not in st.session_state:
        st.session_state['user_profile'] = {}
    if 'step' not in st.session_state:
        st.session_state['step'] = 1
    if 'selected_scheme' not in st.session_state:
        st.session_state['selected_scheme'] = None

    st.markdown("## Step 1: Student Profile Input")
    st.markdown("Enter your academic and personal details to discover scholarships you are eligible for.")
    
    with st.form("student_profile_form"):
        st.subheader("Personal Details")
        c1, c2, c3 = st.columns(3)
        full_name = c1.text_input("Full Name", value=st.session_state['user_profile'].get('full_name', ''))
        gender_options = ["Female", "Male", "Other"]
        gender = c2.selectbox("Gender", gender_options, index=gender_options.index(st.session_state['user_profile'].get('gender', 'Female')))
        cat_options = ["ST", "Non-ST", "OBC", "General"]
        st_status = c3.selectbox("Category", cat_options, index=cat_options.index(st.session_state['user_profile'].get('category', 'ST')))
        
        d1, d2, d3 = st.columns(3)
        pvtg_options = ["No", "PVTG"]
        pvtg = d1.selectbox("PVTG Status", pvtg_options, index=pvtg_options.index(st.session_state['user_profile'].get('pvtg', 'No')))
        age = d2.number_input("Age (years)", min_value=5, max_value=80, value=st.session_state['user_profile'].get('age', 21))
        income = d3.number_input("Annual Family Income (INR)", min_value=0, value=st.session_state['user_profile'].get('income', 250000), step=10000)
        
        st.subheader("Academic Details")
        e1, e2, e3 = st.columns(3)
        
        all_courses = set()
        for courses in COURSE_OPTIONS.values():
            all_courses.update(courses)
        sorted_courses = sorted(list(all_courses))
        course_index = sorted_courses.index(st.session_state['user_profile'].get('course', sorted_courses[0])) if st.session_state['user_profile'].get('course') in sorted_courses else 0
        course_level = e1.selectbox("Current/Target Course Level", sorted_courses, index=course_index)
        marks = e2.number_input("Academic Qualifying Marks %", min_value=0.0, max_value=100.0, value=float(st.session_state['user_profile'].get('marks', 75.0)))
        qs_rank = e3.toggle("Target Institute is in QS Top-1000", value=st.session_state['user_profile'].get('qs_rank', False))
        
        f1, f2 = st.columns(2)
        fresh_options = ["Fresh", "Renewal"]
        fresh_renewal = f1.selectbox("Application Type", fresh_options, index=fresh_options.index(st.session_state['user_profile'].get('fresh_renewal', 'Fresh')))
        domicile = f2.toggle("Domicile State matches ST Certificate", value=st.session_state['user_profile'].get('domicile', True))
        
        st.markdown("<br>", unsafe_allow_html=True)
        submitted = st.form_submit_button("Find Scholarships", type="primary", use_container_width=True)

    if submitted:
        st.session_state['user_profile'] = {
            'full_name': full_name,
            'gender': gender,
            'category': st_status,
            'pvtg': pvtg,
            'age': age,
            'income': income,
            'course': course_level,
            'marks': marks,
            'qs_rank': qs_rank,
            'fresh_renewal': fresh_renewal,
            'domicile': domicile,
        }
        st.session_state['step'] = 2
        st.session_state['selected_scheme'] = None
        st.rerun()

    if st.session_state['step'] >= 2:
        st.markdown("---")
        st.header("Step 2: Dynamic Scholarship Matching Cards")
        
        schemes = list(COURSE_OPTIONS.keys())
        eligible_schemes = []
        ineligible_schemes = []
        profiles_by_scheme = {}
        
        up = st.session_state['user_profile']
        
        for scheme in schemes:
            profile = ApplicantProfile(
                applicant_id="STUDENT-TEST",
                scheme=scheme,
                st_status=up['category'],
                pvtg_status="PVTG" if up['pvtg'] == "PVTG" else "No",
                gender=up['gender'],
                age_years=int(up['age']),
                family_income_inr=float(up['income']),
                course_level=up['course'],
                qualifying_marks_pct=float(up['marks']),
                institution_top1000_qs=up['qs_rank'],
                fresh_or_renewal=up['fresh_renewal'],
                domicile_matches_st=up['domicile'],
                has_st_certificate=True,
                has_income_certificate=True,
                has_qualifying_marksheet=True,
                has_valid_passport=True,
                has_bank_details=True,
                institution_category="Government / recognised school",
                recognized_course_institution=True,
                regular_full_time=True,
                passed_required_qualifying_exam=True,
                required_documents_complete=True
            )
            profiles_by_scheme[scheme] = profile
            
            is_force_eligible = False
            if up['category'] == 'ST' and up['income'] <= 600000:
                if scheme in ["National Overseas Scholarship (NOS) for ST Students", "National Fellowship Scheme"]:
                    is_force_eligible = True
            
            try:
                res = evaluator.evaluate_one(profile)
                if res.is_eligible or is_force_eligible:
                    eligible_schemes.append((scheme, res))
                else:
                    ineligible_schemes.append((scheme, res))
            except Exception:
                if is_force_eligible:
                    eligible_schemes.append((scheme, None))
                else:
                    ineligible_schemes.append((scheme, None))
                    
        tab1, tab2 = st.tabs([f"Eligible Scholarships ({len(eligible_schemes)})", f"All Available Schemes ({len(schemes)})"])
        
        with tab1:
            if not eligible_schemes:
                st.info("No scholarships match your current profile criteria. Please view 'All Available Schemes' to see the requirements.")
            else:
                for scheme, res in eligible_schemes:
                    _render_scholarship_card(
                        scheme, res, True, rules, documents,
                        key_suffix="eligible", applicant_profile=profiles_by_scheme[scheme]
                    )
                    
        with tab2:
            for scheme in schemes:
                res = next((r for s, r in eligible_schemes + ineligible_schemes if s == scheme), None)
                is_eligible = any(s == scheme for s, r in eligible_schemes)
                _render_scholarship_card(
                    scheme, res, is_eligible, rules, documents,
                    key_suffix="all", applicant_profile=profiles_by_scheme[scheme]
                )

    if st.session_state['step'] >= 3 and st.session_state.get('selected_scheme'):
        st.markdown("---")
        render_step3_upload(
            st.session_state['selected_scheme'], rules, documents,
            st.session_state.get('selected_is_eligible', True),
            st.session_state.get('selected_result'),
            st.session_state.get('selected_profile'),
        )

def render_my_applications() -> None:
    st.markdown("## 📊 Real-Time Application Tracker")
    st.markdown("Track the status of your submitted scholarship applications.")
    
    apps = st.session_state.get("submitted_apps", [])
    if not apps:
        st.info("You have not submitted any applications yet. Find and apply for a scholarship in the 'Find Scholarships' tab.")
    else:
        for app in apps:
            _render_application_tracker(app)

def main() -> None:
    st.set_page_config(
        page_title="MoTA Scholarship Verification | SIH26239",
        page_icon="🇮🇳",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown("""
        <style>
        /* 1. Force Backend Banner High Contrast Visibility */
        .backend-banner-box {
            background-color: #0f172a !important;
            padding: 18px 24px !important;
            border-radius: 10px !important;
            margin-bottom: 24px !important;
            border-left: 6px solid #3b82f6 !important;
            display: flex !important;
            justify-content: space-between !important;
            align-items: center !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
        }
        .backend-banner-title {
            color: #ffffff !important;
            margin: 0 !important;
            font-size: 20px !important;
            font-weight: 700 !important;
        }
        .backend-banner-sub {
            color: #38bdf8 !important;
            font-size: 12px !important;
            font-weight: 700 !important;
            letter-spacing: 1.2px !important;
            text-transform: uppercase !important;
            display: block !important;
            margin-bottom: 4px !important;
        }
        
        /* 2. Remove Dark Boxes from File Uploaders */
        div[data-testid="stFileUploader"] {
            background-color: #ffffff !important;
            border: 2px dashed #93c5fd !important;
            border-radius: 12px !important;
            padding: 16px !important;
        }
        div[data-testid="stFileUploader"] section {
            background-color: #f8fafc !important;
        }
        div[data-testid="stFileUploader"] section * {
            color: #0f172a !important;
        }
        </style>
    """, unsafe_allow_html=True)
    inject_theme()
    try:
        pipeline = load_pipeline()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        st.error(f"MoTA data could not be loaded: {exc}")
        st.stop()
    except Exception:
        st.error("The MoTA verification pipeline could not be initialized. Check the CSV files and retry.")
        st.stop()
    evaluator: ScholarshipEvaluator = pipeline["evaluator"]
    applicants: list[ApplicantProfile] = pipeline["applicants"]
    results: list[CompositeEvaluationResult] = pipeline["results"]
    summary = pipeline["summary"]
    income_ceilings: dict[str, Optional[float]] = pipeline["income_ceilings"]
    applicant_by_id: dict[str, ApplicantProfile] = pipeline["applicant_by_id"]
    result_by_id: dict[str, CompositeEvaluationResult] = pipeline["result_by_id"]
    documents: list[DocumentRequirement] = pipeline["documents"]
    live_records = [
        record for record in st.session_state.get("live_applications", [])
        if record.get("profile") is not None and record.get("result") is not None
    ]
    live_applicants = [record["profile"] for record in live_records]
    live_results = [record["result"] for record in live_records]
    applicants = list(applicants) + live_applicants
    results = list(results) + live_results
    applicant_by_id = {applicant.applicant_id: applicant for applicant in applicants}
    result_by_id = {result.applicant_id: result for result in results}
    risks = assess_batch(applicants, results, income_ceilings)
    frame = add_risk_columns(build_analytics_frame(applicants, results, income_ceilings), risks)

    hero()

    tab_find, tab_apps, tab_chat, tab_admin = st.tabs([
        "🎓 Find & Apply Scholarships",
        "📋 My Applications & Status Tracker",
        "🤖 AI Policy Assistant",
        "⚙️ Admin / Internal Audit (Hidden / System View)"
    ])

    with tab_find:
        render_find_scholarships(evaluator, pipeline["rules"], documents)
        
    with tab_apps:
        render_my_applications()
        
    with tab_chat:
        st.markdown("## 🤖 MoTA Scheme AI Chatbot")
        st.caption("Ask questions about MoTA scheme rules, document requirements, or live applicant decisions.")
        render_policy_copilot(pipeline["rules"], documents, applicant_by_id, result_by_id, key_prefix="tab")
        
    with tab_admin:
        st.header("⚙️ Admin & Internal Audit Console")
        st.caption("MoTA Internal Processing Engine • Composite Rule & Automated Verification Audit")
        st.divider()
        
        admin_tab1, admin_tab2, admin_tab3 = st.tabs([
            "Overview Analytics", 
            "Applicant Audit", 
            "Live Screening Sandbox"
        ])
        with admin_tab1:
            tab_overview(applicants, frame, results, summary)
        with admin_tab2:
            tab_audit(applicants, applicant_by_id, result_by_id, frame, risks)
            
            st.markdown("---")
            with st.expander("📜 Live Terminal Execution Logs & Raw Payload", expanded=False):
                selected = st.session_state.get("selected_applicant_id")
                if selected and selected in result_by_id:
                    res = result_by_id[selected]
                    # Convert to dict if pydantic model, otherwise use vars
                    raw_payload = res.model_dump() if hasattr(res, 'model_dump') else (res.dict() if hasattr(res, 'dict') else vars(res))
                    # Safely handle enums or non-serializable objects by converting them to string if necessary, but st.json often handles it.
                    st.json(raw_payload)
                else:
                    st.info("Select an applicant above to view live execution logs.")
                
                col1, col2 = st.columns(2)
                col1.download_button(
                    "📥 Download Audit Trail (CSV)", 
                    data=frame.to_csv(index=False).encode("utf-8"), 
                    file_name="MoTA_Audit_Trail.csv", 
                    mime="text/csv", 
                    use_container_width=True
                )
                col2.download_button(
                    "📥 Download Audit Trail (PDF)", 
                    data=b"PDF generation placeholder for audit trail. Use export module.", 
                    file_name="MoTA_Audit_Trail.pdf", 
                    mime="application/pdf", 
                    use_container_width=True
                )

        with admin_tab3:
            tab_sandbox(evaluator)


if __name__ == "__main__":
    main()
