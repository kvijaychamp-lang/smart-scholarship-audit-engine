"""Unit tests for Phase-3 DocumentAuditor, deficiency notices, and composite routing."""

from __future__ import annotations

from core.deficiency_generator import DEFAULT_CURE_PERIOD_DAYS, DeficiencyNoticeGenerator
from core.document_auditor import (
    FELLOWSHIP_SUPERVISOR,
    FELLOWSHIP_UGC_DOC,
    NOS_PASSPORT,
    NOS_QS_OFFER,
    NOS_VISA,
    STATUS_MISSING,
    STATUS_NOT_APPLICABLE,
    STATUS_PRESENT,
    STATUS_WAIVED,
    TIER_CONDITIONAL,
    TIER_MANDATORY,
    DocumentAuditor,
)
from core.evaluator import ScholarshipEvaluator
from core.models import (
    ApplicantProfile,
    CompositeDecision,
    DocVerificationStatus,
    RequirementLevel,
)

NOS = "National Overseas Scholarship (NOS) for ST Students"
FELLOWSHIP = "National Fellowship Scheme"
HIGHER_ED = "National Scholarship Scheme (Higher Education)"
PRE_MATRIC = "Pre-Matric Scholarship for ST Students"


def _nos(**overrides) -> ApplicantProfile:
    payload = {
        "applicant_id": "DOC-NOS",
        "scheme": NOS,
        "st_status": "ST",
        "course_level": "Master's",
        "age_years": 28,
        "qualifying_marks_pct": 60.0,
        "family_income_inr": 400000,
        "institution_top1000_qs": True,
        "admission_offer": True,
        "same_parents_other_child_awarded": False,
        "previous_nos_award": False,
        "other_govt_scholarship_same_study": False,
        "required_documents_complete": True,
        "fresh_or_renewal": "Fresh",
        "has_valid_passport": True,
        "has_qs_top1000_offer_letter": True,
        "visa_applicable": True,
        "has_visa_proof": True,
        "has_income_certificate": True,
        "has_st_certificate": True,
        "has_qualifying_marksheet": True,
        "rule_eligibility": "Eligible",
    }
    payload.update(overrides)
    return ApplicantProfile.model_validate(payload)


def _fellowship(**overrides) -> ApplicantProfile:
    payload = {
        "applicant_id": "DOC-NF",
        "scheme": FELLOWSHIP,
        "st_status": "ST",
        "course_level": "Ph.D",
        "age_years": 32,
        "qualifying_marks_pct": 58.0,
        "family_income_inr": 1500000,
        "institution_category": "UGC 2(f)/12(B)",
        "regular_full_time": True,
        "admission_certificate": True,
        "other_govt_scholarship_same_study": False,
        "required_documents_complete": True,
        "has_ugc_recognition_document": True,
        "has_supervisor_allocation_letter": True,
        "has_st_certificate": True,
        "has_qualifying_marksheet": True,
        "rule_eligibility": "Eligible",
    }
    payload.update(overrides)
    return ApplicantProfile.model_validate(payload)


def _item(audit, name: str):
    matches = [row for row in audit.items if row.document == name]
    assert matches, f"Expected checklist item {name!r} in {[i.document for i in audit.items]}"
    return matches[0]


def test_matrix_covers_five_mota_schemes() -> None:
    auditor = DocumentAuditor()
    names = set(auditor.matrix.schemes)
    assert NOS in names
    assert FELLOWSHIP in names
    assert HIGHER_ED in names
    assert PRE_MATRIC in names
    assert "Post Matric Scholarship for ST Students" in names


def test_nos_full_checklist_fully_verified() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(_nos())
    assert audit.doc_verification_status is DocVerificationStatus.FULLY_VERIFIED
    assert audit.completeness_score == 1.0
    assert audit.missing_mandatory_docs == []
    assert audit.missing_conditional_docs == []
    assert _item(audit, NOS_PASSPORT).status == STATUS_PRESENT
    assert _item(audit, NOS_QS_OFFER).status == STATUS_PRESENT
    assert _item(audit, NOS_VISA).status == STATUS_PRESENT
    assert _item(audit, "Offer of admission").status == STATUS_PRESENT
    assert _item(audit, "ST certificate issued by competent authority").tier == TIER_MANDATORY


