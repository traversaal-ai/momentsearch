# HTTP API

The endpoints behind the workspace UI. See also the [Architecture deep dive](ARCHITECTURE.md) for what happens inside `/ask`.

> ⚠️ **No authentication.** Every endpoint is open, mutating ones included. `ADMIN_TOKEN` is **not enforced** — setting it changes nothing. `require_auth()` in [src/api/videos.py](src/api/videos.py) is a deliberate no-op, kept as the single place to restore a check. `X-User-Id` and `Authorization` are **ignored**, not honoured, so a stale header can't steer reads or writes. Every request acts as `SINGLE_USER_ID` (default `default`). See [Security](README.md#security).

## Videos

```bash
# 1) presign
curl -X POST localhost:8000/api/videos/presign -H "Content-Type: application/json" \
  -d '{"filename":"demo.mp4","content_type":"video/mp4","size":123456789}'
# 2) PUT the file to the returned url, then 3) register:
curl -X POST localhost:8000/api/videos -H "Content-Type: application/json" \
  -d '{"video_id":"up_ab12cd34ef","key":"default/up_ab12cd34ef/source.mp4","title":"Demo"}'

# YouTube instead
curl -X POST localhost:8000/api/videos -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/VIDEO_ID"}'

# status / retry / delete
curl localhost:8000/api/videos
curl -X POST localhost:8000/api/videos/up_ab12cd34ef/retry
curl -X DELETE localhost:8000/api/videos/up_ab12cd34ef      # purges vectors + files + row

# ask across everything indexed
curl -X POST localhost:8000/api/ask -H "Content-Type: application/json" \
  -d '{"question":"a diagram of the attention mechanism"}'
```

The server picks the object key at presign time (`{user}/{video}/source.{ext}`), never trusting it from the client, and re-verifies size and content-type via HEAD at register. Poll `GET /api/videos` until a video reports `indexed`.

## Sessions

A session is one folder of videos plus the answers asked of it. This is what the workspace UI drives:

```bash
curl localhost:8000/api/sessions                       # list (bootstraps first run)
curl -X POST localhost:8000/api/sessions -H "Content-Type: application/json" \
  -d '{"title":"My first search"}'
curl localhost:8000/api/sessions/s_ab12                # session + videos + messages
curl -X PATCH localhost:8000/api/sessions/s_ab12 -H "Content-Type: application/json" \
  -d '{"title":"Renamed"}'
curl -X DELETE localhost:8000/api/sessions/s_ab12      # + purges videos no other session holds

# ask, scoped to THIS session's indexed videos
curl -X POST localhost:8000/api/sessions/s_ab12/ask -H "Content-Type: application/json" \
  -d '{"question":"what is tokenization?","video_ids":["yt_LPZh9BOjkQs"]}'

# same, but streams what the server is doing (SSE)
curl -N -X POST localhost:8000/api/sessions/s_ab12/ask_stream \
  -H "Content-Type: application/json" -d '{"question":"what is tokenization?"}'
#  -> data: {"type":"stage","stage":"embedding"}     as each stage BEGINS
#     data: {"type":"done","message":{...}}          the stored message
```

## Pages & meta

```
Pages:  GET /   ·  GET /demo   ·  GET /app
Meta:   GET /api/health  ·  GET /api/config  ·  GET /api/providers  ·  GET /api/auth/me
```

`/signin` and `/get-started` are `307` redirects to `/app`. `GET /api/providers` lists the models you can attach; `PUT /api/llm` attaches any provider (or your own vLLM / Ollama / LM Studio endpoint via `base_url`) so every subsequent `/api/ask` answers with it instead of the server default — only the *LLM* is switchable this way, since embeddings live in shared, fixed-dimension collections.
