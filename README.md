# MoTA AI-Driven Scholarship & Fellowship Verification System

**SIH Problem Statement: SIH26239**

Production-oriented reference implementation for Ministry of Tribal Affairs scholarship and fellowship verification. The system combines CSV-driven policy rules, typed applicant records, document completeness auditing, composite workflow routing, an operational Streamlit portal, and exportable PDF deficiency notices.

## Executive Summary

Manual scholarship verification is slow, difficult to audit, and vulnerable to inconsistent interpretation of scheme guidelines. This project provides a repeatable verification workflow for five MoTA schemes:

- National Overseas Scholarship (NOS) for ST Students
- National Fellowship Scheme
- National Scholarship Scheme (Higher Education)
- Pre-Matric Scholarship for ST Students
- Post Matric Scholarship for ST Students

The engine separates **rule eligibility** from **document completeness**. A rule-eligible applicant with missing documents is routed to `PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS`, while a fully verified eligible applicant is routed to `READY_FOR_DISBURSAL`. A failed hard eligibility rule routes the application to `REJECTED`.

## Key Features

- CSV-driven scheme rules and document requirement matrix
- Pydantic domain models for validated applicant, rule, audit, and notice data
- Scheme-aware rule engine with hard and soft rule severity
- Document audit with mandatory, conditional, waived, and not-applicable states
- Composite evaluator for disbursal workflow routing
- 15-day formal deficiency notice generation
- Streamlit operations portal with executive analytics, applicant audit, and live screening sandbox
- Text, JSON, and PDF notice downloads
- ReportLab PDF notices with official header styling, missing-document tables, action plan, watermark, and footer
- CLI batch evaluation to `composite_eval_results.json`

## Architecture

```text
CSV policy and applicant data
         |
         v
     core/config.py -------- typed Pydantic models
         |
         v
     core/rule_engine.py ---- atomic eligibility checks
         |
         +-------------- core/document_auditor.py
         |                         |
         v                         v
    core/evaluator.py ---- composite workflow decision
         |
         +---- core/deficiency_generator.py ---- structured notice
         |
         +---- core/pdf_generator.py ------------ PDF notice bytes
         |
         v
    app.py Streamlit operations portal
```

### Decision flow

1. Load and validate an applicant profile.
2. Evaluate scheme-specific hard and soft rules.
3. Audit mandatory and conditional documents.
4. Compose the workflow status:
   - rule eligible + complete documents: `READY_FOR_DISBURSAL`
   - rule eligible + incomplete documents: `PROVISIONAL_ELIGIBLE_DEFICIENT_DOCS`
   - rule ineligible: `REJECTED`
5. Generate a structured 15-day notice and PDF when document cure is required.

## Technology Stack

- Python 3.10+
- Pydantic 2
- Pandas
- Streamlit
- Plotly
- ReportLab
- Pytest
- CSV and PDF policy artefacts supplied with SIH26239

## Project Layout

```text
app.py                         Streamlit operations portal
evaluate_dataset.py            Batch evaluation CLI
requirements.txt               Runtime and test dependencies
composite_eval_results.json    Generated composite evaluation output
MoTA_*.csv                     Rules, document matrix, and applicant dataset
core/config.py                 CSV loading and project-root resolution
core/models.py                 Typed domain contracts
core/rule_engine.py            Scheme eligibility evaluation
core/document_auditor.py       Document checklist verification
core/deficiency_generator.py   Structured 15-day notice generation
core/pdf_generator.py          ReportLab PDF notice generation
core/evaluator.py              Composite routing and batch summaries
tests/                         Automated regression tests
```

## Installation

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the Portal

```bash
streamlit run app.py
```

The portal loads the root CSV files, evaluates the applicant dataset, and exposes three operating modes: Executive Analytics, Applicant Verification Audit, and Live Screening Sandbox.

## Run Batch Evaluation

```bash
python evaluate_dataset.py
```

Useful options:

```bash
python evaluate_dataset.py --quiet
python evaluate_dataset.py --also-eval-results
python evaluate_dataset.py --output output/composite_eval_results.json
```

## Use the Backend Directly

```python
from core.config import load_applicants
from core.evaluator import ScholarshipEvaluator
from core.pdf_generator import DeficiencyPDFReport

applicant = load_applicants()[0]
evaluator = ScholarshipEvaluator()
result = evaluator.evaluate_one(applicant)

if result.deficiency_notice is not None:
    pdf_bytes = DeficiencyPDFReport().render(applicant, result)
    with open("deficiency_notice.pdf", "wb") as handle:
     handle.write(pdf_bytes)
```

## Benchmark Results

The Phase 1 through Phase 5 regression suite evaluates **120 synthetic applicant records** across the five schemes.

| Metric | Result |
|---|---:|
| Applicants processed | 120 |
| Rule accuracy against labelled CSV data | 100.0% |
| Rule-eligible applicants | 58 |
| Ready for disbursal | 54 |
| Provisional due to deficient documents | 4 |
| Rejected | 62 |
| Automated regression tests | 29 passing |

The benchmark measures deterministic rule-engine agreement with the dataset labels. It is not a substitute for official policy review, applicant identity verification, or human sign-off by the competent authority.

## Testing

```bash
pytest -q
```

The test suite covers scheme rules, boundary conditions, orphan income waivers, document tiers, deficiency notices, composite routing, and dataset-level accuracy.

## Operational Notes

- Keep all CSV and policy PDF artefacts in the repository root unless using the CLI `--root` option.
- Never treat a PDF or generated notice as an approval instrument without competent-authority review.
- Do not place Aadhaar numbers, bank details, or other sensitive personal data in source control or logs.
- The evaluator raises on an unsupported scheme so data quality problems are visible; the Streamlit portal catches these failures and displays a recoverable error state.
- The PDF generator returns in-memory bytes, making it suitable for Streamlit downloads, API responses, or controlled filesystem export.