def test_nos_missing_passport_is_conditional_deficiency() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(_nos(has_valid_passport=False))
    assert NOS_PASSPORT in audit.missing_conditional_docs
    assert NOS_PASSPORT not in audit.missing_mandatory_docs
    assert _item(audit, NOS_PASSPORT).tier == TIER_CONDITIONAL
    assert audit.doc_verification_status is DocVerificationStatus.DEFICIENT
    assert 0.0 < audit.completeness_score < 1.0


def test_nos_qs_offer_not_applicable_when_qs_route_not_claimed() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(
        _nos(
            institution_top1000_qs=False,
            has_qs_top1000_offer_letter=False,
            qualifying_marks_pct=60.0,
        )
    )
    assert _item(audit, NOS_QS_OFFER).status == STATUS_NOT_APPLICABLE
    assert NOS_QS_OFFER not in audit.missing_conditional_docs


def test_nos_qs_offer_required_when_qs_route_claimed() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(
        _nos(
            institution_top1000_qs=True,
            has_qs_top1000_offer_letter=False,
            admission_offer=True,
        )
    )
    # Explicit False on the QS-specific letter overrides the generic offer flag.
    assert NOS_QS_OFFER in audit.missing_conditional_docs
    assert _item(audit, NOS_QS_OFFER).status == STATUS_MISSING


def test_nos_visa_only_when_applicable() -> None:
    auditor = DocumentAuditor()
    skipped = auditor.audit(_nos(visa_applicable=False, has_visa_proof=False))
    assert _item(skipped, NOS_VISA).status == STATUS_NOT_APPLICABLE
    required = auditor.audit(_nos(visa_applicable=True, has_visa_proof=False))
    assert NOS_VISA in required.missing_conditional_docs


def test_fellowship_full_checklist_fully_verified() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(_fellowship())
    assert audit.doc_verification_status is DocVerificationStatus.FULLY_VERIFIED
    joining = [
        row
        for row in audit.items
        if "Admission/Joining certificate" in row.document
    ]
    assert joining and joining[0].status == STATUS_PRESENT
    assert joining[0].tier == TIER_MANDATORY
    assert _item(audit, FELLOWSHIP_UGC_DOC).status == STATUS_PRESENT
    assert _item(audit, FELLOWSHIP_SUPERVISOR).status == STATUS_PRESENT
    assert _item(audit, FELLOWSHIP_SUPERVISOR).tier == TIER_CONDITIONAL
    assert _item(audit, FELLOWSHIP_UGC_DOC).tier == TIER_CONDITIONAL


def test_fellowship_missing_supervisor_is_conditional() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(_fellowship(has_supervisor_allocation_letter=False))
    assert FELLOWSHIP_SUPERVISOR in audit.missing_conditional_docs
    assert audit.doc_verification_status is DocVerificationStatus.DEFICIENT
    assert not audit.missing_mandatory_docs


def test_fellowship_missing_joining_certificate_is_mandatory() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(
        _fellowship(
            admission_certificate=False,
            document_issue="Missing admission/joining certificate",
            required_documents_complete=False,
        )
    )
    missing_join = [
        name for name in audit.missing_mandatory_docs if "joining" in name.lower() or "Admission" in name
    ]
    assert missing_join, audit.missing_mandatory_docs
    assert audit.doc_verification_status is DocVerificationStatus.REJECTED_MISSING_MANDATORY


def test_orphan_income_certificate_waived() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(
        _nos(
            is_orphan_supported_by_guardian=True,
            has_income_certificate=False,
            document_issue="Missing income certificate",
            required_documents_complete=False,
        )
    )
    income_items = [row for row in audit.items if "Income certificate" in row.document]
    assert income_items
    assert income_items[0].status == STATUS_WAIVED
    assert income_items[0].waived is True
    assert "Income certificate" in audit.waived_docs or any(
        "Income" in name for name in audit.waived_docs
    )
    assert not any("Income" in name for name in audit.missing_mandatory_docs)


def test_non_orphan_missing_income_is_mandatory_reject() -> None:
    auditor = DocumentAuditor()
    audit = auditor.audit(
        _nos(
            is_orphan_supported_by_guardian=False,
            has_income_certificate=False,
            document_issue="Missing income certificate",
            required_documents_complete=False,
        )
    )
    assert any("Income" in name for name in audit.missing_mandatory_docs)
    assert audit.doc_verification_status is DocVerificationStatus.REJECTED_MISSING_MANDATORY


