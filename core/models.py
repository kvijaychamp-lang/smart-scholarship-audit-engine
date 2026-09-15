"""Strongly typed domain models for MoTA scholarship / fellowship verification.

Field names on ``ApplicantProfile`` match the 34 columns in
``MoTA_Scholarship_Fellowship_Applicant_Dataset_SIH26239.csv``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


class SchemeType(str, Enum):
    SCHOLARSHIP = "Scholarship"
    FELLOWSHIP = "Fellowship"


class RequirementLevel(str, Enum):
    MANDATORY = "Mandatory"
    CONDITIONAL = "Conditional"
    MANDATORY_CONDITIONAL = "Mandatory/Conditional"
    MANDATORY_FOR_FRESH = "Mandatory for fresh"
    OPTIONAL = "Optional"


class EligibilityLabel(str, Enum):
    ELIGIBLE = "Eligible"
    NOT_ELIGIBLE = "Not Eligible"


class RuleSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    DOCUMENT = "document"


class FreshOrRenewal(str, Enum):
    FRESH = "Fresh"
    RENEWAL = "Renewal"


def _empty_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in {"", "None", "NA", "N/A", "-"}:
        return None
    return value


def _parse_bool(value: Any) -> Optional[bool]:
    value = _empty_to_none(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    raise ValueError(f"Cannot parse boolean from {value!r}")


class ApplicantProfile(BaseModel):
    """Canonical applicant record — one row of the SIH26239 applicant dataset."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        populate_by_name=True,
        use_enum_values=False,
    )

    applicant_id: str
    scheme: str
    st_status: Optional[str] = None
    pvtg_status: Optional[str] = None
    domicile_matches_st: Optional[bool] = None
    gender: Optional[str] = None
    age_years: Optional[int] = None
    course_level: Optional[str] = None
    qualifying_marks_pct: Optional[float] = None
    family_income_inr: Optional[float] = None
    institution_top1000_qs: Optional[bool] = None
    ministry_notified_institution_course: Optional[bool] = None
    institution_category: Optional[str] = None
    school_government_or_recognized: Optional[bool] = None
    recognized_course_institution: Optional[bool] = None
    regular_full_time: Optional[bool] = None
    admission_secured: Optional[bool] = None
    admission_offer: Optional[bool] = None
    admission_certificate: Optional[bool] = None
    passed_required_qualifying_exam: Optional[bool] = None
    scheduled_bank_aadhaar_mobile_linked: Optional[bool] = None
    other_govt_scholarship_same_study: Optional[bool] = None
    other_scholarship: Optional[bool] = None
    same_parents_other_child_awarded: Optional[bool] = None
    previous_nos_award: Optional[bool] = None
    repeating_same_class: Optional[bool] = None
    same_stream_requirement_satisfied: Optional[bool] = None
    fresh_or_renewal: Optional[str] = None
    required_documents_complete: Optional[bool] = None
    document_issue: Optional[str] = None
    rule_eligibility: Optional[str] = None
    source_pdf: Optional[str] = None
    application_ready_for_processing: Optional[str] = None
    document_check_result: Optional[str] = None

    # Optional normalized linkage keys supplied by intake/KYC systems.
    parentage_key: Optional[str] = None
    bank_account_key: Optional[str] = None

    # Policy extension not present in the 34-column CSV; used for orphan income waiver.
    is_orphan_supported_by_guardian: Optional[bool] = None

    # Optional document-inventory evidence used by Phase-3 DocumentAuditor.
    # The 34-column applicant CSV does not carry per-document flags; unit tests
    # and downstream intake APIs populate these when the packet is known.
    submitted_documents: Optional[list[str]] = None
    has_st_certificate: Optional[bool] = None
    has_income_certificate: Optional[bool] = None
    has_qualifying_marksheet: Optional[bool] = None
    has_fee_receipt: Optional[bool] = None
    has_bank_details: Optional[bool] = None
    has_valid_passport: Optional[bool] = None
    has_visa_proof: Optional[bool] = None
    visa_applicable: Optional[bool] = None
    has_qs_top1000_offer_letter: Optional[bool] = None
    has_supervisor_allocation_letter: Optional[bool] = None
    has_ugc_recognition_document: Optional[bool] = None
    is_divyangjan: Optional[bool] = None

    @field_validator(
        "st_status",
        "pvtg_status",
        "gender",
        "course_level",
        "institution_category",
        "fresh_or_renewal",
        "document_issue",
        "rule_eligibility",
        "source_pdf",
        "application_ready_for_processing",
        "document_check_result",
        mode="before",
    )
    @classmethod
    def _blank_strings(cls, value: Any) -> Any:
        return _empty_to_none(value)

    @field_validator(
        "domicile_matches_st",
        "institution_top1000_qs",
        "ministry_notified_institution_course",
        "school_government_or_recognized",
        "recognized_course_institution",
        "regular_full_time",
        "admission_secured",
        "admission_offer",
        "admission_certificate",
        "passed_required_qualifying_exam",
        "scheduled_bank_aadhaar_mobile_linked",
        "other_govt_scholarship_same_study",
        "other_scholarship",
        "same_parents_other_child_awarded",
        "previous_nos_award",
        "repeating_same_class",
        "same_stream_requirement_satisfied",
        "required_documents_complete",
        "is_orphan_supported_by_guardian",
        "has_st_certificate",
        "has_income_certificate",
        "has_qualifying_marksheet",
        "has_fee_receipt",
        "has_bank_details",
        "has_valid_passport",
        "has_visa_proof",
        "visa_applicable",
        "has_qs_top1000_offer_letter",
        "has_supervisor_allocation_letter",
        "has_ugc_recognition_document",
        "is_divyangjan",
        mode="before",
    )
    @classmethod
    def _bool_fields(cls, value: Any) -> Any:
        return _parse_bool(value)

    @field_validator("submitted_documents", mode="before")
    @classmethod
    def _document_list(cls, value: Any) -> Any:
        value = _empty_to_none(value)
        if value is None:
            return None
        if isinstance(value, str):
            parts = [part.strip() for part in value.replace("|", ";").split(";")]
            return [part for part in parts if part]
        return value

    @field_validator(
        "age_years",
        "qualifying_marks_pct",
        "family_income_inr",
        mode="before",
    )
    @classmethod
    def _numeric_fields(cls, value: Any) -> Any:
        value = _empty_to_none(value)
        if value is None or value == "":
            return None
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_st(self) -> bool:
        status = (self.st_status or "").strip().upper()
        return status in {"ST", "YES", "TRUE", "SCHEDULED TRIBE"}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_pvtg(self) -> bool:
        status = (self.pvtg_status or "").strip().upper()
        return status in {"PVTG", "YES", "TRUE"}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_fresh(self) -> bool:
        if not self.fresh_or_renewal:
            return True
        return self.fresh_or_renewal.strip().lower() == "fresh"


