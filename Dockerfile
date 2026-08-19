# The APP image — api + worker + seed (and, on CPU, it can run the clip service
# too). Four entrypoints, one image; the command picks which.
#
#   FAT  (default)         docker build .
#     Includes the local-CLIP torch stack, so it embeds in-process with no
#     separate service. This is the one-box path (docker compose / Fly).
#
#   SLIM                   docker build --build-arg WITH_TORCH=false .
#     Omits torch + sentence-transformers (~hundreds of MB smaller, faster
#     builds). It has NO local model, so it MUST reach a CLIP service via
#     EMBED_SERVICE_URL — deploy the clip image (Dockerfile.clip) alongside it.
#     Without a reachable clip service a slim image cannot index or search
#     (embedding raises a clear "point EMBED_SERVICE_URL at a clip service"
#     error). See README "Split / GPU deployment".
FROM python:3.11-slim

# Fat by default. --build-arg WITH_TORCH=false makes the slim, remote-embed image.
ARG WITH_TORCH=true

# ffmpeg = frame sampling. nodejs = the JavaScript runtime yt-dlp needs to
# extract YouTube formats (without it, EVERY YouTube video fails with "This
# video is not available"). Both matter only to the worker but cost little here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-clip.txt ./
# Base deps always (they carry NO torch — fastembed is ONNX). The local-CLIP
# stack is added only for the fat image: CPU-only torch first (the default Linux
# wheel drags in ~6GB of CUDA libs CLIP-on-CPU never uses), then
# sentence-transformers on top of it.
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_TORCH" = "true" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
   && pip install --no-cache-dir -r requirements-clip.txt; \
    fi

COPY src/ src/
COPY ui/ ui/

EXPOSE 8000
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
