"""Formal deficiency-notice generator for incomplete MoTA document packets.

Produces a structured notice the processing cell can issue to the applicant
with a default **15-day cure period**, matching MoTA scholarship / fellowship
deficiency handling practice (upload or physically submit the named artefacts
before the application is treated as abandoned).
"""

from __future__ import annotations

from typing import Optional, Sequence

from core.models import (
    ApplicantProfile,
    DeficiencyNotice,
    DocumentAuditResult,
)

# Official processing window used by MoTA State/UT implementing agencies
# when an application is held for document cure (days).
DEFAULT_CURE_PERIOD_DAYS = 15

_RESOLUTION_HINTS: dict[str, str] = {
    "st certificate": (
        "Obtain a Scheduled Tribe certificate from the competent authority of "
        "the State/UT of domicile (signed and stamped) and upload a clear scan."
    ),
    "income certificate": (
        "Obtain a family-income certificate for the relevant financial year "
        "from the competent revenue authority and upload a clear scan. "
        "Orphans supported by a guardian should instead upload the guardian "
        "declaration so the income criterion can be waived."
    ),
    "family income": (
        "Obtain a family-income certificate from the competent authority and upload it."
    ),
    "marksheet": (
        "Upload the qualifying examination marksheet / equivalent percentage "
        "(and CGPA-to-percentage conversion formula if the board/university uses CGPA)."
    ),
    "fee receipt": (
        "Upload the institute fee receipt for the current academic period "
        "(fresh and renewal cases)."
    ),
    "bank": (
        "Upload a scanned scheduled-bank passbook page showing the student name, "
        "account number, and IFSC; the account must be Aadhaar- and mobile-linked."
    ),
    "aadhaar": (
        "Provide a valid Aadhaar number linked to the scheduled-bank account and mobile."
    ),
    "domicile": (
        "Upload the domicile certificate issued by the competent State/UT authority."
    ),
    "offer": (
        "Upload the offer / letter of admission from the university. For NOS, "
        "if claiming the QS Top-1000 route, the offer must be from a ranked institute."
    ),
    "passport": (
        "Upload a scan of a valid passport (bio page) with expiry covering the study period."
    ),
    "visa": (
        "Upload visa proof issued for the intended country of study, once the visa is applicable."
    ),
    "joining": (
        "Upload the M.Phil / Ph.D / Integrated M.Phil+Ph.D admission or joining certificate "
        "issued by the university concerned."
    ),
    "admission": (
        "Upload the admission / joining certificate issued by the university concerned."
    ),
    "supervisor": (
        "Upload the supervisor allocation letter issued by the university / research centre."
    ),
    "ugc": (
        "Upload documentary proof that the institute is recognised under UGC 2(f)/12(B), "
        "Section 3 deemed-university status, a Central/State grant institution, or an "
        "Institute of National Importance."
    ),
    "photograph": (
        "Upload a latest coloured passport-size photograph as specified in the scheme form."
    ),
    "qs": (
        "Upload the offer letter showing admission to a QS Top-1000 ranked university."
    ),
}


class DeficiencyNoticeGenerator:
    """Build applicant-facing, step-by-step deficiency notices from an audit."""

    def __init__(self, *, cure_period_days: int = DEFAULT_CURE_PERIOD_DAYS) -> None:
        if cure_period_days <= 0:
            raise ValueError("cure_period_days must be a positive integer")
        self.cure_period_days = cure_period_days

    def generate(
        self,
        applicant: ApplicantProfile,
        audit: DocumentAuditResult,
        *,
        cure_period_days: Optional[int] = None,
    ) -> DeficiencyNotice:
        days = self.cure_period_days if cure_period_days is None else cure_period_days
        mandatory = list(audit.missing_mandatory_docs)
        conditional = list(audit.missing_conditional_docs)
        action = self._compose_action(
            applicant,
            mandatory=mandatory,
            conditional=conditional,
            waived=audit.waived_docs,
            days=days,
        )
        return DeficiencyNotice(
            applicant_id=applicant.applicant_id,
            scheme=audit.scheme or applicant.scheme,
            missing_mandatory_docs=mandatory,
            missing_conditional_docs=conditional,
            action_required=action,
            cure_period_days=days,
        )

    def generate_if_deficient(
        self,
        applicant: ApplicantProfile,
        audit: DocumentAuditResult,
        *,
        cure_period_days: Optional[int] = None,
    ) -> Optional[DeficiencyNotice]:
        if audit.documents_complete:
            return None
        return self.generate(applicant, audit, cure_period_days=cure_period_days)

    def _compose_action(
        self,
        applicant: ApplicantProfile,
        *,
        mandatory: Sequence[str],
        conditional: Sequence[str],
        waived: Sequence[str],
        days: int,
    ) -> str:
        steps: list[str] = [
            (
                f"Applicant {applicant.applicant_id} is placed under document deficiency "
                f"for scheme '{applicant.scheme}'. Complete the following within {days} "
                "calendar days of this notice, failing which the application may be "
                "treated as closed for this cycle."
            )
        ]
        index = 1
        if mandatory:
            steps.append(
                f"{index}. Upload / submit each missing **mandatory** document listed below:"
            )
            index += 1
            for name in mandatory:
                steps.append(f"   - {name}: {_hint_for(name)}")
        if conditional:
            steps.append(
                f"{index}. Upload / submit each missing **conditional** document that "
                "applies to your case:"
            )
            index += 1
            for name in conditional:
                steps.append(f"   - {name}: {_hint_for(name)}")
        if waived:
            steps.append(
                f"{index}. The following artefact(s) are waived and need not be submitted: "
                + "; ".join(waived)
                + "."
            )
            index += 1
        steps.append(
            f"{index}. Ensure scans are legible, uncropped, and match the name on the "
            "application. Re-submit the complete packet on the National Scholarship "
            "Portal / MoTA intake channel used for the original filing."
        )
        index += 1
        steps.append(
            f"{index}. After re-submission, retain the acknowledgement number; the "
            "processing cell will re-run document verification before disbursal."
        )
        return "\n".join(steps)


def _hint_for(document: str) -> str:
    blob = document.lower()
    for token, hint in _RESOLUTION_HINTS.items():
        if token in blob:
            return hint
    return (
        "Obtain the document from the issuing authority and upload a clear, "
        "signed scan matching the name on the application."
    )
