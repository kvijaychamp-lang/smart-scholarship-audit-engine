"""Advisory anomaly and fraud-risk scoring for MoTA applications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from core.models import ApplicantProfile, CompositeEvaluationResult


HIGH_INCOME_WEIGHT = 25
FAMILY_CLAIM_WEIGHT = 35
INSTITUTE_MISMATCH_WEIGHT = 20
DOCUMENT_GAP_WEIGHT = 20


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    band: str
    anomalies: tuple[str, ...]


def _band(score: int) -> str:
    if score > 60:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


def assess_risk(
    applicant: ApplicantProfile,
    result: CompositeEvaluationResult,
    income_ceiling: Optional[float],
    *,
    parentage_counts: Optional[dict[str, int]] = None,
    bank_account_counts: Optional[dict[str, int]] = None,
) -> RiskAssessment:
    score = 0
    anomalies: list[str] = []

    income = applicant.family_income_inr
    if income_ceiling and income is not None and income >= income_ceiling * 0.98:
        score += HIGH_INCOME_WEIGHT
        proximity = min(100, income / income_ceiling * 100)
        anomalies.append(
            f"Warning: Income {_inr(income)} is {proximity:.0f}% close to the "
            f"{_scheme_limit(applicant.scheme)} max limit of {_inr(income_ceiling)}."
        )

    parentage_key = getattr(applicant, "parentage_key", None)
    family_claim = applicant.same_parents_other_child_awarded is True
    if parentage_key and parentage_counts and parentage_counts.get(parentage_key, 0) > 1:
        family_claim = True
    bank_key = getattr(applicant, "bank_account_key", None)
    shared_bank = bool(bank_key and bank_account_counts and bank_account_counts.get(bank_key, 0) > 1)
    if family_claim or shared_bank:
        score += FAMILY_CLAIM_WEIGHT
        reasons = []
        if family_claim:
            reasons.append("multiple applicants share parentage / an awarded sibling claim")
        if shared_bank:
            reasons.append("multiple applicants share a bank account")
        anomalies.append("Warning: " + " and ".join(reasons).capitalize() + ".")

    recognized = applicant.recognized_course_institution is False
    category = (applicant.institution_category or "").lower()
    if "private unaided" in category or "unlisted" in category:
        recognized = True
    if recognized:
        score += INSTITUTE_MISMATCH_WEIGHT
        anomalies.append(
            f"Warning: Institution '{applicant.institution_category or 'not provided'}' "
            "is not marked as recognized / UGC-compliant."
        )

    missing = result.document_audit_result.missing_mandatory_docs
    if missing:
        score += DOCUMENT_GAP_WEIGHT
        anomalies.append(
            "Warning: Missing critical mandatory proof(s): " + ", ".join(missing) + "."
        )

    bounded_score = min(score, 100)
    return RiskAssessment(score=bounded_score, band=_band(bounded_score), anomalies=tuple(anomalies))


def assess_batch(
    applicants: Sequence[ApplicantProfile],
    results: Sequence[CompositeEvaluationResult],
    income_ceilings: dict[str, Optional[float]],
) -> dict[str, RiskAssessment]:
    result_by_id = {item.applicant_id: item for item in results}
    parentage_counts = _counts(applicants, "parentage_key")
    bank_account_counts = _counts(applicants, "bank_account_key")
    return {
        applicant.applicant_id: assess_risk(
            applicant,
            result_by_id[applicant.applicant_id],
            income_ceilings.get(applicant.scheme),
            parentage_counts=parentage_counts,
            bank_account_counts=bank_account_counts,
        )
        for applicant in applicants
        if applicant.applicant_id in result_by_id
    }


def _counts(applicants: Sequence[ApplicantProfile], field: str) -> dict[str, int]:
    values = [getattr(applicant, field, None) for applicant in applicants]
    return {value: values.count(value) for value in set(values) if value}


def _inr(value: float) -> str:
    return f"INR {value:,.0f}"


def _scheme_limit(scheme: str) -> str:
    return "NOS" if "Overseas" in scheme else "scheme"