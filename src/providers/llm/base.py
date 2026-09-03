"""Shared pieces of every LLM adapter: the config object, the prompt, and the
one image transform that decides what a call costs.

The adapters below this module (openai_compat / anthropic / gemini) differ only
in wire format. Everything that affects ANSWER QUALITY lives here, so switching
provider changes cost and latency — never the instructions, the moment
numbering, or the image size.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, replace

from ... import config
from ..registry import llm_preset, llm_provider_key

# Kept for backwards compatibility — src/llm.py re-exported this constant, and
# it is now just the nvidia preset's base_url.
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

SYSTEM = (
    "You answer a user's question about a video using the numbered moments "
    "provided as your evidence. Each moment has a timestamp and may include a "
    "video FRAME (what was shown on screen) and/or a TRANSCRIPT excerpt (what was "
    "said out loud). Use BOTH kinds of evidence: for a question about what someone "
    "SAID or talked about, read the transcript text; for a question about what is "
    "SHOWN, read the frame. A moment may also carry CONTEXT — what was said just "
    "before/after the matched excerpt, or while a frame was on screen. Treat context "
    "as part of that moment: use it, and cite the same [n].\n"
    "Rules:\n"
    "1. Read the question carefully and answer exactly what is asked. Start with a "
    "one-line direct answer, then explain in short paragraphs — ONE paragraph per "
    "distinct point. Keep it focused, don't pad. No preamble, don't restate the "
    "question.\n"
    "2. Cite a moment [n] ONLY when that moment's frame or transcript actually "
    "supports the specific sentence. NEVER attach a citation to a moment that "
    "doesn't contain what you're claiming — a wrong citation is worse than none. A "
    "general statement of what the video is broadly about (e.g. from its title, or "
    "the overall gist of the frames) may stay UNCITED; that's fine. When you cite, "
    "use square brackets, e.g. [1] or [2, 3]; when the question is about what was "
    "said, quote the transcript accurately — keep the wording and numbers.\n"
    "3. Group the relevant moments by the point they make:\n"
    "   - Moments that make the SAME point (especially several from the same "
    "video) belong TOGETHER in ONE paragraph, cited together, e.g. [1, 2]. Do not "
    "split one shared point across separate paragraphs.\n"
    "   - Moments that make DIFFERENT points, or come from different videos, go in "
    "SEPARATE paragraphs, each with its own citation.\n"
    "   Cover every distinct relevant point — don't merge unrelated ones and don't "
    "drop any.\n"
    "4. You MAY add a light framing from the video's title/obvious context, but the "
    "ANSWER ITSELF must come from the moments AND address the question. Never turn "
    "\"what the video is broadly about\" into the answer when the moments don't "
    "actually address what was asked (see rule 5). Do NOT invent SPECIFIC facts, "
    "quotes, numbers, opinions or claims that aren't in the moments — if a specific "
    "detail isn't shown or said in a moment, don't state it, and don't cite one.\n"
    "5. STAY TRUE TO THE QUESTION. First judge whether the moments actually ADDRESS "
    "WHAT THE USER ASKED — not merely whether they come from the video. If none of "
    "the moments address the question, reply with ONE sentence that you couldn't "
    "find it in the video(s) and STOP — do NOT substitute a general summary of the "
    "video instead. Answer only when at least one moment genuinely speaks to the "
    "question; a partial but on-topic match is still worth answering. (If the "
    "'question' is not a real question — e.g. a bare URL or gibberish — say you "
    "couldn't find an answer rather than summarizing.)\n"
    "6. SPEAKER ATTRIBUTION — only for moments tagged 'speaker: <name>'; attribute "
    "that moment's point to that exact person by name. NEVER invent a name or write "
    "a placeholder like \"Unnamed Speaker\"; don't attribute untagged moments to "
    "anyone. If (and only if) the instructions below the question ask for a "
    "\"Who said what\" table, add it as the very last thing in your answer.\n"
    "7. ANSWER COMPLETELY, AND USE THE FACTS THAT ARE THERE. Address EVERY part of "
    "the question — a multi-part question needs each part answered, each with its "
    "own citation. And do not leave specifics on the table: when a moment's "
    "transcript or frame holds an exact detail the question asks for — a number, "
    "name, definition, step, quote or example — state it explicitly and cite it. "
    "A vague or half answer while the precise fact is sitting right there in a "
    "moment is a failure, even if what you did say is correct."
)


@dataclass
class LLMConfig:
    """A fully-resolved model handle. `provider` is a registry key; everything
    else is either explicit or filled from that provider's preset."""
    provider: str = "openai"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 1024

    @property
    def kind(self) -> str:
        """Which wire dialect to speak: openai | azure | anthropic | gemini."""
        return llm_preset(self.provider).kind

    @property
    def label(self) -> str:
        return llm_preset(self.provider).label