def test_orphan_waiver_also_applies_on_pre_matric() -> None:
    auditor = DocumentAuditor()
    applicant = ApplicantProfile.model_validate(
        {
            "applicant_id": "DOC-PRE-ORPHAN",
            "scheme": PRE_MATRIC,
            "st_status": "ST",
            "course_level": "Class IX",
            "is_orphan_supported_by_guardian": True,
            "has_income_certificate": False,
            "required_documents_complete": True,
            "scheduled_bank_aadhaar_mobile_linked": True,
        }
    )
    audit = auditor.audit(applicant)
    income = [row for row in audit.items if "Income" in row.document]
    assert income and income[0].status == STATUS_WAIVED


def test_pvtg_certificate_conditional_only_for_pvtg() -> None:
    auditor = DocumentAuditor()
    non_pvtg = auditor.audit(_nos(pvtg_status="ST"))
    pvtg_row = _item(non_pvtg, "PVTG certificate")
    assert pvtg_row.status == STATUS_NOT_APPLICABLE
    assert pvtg_row.requirement is RequirementLevel.CONDITIONAL

    pvtg = auditor.audit(
        _nos(pvtg_status="PVTG", submitted_documents=["PVTG certificate"])
    )
    assert _item(pvtg, "PVTG certificate").status == STATUS_PRESENT


def test_deficiency_notice_schema_and_cure_period() -> None:
    auditor = DocumentAuditor()
    applicant = _nos(has_valid_passport=False, has_income_certificate=False)
    # Named mandatory miss via issue text so income is scored missing for a non-orphan.
    applicant = _nos(
        has_valid_passport=False,
        has_income_certificate=False,
        is_orphan_supported_by_guardian=False,
        document_issue="Missing income certificate",
        required_documents_complete=False,
    )
    audit = auditor.audit(applicant)
    notice = DeficiencyNoticeGenerator().generate(applicant, audit)
    dumped = notice.model_dump()
    assert dumped["applicant_id"] == applicant.applicant_id
    assert isinstance(dumped["missing_mandatory_docs"], list)
    assert isinstance(dumped["missing_conditional_docs"], list)
    assert dumped["cure_period_days"] == DEFAULT_CURE_PERIOD_DAYS == 15
    assert "1." in dumped["action_required"]
    assert any("Income" in name for name in dumped["missing_mandatory_docs"])
    assert NOS_PASSPORT in dumped["missing_conditional_docs"]


def test_composite_ready_for_disbursal() -> None:
    evaluator = ScholarshipEvaluator()
    result = evaluator.evaluate_one(_nos())
    assert result.is_eligible is True
    assert result.composite_status is CompositeDecision.READY_FOR_DISBURSAL
    assert result.approved is True
    assert result.deficiency_notice is None
    assert result.doc_verification_status is DocVerificationStatus.FULLY_VERIFIED


def test_composite_provisional_when_docs_deficient() -> None:
    evaluator = ScholarshipEvaluator()
    result = evaluator.evaluate_one(_nos(has_valid_passport=False))
    assert result.is_eligible is True
    assert result.composite_status is CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS
    assert result.approved is False
    assert result.deficiency_notice is not None
    assert result.deficiency_notice.cure_period_days == 15


def test_composite_dataset_preserves_rule_accuracy_and_routes_deficiencies() -> None:
    evaluator = ScholarshipEvaluator()
    results, summary = evaluator.evaluate_dataset()
    assert summary.total_applicants == 120
    assert summary.accuracy_pct == 100.0
    assert summary.composite_status_counts[CompositeDecision.REJECTED.value] == 62
    assert summary.composite_status_counts[CompositeDecision.READY_FOR_DISBURSAL.value] == 54
    assert summary.composite_status_counts[
        CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS.value
    ] == 4
    assert all(r.ground_truth_match is True for r in results)


def test_composite_rejected_when_rules_fail() -> None:
    evaluator = ScholarshipEvaluator()
    result = evaluator.evaluate_one(
        _nos(st_status="General", rule_eligibility="Not Eligible")
    )
    assert result.is_eligible is False
    assert result.composite_status is CompositeDecision.REJECTED
    assert result.approved is False
