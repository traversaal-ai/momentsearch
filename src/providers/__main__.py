"""Model doctor — `python -m src.providers`.

Answers the two questions a new cloner actually has: *which models am I about
to use?* and *why isn't my key being picked up?* Reads env only, so it is safe
to run anywhere.

    python -m src.providers            # resolved config + what's supported
    python -m src.providers --live     # also CALL each model (costs a few cents)
    python -m src.providers --json     # machine-readable (same as /api/providers)
"""
from __future__ import annotations

import json
import os
import sys
import time

from . import status
from .registry import (IMAGE_EMBED_PRESETS, LLM_PRESETS, TEXT_EMBED_PRESETS,
                       image_embed_preset, is_known_image_embed, is_known_llm,
                       is_known_text_embed, llm_preset, text_embed_preset)

OK, BAD, DASH = "OK ", "!! ", "-- "


def _line(k: str, v) -> None:
    print(f"  {k:<12} {v}")


def _key_hint(generic: str, key_envs: tuple[str, ...] | list[str], is_set: bool) -> str:
    """Name the env var the key actually came from — in the same precedence order
    config.py uses (generic first, then the provider's conventional names), or the
    hint is worse than useless when several are set."""
    if is_set:
        found = next((n for n in (generic, *key_envs) if os.getenv(n, "").strip()), None)
        return f"set (from {found})" if found else "set (from .env)"
    names = " / ".join(key_envs) if key_envs else generic
    return f"NOT set — export {names}"


def _typo_warnings() -> list[str]:
    """Unknown provider names resolve to a FALLBACK rather than failing, which
    is friendly for new providers but hides typos — so name them explicitly."""
    checks = (("LLM_PROVIDER", is_known_llm, "custom (generic OpenAI-compatible)"),
              ("IMAGE_EMBED_PROVIDER", is_known_image_embed, "clip"),
              ("TEXT_EMBED_PROVIDER", is_known_text_embed, "fastembed"))
    out = []
    for var, known, fallback in checks:
        raw = os.getenv(var, "").strip()
        if raw and not known(raw):
            out.append(f"{var}={raw!r} is not a known provider — falling back to "
                       f"{fallback}. Check the spelling against the list below.")
    return out


def _key_warnings() -> list[str]:
    """Catch the one genuinely baffling misconfiguration: a leftover generic key.

    The generic var (LLM_API_KEY) takes precedence over the provider-specific
    one, which is right for upgrades but means that switching LLM_PROVIDER to a
    new provider while an old LLM_API_KEY is still in .env sends the WRONG key
    and the provider answers "API key not valid" — with nothing pointing at the
    cause.
    """
    from .. import config

    out = []
    for generic, key_envs, provider in (
            ("LLM_API_KEY", llm_preset(config.LLM_PROVIDER).key_envs,
             config.LLM_PROVIDER),
            ("IMAGE_EMBED_API_KEY",
             image_embed_preset(config.IMAGE_EMBED_PROVIDER).key_envs,
             config.IMAGE_EMBED_PROVIDER),
            ("TEXT_EMBED_API_KEY",
             text_embed_preset(config.TEXT_EMBED_PROVIDER).key_envs,
             config.TEXT_EMBED_PROVIDER)):
        value = os.getenv(generic, "").strip()
        if not value:
            continue
        clash = [n for n in key_envs
                 if os.getenv(n, "").strip() and os.getenv(n, "").strip() != value]
        if clash:
            out.append(f"{generic} is set and takes precedence, but "
                       f"{' / '.join(clash)} is also set to something different. "
                       f"If {provider} rejects the key, {generic} probably belongs "
                       f"to a different provider — clear it, or set it to the "
                       f"{provider} key.")
    return out


def _llm_section() -> None:
    from . import llm

    print("\nANSWER MODEL (multimodal LLM — synthesis only; retrieval works without it)")
    cfg = llm.env_config()
    if cfg is None:
        print(f"  {DASH}none configured -> retrieval-only mode (citations, no "
              f"synthesized answer)")
        _line("fix", "set LLM_PROVIDER + that provider's API key, e.g. "
                     "LLM_PROVIDER=gemini GEMINI_API_KEY=...")
        return
    preset = llm_preset(cfg.provider)
    info = llm.describe(cfg)
    _line("provider", f"{cfg.provider}  ({preset.label})")
    _line("dialect", preset.kind)
    _line("model", cfg.model or "(unset)")
    _line("endpoint", cfg.base_url or "provider default")
    _line("api key", _key_hint("LLM_API_KEY", preset.key_envs, bool(cfg.api_key)))
    _line("sdk", f"{preset.sdk} "
                 f"{'installed' if status.sdk_installed(preset.sdk) else 'MISSING -> pip install ' + preset.sdk}")
    _line("status", f"{OK}ready" if info["ready"] else f"{BAD}{info['problem']}")


