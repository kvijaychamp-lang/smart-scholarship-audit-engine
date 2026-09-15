"""Load MoTA scheme rules and document checklists from the project root CSVs."""

from __future__ import annotations

import csv
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from core.models import (
    ApplicantProfile,
    DocumentRequirement,
    SchemeRule,
    SchemeType,
)

logger = logging.getLogger(__name__)

SCHEME_RULES_FILENAME = "MoTA_Scheme_Rules_SIH26239.csv"
DOCUMENT_REQUIREMENTS_FILENAME = "MoTA_Document_Requirements_SIH26239.csv"
APPLICANT_DATASET_FILENAME = "MoTA_Scholarship_Fellowship_Applicant_Dataset_SIH26239.csv"

_AGE_PAIR_RE = re.compile(
    r"(?P<course>[A-Za-z0-9 .'+/-]+?)\s+(?P<age>\d{1,2})(?:\s*years?)?",
    re.IGNORECASE,
)


def get_project_root(start: Optional[Path] = None) -> Path:
    """Resolve the repository root that contains the MoTA CSV/PDF artefacts."""
    cursor = Path(start or Path(__file__).resolve()).parent
    markers = {
        SCHEME_RULES_FILENAME,
        DOCUMENT_REQUIREMENTS_FILENAME,
        APPLICANT_DATASET_FILENAME,
    }
    for candidate in [cursor, *cursor.parents]:
        if any((candidate / name).is_file() for name in markers):
            return candidate
    return Path(__file__).resolve().parent.parent


