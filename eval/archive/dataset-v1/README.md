# Evaluation dataset `insureassist-eval` v1.0.0

36 questions about the synthetic documents in [samples/](../../../samples/README.md):

| Category | Count | Tests |
|---|---|---|
| factual, definition | 10 | General look-up |
| numeric | 10 | Amounts, limits, periods |
| keyword | 2 | Exact identifiers (form numbers) |
| paraphrase | 5 | Question words differ from document words |
| conflicting | 2 | Summary vs amendment (both pages are relevant) |
| multi_page | 1 | Answer spread over two documents |
| unanswerable | 5 | Not in any document (used to tune "not enough information"; excluded from retrieval scores) |

Format (one JSON object per line in `questions.jsonl`):

```json
{"id": "q01", "category": "factual", "question": "...",
 "relevant": [{"doc": "auto-policy.pdf", "page": 3}],
 "key_facts": ["$500"]}
```

- `relevant` lists the **pages** that answer the question (doc = filename). Labels are page-level, so they stay valid when the chunk size changes.
- `key_facts` are short strings that must appear on a relevant page. The evaluator checks this automatically, which catches labelling mistakes. Later they're also used to check answers.

**Versioning:** changing a label bumps the minor version (1.1.0); adding or removing questions bumps the major version (2.0.0). Only compare scores between runs on the same major version.

Written by the project team (not independent), so scores will look better than on real, unseen documents.
