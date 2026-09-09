"""Multi-part questions: decide whether to split, and into what.

"What figures does Cleo cite about friendship, and how does Mark respond?" is
two retrievals, not one: a single embedding of the whole sentence lands between
the two topics and finds neither well. One small LLM call — the same model and
key that write the answers — returns the self-contained sub-questions;
src/rag/search.py retrieves each in parallel and answers the ORIGINAL question
once from all of them.

Fail-safe by construction: any error, timeout or unparseable reply returns
[question] — the plain single-retrieval path — and is logged, never raised.
"""
from __future__ import annotations

import json
import re

from .. import config, llm

_SYSTEM = (
    "You prepare a user's question about video content for search.\n"
    "FIRST decide: could ONE short passage of a video (about 20 seconds) answer the "
    "whole question? That is the normal case, even for a question with two clauses "
    "(\"what does he add, and why\"; \"what appears on screen and what does he say it "
    "proves\"; \"what makes it famous and what surrounds it\"; \"the red and green "
    "cells\"): the clauses are about the SAME moment, so it is ONE search.\n"
    "Split ONLY when the parts need evidence from DIFFERENT places: different topics, "
    "different speakers, different videos, or moments far apart in time (a figure "
    "quoted early and a reply given later; two people's separate takes; two videos). "
    "Then each part must stand alone: replace pronouns with the names they refer to, "
    "keep the user's exact terms and wording, and add nothing the user didn't ask.\n"
    "Reply with ONLY this JSON, nothing else: "
    "{\"same_passage\": true|false, \"parts\": [\"...\"]}. When same_passage is true, "
    "parts holds the question unchanged as its only item."
)

_EXAMPLES = (
    "Examples:\n"
    "Q: What figures does Cleo cite about friendship decline, and how does Mark "
    "respond to them?\n"
    "{\"same_passage\": false, \"parts\": [\"What figures does Cleo cite about "
    "friendship decline?\", \"How does Mark respond to the friendship decline figures?\"]}\n"
    "Q: Compare how the stir-fry video and the sauces video use high heat\n"
    "{\"same_passage\": false, \"parts\": [\"How does the stir-fry video use high heat?\", "
    "\"How does the sauces video use high heat?\"]}\n"
    "Q: What do Ana Ruiz and Ben Cole each call the checking step they add after the "
    "model acts, and what does that step do?\n"
    "{\"same_passage\": false, \"parts\": [\"What does Ana Ruiz call the checking step she "
    "adds after the model acts, and what does it do?\", \"What does Ben Cole call the "
    "checking step he adds after the model acts, and what does it do?\"]}\n"
    "Q: What is a Lagrange point and why does it matter?\n"
    "{\"same_passage\": true, \"parts\": [\"What is a Lagrange point and why does it matter?\"]}\n"
    "Q: When he prints both emp_1 and emp_2, what appears on screen and what does he "
    "say it proves?\n"
    "{\"same_passage\": true, \"parts\": [\"When he prints both emp_1 and emp_2, what "
    "appears on screen and what does he say it proves?\"]}\n"
    "Q: How does the goal come about, and how do the commentators judge the decision?\n"
    "{\"same_passage\": true, \"parts\": [\"How does the goal come about, and how do the "
    "commentators judge the decision?\"]}"
)

_JSON = re.compile(r"\{.*\}", re.S)

# A question with none of these can't be asking two things, so it skips the LLM
# call entirely — "Show me a diagram", "What is this video about?" pay nothing.
# Deliberately loose (a comma or an "and" is enough to ask the model); its job is
# to skip the obvious singles, never to decide a split itself.
_MULTI_SIGNAL = re.compile(
    r"\b(and|or|both|each|versus|vs|compare\w*|differen\w*|respectively)\b|[,;]|\?.*\S.*\?",
    re.I | re.S)


def _maybe_multi(question: str) -> bool:
    return bool(_MULTI_SIGNAL.search(question))


def _parse(raw: str, question: str, max_parts: int) -> list[str]:
    """The model's reply -> parts. Anything short of 2 clean, distinct parts
    means "don't split"."""
    m = _JSON.search(raw or "")
    if not m:
        return [question]
    data = json.loads(m.group(0))
    if not isinstance(data, dict) or data.get("same_passage") is True:
        return [question]
    parts = data.get("parts")
    if not isinstance(parts, list):
        return [question]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not isinstance(p, str):
            continue
        p = " ".join(p.split())
        key = p.lower().rstrip("?. ")
        # Empty, duplicate, or a "part" far longer than the whole question (the
        # model padded rather than split) all fail closed.
        if not key or key in seen or len(p) > 2 * len(question) + 40:
            continue
        seen.add(key)
        out.append(p)
    out = out[:max_parts]
    return out if len(out) >= 2 else [question]


def split(question: str, cfg: llm.LLMConfig | None) -> list[str]:
    """The question's parts — `[question]` itself when it is one thing (or when
    splitting is off, no model is configured, or anything goes wrong)."""
    max_parts = config.MULTI_QUERY_MAX_PARTS
    if not config.MULTI_QUERY or cfg is None or max_parts < 2 or not _maybe_multi(question):
        return [question]
    try:
        user = f"{_EXAMPLES}\n\nAt most {max_parts} parts.\nQ: {question}"
        raw = llm.complete(cfg, _SYSTEM, user, max_tokens=1500)
        parts = _parse(raw, question, max_parts)
    except Exception as exc:                 # never let the helper sink an answer
        print(f"[split] skipped ({type(exc).__name__}: {exc})")
        return [question]
    if len(parts) > 1:
        print(f"[split] {len(parts)} parts: " + " | ".join(parts))
    return parts
