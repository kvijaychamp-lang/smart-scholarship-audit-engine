"""Unit tests for Phase-2 ScholarshipEvaluator and guideline edge cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.evaluator import ScholarshipEvaluator, attach_ground_truth
from core.models import ApplicantProfile, EligibilityLabel
from core.rule_engine import ScholarshipRuleEngine

NOS = "National Overseas Scholarship (NOS) for ST Students"
FELLOWSHIP = "National Fellowship Scheme"
HIGHER_ED = "National Scholarship Scheme (Higher Education)"
PRE_MATRIC = "Pre-Matric Scholarship for ST Students"
POST_MATRIC = "Post Matric Scholarship for ST Students"


@pytest.fixture(scope="module")
def evaluator() -> ScholarshipEvaluator:
    return ScholarshipEvaluator()


def _nos(**overrides) -> ApplicantProfile:
    payload = {
        "applicant_id": "UNIT-NOS",
        "scheme": NOS,
        "st_status": "ST",
        "course_level": "Master's",
        "age_years": 28,
        "qualifying_marks_pct": 60.0,
        "family_income_inr": 400000,
        "institution_top1000_qs": False,
        "admission_offer": True,
        "same_parents_other_child_awarded": False,
        "previous_nos_award": False,
        "other_govt_scholarship_same_study": False,
        "required_documents_complete": True,
        "rule_eligibility": "Eligible",
    }
    payload.update(overrides)
    return ApplicantProfile.model_validate(payload)


def _fellowship(**overrides) -> ApplicantProfile:
    payload = {
        "applicant_id": "UNIT-NF",
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
        "rule_eligibility": "Eligible",
    }
    payload.update(overrides)
    return ApplicantProfile.model_validate(payload)


def test_orphan_income_waiver_nos(evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos(
        applicant_id="UNIT-ORPHAN",
        family_income_inr=950000,
        is_orphan_supported_by_guardian=True,
        rule_eligibility="Eligible",
    )
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is True
    assert "INCOME_LIMIT" in result.passed_rules
    assert result.ground_truth_match is True


def test_orphan_income_waiver_does_not_apply_when_not_orphan(evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos(
        applicant_id="UNIT-NOT-ORPHAN",
        family_income_inr=950000,
        is_orphan_supported_by_guardian=False,
        rule_eligibility="Not Eligible",
    )
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is False
    assert "INCOME_LIMIT" in result.failed_rules
    assert result.ground_truth_match is True


def test_single_child_restriction_blocks_nos(evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos(
        applicant_id="UNIT-SIBLING",
        same_parents_other_child_awarded=True,
        rule_eligibility="Not Eligible",
    )
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is False
    assert "ONE_CHILD_PER_FAMILY" in result.failed_rules


def test_nos_qs_top1000_waives_marks_not_hard_gate(evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos(
        applicant_id="UNIT-QS",
        qualifying_marks_pct=48.0,
        institution_top1000_qs=True,
        rule_eligibility="Eligible",
    )
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is True
    assert "MIN_MARKS" in result.passed_rules
    assert "QS_TOP1000_PRIORITY" in result.passed_rules
    qs_check = next(c for c in result.rule_checks if c.rule_id == "QS_TOP1000_PRIORITY")
    assert qs_check.severity.value == "soft"


def test_nos_low_marks_without_qs_fail(evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos(
        applicant_id="UNIT-MARKS",
        qualifying_marks_pct=48.0,
        institution_top1000_qs=False,
        rule_eligibility="Not Eligible",
    )
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is False
    assert "MIN_MARKS" in result.failed_rules


def test_fellowship_has_no_income_ceiling(evaluator: ScholarshipEvaluator) -> None:
    applicant = _fellowship(family_income_inr=2_500_000)
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is True
    income = next(c for c in result.rule_checks if c.rule_id == "INCOME_LIMIT")
    assert income.passed is True
    assert income.severity.value == "soft"


def test_fellowship_rejects_unrecognised_institute(evaluator: ScholarshipEvaluator) -> None:
    applicant = _fellowship(
        institution_category="Unaided private college",
        rule_eligibility="Not Eligible",
    )
    result = evaluator.evaluate_one(applicant)
    assert result.is_eligible is False
    assert "UGC_INSTITUTION_CATEGORY" in result.failed_rules


def test_batch_ground_truth_match_on_full_dataset(evaluator: ScholarshipEvaluator) -> None:
    results, summary = evaluator.evaluate_dataset()
    assert summary.total_applicants == 120
    assert summary.mismatched == 0
    assert summary.accuracy_pct == 100.0
    assert all(r.ground_truth_match is True for r in results)
    assert set(summary.scheme_breakdown) == {
        NOS,
        FELLOWSHIP,
        HIGHER_ED,
        PRE_MATRIC,
        POST_MATRIC,
    }


def test_export_json_contains_required_result_fields(tmp_path: Path, evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos()
    results = evaluator.evaluate_batch([applicant])
    out = tmp_path / "eval_results.json"
    evaluator.export_json(results, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    record = payload["results"][0]
    for key in (
        "applicant_id",
        "scheme",
        "is_eligible",
        "passed_rules",
        "failed_rules",
        "confidence_score",
        "ground_truth_match",
    ):
        assert key in record
    assert payload["summary"]["total_applicants_processed"] == 1
    assert 0.0 <= record["confidence_score"] <= 1.0


def test_attach_ground_truth_none_when_unlabelled(evaluator: ScholarshipEvaluator) -> None:
    applicant = _nos(rule_eligibility=None)
    result = evaluator.engine.evaluate(applicant)
    scored = attach_ground_truth(result, applicant)
    assert scored.ground_truth_match is None


def test_engine_can_be_injected() -> None:
    engine = ScholarshipRuleEngine()
    evaluator = ScholarshipEvaluator(engine=engine)
    assert evaluator.engine is engine
    result = evaluator.evaluate_one(_nos())
    assert result.eligibility_label is EligibilityLabel.ELIGIBLE