def _embed_section(title: str, branch: dict, preset, prefix: str) -> None:
    print(f"\n{title}")
    _line("provider", f"{branch['provider']}  ({branch['label']})")
    _line("model", branch["model"] or "(unset)")
    _line("dim", branch["dim"] or "unknown")
    _line("mode", {"service": "warm embedding service (EMBED_SERVICE_URL)",
                   "in-process": "in-process (weights load here)",
                   "api": "hosted API (called directly)"}[branch["mode"]])
    if not preset.local:
        _line("endpoint", branch["base_url"] or "provider default")
        _line("api key", _key_hint(f"{prefix}_API_KEY", preset.key_envs,
                                   branch["api_key_set"]))
    if preset.sdk:
        _line("sdk", f"{preset.sdk} "
                     f"{'installed' if status.sdk_installed(preset.sdk) else 'MISSING -> pip install ' + preset.sdk}")
    _line("gate", f"suggested confidence threshold {preset.threshold or 0.0}"
                  + ("" if preset.threshold else "  (uncalibrated -> gate off)"))
    _line("status", f"{OK}ready" if branch["ready"] else f"{BAD}{branch['problem']}")


def _catalog_section() -> None:
    print("\nSUPPORTED PROVIDERS  (* = optional SDK missing)")

    def names(table, kind: str) -> str:
        out = []
        for name, preset in table.items():
            mark = "" if status.sdk_installed(preset.sdk) else "*"
            out.append(name + mark)
        return ", ".join(out)

    _line("llm", names(LLM_PRESETS, "llm"))
    _line("visual", names(IMAGE_EMBED_PRESETS, "image"))
    _line("transcript", names(TEXT_EMBED_PRESETS, "text"))
    print("\n  Aliases are accepted too: grok=xai, claude=anthropic, "
          "google=gemini, azure=azure_openai.")
    print("  Any OpenAI-compatible server works without a preset: "
          "LLM_PROVIDER=custom + LLM_BASE_URL.")


def _live() -> None:
    """Actually call everything that's configured. Costs money on hosted APIs."""
    from . import embed, llm

    print("\nLIVE CHECKS")
    cfg = llm.env_config()
    if cfg is None:
        print(f"  {DASH}llm         skipped (not configured)")
    else:
        t = time.perf_counter()
        try:
            reply = llm.ping(cfg)
            print(f"  {OK}llm         {cfg.model} -> {reply[:60]!r} "
                  f"({time.perf_counter() - t:.1f}s)")
        except Exception as exc:
            print(f"  {BAD}llm         {type(exc).__name__}: {exc}")

    # A 32x32 JPEG is enough to prove the visual path end to end.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 220)).save(buf, format="JPEG")
    for label, fn in (("visual img", lambda: embed.embed_jpegs([buf.getvalue()])),
                      ("visual txt", lambda: embed.embed_text("a blue square")),
                      ("transcript", lambda: embed.embed_docs(["hello world"])),
                      ("txt query", lambda: embed.embed_query("hello"))):
        t = time.perf_counter()
        try:
            vecs = fn()
            shape = getattr(vecs, "shape", ())
            print(f"  {OK}{label:<11} shape {shape} ({time.perf_counter() - t:.1f}s)")
        except Exception as exc:
            print(f"  {BAD}{label:<11} {type(exc).__name__}: {exc}")


def main(argv: list[str]) -> int:
    if "--json" in argv:
        print(json.dumps(status.catalog(), indent=2, default=str))
        return 0

    from .. import config

    print("MomentSearch — model providers")
    print(f"  transcript branch {'ENABLED' if config.ENABLE_TRANSCRIPT else 'disabled'}"
          f" (ENABLE_TRANSCRIPT)")
    for w in _typo_warnings() + _key_warnings():
        print(f"  {BAD}{w}")
    _llm_section()

    # measure=True: the CLI may load a local model to pin down an unknown dim.
    branches = status.active(measure=True)["embeddings"]
    _embed_section("VISUAL EMBEDDINGS  (frames + question in one joint space)",
                   branches["image"], image_embed_preset(branches["image"]["provider"]),
                   "IMAGE_EMBED")
    _embed_section("TRANSCRIPT EMBEDDINGS  (caption chunks)",
                   branches["text"], text_embed_preset(branches["text"]["provider"]),
                   "TEXT_EMBED")
    _catalog_section()

    if "--live" in argv:
        _live()
    else:
        print("\n  (add --live to actually call each configured model)")

    ready = branches["image"]["ready"] and (
        branches["text"]["ready"] or not config.ENABLE_TRANSCRIPT)
    if not ready:
        print(f"\n{BAD}Retrieval is NOT ready — fix the embedding problems above.")
        return 1
    print(f"\n{OK}Retrieval is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
