"""Automated evaluation engine wrapping rule eligibility and document audit.

Phase 2 scored ``rule_eligibility`` against the CSV gold labels.
Phase 3 composes that result with ``DocumentAuditor`` into a
``CompositeEvaluationResult`` used for disbursal routing:

* Rule eligible + documents fully verified → ``READY_FOR_DISBURSAL`` (approved)
* Rule eligible + documents incomplete → ``PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS``
* Rule ineligible → ``REJECTED`` (document cure does not revive a failed rule set)
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

from core.config import load_applicants
from core.deficiency_generator import DeficiencyNoticeGenerator
from core.document_auditor import DocumentAuditor
from core.models import (
    ApplicantProfile,
    CompositeDecision,
    CompositeEvaluationResult,
    DeficiencyNotice,
    DocVerificationStatus,
    DocumentAuditResult,
    EligibilityLabel,
    ValidationResult,
)
from core.rule_engine import ScholarshipRuleEngine

logger = logging.getLogger(__name__)

ScoredResult = Union[ValidationResult, CompositeEvaluationResult]


def _normalize_label(value: Optional[str] | bool | EligibilityLabel) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return EligibilityLabel.ELIGIBLE.value if value else EligibilityLabel.NOT_ELIGIBLE.value
    if isinstance(value, EligibilityLabel):
        return value.value
    text = str(value).strip().lower()
    if text in {"eligible", "yes", "true", "1", "pass"}:
        return EligibilityLabel.ELIGIBLE.value
    if text in {"not eligible", "ineligible", "no", "false", "0", "fail"}:
        return EligibilityLabel.NOT_ELIGIBLE.value
    return str(value).strip()


def attach_ground_truth(
    result: ValidationResult,
    applicant: ApplicantProfile,
) -> ValidationResult:
    """Compare engine eligibility against the dataset ``rule_eligibility`` column."""
    predicted = _normalize_label(result.is_eligible)
    gold = _normalize_label(applicant.rule_eligibility)
    result.ground_truth_label = gold
    if gold is None:
        result.ground_truth_match = None
    else:
        result.ground_truth_match = predicted == gold
    return result


def combine_rule_and_documents(
    rule_result: ValidationResult,
    audit: DocumentAuditResult,
    *,
    deficiency_notice: Optional[DeficiencyNotice] = None,
) -> CompositeEvaluationResult:
    """Map rule eligibility × document status onto a composite workflow decision."""
    if not rule_result.is_eligible:
        status = CompositeDecision.REJECTED
        approved = False
    elif audit.doc_verification_status is DocVerificationStatus.FULLY_VERIFIED:
        status = CompositeDecision.READY_FOR_DISBURSAL
        approved = True
    else:
        status = CompositeDecision.PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS
        approved = False

    rule_result.documents_complete = audit.documents_complete
    rule_result.document_audit = audit.items
    rule_result.document_issues = audit.issues
    rule_result.application_ready_for_processing = approved

    return CompositeEvaluationResult(
        rule_result=rule_result,
        document_audit_result=audit,
        composite_status=status,
        deficiency_notice=deficiency_notice,
        approved=approved,
    )


class EvaluationSummary:
    """Aggregate accuracy and scheme-wise counts for a scored batch."""

    def __init__(self, results: Sequence[ScoredResult]) -> None:
        self.total_applicants = len(results)
        labelled = [r for r in results if r.ground_truth_match is not None]
        self.labelled_applicants = len(labelled)
        self.matched = sum(1 for r in labelled if r.ground_truth_match)
        self.mismatched = self.labelled_applicants - self.matched
        self.unlabelled = self.total_applicants - self.labelled_applicants
        self.accuracy_pct = (
            round(100.0 * self.matched / self.labelled_applicants, 4)
            if self.labelled_applicants
            else 0.0
        )
        self.predicted_eligible = sum(1 for r in results if r.is_eligible)
        self.predicted_not_eligible = self.total_applicants - self.predicted_eligible
        self.scheme_breakdown = self._scheme_breakdown(results)
        self.confusion_matrix = self._confusion_matrix(labelled)
        self.composite_status_counts = dict(
            Counter(
                (
                    r.composite_status.value
                    if isinstance(r, CompositeEvaluationResult)
                    else None
                )
                for r in results
            )
        )
        self.composite_status_counts.pop(None, None)
        self.doc_status_counts = dict(
            Counter(
                (
                    r.doc_verification_status.value
                    if isinstance(r, CompositeEvaluationResult)
                    else None
                )
                for r in results
            )
        )
        self.doc_status_counts.pop(None, None)
        self.mean_doc_completeness = (
            round(
                sum(
                    r.document_completeness_score
                    for r in results
                    if isinstance(r, CompositeEvaluationResult)
                )
                / max(1, sum(1 for r in results if isinstance(r, CompositeEvaluationResult))),
                4,
            )
            if any(isinstance(r, CompositeEvaluationResult) for r in results)
            else None
        )
        self.misclassifications = [
            {
                "applicant_id": r.applicant_id,
                "scheme": r.scheme,
                "predicted": r.eligibility_label.value if isinstance(r.eligibility_label, EligibilityLabel) else str(r.eligibility_label),
                "ground_truth": r.ground_truth_label,
                "failed_rules": r.failed_rules,
                "passed_rules": r.passed_rules,
                "rationale": r.rationale,
            }
            for r in results
            if r.ground_truth_match is False
        ]

    @staticmethod
    def _scheme_breakdown(results: Sequence[ScoredResult]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[ScoredResult]] = defaultdict(list)
        for item in results:
            grouped[item.scheme].append(item)

        breakdown: dict[str, dict[str, Any]] = {}
        for scheme, rows in grouped.items():
            labelled = [r for r in rows if r.ground_truth_match is not None]
            matched = sum(1 for r in labelled if r.ground_truth_match)
            row: dict[str, Any] = {
                "total": len(rows),
                "eligible": sum(1 for r in rows if r.is_eligible),
                "not_eligible": sum(1 for r in rows if not r.is_eligible),
                "matched": matched,
                "mismatched": len(labelled) - matched,
                "accuracy_pct": round(100.0 * matched / len(labelled), 4) if labelled else 0.0,
            }
            composite_rows = [r for r in rows if isinstance(r, CompositeEvaluationResult)]
            if composite_rows:
                row["composite_status"] = dict(
                    Counter(r.composite_status.value for r in composite_rows)
                )
                row["mean_doc_completeness"] = round(
                    sum(r.document_completeness_score for r in composite_rows) / len(composite_rows),
                    4,
                )
            breakdown[scheme] = row
        return breakdown

    @staticmethod
    def _confusion_matrix(labelled: Sequence[ScoredResult]) -> dict[str, int]:
        true_pos = sum(
            1
            for r in labelled
            if r.is_eligible and r.ground_truth_label == EligibilityLabel.ELIGIBLE.value
        )
        true_neg = sum(
            1
            for r in labelled
            if (not r.is_eligible) and r.ground_truth_label == EligibilityLabel.NOT_ELIGIBLE.value
        )
        false_pos = sum(
            1
            for r in labelled
            if r.is_eligible and r.ground_truth_label == EligibilityLabel.NOT_ELIGIBLE.value
        )
        false_neg = sum(
            1
            for r in labelled
            if (not r.is_eligible) and r.ground_truth_label == EligibilityLabel.ELIGIBLE.value
        )
        return {
            "true_positive": true_pos,
            "true_negative": true_neg,
            "false_positive": false_pos,
            "false_negative": false_neg,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "total_applicants_processed": self.total_applicants,
            "labelled_applicants": self.labelled_applicants,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "unlabelled": self.unlabelled,
            "accuracy_percentage": self.accuracy_pct,
            "predicted_eligible": self.predicted_eligible,
            "predicted_not_eligible": self.predicted_not_eligible,
            "scheme_wise_breakdown": self.scheme_breakdown,
            "confusion_matrix": self.confusion_matrix,
            "misclassifications": self.misclassifications,
        }
        if self.composite_status_counts:
            payload["composite_status_counts"] = self.composite_status_counts
        if self.doc_status_counts:
            payload["doc_verification_status_counts"] = self.doc_status_counts
        if self.mean_doc_completeness is not None:
            payload["mean_document_completeness_score"] = self.mean_doc_completeness
        return payload

    def format_report(self) -> str:
        lines = [
            "MoTA Scholarship Evaluator - Executive Summary",
            "=" * 52,
            f"Total Applicants Processed : {self.total_applicants}",
            f"Accuracy Percentage        : {self.accuracy_pct:.2f}%  "
            f"({self.matched} matched / {self.labelled_applicants} labelled)",
            f"Predicted Eligible         : {self.predicted_eligible}",
            f"Predicted Not Eligible     : {self.predicted_not_eligible}",
            "",
            "Scheme-wise Eligible vs Not Eligible",
            "-" * 52,
        ]
        for scheme, stats in self.scheme_breakdown.items():
            lines.append(
                f"{scheme}: total={stats['total']}  eligible={stats['eligible']}  "
                f"not_eligible={stats['not_eligible']}  accuracy={stats['accuracy_pct']:.2f}%"
            )
        matrix = self.confusion_matrix
        lines.extend(
            [
                "",
                "Confusion Matrix (Eligible = positive class)",
                "-" * 52,
                f"True Positive  : {matrix['true_positive']}",
                f"True Negative  : {matrix['true_negative']}",
                f"False Positive : {matrix['false_positive']}",
                f"False Negative : {matrix['false_negative']}",
            ]
        )
        if self.composite_status_counts:
            lines.extend(
                [
                    "",
                    "Composite workflow status (rules + documents)",
                    "-" * 52,
                ]
            )
            for key, value in self.composite_status_counts.items():
                lines.append(f"{key}: {value}")
            if self.doc_status_counts:
                lines.append("Document verification: " + ", ".join(
                    f"{k}={v}" for k, v in self.doc_status_counts.items()
                ))
            if self.mean_doc_completeness is not None:
                lines.append(
                    f"Mean document completeness score: {self.mean_doc_completeness:.4f}"
                )
        if self.misclassifications:
            lines.extend(["", "Misclassification analysis", "-" * 52])
            for item in self.misclassifications:
                lines.append(
                    f"{item['applicant_id']} | {item['scheme']} | "
                    f"predicted={item['predicted']} gold={item['ground_truth']} | "
                    f"failed={item['failed_rules']}"
                )
        else:
            lines.extend(
                [
                    "",
                    "Misclassification analysis",
                    "-" * 52,
                    "None. Engine labels match CSV rule_eligibility for every labelled record.",
                ]
            )
        return "\n".join(lines)


class ScholarshipEvaluator:
    """Batch evaluator: Phase-1 rules + Phase-3 document audit."""

    def __init__(
        self,
        engine: Optional[ScholarshipRuleEngine] = None,
        *,
        root: Optional[str] = None,
        auditor: Optional[DocumentAuditor] = None,
        deficiency_generator: Optional[DeficiencyNoticeGenerator] = None,
    ) -> None:
        self.engine = engine or ScholarshipRuleEngine(root=root)
        self.auditor = auditor or DocumentAuditor(root=root)
        self.deficiency_generator = deficiency_generator or DeficiencyNoticeGenerator()
        self.root = root

    def evaluate_rules(self, applicant: ApplicantProfile) -> ValidationResult:
        """Rule-only evaluation (Phase 2), with ground-truth attached."""
        result = self.engine.evaluate(applicant)
        return attach_ground_truth(result, applicant)

    def evaluate_one(self, applicant: ApplicantProfile) -> CompositeEvaluationResult:
        """Joint rule + document evaluation used by the production workflow."""
        rule_result = self.evaluate_rules(applicant)
        audit = self.auditor.audit(applicant)
        notice = self.deficiency_generator.generate_if_deficient(applicant, audit)
        return combine_rule_and_documents(rule_result, audit, deficiency_notice=notice)

    def evaluate_batch(
        self,
        applicants: Iterable[ApplicantProfile],
    ) -> list[CompositeEvaluationResult]:
        results: list[CompositeEvaluationResult] = []
        for applicant in applicants:
            try:
                results.append(self.evaluate_one(applicant))
            except Exception:
                logger.exception("Failed evaluating applicant %s", getattr(applicant, "applicant_id", "?"))
                raise
        return results

    def evaluate_dataset(
        self, root: Optional[str] = None
    ) -> tuple[list[CompositeEvaluationResult], EvaluationSummary]:
        applicants = load_applicants(root or self.root)
        results = self.evaluate_batch(applicants)
        return results, EvaluationSummary(results)

    def summarize(self, results: Sequence[ScoredResult]) -> EvaluationSummary:
        return EvaluationSummary(results)

    def export_json(
        self,
        results: Sequence[ScoredResult],
        output_path: Path | str,
        *,
        summary: Optional[EvaluationSummary] = None,
    ) -> Path:
        summary = summary or EvaluationSummary(results)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "phase": "phase3_composite",
            "summary": summary.to_dict(),
            "results": [item.model_dump(mode="json") for item in results],
            "label_distribution": dict(Counter(
                (r.eligibility_label.value if isinstance(r.eligibility_label, EligibilityLabel) else str(r.eligibility_label))
                for r in results
            )),
            "composite_status_distribution": dict(
                Counter(
                    r.composite_status.value
                    for r in results
                    if isinstance(r, CompositeEvaluationResult)
                )
            ),
        }
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Wrote evaluation payload to %s (%s records)", path, len(results))
        return path
