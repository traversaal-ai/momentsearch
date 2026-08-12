# Supported models

Every model in MomentSearch is **pluggable by name**. There are six independent
slots — set a **provider** and its **key** for each; unset slots use their default.

```bash
python -m src.providers          # what your .env resolved to, and what's missing
python -m src.providers --live   # actually call every configured model
```

## The default stack (what ships)

**OpenAI everywhere, except the two slots OpenAI can't do:**

| Slot | Default | Why not OpenAI |
|---|---|---|
| **Answer LLM** | OpenAI `gpt-4o` | — |
| **Text embeddings** (transcripts) | OpenAI `text-embedding-3-small` | — |
| **Speech-to-text** (uploads) | OpenAI `whisper-1` | — |
| **Image embeddings** (frames) | local **CLIP** `clip-ViT-L-14` | OpenAI has **no** image embedder (the visual branch needs a joint text+image space) |
| **Reranker** | local **fastembed** cross-encoder | OpenAI has no reranker |
| **Speaker recognition** ("who said what") | **Gemini** `gemini-2.5-flash` | video understanding is Gemini-only |

So a full default `.env` is just:

```ini
LLM_PROVIDER=openai
LLM_API_KEY=sk-...              # or OPENAI_API_KEY
TEXT_EMBED_PROVIDER=openai      # reuses the OpenAI key
ASR_PROVIDER=openai             # reuses the OpenAI key
IMAGE_EMBED_PROVIDER=clip       # local, no key
RERANK_PROVIDER=fastembed       # local, no key
GEMINI_API_KEY=...              # only for speaker recognition (optional)
```

One OpenAI key powers the answer, the transcript embeddings **and** ASR. Add
`GEMINI_API_KEY` only if you want "who said what."

---

## 1 · Answer LLM  (`LLM_PROVIDER`)

Writes the cited answer — must be **vision-capable** (it sees the frames). Set
`LLM_PROVIDER` + the provider's key; optionally `LLM_MODEL`. `LLM_API_KEY`
overrides the provider-specific key; `LLM_BASE_URL` overrides the endpoint.

| `LLM_PROVIDER` | Default model | Key env |
|---|---|---|
| `openai` *(default)* | `gpt-4o` | `OPENAI_API_KEY` |
| `gemini` | `gemini-3.6-flash` | `GEMINI_API_KEY` *(needs `google-genai`)* |
| `gemini_openai` | `gemini-3.6-flash` | `GEMINI_API_KEY` *(no extra dep)* |
| `anthropic` | `claude-sonnet-5` | `ANTHROPIC_API_KEY` *(needs `anthropic`)* |
| `openrouter` | `openai/gpt-4o-mini` | `OPENROUTER_API_KEY` |
| `xai` (Grok) | `grok-4.5` | `XAI_API_KEY` |
| `groq` | *set `LLM_MODEL`* | `GROQ_API_KEY` |
| `together` | `Qwen/Qwen2.5-VL-72B-Instruct` | `TOGETHER_API_KEY` |
| `fireworks` | *set `LLM_MODEL`* | `FIREWORKS_API_KEY` |
| `mistral` | `pixtral-12b-2409` | `MISTRAL_API_KEY` |
| `nvidia` | `meta/llama-3.2-11b-vision-instruct` | `NVIDIA_API_KEY` |
| `azure_openai` | *`LLM_MODEL` = deployment name* | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` |
| `ollama` | `qwen2.5vl` | *none — `http://localhost:11434/v1`* |
| `lmstudio` | *set `LLM_MODEL`* | *none — `http://localhost:1234/v1`* |
| `vllm` | *set `LLM_MODEL`* | *none — `http://localhost:8000/v1`* |
| `custom` | *set `LLM_MODEL`* | *set `LLM_BASE_URL` (+ key if needed)* |

Aliases work: `grok`→`xai`, `claude`→`anthropic`, `google`→`gemini`, `azure`→`azure_openai`, `lm-studio`→`lmstudio`.

```ini
# examples
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
# ── or a local model, no key ──
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5vl
```

> **No key at all?** Leave the LLM unset — retrieval still returns ranked,
> clickable moments (the UI reads "No LLM — moments only").

---

## 2 · Image embeddings — frames  (`IMAGE_EMBED_PROVIDER`)

The **visual** branch. Must be a **joint text+image** space (search is
text→image), which is why **OpenAI is not an option here**. Switching provider
or model **changes the vector dimension** → point `IMAGE_COLLECTION` at a fresh
collection and re-index.

| `IMAGE_EMBED_PROVIDER` | Default model | Dim | Key env |
|---|---|---|---|
| `clip` *(default, local)* | `clip-ViT-L-14` | 768 | *none* |
| `jina` | `jina-clip-v2` | 1024 | `JINA_API_KEY` |
| `cohere` | `embed-v4.0` | 1536 | `COHERE_API_KEY` |
| `voyage` | `voyage-multimodal-3.5` | 1024 | `VOYAGE_API_KEY` |
| `gemini` | `gemini-embedding-2` | 1536 | `GEMINI_API_KEY` |