def resolve(cfg: LLMConfig) -> LLMConfig:
    """Fill the blanks from the provider's preset.

    Explicit values always win — base_url in particular, so `provider=openai`
    plus your own base_url still reaches your own server. This is what makes a
    provider name enough on its own: `LLM_PROVIDER=xai` + `XAI_API_KEY` needs
    no endpoint and no model id.
    """
    preset = llm_preset(cfg.provider)
    return replace(
        cfg,
        provider=llm_provider_key(cfg.provider),
        model=cfg.model or preset.default_model,
        base_url=cfg.base_url or preset.base_url,
    )


def is_self_hosted(cfg: LLMConfig) -> bool:
    """True when base_url points somewhere the USER chose.

    A key-less endpoint is only plausible for a server they run, so this is what
    lets an API key be optional. It must not be satisfied by the preset's own
    base_url: every hosted provider ships one, and treating that as "endpoint
    supplied" would turn a missing key into a 401 mid-answer instead of a
    message naming the env var to set.
    """
    return bool(cfg.base_url) and cfg.base_url != llm_preset(cfg.provider).base_url


def missing_requirement(cfg: LLMConfig) -> str | None:
    """Why this config can't work yet, in words a user can act on (None = fine).
    Checked before the call so the failure names the fix, not a 401."""
    preset = llm_preset(cfg.provider)
    if not cfg.model:
        return (f"{preset.label}: no model set. Set LLM_MODEL"
                + (f" — {preset.notes}" if preset.notes else "."))
    if preset.requires_key and not cfg.api_key and not is_self_hosted(cfg):
        envs = " or ".join(preset.key_envs) or "LLM_API_KEY"
        return f"{preset.label}: no API key. Set {envs} (or LLM_API_KEY)."
    if preset.kind == "azure" and not (cfg.base_url or os.getenv("AZURE_OPENAI_ENDPOINT")):
        return ("Azure OpenAI: no endpoint. Set AZURE_OPENAI_ENDPOINT (or "
                "LLM_BASE_URL) to https://<resource>.openai.azure.com.")
    if cfg.provider == "custom" and not cfg.base_url:
        return ("Custom provider: no endpoint. Set LLM_BASE_URL to your "
                "OpenAI-compatible server, or name a known provider "
                "(see /api/providers).")
    return None


# ── Prompt assembly (identical across providers) ──────────────────────────────

_ANON_SPEAKER = re.compile(r"^\s*speaker\s*\d+\s*$", re.I)   # "Speaker 1", "Speaker 2"


def named_speakers(moments: list[dict]) -> list[str]:
    """Distinct REAL speaker names across the moments (anonymous "Speaker N"
    placeholders and blanks excluded), in first-seen order."""
    out: list[str] = []
    for m in moments:
        s = (m.get("speaker") or "").strip()
        if s and not _ANON_SPEAKER.match(s) and s not in out:
            out.append(s)
    return out


def intro(question: str, moments: list[dict], parts: list[str] | None = None,
          missing: list[int] | None = None) -> str:
    """The user turn's preamble. `parts`/`missing` come from the multi-part path
    (src/rag/query_split.py): the sub-questions the moments were retrieved for,
    and the indexes of any part that found nothing worth showing."""
    n = len(moments)
    text = (
        f"QUESTION: {question}\n\n"
        f"Answer this question using the {n} moments below (numbered 1 to {n}). "
        "Each has a timestamp and a video frame and/or a transcript excerpt. If "
        "the question is about what was said, use the transcript text. Give a "
        "direct answer grounded in the relevant moment(s), cited as [n]. FIRST check "
        "the moments actually ADDRESS the question — if none do, reply with ONE "
        "sentence that you couldn't find it and STOP; do NOT summarize the video "
        "instead."
    )
    if parts and len(parts) > 1:
        listed = "\n".join(f"  {i}) {p}" for i, p in enumerate(parts, 1))
        text += (
            f"\n\nThe question has {len(parts)} parts:\n{listed}\n"
            "Each moment below says which part(s) it was retrieved for. Answer EVERY "
            "part, in order, one short paragraph each with its own citations; a "
            "moment retrieved for one part may still support another."
        )
        gaps = "; ".join(f"{i + 1}) {parts[i]}" for i in (missing or []) if i < len(parts))
        if gaps:
            text += (
                f"\nNo matching moments were found for: {gaps}. For each of those, "
                "write ONE sentence saying the videos don't cover it — do not guess."
            )
    # The "Who said what" table is decided HERE (deterministically), not left to
    # the model: add the directive only when 2+ real names actually appear.
    names = named_speakers(moments)
    if len(names) >= 2:
        text += (
            f"\n\nThese moments feature multiple speakers ({', '.join(names)}). "
            "IF you actually answer (the moments address the question), end your "
            "answer with a markdown table titled exactly `### Who said what`, header "
            "row EXACTLY `| Speaker | Their point | Source |` then a "
            "`| --- | --- | --- |` row, then ONE row per speaker WHO HAS A REAL POINT "
            "in the moments: their point in a single line in their own voice, "
            "Source = the moment number(s) like [1] or [2, 3]. Every row starts and "
            "ends with a pipe `|`. Skip any speaker with no relevant point — never "
            "write filler like \"no specific point\". If you couldn't find an answer, "
            "OMIT the table entirely."
        )
    return text


