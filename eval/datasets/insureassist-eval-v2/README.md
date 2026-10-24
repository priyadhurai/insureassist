# Evaluation dataset `insureassist-eval` v2.0.0

28 questions about the three synthetic documents in [samples/](../../../samples/README.md): the **motor policy** (`auto-policy.pdf`), its **amendment** (`auto-policy-amendment.pdf`) and the **claims guide** (`claims-procedure-guide.pdf`).

| Category | Count | Tests |
|---|---|---|
| numeric | 6 | Amounts, limits, periods |
| factual, definition | 10 | General look-up |
| keyword | 2 | Exact identifiers (form number, phone number) |
| paraphrase | 3 | Question words differ from document words |
| conflicting | 2 | Policy vs amendment: collision deductible $500 vs $750, rental $40 vs $50 per day (both pages relevant) |
| multi_doc | 1 | Answer needs both documents |
| unanswerable | 5 | Not in either document (excluded from retrieval scores) |

Format and rules as in v1: `relevant` = pages that answer the question, and `key_facts` = strings that must appear on a relevant page. The evaluator checks this automatically.

**Why v2 (major version):** on 2026-10-19 the sample set was reduced from 6 to 2 documents at the owner's request. v1 (36 questions, 6 documents) and its reports are kept in [eval/archive/](../../archive/). **Scores from v1 and v2 must not be compared.**

The amendment was added the same day, before any v2 answer evaluation was run, so it is part of v2.0.0. q01 and q06 became `conflicting` questions, and q23 was added.
