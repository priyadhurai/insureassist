# Sample documents (synthetic)

Every document here is **fictional**, written for this project. "Example Mutual Insurance Co." is not a real company. The texts contain no real policy wording, customers or personal data. Do not rely on them as insurance information.

| Source (edit these) | Generated PDF | Pages | Content |
|---|---|---|---|
| `sources/auto-policy.md` | `pdfs/auto-policy.pdf` | 4 | **Motor policy**: liability limits, deductibles, rental car, exclusions (racing, rideshare Form AP-RS-12), duties after an accident |
| `sources/auto-policy-amendment.md` | `pdfs/auto-policy-amendment.pdf` | 1 | **Motor amendment** (from 1 Jan 2027): collision deductible $500 → $750, rental $40 → $50/day; liability, comprehensive and glass unchanged. **Deliberately contradicts** the policy, to test conflict handling |
| `sources/claims-procedure-guide.md` | `pdfs/claims-procedure-guide.pdf` | 2 | **Claims**: how to file (5 steps), timelines, appeals, fraud warning |

On 2026-10-19 the set was reduced from 6 to 2 documents (homeowners, dental summary and amendment, and travel were removed), and the motor amendment was added to keep a real conflict case. The matching question set is [eval/datasets/insureassist-eval-v2](../eval/datasets/insureassist-eval-v2/README.md).

Regenerate the PDFs after editing a source:

```powershell
uv run python scripts/generate_sample_pdfs.py                       # all
uv run python scripts/generate_sample_pdfs.py auto-policy-amendment # only one
```

Regenerating changes a PDF's bytes and therefore its document ID: regenerate only what you edited, then re-ingest.

Source format: Markdown with `#`/`##`/`###` headings, paragraphs, `-` bullet lists, `|` tables, and `<!-- pagebreak -->` to start a new PDF page. Page breaks are explicit, so evaluation questions can name exact page numbers.