def label(i: int, m: dict) -> str:
    line = f"[{i}]"
    if m.get("parts"):     # multi-part question: which sub-question found it
        line += " (for part " + "/".join(str(p) for p in m["parts"]) + ")"
    line += f" @ {m.get('timestamp', '')}"
    if m.get("speaker"):
        line += f' speaker: {m["speaker"]}'
    if m.get("transcript"):
        line += f' transcript: "{m["transcript"]}"'
    if m.get("image") is None:
        line += " (transcript only, no frame)"
    # CONTEXT_PAD_S: the speech around the moment — indented under [i] so it reads
    # as part of this moment, not as a new one.
    for where, text in m.get("context") or []:
        line += f'\n    {where}: "{text}"'
    return line


def _shrink(jpeg: bytes, max_px: int) -> bytes:
    """Resize a JPEG so its longest side is at most `max_px`. Already-smaller
    images are returned untouched, which is what makes this safe to apply twice."""
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg))
    if max(img.size) <= max_px:
        return jpeg
    img.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def downscale(jpeg: bytes) -> bytes:
    """Shrink a frame before it becomes LLM image tokens.

    The single biggest cost lever in the whole read path: TOP_K frames at full
    thumbnail size can be several times the tokens of the same frames at
    LLM_IMAGE_MAX_PX, for no measurable answer-quality gain.
    """
    return _shrink(jpeg, config.LLM_IMAGE_MAX_PX)


# ── Local-runtime context budget ──────────────────────────────────────────────
# Ollama, LM Studio and vLLM serve whatever context the model was BUILT with, and
# Ollama's default is 4096 tokens. One 512px frame is ~1000 tokens, so a normal
# six-moment request (~5.4k) is rejected outright — the first real question a
# local user asks returns a 500, not a slow answer. See config.LOCAL_LLM_*.
_LOCAL_RUNTIMES = ("ollama", "lmstudio", "vllm")


def is_local_runtime(cfg: LLMConfig) -> bool:
    """True for the key-less local servers whose default context is small.

    Deliberately NOT is_self_hosted(): `custom` with a base_url may well be a
    large hosted deployment behind a proxy, and silently halving its evidence
    would be a worse surprise than the error this avoids.
    """
    return llm_provider_key(cfg.provider) in _LOCAL_RUNTIMES


def fit_local_context(cfg: LLMConfig,
                      moments: list[dict]) -> tuple[list[dict], str | None]:
    """Trim a request to fit a small local context. Returns (moments, note).

    Order matters: moments arrive best-first, so keeping a prefix keeps the
    STRONGEST evidence and the [n] numbering still lines up with the citation
    list the UI renders.

    The note is not optional politeness — the answer is being built from less
    evidence than was retrieved, and a user who isn't told that has no way to
    know why a local answer is thinner than a hosted one.
    """
    if not config.LOCAL_LLM_TRIM or not is_local_runtime(cfg) or not moments:
        return moments, None

    keep = max(1, config.LOCAL_LLM_MAX_MOMENTS)
    kept, dropped = moments[:keep], max(0, len(moments) - keep)
    max_px = config.LOCAL_LLM_IMAGE_MAX_PX
    chars = config.LOCAL_LLM_TRANSCRIPT_CHARS

    out = []
    for m in kept:
        m = dict(m)
        if m.get("image") and max_px > 0:
            try:
                m["image"] = _shrink(m["image"], max_px)
            except Exception:      # a corrupt frame must not sink the answer
                pass
        text = m.get("transcript")
        if text and chars > 0 and len(text) > chars:
            m["transcript"] = text[:chars].rstrip() + "…"
        if chars > 0:
            m.pop("context", None)   # the surrounding speech is the first thing to go
        out.append(m)

    # Nothing actually changed (few moments, already-small frames) -> no note.
    if not dropped and max_px >= config.LLM_IMAGE_MAX_PX:
        return out, None

    # Written for whoever is reading the answer, not for whoever configured the
    # server: what happened first, why second, the fix last.
    if dropped:
        did = f"used the best {len(out)} of {len(moments)} moments"
        if max_px < config.LLM_IMAGE_MAX_PX:
            did += ", with smaller frames"
    else:
        did = "used smaller frames"
    return out, (
        f"This answer {did}. A local model can only read so much at once, so "
        f"MomentSearch sends {llm_preset(cfg.provider).label} less than it sends "
        "a hosted model. See MODELS.md to send everything."
    )
