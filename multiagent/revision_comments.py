# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Render human revision comments for the next attempt's prompt."""
from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote as url_quote

from .contracts import RevisionComment
from .document_trust import Provenance, wrap_untrusted

MAX_REVISION_FEEDBACK = 50000


class RevisionFeedbackTooLong(ValueError):
    """The rendered prompt feedback exceeds its storage limit."""


def render_revision_feedback(feedback: str, comments: Sequence[RevisionComment], *,
                             step_id: str, attempt: int) -> str:
    """Keep the person's instructions literal, but envelope quoted model output."""
    if not comments:
        return feedback
    ref = f"step:{url_quote(step_id, safe='')}#{attempt}"
    parts = [feedback] if feedback else []
    lines = []
    for number, comment in enumerate(comments, 1):
        line = f"{number}. Part: {comment.target}; type: {comment.kind}; {comment.text}"
        if comment.quote is not None:
            line += "\nQuoted output:\n" + wrap_untrusted(
                comment.quote, Provenance(kind="model_output", ref=ref))
        lines.append(line)
    parts.append("Revision comments:\n" + "\n".join(lines))
    rendered = "\n\n".join(parts)
    if len(rendered) > MAX_REVISION_FEEDBACK:
        raise RevisionFeedbackTooLong(f"Revision feedback exceeds {MAX_REVISION_FEEDBACK} characters")
    return rendered