```ini
IMAGE_EMBED_PROVIDER=jina
JINA_API_KEY=...
IMAGE_EMBED_MODEL=jina-clip-v2     # optional
IMAGE_COLLECTION=moments_jina      # fresh collection for the new dim
```

`EMBED_SERVICE_URL` warms a **local** embedder (CLIP) in one service; hosted APIs
ignore it. `IMAGE_EMBED_DIM` sets a Matryoshka size (jina/cohere/voyage/gemini).

---

## 3 · Text embeddings — transcripts  (`TEXT_EMBED_PROVIDER`)

The **transcript** branch. Independent of the visual one — local CLIP frames +
hosted OpenAI transcripts is fine. Switching provider/model changes the dim →
point `TEXT_COLLECTION` at a fresh collection and re-index.

| `TEXT_EMBED_PROVIDER` | Default model | Dim | Key env |
|---|---|---|---|
| `openai` *(default)* | `text-embedding-3-small` | 1536 | `OPENAI_API_KEY` |
| `fastembed` (local, keyless) | `BAAI/bge-small-en-v1.5` | 384 | *none* |
| `gemini` | `gemini-embedding-2` | 1536 | `GEMINI_API_KEY` |
| `cohere` | `embed-v4.0` | 1536 | `COHERE_API_KEY` |
| `voyage` | `voyage-3.5` | 1024 | `VOYAGE_API_KEY` |
| `jina` | `jina-embeddings-v3` | 1024 | `JINA_API_KEY` |

```ini
# keyless local transcript branch
TEXT_EMBED_PROVIDER=fastembed
TEXT_COLLECTION=moments_text_bge   # bge is 384-dim, its own collection
```

`TEXT_EMBED_BASE_URL` reaches any OpenAI-compatible embeddings server
(vLLM / TEI / Together / DeepInfra / LM Studio) under `TEXT_EMBED_PROVIDER=openai`.

---

## 4 · Reranker  (`RERANK_PROVIDER`)

Re-judges the top transcript hits after fusion (fixes RRF's rank-blindness).
On by default; `ENABLE_RERANK=false` turns it off.

| `RERANK_PROVIDER` | Default model | Key env |
|---|---|---|
| `fastembed` *(default, local ONNX)* | `Xenova/ms-marco-MiniLM-L-6-v2` | *none* |
| `cohere` | `rerank-english-v3.0` | `RERANK_API_KEY` or `COHERE_API_KEY` |

```ini
RERANK_PROVIDER=cohere
COHERE_API_KEY=...
```

---

## 5 · Speech-to-text — uploads  (`ASR_PROVIDER`)

Transcribes uploaded videos (YouTube uses its own captions). `ENABLE_ASR=false`
leaves uploads visual-only.

| `ASR_PROVIDER` | Models | Key env |
|---|---|---|
| `openai` *(default)* | `whisper-1`, `gpt-4o-transcribe` | `ASR_API_KEY` → `OPENAI_API_KEY` → `LLM_API_KEY` |

```ini
ASR_PROVIDER=openai
ASR_MODEL=whisper-1        # or gpt-4o-transcribe
# ASR_API_KEY=            # blank reuses the OpenAI/LLM key
```

Any OpenAI-compatible ASR server works via `ASR_BASE_URL`.

---

## 6 · Speaker recognition — "who said what"  (`GEMINI_API_KEY`)

Opt-in **per video** (a checkbox at upload). Gemini watches the video and labels
who said each line. **Gemini-only** (video understanding), independent of
`LLM_PROVIDER`.

| Setting | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | — | **required** for this feature (needs `google-genai`) |
| `DIARIZE_ENABLED` | `true` | master switch; each video is still opt-in |
| `DIARIZE_MODEL` | `gemini-2.5-flash` | a video-capable Gemini model |
| `DIARIZE_WINDOW_S` / `DIARIZE_MAX_WORKERS` | `600` / `6` | video seconds per call / parallel windows |

```ini
GEMINI_API_KEY=...
# checkbox on the upload page turns it on per video
```

Check the box without a key set → the API rejects it (*"Gemini key is missing"*).

---

## Rules that apply to every embedding slot

- **Switching provider/model changes the vector dimension.** The old index
  becomes unusable and MomentSearch refuses to mix them — point that branch's
  collection (`IMAGE_COLLECTION` / `TEXT_COLLECTION`) at a fresh name and
  re-ingest (`python -m src.seed`).
- **The two branches are independent** — mix freely (e.g. local CLIP frames +
  hosted OpenAI transcripts).
- `LLM_API_KEY` (generic) **overrides** the provider-specific LLM key — a
  leftover from another provider is the #1 cause of *"API key not valid."* Run
  `python -m src.providers` to see exactly which var each key came from.