class SchemeRule(BaseModel):
    """Structured constraints loaded from ``MoTA_Scheme_Rules_SIH26239.csv``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    scheme: str
    scheme_type: SchemeType
    study_location: str
    eligible_courses: list[str] = Field(default_factory=list)
    min_marks_pct: Optional[float] = None
    max_age_years: Optional[int] = None
    max_age_by_course: dict[str, int] = Field(default_factory=dict)
    income_limit_inr: Optional[float] = None
    income_criterion_applies: bool = True
    orphan_income_exempt: bool = False
    income_rule: str = ""
    other_key_rules: str = ""
    institution_rule: str = ""
    source: str = ""
    qs_top1000_marks_waiver: bool = False
    qs_top1000_merit_priority: bool = False
    one_child_per_family: bool = False
    one_time_award: bool = False
    requires_regular_full_time: bool = False
    requires_ugc_recognition: bool = False
    requires_ministry_notified_institute: bool = False
    requires_government_or_recognized_school: bool = False
    requires_recognized_course_institution: bool = False
    requires_scheduled_bank_aadhaar_mobile: bool = False
    disallows_other_scholarship: bool = False
    disallows_repeat_same_class: bool = False
    requires_same_stream: bool = False
    requires_passed_qualifying_exam: bool = False
    requires_admission_offer: bool = False
    requires_admission_certificate: bool = False
    requires_admission_secured: bool = False
    requires_domicile_match: bool = False

    def course_is_eligible(self, course_level: Optional[str]) -> bool:
        if not self.eligible_courses:
            return True
        if not course_level:
            return False
        needle = _normalize_course(course_level)
        allowed = {_normalize_course(item) for item in self.eligible_courses}
        if needle in allowed:
            return True
        aliases = COURSE_ALIASES.get(needle, set())
        return bool(allowed.intersection(aliases | {needle}))

    def max_age_for(self, course_level: Optional[str]) -> Optional[int]:
        if course_level:
            key = _normalize_course(course_level)
            for course, age in self.max_age_by_course.items():
                if _normalize_course(course) == key:
                    return age
        return self.max_age_years


class DocumentRequirement(BaseModel):
    """One checklist row from ``MoTA_Document_Requirements_SIH26239.csv``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    scheme: str
    document: str
    requirement: RequirementLevel
    remarks: Optional[str] = None

    @field_validator("requirement", mode="before")
    @classmethod
    def _parse_requirement(cls, value: Any) -> RequirementLevel:
        if isinstance(value, RequirementLevel):
            return value
        raw = str(value or "").strip()
        mapping = {
            "mandatory": RequirementLevel.MANDATORY,
            "conditional": RequirementLevel.CONDITIONAL,
            "mandatory/conditional": RequirementLevel.MANDATORY_CONDITIONAL,
            "mandatory for fresh": RequirementLevel.MANDATORY_FOR_FRESH,
            "optional": RequirementLevel.OPTIONAL,
        }
        if raw.lower() not in mapping:
            raise ValueError(f"Unknown document requirement level: {raw!r}")
        return mapping[raw.lower()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_hard_mandatory(self) -> bool:
        return self.requirement in {
            RequirementLevel.MANDATORY,
            RequirementLevel.MANDATORY_FOR_FRESH,
        }


class RuleCheck(BaseModel):
    rule_id: str
    description: str
    passed: bool
    severity: RuleSeverity = RuleSeverity.HARD
    evidence: str = ""
    guideline_ref: Optional[str] = None


class DocumentAuditItem(BaseModel):
    document: str
    requirement: RequirementLevel
    status: str
    remarks: Optional[str] = None
    tier: str = "mandatory"
    evidence: str = ""
    waived: bool = False


class DocVerificationStatus(str, Enum):
    """Outcome of the two-tier document audit (independent of rule eligibility)."""

    FULLY_VERIFIED = "FULLY_VERIFIED"
    DEFICIENT = "DEFICIENT"
    REJECTED_MISSING_MANDATORY = "REJECTED_MISSING_MANDATORY"


class CompositeDecision(str, Enum):
    """Joint rule + document decision used for disbursal workflow routing."""

    READY_FOR_DISBURSAL = "READY_FOR_DISBURSAL"
    PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS = "PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS"
    REJECTED = "REJECTED"


class DocumentAuditResult(BaseModel):
    """Structured output of ``DocumentAuditor.audit``."""

    applicant_id: str
    scheme: str
    completeness_score: float = Field(ge=0.0, le=1.0)
    doc_verification_status: DocVerificationStatus
    items: list[DocumentAuditItem] = Field(default_factory=list)
    missing_mandatory_docs: list[str] = Field(default_factory=list)
    missing_conditional_docs: list[str] = Field(default_factory=list)
    waived_docs: list[str] = Field(default_factory=list)
    not_applicable_docs: list[str] = Field(default_factory=list)
    documents_complete: bool = False
    issues: list[str] = Field(default_factory=list)


class DeficiencyNotice(BaseModel):
    """Formal cure-period notice for an incomplete document packet."""

    applicant_id: str
    scheme: str
    missing_mandatory_docs: list[str] = Field(default_factory=list)
    missing_conditional_docs: list[str] = Field(default_factory=list)
    action_required: str
    cure_period_days: int = 15
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    policy_ref: str = "MoTA scholarship / fellowship processing guidelines — 15-day deficiency cure period"


class CompositeEvaluationResult(BaseModel):
    """Unified Phase-3 outcome: rule eligibility + document audit + routing status."""

    model_config = ConfigDict(extra="ignore")

    rule_result: ValidationResult
    document_audit_result: DocumentAuditResult
    composite_status: CompositeDecision
    deficiency_notice: Optional[DeficiencyNotice] = None
    approved: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def applicant_id(self) -> str:
        return self.rule_result.applicant_id

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scheme(self) -> str:
        return self.rule_result.scheme

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_eligible(self) -> bool:
        return self.rule_result.is_eligible

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eligibility_label(self) -> EligibilityLabel:
        return self.rule_result.eligibility_label

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed_rules(self) -> list[str]:
        return self.rule_result.passed_rules

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_rules(self) -> list[str]:
        return self.rule_result.failed_rules

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rule_checks(self) -> list[RuleCheck]:
        return self.rule_result.rule_checks

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence_score(self) -> float:
        return self.rule_result.confidence_score

    @computed_field  # type: ignore[prop-decorator]
    @property
    def document_audit(self) -> list[DocumentAuditItem]:
        return self.document_audit_result.items

    @computed_field  # type: ignore[prop-decorator]
    @property
    def documents_complete(self) -> bool:
        return self.document_audit_result.documents_complete

    @computed_field  # type: ignore[prop-decorator]
    @property
    def document_issues(self) -> list[str]:
        return self.document_audit_result.issues

    @computed_field  # type: ignore[prop-decorator]
    @property
    def application_ready_for_processing(self) -> bool:
        return self.rule_result.application_ready_for_processing

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rationale(self) -> str:
        return self.rule_result.rationale

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ground_truth_label(self) -> Optional[str]:
        return self.rule_result.ground_truth_label

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ground_truth_match(self) -> Optional[bool]:
        return self.rule_result.ground_truth_match

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evaluated_at(self) -> datetime:
        return self.rule_result.evaluated_at

    @computed_field  # type: ignore[prop-decorator]
    @property
    def doc_verification_status(self) -> DocVerificationStatus:
        return self.document_audit_result.doc_verification_status

    @computed_field  # type: ignore[prop-decorator]
    @property
    def document_completeness_score(self) -> float:
        return self.document_audit_result.completeness_score


class ValidationResult(BaseModel):
    applicant_id: str
    scheme: str
    is_eligible: bool
    eligibility_label: EligibilityLabel
    passed_rules: list[str] = Field(default_factory=list)
    failed_rules: list[str] = Field(default_factory=list)
    rule_checks: list[RuleCheck] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
    document_audit: list[DocumentAuditItem] = Field(default_factory=list)
    documents_complete: bool = False
    document_issues: list[str] = Field(default_factory=list)
    application_ready_for_processing: bool = False
    rationale: str = ""
    ground_truth_label: Optional[str] = None
    ground_truth_match: Optional[bool] = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _sync_label(self) -> "ValidationResult":
        self.eligibility_label = (
            EligibilityLabel.ELIGIBLE if self.is_eligible else EligibilityLabel.NOT_ELIGIBLE
        )
        return self


COURSE_ALIASES: dict[str, set[str]] = {
    "masters": {"master's", "masters", "master", "post graduate", "postgraduate", "pg"},
    "master's": {"masters", "master", "post graduate"},
    "ph.d": {"phd", "ph.d.", "doctorate"},
    "post-doctoral research": {"post doctoral research", "postdoctoral research", "pdr"},
    "m.phil": {"mphil", "m.phil."},
    "m.phil + ph.d": {"m.phil + phd", "integrated m.phil+ph.d", "mphil + phd"},
    "graduate": {"graduate level", "undergraduate", "ug", "bachelors"},
    "post graduate": {"post graduate level", "postgraduate", "pg", "masters"},
    "class ix": {"class 9", "ix", "9"},
    "class x": {"class 10", "x", "10"},
    "class xi-xii": {"class xi", "class xii", "xi-xii", "11-12"},
    "diploma": {"diploma"},
    "undergraduate": {"graduate", "ug"},
}


def _normalize_course(value: str) -> str:
    return " ".join(value.strip().lower().replace("–", "-").replace("—", "-").split())
