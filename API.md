# HTTP API

Every endpoint the MomentSearch server exposes, what it takes, what it returns, and **which file owns it**. Written to be read by a person or an agent before touching `src/api/`. What happens *inside* an ask (retrieval, gate, answer) is in [ARCHITECTURE.md](ARCHITECTURE.md).

> ⚠️ **No authentication.** Every endpoint is open, mutating ones included. `ADMIN_TOKEN` is **not enforced** — setting it changes nothing. `require_auth()` in [src/api/videos.py](src/api/videos.py) is a deliberate no-op, kept as the single place to restore a check. `X-User-Id` and `Authorization` are **ignored**, not honoured. Every request acts as `SINGLE_USER_ID` (default `default`). See [Security](README.md#security).

## Where the code lives

Four routers, one file each, all mounted in [src/app.py](src/app.py):

| URL prefix | File | Covers |
|---|---|---|
| `/api/videos` | [src/api/videos.py](src/api/videos.py) | upload, register, list, retry, delete |
| `/api/sessions` | [src/api/sessions.py](src/api/sessions.py) | sessions, their videos, session-scoped ask |
| `/api/ask`, `/api/llm`, meta, media, pages | [src/api/search.py](src/api/search.py) | everything else |
| `/api/auth` | [src/api/auth.py](src/api/auth.py) | the one account |

Live references generated from the code on any running instance: **`/docs`** (Swagger UI), **`/redoc`**, **`/openapi.json`**. This file adds what those can't: which file to open, what each field means, and the flows.

## Route map

| Method | Path | File | What it does |
|---|---|---|---|
| **Videos** | | | |
| POST | `/api/videos/presign` | videos.py | Mint an upload slot: the object key and where to PUT the bytes |
| PUT | `/api/videos/{video_id}/content?key=` | videos.py | Local-dev only: the API accepts the bytes itself (`STORAGE_PROVIDER=local`) |
| POST | `/api/videos` | videos.py | Register a YouTube URL or an uploaded file for indexing → `202` |
| GET | `/api/videos` | videos.py | List your videos plus the ten demo videos (`is_sample`); `?status=` filters |
| GET | `/api/videos/{video_id}` | videos.py | One video's status |
| POST | `/api/videos/{video_id}/retry` | videos.py | Re-queue a failed video → `202` |
| DELETE | `/api/videos/{video_id}` | videos.py | Purge vectors, frames, raw file and row (samples refused with `403`) |
| **Sessions** | | | |
| GET | `/api/sessions` | sessions.py | List sessions (the first call seeds a demo session) |
| POST | `/api/sessions` | sessions.py | Create an empty session → `201` |
| GET | `/api/sessions/{id}` | sessions.py | Session + its videos + its messages |
| PATCH | `/api/sessions/{id}` | sessions.py | Rename |
| DELETE | `/api/sessions/{id}` | sessions.py | Delete; purges videos no other session holds |
| POST | `/api/sessions/{id}/videos` | sessions.py | Link an existing video into the session |
| DELETE | `/api/sessions/{id}/videos/{video_id}` | sessions.py | Unlink (does not delete the video) |
| POST | `/api/sessions/{id}/ask` | sessions.py | Ask, scoped to the session's indexed videos; stores the turn |
| POST | `/api/sessions/{id}/ask_stream` | sessions.py | Same, as Server-Sent Events with live stages |
| **Ask (library-wide)** | | | |
| POST | `/api/ask` | search.py | Ask across everything indexed (what `/demo` and the benchmark use) |
| POST | `/api/ask_stream` | search.py | Same, as SSE |
| **Model settings** | | | |
| GET | `/api/llm` | search.py | Which model answers: yours, the server's, or none |
| PUT | `/api/llm` | search.py | Attach your own provider or endpoint for answers |
| POST | `/api/llm/test` | search.py | One tiny image through the model: proves connectivity and vision |
| DELETE | `/api/llm` | search.py | Back to the server default |
| **Meta** | | | |
| GET | `/api/health` | search.py | `{"ok": true}` |
| GET | `/api/config` | search.py | What this instance runs: models, setup gaps, upload mode, limits |
| GET | `/api/providers` | search.py | Every supported provider and whether it is configured here |
| GET | `/api/auth/me` | auth.py | `{"name", "user_id"}` of the single account |
| **Media** | | | |
| GET | `/api/transcript/{video_id}` | search.py | Full timed transcript from storage (works with any storage provider) |
| GET | `/api/frame/{video_id}/{name}` | search.py | A thumbnail; local dev only, `404` when a bucket serves them via presigned URLs |
| GET | `/api/video/{video_id}` | search.py | Range-capable playback of an upload; local dev only, `404` when a bucket serves it |
| **Pages** | | | |
| GET | `/`, `/demo`, `/app` | search.py | Landing, demo, workspace (`/signin` and `/get-started` redirect to `/app`) |

## Ask

The read path. Both flavours, `/api/ask` and `/api/sessions/{id}/ask`, take the same body and produce the same result. The session one also stores the question and answer as messages and defaults its scope to the session's indexed videos.

**Request**

```jsonc
{"question": "what is tokenization?",
 "video_ids": ["yt_LPZh9BOjkQs"],   // optional: restrict to these; omit or empty = all
 "top_k": 6}                          // optional: moments to retrieve (default TOP_K)
```

`/api/ask` also accepts the legacy single `video_id`. A session ask whose `video_ids` name videos the session does not hold is answered honestly ("No videos are selected") rather than widened.

**Response** for `/api/ask`. The session variant wraps it as `{"message": {...}}` with the answer under `content`, the moments under `citations`, and the other fields under `meta`.

```jsonc
{"question": "...",
 "answer": "markdown with [n] citations",
 "citations": [ {...moment...} ],
 "llm_used": true, "llm_source": "server", "llm_model": "gpt-4.1-mini",
 "abstained": false,                  // true when nothing relevant was found; citations is then []
 "note": "...",                       // optional, e.g. a local model read fewer moments
 "parts": ["sub-question 1", "..."]}  // only when the question was split (MULTI_QUERY)
```

**A citation (one moment card)**

| Field | Meaning |
|---|---|
| `n` | 1-based number the answer cites as `[n]` |
| `video_id`, `title`, `url`, `source` | which video; `source` is `youtube` or `upload` |
| `ms`, `timestamp` | anchor time in ms and as `mm:ss` |
| `t_start`, `t_end` | the matched span in seconds (a transcript chunk's extent; a frame is a point) |
| `modalities` | `["frame"]`, `["text"]` or both: what matched |
| `match` | 0 to 100 match strength shown on the card (strength, not rank) |
| `score` | internal blended rank score |
| `transcript`, `speaker` | the matched excerpt and, if speaker recognition ran, who said it |
| `thumbnail`, `preview` | the matched frame, or the nearest still for a text-only moment |
| `deeplink`, `media_url` | seek URL (YouTube `&t=`), playback URL for uploads |
| `parts` | which sub-question(s) found it, e.g. `[1, 2]`; only on split questions |

**Abstaining.** If neither what is on screen nor what is said clears the confidence gate, or the model cites nothing, the reply is a one-line "couldn't find it", `abstained` is `true` and `citations` is `[]`, so no cards appear under a refusal.

**Streaming** (`/api/ask_stream`, `/api/sessions/{id}/ask_stream`). One JSON object per SSE `data:` line, emitted as each stage *begins*:

```text
{"type":"stage","stage":"embedding"}
{"type":"stage","stage":"searching"}
{"type":"stage","stage":"ranking","detail":"32 candidate moments"}
{"type":"stage","stage":"splitting"}                          only if the multi-part check outlasts retrieval
{"type":"stage","stage":"parts","detail":"2 parts · … · …"}   only when the question was split
{"type":"stage","stage":"reading","detail":"8 moments"}
{"type":"stage","stage":"answering","detail":"gpt-4.1-mini reading 8 frames"}
{"type":"done","result":{...}}        /api/ask_stream: the /api/ask payload
{"type":"done","message":{...}}       sessions: the stored message
{"type":"error","detail":"..."}
```

Stage names are a contract with the UI labels: `STAGE_WORDS` in [ui/workspace.js](ui/workspace.js) and `TRACE` in [ui/demo.js](ui/demo.js). A stage is added in [src/rag/search.py](src/rag/search.py) and labelled in both.

## Videos

**Upload flow**: three calls. The server mints the object key and never trusts one from the client.

```bash
# 1) presign: the server mints the key {user}/{video}/source.{ext}
curl -X POST localhost:8000/api/videos/presign -H "Content-Type: application/json" \
  -d '{"filename":"demo.mp4","content_type":"video/mp4","size":123456789,"sha256":"<optional>"}'
#  -> {"mode":"presigned","video_id":"up_ab12cd34ef","key":"default/up_ab12cd34ef/source.mp4","url":"...","headers":{...}}
#  -> {"mode":"direct", ..., "url":"/api/videos/up_.../content?key=..."}   local storage: PUT the bytes to the API
#  -> {"mode":"exists","video_id":"up_...","title":"..."}                  same sha256 already indexed: skip the upload

# 2) PUT the file to `url` with `headers`

# 3) register (the server re-verifies size and content-type via HEAD)
curl -X POST localhost:8000/api/videos -H "Content-Type: application/json" \
  -d '{"video_id":"up_ab12cd34ef","key":"default/up_ab12cd34ef/source.mp4","title":"Demo","session_id":"s_...","speaker_recognition":false}'

# YouTube instead (video_id becomes yt_<11 chars>)
curl -X POST localhost:8000/api/videos -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/VIDEO_ID","session_id":"s_...","speaker_recognition":true}'
```

Register returns `202 {"video_id", "status": "pending"}`. An already-indexed YouTube video returns `{"status": "indexed", "deduped": true}` and is only linked into the session. It fails fast with `503` naming the missing env keys when ingest is not configured, and with `400` when `speaker_recognition` is requested without `GEMINI_API_KEY`.

**A video**, as returned by `GET /api/videos`, `GET /api/videos/{id}` and inside a session:

`id, source, url, title, status, error, frame_count, diarize, progress, attempts, transcript_note, created_at, updated_at, thumbnail`

`status` moves `pending → queued → fetching → sampling → embedding → indexed`, or ends in `failed` (then `error` says why and `retry` re-queues it). `transcript_note` is set when the transcript could not be indexed; search still works on frames. `diarize` says whether speaker recognition was on. Poll the list while any status is in flight.

## Sessions

A session is one folder of videos plus the turns asked of it.

```bash
curl localhost:8000/api/sessions                        # {"sessions":[{id,title,kind,video_count,message_count,created_at,updated_at}]}
curl -X POST localhost:8000/api/sessions -H "Content-Type: application/json" -d '{"title":"My first search"}'
curl localhost:8000/api/sessions/s_ab12                 # + "videos":[...], "messages":[{id,role,content,citations,meta,created_at}]
curl -X PATCH  localhost:8000/api/sessions/s_ab12 -H "Content-Type: application/json" -d '{"title":"Renamed"}'
curl -X POST   localhost:8000/api/sessions/s_ab12/videos -H "Content-Type: application/json" -d '{"video_id":"yt_LPZh9BOjkQs"}'
curl -X DELETE localhost:8000/api/sessions/s_ab12/videos/yt_LPZh9BOjkQs
curl -X DELETE localhost:8000/api/sessions/s_ab12       # -> {"ok":true,"purged":[video ids no other session held]}
```

`kind` is `demo` for the first-run session (it holds the ten demo videos) and `own` otherwise. An assistant message's `meta` carries `llm_used`, `abstained`, `llm_source`, `llm_model`, `note` and `parts`, the same fields as the ask response.

## Model settings (`/api/llm`)

Only the *answer* model is switchable here. Embeddings live in fixed-dimension collections and are server config, see [MODELS.md](MODELS.md).

```bash
curl localhost:8000/api/llm
#  -> {"configured":false,"active_source":"server","settings":null,"server_fallback":true}
curl -X PUT localhost:8000/api/llm -H "Content-Type: application/json" \
  -d '{"provider":"openai","model":"gpt-4.1-mini","api_key":"sk-...","base_url":null}'
#     provider: any name or alias from GET /api/providers; model blank = that provider's default
#     base_url: your own vLLM / Ollama / LM Studio endpoint; api_key empty keeps the stored one
curl -X POST localhost:8000/api/llm/test        # {"ok":true,"source":"user","model":"...","reply":"red"}
curl -X DELETE localhost:8000/api/llm           # back to the server's LLM_* env config
```

`active_source` is `user` (your attached model), `server` (the `LLM_*` env) or `none` (retrieval-only answers).

## Building your own UI

The bundled UI is plain files in [ui/](ui/) served at `/ui/*`, no build step, and it uses nothing but the endpoints above. [ui/workspace.js](ui/workspace.js) is a complete reference client (sessions, upload, polling, streaming ask, cards, player); [ui/demo.js](ui/demo.js) is the smaller read-only one (corpus slider, browse drawer, ask).

**Origin first.** The server sets **no CORS headers**. A UI on another origin cannot call `/api/*` from the browser as-is. Either serve your UI from the same origin (put it behind the same reverse proxy that forwards `/api/*` to port 8000), or add `CORSMiddleware` in [src/app.py](src/app.py) for your origin. Remember the no-authentication note at the top: opening CORS to a browser origin means that origin's pages can do everything, including deletes.

**The minimal loop**

1. **Bootstrap.** `GET /api/config`: read `setup` (what is still unconfigured, to show a banner), `upload_mode` (`presigned` or `direct`), `diarize_available` (whether to offer the speaker-recognition checkbox), `max_upload_mb`. Sessions are optional: a UI can work purely with `/api/videos` + `/api/ask`.
2. **Add a video.** YouTube: `POST /api/videos {"url"}`. File: `presign` → PUT the bytes to the returned `url` with its `headers` → `POST /api/videos` with `video_id` + `key`. Pass `session_id` if you use sessions. Then poll `GET /api/videos` every 2 to 3 seconds while any `status` is not `indexed`, `failed` or `skipped`; show `progress`, `error`, and `transcript_note` when set.
3. **Ask.** `POST /api/ask_stream` (or the session twin) with `fetch` and a body stream reader, not `EventSource`, because the question travels in a POST body. Split the stream on blank lines, take lines starting with `data:`, parse the JSON. Show each `stage` as it arrives; on `done` render `answer` as markdown, turn every `[n]` into a link to citation `n`, draw the cards from `citations` (`thumbnail`, else `preview`, else the YouTube still `https://img.youtube.com/vi/<id>/hqdefault.jpg`), and show `match`, `modalities`, `speaker`, `transcript`. When `abstained` is true there are no citations: show the answer text alone. When `parts` is present, list the sub-questions and tag each card with its `parts`.
4. **Open a moment.** YouTube: `deeplink` already carries `&t=<seconds>`, or seek an IFrame player to `ms / 1000`. Upload: `media_url` is a playable URL, seek with `#t=`. For a synced transcript panel, `GET /api/transcript/{video_id}` returns `chunks` with `t_start`, `t_end`, `text` and optional `speaker`.
5. **Model picker (optional).** `GET /api/providers` for the list, `PUT /api/llm` to attach one, `POST /api/llm/test` to prove it works, `DELETE /api/llm` to go back to the server default.

**Things that bite**

- `thumbnail`, `preview` and `media_url` are presigned bucket URLs that expire (`PRESIGN_GET_EXPIRY_S`, default one hour). Re-fetch the session or message rather than caching the URLs.
- `/api/frame/*` and `/api/video/*` exist only for `STORAGE_PROVIDER=local`. With a bucket they return `404`; use the URLs already in the payload.
- Video ids are `yt_<11 chars>` for YouTube and `up_<10 hex>` for uploads. The ten demo videos ship pre-indexed and cannot be deleted; unselect them in `video_ids` instead.
- Stage names can grow (see **Streaming**). Label the ones you know and print the raw name for the rest so a new stage never blanks your progress line.

## Keeping this file current

The rule: **a route change and its API.md change land in the same commit.** To see what the code actually exposes right now:

```bash
grep -nE '@router\.(get|post|put|delete|patch)\(' src/api/*.py     # every route, with file:line
grep -nE 'prefix=' src/api/*.py                                    # the prefix each file mounts under
```

or open `/docs` on a running instance and compare it with the route map above.

When you add or change an endpoint:

1. Put it in the **Route map** with its file.
2. Document new request or response fields in the matching section. Request bodies are the `BaseModel` classes just above each handler. Response shapes come from `_public` in [src/api/videos.py](src/api/videos.py), `_session_out` and `_message_out` in [src/api/sessions.py](src/api/sessions.py), and the citation dict built at the end of `retrieve()` in [src/rag/search.py](src/rag/search.py).
3. If it emits a new SSE stage, add the label to both UI files named under **Streaming**.
