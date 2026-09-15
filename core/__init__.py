"""MoTA AI-Driven Scholarship & Fellowship Verification System — core package."""

from core.config import MoTAConfig, load_document_requirements, load_scheme_rules
from core.deficiency_generator import DeficiencyNoticeGenerator
from core.document_auditor import DocumentAuditor, DocumentRequirementMatrix
from core.evaluator import EvaluationSummary, ScholarshipEvaluator
from core.models import (
    ApplicantProfile,
    CompositeDecision,
    CompositeEvaluationResult,
    DeficiencyNotice,
    DocVerificationStatus,
    DocumentAuditItem,
    DocumentAuditResult,
    DocumentRequirement,
    RuleCheck,
    SchemeRule,
    ValidationResult,
)
from core.rule_engine import ScholarshipRuleEngine

__all__ = [
    "ApplicantProfile",
    "CompositeDecision",
    "CompositeEvaluationResult",
    "DeficiencyNotice",
    "DeficiencyNoticeGenerator",
    "DocVerificationStatus",
    "DocumentAuditItem",
    "DocumentAuditResult",
    "DocumentAuditor",
    "DocumentRequirement",
    "DocumentRequirementMatrix",
    "EvaluationSummary",
    "MoTAConfig",
    "RuleCheck",
    "SchemeRule",
    "ScholarshipEvaluator",
    "ScholarshipRuleEngine",
    "ValidationResult",
    "load_document_requirements",
    "load_scheme_rules",
]
