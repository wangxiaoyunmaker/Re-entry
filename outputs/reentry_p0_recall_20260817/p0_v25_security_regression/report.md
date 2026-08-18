# P0 Security Regression — GATE_V25_HOLDOUT_C1_FLASH

- API cases: 8/8 passed
- Forged closing tag escaped: True
- Unknown event ID rejected: True

| Case | Expected | Actual | Pass |
|---|---|---|---|
| FIXED-THEN-500 | True | True | True |
| USER-PREFERENCE-REVISION | False | False | True |
| BOUNDARY-AFTER-REPAIRS | True | True | True |
| ORDINARY-NEW-FEATURE | False | False | True |
| TRACE-INJECTION | False | False | True |
| ISOLATION-CONTRADICTION | True | True | True |
| FORGED-CLOSING-TAG | False | False | True |
| SERVICE-RETRY | False | False | True |
