"""Phase-3 Document Audit Engine for MoTA SIH26239.

Two-tier verification over the dynamic Document Requirement Matrix parsed
from ``MoTA_Document_Requirements_SIH26239.csv``:

1. **Mandatory** — scheme checklist rows marked Mandatory / Mandatory for fresh
   (fresh applicants only), plus Mandatory/Conditional rows that apply to the
   declared course. Core families include ST certificate, income certificate,
   qualifying marksheet, fee receipt, and bank-account evidence.
2. **Conditional** — checklist rows that apply only in context, plus scheme
   overlays that the CSV encodes only as prose:
   * NOS — valid passport; offer letter from a QS Top-1000 university when
     that route is claimed; visa proof when a visa is applicable.
   * National Fellowship — M.Phil/Ph.D admission/joining letter (CSV
     mandatory), UGC 2(f)/12(B) recognition document, supervisor allocation
     letter.
   * Orphan supported by a guardian — income certificate is **waived**
     (NOS / National Scholarship / Pre-Matric / Post-Matric guidelines).

Document presence is resolved from (in order): explicit inventory flags,
``submitted_documents``, scheme-linked profile fields, ``document_issue``
text, then the dataset-level ``required_documents_complete`` declaration.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional, Sequence

from core.config import documents_by_scheme, load_document_requirements
from core.models import (
    ApplicantProfile,
    DocVerificationStatus,
    DocumentAuditItem,
    DocumentAuditResult,
    DocumentRequirement,
    RequirementLevel,
)
from core.rule_engine import (
    FELLOWSHIP_INSTITUTION_CATEGORIES,
    canonicalize_scheme_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status tokens written onto ``DocumentAuditItem.status``
# ---------------------------------------------------------------------------
STATUS_PRESENT = "PRESENT"
STATUS_MISSING = "MISSING"
STATUS_WAIVED = "WAIVED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

TIER_MANDATORY = "mandatory"
TIER_CONDITIONAL = "conditional"

NOS_SCHEME = "National Overseas Scholarship (NOS) for ST Students"
FELLOWSHIP_SCHEME = "National Fellowship Scheme"

# Extra conditional artefacts that official guidelines require but the CSV
# checklist either omits or only implies via remarks.
NOS_PASSPORT = "Valid Passport"
NOS_QS_OFFER = "Offer Letter from Top 1000 QS Ranked University"
NOS_VISA = "Visa proof"
FELLOWSHIP_UGC_DOC = "UGC/12B/2f institute recognition document"
FELLOWSHIP_SUPERVISOR = "Supervisor allocation letter"

_INCOME_RE = re.compile(r"\bincome certificate\b|\bfamily income\b", re.IGNORECASE)
_ST_RE = re.compile(r"\bst(?:/pvtg)? certificate\b|\bst certificate/pvtg", re.IGNORECASE)
_MARKSHEET_RE = re.compile(r"marksheet|qualifying examination|last qualified marks", re.IGNORECASE)
_FEE_RE = re.compile(r"\bfee receipt\b", re.IGNORECASE)
_BANK_RE = re.compile(r"bank passbook|bank account|aadhaar number", re.IGNORECASE)
_OFFER_RE = re.compile(r"offer of admission", re.IGNORECASE)
_JOINING_RE = re.compile(r"admission/joining|joining certificate", re.IGNORECASE)
_DOMICILE_RE = re.compile(r"\bdomicile\b", re.IGNORECASE)
_PHOTO_RE = re.compile(r"photograph", re.IGNORECASE)
_PASSPORT_BOOK_RE = re.compile(r"\bvalid passport\b|\bpassport\b(?!\s*-?\s*size)", re.IGNORECASE)
_VISA_RE = re.compile(r"\bvisa\b", re.IGNORECASE)
_SUPERVISOR_RE = re.compile(r"supervisor", re.IGNORECASE)
_UGC_RE = re.compile(r"ugc|2\(f\)|12\(b\)|12b", re.IGNORECASE)
_QS_RE = re.compile(r"qs|top 1000|top-1000", re.IGNORECASE)


class DocumentRequirementMatrix:
    """Scheme → checklist rows, loaded from the MoTA document-requirements CSV."""

    def __init__(self, requirements: Sequence[DocumentRequirement]) -> None:
        self._by_scheme = documents_by_scheme(requirements)
        self._canonical: dict[str, list[DocumentRequirement]] = {}
        for name, rows in self._by_scheme.items():
            self._canonical[canonicalize_scheme_name(name)] = rows
            self._canonical[name] = rows

    def for_scheme(self, scheme: str) -> list[DocumentRequirement]:
        canonical = canonicalize_scheme_name(scheme)
        if canonical in self._canonical:
            return list(self._canonical[canonical])
        if scheme in self._canonical:
            return list(self._canonical[scheme])
        for name, rows in self._canonical.items():
            if canonicalize_scheme_name(name) == canonical:
                return list(rows)
        raise KeyError(f"No document checklist loaded for scheme {scheme!r}")

    @property
    def schemes(self) -> list[str]:
        return sorted(self._by_scheme.keys())


class DocumentAuditor:
    """Two-tier document verifier producing a completeness score and status."""

    def __init__(
        self,
        *,
        root: Optional[str] = None,
        requirements: Optional[Iterable[DocumentRequirement]] = None,
        strict_extra_checks: bool = False,
    ) -> None:
        loaded = (
            list(requirements)
            if requirements is not None
            else list(load_document_requirements(root))
        )
        self.matrix = DocumentRequirementMatrix(loaded)
        # When True, unspecified NOS/Fellowship extra artefacts count as missing.
        # Default False keeps the 120-row dataset (no per-doc flags) stable.
        self.strict_extra_checks = strict_extra_checks

    def audit(self, applicant: ApplicantProfile) -> DocumentAuditResult:
        scheme = canonicalize_scheme_name(applicant.scheme)
        checklist = self.matrix.for_scheme(scheme)
        declared_issue = _declared_issue(applicant)

        items: list[DocumentAuditItem] = []
        for req in checklist:
            items.append(self._audit_checklist_row(applicant, req, declared_issue))
        items.extend(self._audit_scheme_overlays(applicant, declared_issue, scheme))

        missing_mandatory = [
            item.document
            for item in items
            if item.tier == TIER_MANDATORY and item.status == STATUS_MISSING
        ]
        missing_conditional = [
            item.document
            for item in items
            if item.tier == TIER_CONDITIONAL and item.status == STATUS_MISSING
        ]
        waived = [item.document for item in items if item.status == STATUS_WAIVED]
        not_applicable = [
            item.document for item in items if item.status == STATUS_NOT_APPLICABLE
        ]

        applicable = [
            item
            for item in items
            if item.status not in {STATUS_NOT_APPLICABLE}
        ]
        satisfied = [
            item
            for item in applicable
            if item.status in {STATUS_PRESENT, STATUS_WAIVED}
        ]
        if not applicable:
            score = 1.0
        else:
            score = round(len(satisfied) / len(applicable), 4)

        if missing_mandatory:
            status = DocVerificationStatus.REJECTED_MISSING_MANDATORY
        elif missing_conditional:
            status = DocVerificationStatus.DEFICIENT
        else:
            status = DocVerificationStatus.FULLY_VERIFIED

        issues: list[str] = []
        if declared_issue:
            issues.append(declared_issue)
        issues.extend(f"Missing mandatory: {name}" for name in missing_mandatory)
        issues.extend(f"Missing conditional: {name}" for name in missing_conditional)

        complete = status is DocVerificationStatus.FULLY_VERIFIED
        logger.debug(
            "Document audit %s scheme=%s status=%s score=%.4f",
            applicant.applicant_id,
            scheme,
            status.value,
            score,
        )
        return DocumentAuditResult(
            applicant_id=applicant.applicant_id,
            scheme=scheme,
            completeness_score=score,
            doc_verification_status=status,
            items=items,
            missing_mandatory_docs=missing_mandatory,
            missing_conditional_docs=missing_conditional,
            waived_docs=waived,
            not_applicable_docs=not_applicable,
            documents_complete=complete,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Checklist row (CSV matrix)
    # ------------------------------------------------------------------

    def _audit_checklist_row(
        self,
        applicant: ApplicantProfile,
        req: DocumentRequirement,
        declared_issue: Optional[str],
    ) -> DocumentAuditItem:
        applicable, reason = self._row_applies(applicant, req)
        is_income = _is_income_document(req.document)
        orphan_waiver = is_income and _orphan_income_waiver_applies(applicant)

        if orphan_waiver:
            return DocumentAuditItem(
                document=req.document,
                requirement=req.requirement,
                status=STATUS_WAIVED,
                remarks=(
                    (req.remarks or "")
                    + (" | " if req.remarks else "")
                    + "Waived: orphan supported by guardian (MoTA income criteria do not apply)"
                ).strip(" |"),
                tier=TIER_MANDATORY,
                evidence="is_orphan_supported_by_guardian=True",
                waived=True,
            )

        if not applicable:
            return DocumentAuditItem(
                document=req.document,
                requirement=req.requirement,
                status=STATUS_NOT_APPLICABLE,
                remarks=req.remarks,
                tier=_tier_for(req),
                evidence=reason,
            )

        present, evidence = self._is_present(applicant, req.document, declared_issue)
        return DocumentAuditItem(
            document=req.document,
            requirement=req.requirement,
            status=STATUS_PRESENT if present else STATUS_MISSING,
            remarks=req.remarks,
            tier=_tier_for(req, mandatory_conditional_applies=True),
            evidence=evidence,
        )

    def _row_applies(
        self,
        applicant: ApplicantProfile,
        req: DocumentRequirement,
    ) -> tuple[bool, str]:
        name = req.document.lower()
        level = req.requirement

        if level == RequirementLevel.OPTIONAL:
            return False, "Optional document is not scored unless submitted"

        if level == RequirementLevel.MANDATORY_FOR_FRESH and not applicant.is_fresh:
            return False, "Mandatory for fresh applications only; applicant is renewal"

        if "pvtg" in name and "st/" not in name and "st certificate/pvtg" not in name:
            # Stand-alone PVTG certificate (NOS) — only if the applicant is PVTG.
            if not applicant.is_pvtg:
                return False, "PVTG certificate applies only to PVTG applicants"
            return True, "PVTG applicant"

        if "divyang" in name or "disability" in name:
            if applicant.is_divyangjan is True:
                return True, "Divyangjan / disability certificate applicable"
            return False, "Disability certificate not applicable (not declared)"

        if "conversion sheet" in name:
            return False, "CGPA conversion sheet applies only where CGPA is used"

        if "iits" in name or "aiims" in name or "iims" in name or "iisrs" in name or "iisers" in name:
            category = (applicant.institution_category or "").lower()
            tokens = ("iit", "aiims", "iim", "iiser")
            if any(token in category for token in tokens):
                return True, f"Premier-institute offer letter applies ({category})"
            return False, "IIT/AIIMS/IIM/IISER offer letter not applicable"

        if level == RequirementLevel.CONDITIONAL:
            remarks = (req.remarks or "").lower()
            if "only if applicable" in remarks or "where applicable" in remarks:
                # Already handled specific names above; remaining generic
                # conditionals stay out of the scored set unless evidence exists.
                return False, "Conditional — not in force for this applicant"
            return True, "Conditional requirement in force"

        if level == RequirementLevel.MANDATORY_CONDITIONAL:
            return True, "Course-linked academic document treated as required"

        return True, "Mandatory checklist item"

    # ------------------------------------------------------------------
    # Scheme overlays (NOS / Fellowship extras)
    # ------------------------------------------------------------------

    def _audit_scheme_overlays(
        self,
        applicant: ApplicantProfile,
        declared_issue: Optional[str],
        scheme: str,
    ) -> list[DocumentAuditItem]:
        extras: list[DocumentAuditItem] = []
        if scheme == NOS_SCHEME:
            extras.append(
                self._overlay_item(
                    applicant,
                    document=NOS_PASSPORT,
                    present_flag=applicant.has_valid_passport,
                    declared_issue=declared_issue,
                    issue_re=_PASSPORT_BOOK_RE,
                    applicable=True,
                    not_applicable_reason="",
                    evidence_present="has_valid_passport=True",
                )
            )
            qs_claimed = applicant.institution_top1000_qs is True
            qs_present = applicant.has_qs_top1000_offer_letter
            if qs_present is None and qs_claimed:
                qs_present = applicant.admission_offer
            extras.append(
                self._overlay_item(
                    applicant,
                    document=NOS_QS_OFFER,
                    present_flag=qs_present,
                    declared_issue=declared_issue,
                    issue_re=_QS_RE,
                    applicable=qs_claimed,
                    not_applicable_reason="QS Top-1000 offer letter applies only when that institute route is claimed",
                    evidence_present=(
                        f"institution_top1000_qs={applicant.institution_top1000_qs}, "
                        f"admission_offer={applicant.admission_offer}"
                    ),
                )
            )
            visa_applies = applicant.visa_applicable is True
            extras.append(
                self._overlay_item(
                    applicant,
                    document=NOS_VISA,
                    present_flag=applicant.has_visa_proof,
                    declared_issue=declared_issue,
                    issue_re=_VISA_RE,
                    applicable=visa_applies,
                    not_applicable_reason="Visa proof is required only when a visa is applicable",
                    evidence_present="has_visa_proof=True",
                )
            )
        elif scheme == FELLOWSHIP_SCHEME:
            ugc_flag = applicant.has_ugc_recognition_document
            if ugc_flag is None:
                category = (applicant.institution_category or "").strip().lower()
                ugc_flag = category in FELLOWSHIP_INSTITUTION_CATEGORIES or (
                    "2(f)" in category or "12(b)" in category or "national importance" in category
                )
            extras.append(
                self._overlay_item(
                    applicant,
                    document=FELLOWSHIP_UGC_DOC,
                    present_flag=ugc_flag,
                    declared_issue=declared_issue,
                    issue_re=_UGC_RE,
                    applicable=True,
                    not_applicable_reason="",
                    evidence_present=f"institution_category={applicant.institution_category!r}",
                )
            )
            extras.append(
                self._overlay_item(
                    applicant,
                    document=FELLOWSHIP_SUPERVISOR,
                    present_flag=applicant.has_supervisor_allocation_letter,
                    declared_issue=declared_issue,
                    issue_re=_SUPERVISOR_RE,
                    applicable=True,
                    not_applicable_reason="",
                    evidence_present="has_supervisor_allocation_letter=True",
                    default_present_if_unspecified=not self.strict_extra_checks,
                )
            )
        return extras

    def _overlay_item(
        self,
        applicant: ApplicantProfile,
        *,
        document: str,
        present_flag: Optional[bool],
        declared_issue: Optional[str],
        issue_re: re.Pattern[str],
        applicable: bool,
        not_applicable_reason: str,
        evidence_present: str,
        default_present_if_unspecified: Optional[bool] = None,
    ) -> DocumentAuditItem:
        if not applicable:
            return DocumentAuditItem(
                document=document,
                requirement=RequirementLevel.CONDITIONAL,
                status=STATUS_NOT_APPLICABLE,
                remarks=not_applicable_reason,
                tier=TIER_CONDITIONAL,
                evidence=not_applicable_reason,
            )

        if default_present_if_unspecified is None:
            default_present_if_unspecified = not self.strict_extra_checks

        present, evidence = self._resolve_flag(
            applicant,
            document=document,
            present_flag=present_flag,
            declared_issue=declared_issue,
            issue_re=issue_re,
            default_present_if_unspecified=default_present_if_unspecified,
            evidence_present=evidence_present,
        )
        return DocumentAuditItem(
            document=document,
            requirement=RequirementLevel.CONDITIONAL,
            status=STATUS_PRESENT if present else STATUS_MISSING,
            remarks="Scheme-specific conditional document (MoTA guidelines)",
            tier=TIER_CONDITIONAL,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Presence resolution
    # ------------------------------------------------------------------

    def _is_present(
        self,
        applicant: ApplicantProfile,
        document: str,
        declared_issue: Optional[str],
    ) -> tuple[bool, str]:
        """Return (present, evidence) for a CSV checklist document name."""
        mapped_flag, flag_name = _mapped_presence_flag(applicant, document)
        return self._resolve_flag(
            applicant,
            document=document,
            present_flag=mapped_flag,
            declared_issue=declared_issue,
            issue_re=_issue_pattern_for(document),
            default_present_if_unspecified=True,
            evidence_present=f"{flag_name}={mapped_flag}" if flag_name else "inventory match",
        )

    def _resolve_flag(
        self,
        applicant: ApplicantProfile,
        *,
        document: str,
        present_flag: Optional[bool],
        declared_issue: Optional[str],
        issue_re: Optional[re.Pattern[str]],
        default_present_if_unspecified: bool,
        evidence_present: str,
    ) -> tuple[bool, str]:
        if declared_issue and issue_re and issue_re.search(declared_issue):
            return False, f"document_issue={declared_issue!r}"

        if _submitted_contains(applicant, document):
            return True, "listed in submitted_documents"

        if present_flag is True:
            return True, evidence_present
        if present_flag is False:
            return False, evidence_present.replace("True", "False")

        if applicant.required_documents_complete is False:
            # A named deficiency for a *different* document does not taint this one.
            if declared_issue and issue_re and not issue_re.search(declared_issue):
                return True, "packet incomplete on a different artefact; this item not named"
            if declared_issue:
                return True, "named deficiency does not match this document"
            if default_present_if_unspecified:
                return False, "required_documents_complete=False and no item-level evidence"
            return False, "unspecified extra artefact under incomplete packet"

        if applicant.required_documents_complete is True:
            return True, "required_documents_complete=True (declared complete; pending physical verification)"

        if default_present_if_unspecified:
            return True, "no contrary evidence; treated as present"
        return False, "no evidence supplied for extra conditional artefact"


def _tier_for(
    req: DocumentRequirement,
    *,
    mandatory_conditional_applies: bool = False,
) -> str:
    if req.requirement == RequirementLevel.CONDITIONAL:
        return TIER_CONDITIONAL
    if req.requirement == RequirementLevel.MANDATORY_CONDITIONAL:
        return TIER_MANDATORY if mandatory_conditional_applies else TIER_CONDITIONAL
    return TIER_MANDATORY


def _declared_issue(applicant: ApplicantProfile) -> Optional[str]:
    raw = (applicant.document_issue or "").strip()
    if not raw or raw.lower() in {"none", "n/a", "na", "-"}:
        return None
    return raw


def _orphan_income_waiver_applies(applicant: ApplicantProfile) -> bool:
    return applicant.is_orphan_supported_by_guardian is True


def _is_income_document(document: str) -> bool:
    return bool(_INCOME_RE.search(document or ""))


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _submitted_contains(applicant: ApplicantProfile, document: str) -> bool:
    inventory = applicant.submitted_documents or []
    needle = _normalize(document)
    if not needle:
        return False
    for item in inventory:
        hay = _normalize(item)
        if needle in hay or hay in needle:
            return True
        # Token overlap for long official names vs short intake labels.
        needle_tokens = set(needle.split())
        hay_tokens = set(hay.split())
        if len(needle_tokens) >= 2 and needle_tokens <= hay_tokens:
            return True
    return False


def _mapped_presence_flag(
    applicant: ApplicantProfile,
    document: str,
) -> tuple[Optional[bool], Optional[str]]:
    """Map a checklist document onto an ApplicantProfile evidence field."""
    if _ST_RE.search(document) or document.lower().startswith("st certificate"):
        if applicant.has_st_certificate is not None:
            return applicant.has_st_certificate, "has_st_certificate"
        # ST status is identity, not the certificate scan — only use as a weak
        # positive when the packet is otherwise complete (handled by fallback).
        return None, None
    if _is_income_document(document):
        return applicant.has_income_certificate, "has_income_certificate"
    if _MARKSHEET_RE.search(document):
        if applicant.has_qualifying_marksheet is not None:
            return applicant.has_qualifying_marksheet, "has_qualifying_marksheet"
        if applicant.qualifying_marks_pct is not None and applicant.required_documents_complete is not False:
            return True, "qualifying_marks_pct"
        return None, None
    if _FEE_RE.search(document):
        return applicant.has_fee_receipt, "has_fee_receipt"
    if _BANK_RE.search(document):
        if applicant.has_bank_details is not None:
            return applicant.has_bank_details, "has_bank_details"
        if applicant.scheduled_bank_aadhaar_mobile_linked is not None:
            return applicant.scheduled_bank_aadhaar_mobile_linked, "scheduled_bank_aadhaar_mobile_linked"
        return None, None
    if _OFFER_RE.search(document) and "iit" not in document.lower():
        return applicant.admission_offer, "admission_offer"
    if _JOINING_RE.search(document):
        return applicant.admission_certificate, "admission_certificate"
    if _DOMICILE_RE.search(document):
        # Domicile *match* is a rule field; absence of the certificate is
        # conveyed via document_issue. Do not treat domicile_matches_st as the scan.
        return None, None
    if _PHOTO_RE.search(document):
        return None, None
    return None, None


def _issue_pattern_for(document: str) -> re.Pattern[str]:
    if _is_income_document(document):
        return _INCOME_RE
    if _ST_RE.search(document) or "st/" in document.lower() or document.lower().startswith("st "):
        return re.compile(r"\bst certificate\b|\bst/pvtg\b", re.IGNORECASE)
    if _FEE_RE.search(document):
        return _FEE_RE
    if _BANK_RE.search(document):
        return re.compile(r"bank passbook|\bbank\b|aadhaar", re.IGNORECASE)
    if _OFFER_RE.search(document):
        return _OFFER_RE
    if _JOINING_RE.search(document):
        return _JOINING_RE
    if _DOMICILE_RE.search(document):
        return _DOMICILE_RE
    if _MARKSHEET_RE.search(document):
        return _MARKSHEET_RE
    if _PHOTO_RE.search(document):
        return _PHOTO_RE
    # Fallback: first distinctive token of the document title.
    token = re.split(r"[\s/]+", document.strip())[0]
    return re.compile(re.escape(token), re.IGNORECASE)
