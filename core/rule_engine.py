"""Dynamic, extensible rule engine for the five MoTA scholarship / fellowship schemes.

Rules are loaded from ``MoTA_Scheme_Rules_SIH26239.csv`` and document checklists
from ``MoTA_Document_Requirements_SIH26239.csv``. Domain semantics that the CSVs
encode only as prose (QS Top-1000 marks waiver, orphan income exemption,
UGC 2(f)/12(B) recognition, one-child NOS restriction) are implemented as
named checks with guideline citations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Iterable, Optional, Sequence

from core.config import (
    documents_by_scheme,
    load_document_requirements,
    load_scheme_rules,
    rules_by_scheme,
)
from core.models import (
    ApplicantProfile,
    DocumentAuditItem,
    DocumentRequirement,
    EligibilityLabel,
    RequirementLevel,
    RuleCheck,
    RuleSeverity,
    SchemeRule,
    ValidationResult,
)

logger = logging.getLogger(__name__)

FELLOWSHIP_INSTITUTION_CATEGORIES = {
    "ugc 2(f)/12(b)",
    "ugc 2(f)",
    "ugc 12(b)",
    "deemed university eligible under section 3",
    "deemed to be university",
    "central/state government grant institution",
    "institute of national importance",
}


class SchemeValidator(ABC):
    """Abstract per-scheme validator. Subclasses add scheme-specific checks."""

    scheme_name: str

    def __init__(self, rule: SchemeRule, documents: Sequence[DocumentRequirement]) -> None:
        self.rule = rule
        self.documents = list(documents)

    def validate(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        checks.extend(self._common_identity_checks(applicant))
        checks.extend(self._course_and_age_checks(applicant))
        checks.extend(self.scheme_specific_checks(applicant))
        return checks

    @abstractmethod
    def scheme_specific_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        raise NotImplementedError

    def _common_identity_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        return [
            RuleCheck(
                rule_id="ST_STATUS",
                description="Applicant must belong to the Scheduled Tribe community.",
                passed=bool(applicant.is_st),
                evidence=f"st_status={applicant.st_status!r}",
                guideline_ref=self.rule.source,
            )
        ]

    def _course_and_age_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        if self.rule.eligible_courses:
            checks.append(
                RuleCheck(
                    rule_id="ELIGIBLE_COURSE",
                    description=(
                        "Course level must be among: "
                        + "; ".join(self.rule.eligible_courses)
                    ),
                    passed=self.rule.course_is_eligible(applicant.course_level),
                    evidence=f"course_level={applicant.course_level!r}",
                    guideline_ref=self.rule.source,
                )
            )
        max_age = self.rule.max_age_for(applicant.course_level)
        if max_age is not None:
            age_ok = applicant.age_years is not None and applicant.age_years <= max_age
            checks.append(
                RuleCheck(
                    rule_id="MAX_AGE",
                    description=f"Age must be <= {max_age} years as on 1 July of the award year.",
                    passed=age_ok,
                    evidence=f"age_years={applicant.age_years}, max_age={max_age}",
                    guideline_ref=self.rule.source,
                )
            )
        return checks

    def _income_check(self, applicant: ApplicantProfile) -> Optional[RuleCheck]:
        if not self.rule.income_criterion_applies:
            return RuleCheck(
                rule_id="INCOME_LIMIT",
                description="No family-income ceiling applies under this scheme.",
                passed=True,
                severity=RuleSeverity.SOFT,
                evidence=self.rule.income_rule or "No income criterion",
                guideline_ref=self.rule.source,
            )
        if (
            self.rule.orphan_income_exempt
            and applicant.is_orphan_supported_by_guardian is True
        ):
            return RuleCheck(
                rule_id="INCOME_LIMIT",
                description="Income ceiling waived for orphan supported by a guardian.",
                passed=True,
                evidence="is_orphan_supported_by_guardian=True",
                guideline_ref=self.rule.source,
            )
        if self.rule.income_limit_inr is None:
            return None
        income = applicant.family_income_inr
        passed = income is not None and income <= self.rule.income_limit_inr
        return RuleCheck(
            rule_id="INCOME_LIMIT",
            description=(
                f"Family income from all sources must be <= INR "
                f"{self.rule.income_limit_inr:,.0f} per annum."
            ),
            passed=passed,
            evidence=f"family_income_inr={income}",
            guideline_ref=self.rule.source,
        )

    def _marks_check(self, applicant: ApplicantProfile, *, qs_waiver: bool = False) -> Optional[RuleCheck]:
        if self.rule.min_marks_pct is None:
            return None
        waived = qs_waiver and applicant.institution_top1000_qs is True
        if waived:
            return RuleCheck(
                rule_id="MIN_MARKS",
                description=(
                    f"Minimum {self.rule.min_marks_pct}% waived because the applicant "
                    "holds admission in a QS Top-1000 institute."
                ),
                passed=True,
                evidence=(
                    f"qualifying_marks_pct={applicant.qualifying_marks_pct}, "
                    "institution_top1000_qs=True"
                ),
                guideline_ref="RevisedGuidelinesNOS07102022.pdf §2.2(i) Note",
            )
        marks = applicant.qualifying_marks_pct
        passed = marks is not None and marks >= self.rule.min_marks_pct
        return RuleCheck(
            rule_id="MIN_MARKS",
            description=f"Qualifying marks must be >= {self.rule.min_marks_pct}%.",
            passed=passed,
            evidence=f"qualifying_marks_pct={marks}",
            guideline_ref=self.rule.source,
        )

    @staticmethod
    def _flag_check(
        *,
        rule_id: str,
        description: str,
        actual: Optional[bool],
        expected: bool,
        guideline_ref: str,
        missing_fails: bool = True,
    ) -> RuleCheck:
        if actual is None:
            passed = not missing_fails
            evidence = "field not provided"
        else:
            passed = actual is expected
            evidence = f"actual={actual}, expected={expected}"
        return RuleCheck(
            rule_id=rule_id,
            description=description,
            passed=passed,
            evidence=evidence,
            guideline_ref=guideline_ref,
        )


class NationalOverseasScholarshipValidator(SchemeValidator):
    scheme_name = "National Overseas Scholarship (NOS) for ST Students"

    def scheme_specific_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        marks = self._marks_check(applicant, qs_waiver=self.rule.qs_top1000_marks_waiver)
        if marks:
            checks.append(marks)
        income = self._income_check(applicant)
        if income:
            checks.append(income)
        checks.append(
            self._flag_check(
                rule_id="ADMISSION_OFFER",
                description="Offer of admission from a reputed foreign university is required.",
                actual=applicant.admission_offer,
                expected=True,
                guideline_ref="RevisedGuidelinesNOS07102022.pdf §4.3",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="ONE_CHILD_PER_FAMILY",
                description="Not more than one child of the same parents may receive NOS.",
                actual=applicant.same_parents_other_child_awarded,
                expected=False,
                guideline_ref="RevisedGuidelinesNOS07102022.pdf §2.2(ii)",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="ONE_TIME_AWARD",
                description="An individual is eligible for only one NOS award (no repeat / higher-level second award).",
                actual=applicant.previous_nos_award,
                expected=False,
                guideline_ref="RevisedGuidelinesNOS07102022.pdf §2.2(ii)",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="NO_OTHER_SCHOLARSHIP",
                description="Student shall not receive another Centre/State scholarship for the same study.",
                actual=applicant.other_govt_scholarship_same_study,
                expected=False,
                guideline_ref="RevisedGuidelinesNOS07102022.pdf Note 2",
                missing_fails=False,
            )
        )
        checks.append(
            RuleCheck(
                rule_id="QS_TOP1000_PRIORITY",
                description=(
                    "Merit priority is given to QS Top-1000 ranked institutes; "
                    "this is a ranking preference, not a hard eligibility gate."
                ),
                passed=True,
                severity=RuleSeverity.SOFT,
                evidence=f"institution_top1000_qs={applicant.institution_top1000_qs}",
                guideline_ref="RevisedGuidelinesNOS07102022.pdf §4.4",
            )
        )
        return checks


class NationalFellowshipValidator(SchemeValidator):
    scheme_name = "National Fellowship Scheme"

    def scheme_specific_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        marks = self._marks_check(applicant)
        if marks:
            checks.append(marks)
        income = self._income_check(applicant)
        if income:
            checks.append(income)
        checks.append(
            self._flag_check(
                rule_id="REGULAR_FULL_TIME",
                description="Research programme must be regular and full-time M.Phil / Ph.D.",
                actual=applicant.regular_full_time,
                expected=True,
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf §2.4",
            )
        )
        category = (applicant.institution_category or "").strip().lower()
        recognized = category in FELLOWSHIP_INSTITUTION_CATEGORIES or (
            "2(f)" in category or "12(b)" in category or "national importance" in category
        )
        checks.append(
            RuleCheck(
                rule_id="UGC_INSTITUTION_CATEGORY",
                description=(
                    "Institution must be UGC 2(f)/12(B), a Section-3 deemed university "
                    "eligible for UGC grants, a Central/State grant institution, or an "
                    "Institute of National Importance."
                ),
                passed=recognized,
                evidence=f"institution_category={applicant.institution_category!r}",
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf §2.4",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="ADMISSION_CERTIFICATE",
                description="Admission / joining certificate of M.Phil / Ph.D is required.",
                actual=applicant.admission_certificate,
                expected=True,
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="NO_OTHER_FELLOWSHIP",
                description="Scholar shall not hold another Union/State fellowship for the same study.",
                actual=applicant.other_govt_scholarship_same_study,
                expected=False,
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf Note 1",
                missing_fails=False,
            )
        )
        return checks


class NationalScholarshipHigherEducationValidator(SchemeValidator):
    scheme_name = "National Scholarship Scheme (Higher Education)"

    def scheme_specific_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        income = self._income_check(applicant)
        if income:
            checks.append(income)
        checks.append(
            self._flag_check(
                rule_id="MINISTRY_NOTIFIED_INSTITUTE",
                description="Admission must be in a Ministry-notified premier institution and course.",
                actual=applicant.ministry_notified_institution_course,
                expected=True,
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf §2.1 / §2.3",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="ADMISSION_SECURED",
                description="Applicant must have secured admission in the notified programme.",
                actual=applicant.admission_secured,
                expected=True,
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf §2.1",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="NO_OTHER_SCHOLARSHIP",
                description="Student cannot claim another Centre/State scholarship for the same study.",
                actual=applicant.other_govt_scholarship_same_study,
                expected=False,
                guideline_ref="GuidelinesFellowshipandScholarship2022.pdf §2.1 Note 1",
                missing_fails=False,
            )
        )
        return checks


class PreMatricScholarshipValidator(SchemeValidator):
    scheme_name = "Pre-Matric Scholarship for ST Students"

    def scheme_specific_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        income = self._income_check(applicant)
        if income:
            checks.append(income)
        checks.append(
            self._flag_check(
                rule_id="DOMICILE_ST",
                description="ST status must be specified in relation to the student's domicile State/UT.",
                actual=applicant.domicile_matches_st,
                expected=True,
                guideline_ref="guidelinesPrematric.pdf §3.2(I)",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="RECOGNIZED_SCHOOL",
                description="Student must study in a Government or Government/Board-recognized school.",
                actual=applicant.school_government_or_recognized,
                expected=True,
                guideline_ref="guidelinesPrematric.pdf §3.2(II)",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="BANK_AADHAAR_MOBILE",
                description="Valid Scheduled Bank account linked with Aadhaar and mobile is required.",
                actual=applicant.scheduled_bank_aadhaar_mobile_linked,
                expected=True,
                guideline_ref="guidelinesPrematric.pdf §3.2(IV)",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="NO_OTHER_SCHOLARSHIP",
                description="Student should not be receiving any other scholarship.",
                actual=applicant.other_scholarship,
                expected=False,
                guideline_ref="guidelinesPrematric.pdf §3.2(V)",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="NO_REPEAT_SAME_CLASS",
                description="Scholarship for a class is available for only one year (no repeat funding).",
                actual=applicant.repeating_same_class,
                expected=False,
                guideline_ref="guidelinesPrematric.pdf §3.2(VI)",
                missing_fails=False,
            )
        )
        return checks


class PostMatricScholarshipValidator(SchemeValidator):
    scheme_name = "Post Matric Scholarship for ST Students"

    def scheme_specific_checks(self, applicant: ApplicantProfile) -> list[RuleCheck]:
        checks: list[RuleCheck] = []
        income = self._income_check(applicant)
        if income:
            checks.append(income)
        checks.append(
            self._flag_check(
                rule_id="DOMICILE_ST",
                description="ST status must be specified in relation to the student's domicile State/UT.",
                actual=applicant.domicile_matches_st,
                expected=True,
                guideline_ref="guidelinesPostmatric.pdf §3.2(a)",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="PASSED_QUALIFYING_EXAM",
                description="Student must have passed Matriculation / Higher Secondary or a higher qualifying exam.",
                actual=applicant.passed_required_qualifying_exam,
                expected=True,
                guideline_ref="guidelinesPostmatric.pdf §3.2(b)",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="RECOGNIZED_COURSE_INSTITUTION",
                description="Course and institution must be recognized as specified in the Post-Matric guidelines.",
                actual=applicant.recognized_course_institution,
                expected=True,
                guideline_ref="guidelinesPostmatric.pdf §3.2(f)",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="BANK_AADHAAR_MOBILE",
                description="Valid Scheduled Bank account linked with Aadhaar and mobile is required.",
                actual=applicant.scheduled_bank_aadhaar_mobile_linked,
                expected=True,
                guideline_ref="guidelinesPostmatric.pdf §3.2(d)",
            )
        )
        checks.append(
            self._flag_check(
                rule_id="NO_OTHER_SCHOLARSHIP",
                description="Student should not be receiving any other scholarship.",
                actual=applicant.other_scholarship,
                expected=False,
                guideline_ref="guidelinesPostmatric.pdf §3.2(e)",
                missing_fails=False,
            )
        )
        checks.append(
            self._flag_check(
                rule_id="SAME_STREAM",
                description="A completed stream cannot be followed by a different-stream diploma/degree under this scheme.",
                actual=applicant.same_stream_requirement_satisfied,
                expected=True,
                guideline_ref="guidelinesPostmatric.pdf §3.2.1",
                missing_fails=False,
            )
        )
        return checks


VALIDATOR_REGISTRY: dict[str, type[SchemeValidator]] = {
    NationalOverseasScholarshipValidator.scheme_name: NationalOverseasScholarshipValidator,
    NationalFellowshipValidator.scheme_name: NationalFellowshipValidator,
    NationalScholarshipHigherEducationValidator.scheme_name: NationalScholarshipHigherEducationValidator,
    PreMatricScholarshipValidator.scheme_name: PreMatricScholarshipValidator,
    PostMatricScholarshipValidator.scheme_name: PostMatricScholarshipValidator,
}

SCHEME_ALIASES: dict[str, str] = {
    "nos": NationalOverseasScholarshipValidator.scheme_name,
    "national overseas scholarship": NationalOverseasScholarshipValidator.scheme_name,
    "national fellowship": NationalFellowshipValidator.scheme_name,
    "national fellowship scheme": NationalFellowshipValidator.scheme_name,
    "national scholarship scheme": NationalScholarshipHigherEducationValidator.scheme_name,
    "national scholarship scheme (higher education)": NationalScholarshipHigherEducationValidator.scheme_name,
    "pre-matric scholarship for st students": PreMatricScholarshipValidator.scheme_name,
    "pre matric scholarship for st students": PreMatricScholarshipValidator.scheme_name,
    "post-matric scholarship for st students": PostMatricScholarshipValidator.scheme_name,
    "post matric scholarship for st students": PostMatricScholarshipValidator.scheme_name,
}


def canonicalize_scheme_name(name: str) -> str:
    raw = (name or "").strip()
    if raw in VALIDATOR_REGISTRY:
        return raw
    return SCHEME_ALIASES.get(raw.lower(), raw)


def _confidence(checks: Sequence[RuleCheck], documents_complete: bool) -> float:
    hard = [c for c in checks if c.severity == RuleSeverity.HARD]
    if not hard:
        rule_score = 1.0
    else:
        rule_score = sum(1.0 for c in hard if c.passed) / len(hard)
    doc_score = 1.0 if documents_complete else 0.55
    return round(0.85 * rule_score + 0.15 * doc_score, 4)


def _audit_documents(
    applicant: ApplicantProfile,
    requirements: Sequence[DocumentRequirement],
) -> tuple[list[DocumentAuditItem], list[str], bool]:
    issues: list[str] = []
    if applicant.document_issue and applicant.document_issue.strip().lower() not in {"none", ""}:
        issues.append(applicant.document_issue.strip())

    complete = bool(applicant.required_documents_complete)
    if applicant.required_documents_complete is None:
        complete = not issues

    audit: list[DocumentAuditItem] = []
    issue_blob = " ".join(issues).lower()
    for req in requirements:
        if req.requirement == RequirementLevel.CONDITIONAL:
            status = "Conditional — apply only if applicable"
        elif req.requirement == RequirementLevel.MANDATORY_CONDITIONAL:
            status = "Mandatory or conditional depending on course"
        elif req.requirement == RequirementLevel.MANDATORY_FOR_FRESH and not applicant.is_fresh:
            status = "Not required for renewal"
        else:
            token = req.document.split()[0].lower()
            missing = any(token in item.lower() for item in issues) or (
                token in issue_blob and not complete
            )
            if complete and not issues:
                status = "Present (declared complete; pending verification)"
            elif missing or not complete:
                status = "Deficiency / not evidenced"
            else:
                status = "Present (declared complete; pending verification)"
        audit.append(
            DocumentAuditItem(
                document=req.document,
                requirement=req.requirement,
                status=status,
                remarks=req.remarks,
            )
        )
    return audit, issues, complete


class ScholarshipRuleEngine:
    """Loads CSV scheme parameters at runtime and dispatches to scheme validators."""

    def __init__(
        self,
        *,
        root: Optional[str] = None,
        rules: Optional[Iterable[SchemeRule]] = None,
        documents: Optional[Iterable[DocumentRequirement]] = None,
    ) -> None:
        self._rules = list(rules) if rules is not None else list(load_scheme_rules(root))
        self._documents = (
            list(documents) if documents is not None else list(load_document_requirements(root))
        )
        self._rules_map = rules_by_scheme(self._rules)
        self._docs_map = documents_by_scheme(self._documents)
        self._validators: dict[str, SchemeValidator] = {}
        self._bind_validators()

    def _bind_validators(self) -> None:
        for scheme_name, validator_cls in VALIDATOR_REGISTRY.items():
            rule = self._resolve_rule(scheme_name)
            docs = self._docs_map.get(rule.scheme, [])
            self._validators[scheme_name] = validator_cls(rule, docs)
            logger.debug("Bound validator %s -> %s", scheme_name, validator_cls.__name__)

        for rule in self._rules:
            canonical = canonicalize_scheme_name(rule.scheme)
            if canonical not in self._validators:
                logger.warning("No dedicated validator for scheme %r; skipped.", rule.scheme)

    def _resolve_rule(self, scheme_name: str) -> SchemeRule:
        if scheme_name in self._rules_map:
            return self._rules_map[scheme_name]
        canonical = canonicalize_scheme_name(scheme_name)
        if canonical in self._rules_map:
            return self._rules_map[canonical]
        for name, rule in self._rules_map.items():
            if canonicalize_scheme_name(name) == canonical:
                return rule
        raise KeyError(f"No scheme rule loaded for {scheme_name!r}")

    @property
    def supported_schemes(self) -> list[str]:
        return list(self._validators.keys())

    def get_validator(self, scheme: str) -> SchemeValidator:
        canonical = canonicalize_scheme_name(scheme)
        try:
            return self._validators[canonical]
        except KeyError as exc:
            raise KeyError(
                f"Unsupported scheme {scheme!r}. Supported: {self.supported_schemes}"
            ) from exc

    def evaluate(self, applicant: ApplicantProfile) -> ValidationResult:
        validator = self.get_validator(applicant.scheme)
        checks = validator.validate(applicant)
        audit, issues, docs_complete = _audit_documents(applicant, validator.documents)

        hard = [c for c in checks if c.severity == RuleSeverity.HARD]
        is_eligible = all(c.passed for c in hard)
        passed = [c.rule_id for c in checks if c.passed]
        failed = [c.rule_id for c in checks if not c.passed]
        confidence = _confidence(checks, docs_complete)
        ready = is_eligible and docs_complete and not issues

        failed_hard = [c for c in hard if not c.passed]
        if is_eligible:
            rationale = "All hard eligibility rules passed as per loaded MoTA scheme parameters."
        else:
            rationale = "Failed: " + "; ".join(
                f"{c.rule_id} ({c.evidence})" for c in failed_hard
            )

        return ValidationResult(
            applicant_id=applicant.applicant_id,
            scheme=canonicalize_scheme_name(applicant.scheme),
            is_eligible=is_eligible,
            eligibility_label=(
                EligibilityLabel.ELIGIBLE if is_eligible else EligibilityLabel.NOT_ELIGIBLE
            ),
            passed_rules=passed,
            failed_rules=failed,
            rule_checks=checks,
            confidence_score=confidence,
            document_audit=audit,
            documents_complete=docs_complete,
            document_issues=issues,
            application_ready_for_processing=ready,
            rationale=rationale,
        )

    def evaluate_many(self, applicants: Iterable[ApplicantProfile]) -> list[ValidationResult]:
        return [self.evaluate(item) for item in applicants]