class MoTAConfig:
    """Filesystem locations for official CSVs and guideline PDFs."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = get_project_root(root)

    @property
    def scheme_rules_csv(self) -> Path:
        return self.root / SCHEME_RULES_FILENAME

    @property
    def document_requirements_csv(self) -> Path:
        return self.root / DOCUMENT_REQUIREMENTS_FILENAME

    @property
    def applicant_dataset_csv(self) -> Path:
        return self.root / APPLICANT_DATASET_FILENAME

    def guideline_pdf(self, filename: str) -> Path:
        return self.root / filename


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required dataset not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _split_list(raw: Optional[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [part.strip() for part in re.split(r"[;|]", str(raw)) if part.strip()]


def _optional_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or str(raw).strip() == "":
        return None
    return float(str(raw).replace(",", "").strip())


def _parse_age_limits(raw: Optional[str]) -> tuple[Optional[int], dict[str, int]]:
    if raw is None or str(raw).strip() == "":
        return None, {}
    text = str(raw).strip()
    if re.fullmatch(r"\d{1,2}", text):
        return int(text), {}

    by_course: dict[str, int] = {}
    for chunk in _split_list(text):
        match = _AGE_PAIR_RE.search(chunk.strip())
        if not match:
            continue
        course = match.group("course").strip(" :-")
        by_course[course] = int(match.group("age"))
    fallback = max(by_course.values()) if by_course else None
    return fallback, by_course


def _income_flags(income_rule: str, income_limit: Optional[float]) -> tuple[bool, bool]:
    text = (income_rule or "").lower()
    applies = True
    if "no income criterion" in text or "no income criteria" in text:
        applies = False
    if income_limit is None and applies and "no income" in text:
        applies = False
    orphan_exempt = "orphan" in text and (
        "do not apply" in text or "shall not apply" in text or "does not apply" in text
    )
    return applies, orphan_exempt


def _derive_operational_flags(row: dict[str, str], rule: SchemeRule) -> SchemeRule:
    other = (row.get("other_key_rules") or "").lower()
    institution = (row.get("institution_rule") or "").lower()
    scheme = rule.scheme.lower()

    rule.qs_top1000_merit_priority = "qs" in institution or "qs" in other
    rule.qs_top1000_marks_waiver = "qs" in institution or "top 1,000" in institution or "top 1000" in institution
    rule.one_child_per_family = "not more than one child" in other or "one child" in other
    rule.one_time_award = "one-time" in other or "one time" in other or "cannot receive another award" in other
    rule.requires_regular_full_time = "regular and full-time" in other or "regular and full time" in other
    rule.requires_ugc_recognition = "2(f)" in institution or "12(b)" in institution or "ugc" in institution
    rule.requires_ministry_notified_institute = "ministry-notified" in other or "notified by ministry" in institution
    rule.requires_government_or_recognized_school = "government school" in other or "government/recognized school" in institution
    rule.requires_recognized_course_institution = "recognized course" in other or "recognized government" in institution
    rule.requires_scheduled_bank_aadhaar_mobile = "aadhaar" in other or "scheduled bank" in other
    rule.disallows_other_scholarship = "no other scholarship" in other or "cannot claim another" in other or "cannot receive another" in other
    rule.disallows_repeat_same_class = "repeat" in other
    rule.requires_same_stream = "same stream" in other
    rule.requires_passed_qualifying_exam = "passed matriculation" in other or "passed" in other and "qualifying" in other
    rule.requires_admission_offer = "offer of admission" in other or "nos" in scheme
    rule.requires_admission_certificate = "joining" in other or "fellowship" in scheme
    rule.requires_admission_secured = "admission must be" in other or "higher education" in scheme
    rule.requires_domicile_match = "pre-matric" in scheme or "post matric" in scheme or "post-matric" in scheme

    if "fellowship" in scheme:
        rule.requires_regular_full_time = True
        rule.requires_ugc_recognition = True
        rule.requires_admission_certificate = True
        rule.disallows_other_scholarship = True
        rule.income_criterion_applies = False
    if "overseas" in scheme or "nos" in scheme:
        rule.qs_top1000_marks_waiver = True
        rule.qs_top1000_merit_priority = True
        rule.one_child_per_family = True
        rule.one_time_award = True
        rule.requires_admission_offer = True
        rule.disallows_other_scholarship = True
        rule.orphan_income_exempt = True
    if "higher education" in scheme:
        rule.requires_ministry_notified_institute = True
        rule.requires_admission_secured = True
        rule.disallows_other_scholarship = True
        rule.orphan_income_exempt = True
    if "pre-matric" in scheme or "pre matric" in scheme:
        rule.requires_government_or_recognized_school = True
        rule.requires_scheduled_bank_aadhaar_mobile = True
        rule.disallows_other_scholarship = True
        rule.disallows_repeat_same_class = True
        rule.requires_domicile_match = True
        rule.orphan_income_exempt = True
    if "post matric" in scheme or "post-matric" in scheme:
        rule.requires_recognized_course_institution = True
        rule.requires_passed_qualifying_exam = True
        rule.requires_scheduled_bank_aadhaar_mobile = True
        rule.disallows_other_scholarship = True
        rule.requires_same_stream = True
        rule.requires_domicile_match = True
        rule.orphan_income_exempt = True
        joined_courses = " ".join(rule.eligible_courses).lower()
        if "onward" in joined_courses or "post-matric" in joined_courses:
            rule.eligible_courses = [
                "Class XI-XII",
                "Diploma",
                "Undergraduate",
                "Graduate",
                "Post Graduate",
                "Post Graduation",
            ]
    return rule


def parse_scheme_rule_row(row: dict[str, str]) -> SchemeRule:
    income_limit = _optional_float(row.get("income_limit_inr"))
    min_marks = _optional_float(row.get("min_marks_pct"))
    fallback_age, by_course = _parse_age_limits(row.get("max_age_years"))
    income_rule = row.get("income_rule") or ""
    applies, orphan_exempt = _income_flags(income_rule, income_limit)

    scheme_type_raw = (row.get("type") or "Scholarship").strip()
    scheme_type = (
        SchemeType.FELLOWSHIP
        if scheme_type_raw.lower().startswith("fellow")
        else SchemeType.SCHOLARSHIP
    )

    rule = SchemeRule(
        scheme=(row.get("scheme") or "").strip(),
        scheme_type=scheme_type,
        study_location=(row.get("study_location") or "").strip(),
        eligible_courses=_split_list(row.get("eligible_courses")),
        min_marks_pct=min_marks,
        max_age_years=fallback_age,
        max_age_by_course=by_course,
        income_limit_inr=income_limit,
        income_criterion_applies=applies,
        orphan_income_exempt=orphan_exempt,
        income_rule=income_rule,
        other_key_rules=row.get("other_key_rules") or "",
        institution_rule=row.get("institution_rule") or "",
        source=row.get("source") or "",
    )
    return _derive_operational_flags(row, rule)


def parse_document_requirement_row(row: dict[str, str]) -> DocumentRequirement:
    return DocumentRequirement(
        scheme=(row.get("Scheme") or row.get("scheme") or "").strip(),
        document=(row.get("Document") or row.get("document") or "").strip(),
        requirement=row.get("Requirement") or row.get("requirement") or "Mandatory",
        remarks=_empty(row.get("Remarks") or row.get("remarks")),
    )


def _empty(value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() in {"", "None"}:
        return None
    return str(value).strip()


@lru_cache(maxsize=4)
def load_scheme_rules(root: Optional[str] = None) -> tuple[SchemeRule, ...]:
    config = MoTAConfig(Path(root) if root else None)
    rows = _read_csv_rows(config.scheme_rules_csv)
    rules = tuple(parse_scheme_rule_row(row) for row in rows)
    logger.info("Loaded %s scheme rules from %s", len(rules), config.scheme_rules_csv)
    return rules


@lru_cache(maxsize=4)
def load_document_requirements(root: Optional[str] = None) -> tuple[DocumentRequirement, ...]:
    config = MoTAConfig(Path(root) if root else None)
    rows = _read_csv_rows(config.document_requirements_csv)
    docs = tuple(parse_document_requirement_row(row) for row in rows)
    logger.info("Loaded %s document requirements from %s", len(docs), config.document_requirements_csv)
    return docs


def load_applicants(root: Optional[str] = None) -> list[ApplicantProfile]:
    config = MoTAConfig(Path(root) if root else None)
    rows = _read_csv_rows(config.applicant_dataset_csv)
    return [ApplicantProfile.model_validate(row) for row in rows]


def rules_by_scheme(rules: Iterable[SchemeRule]) -> dict[str, SchemeRule]:
    return {rule.scheme: rule for rule in rules}


def documents_by_scheme(
    documents: Iterable[DocumentRequirement],
) -> dict[str, list[DocumentRequirement]]:
    grouped: dict[str, list[DocumentRequirement]] = {}
    for item in documents:
        grouped.setdefault(item.scheme, []).append(item)
    return grouped
