# Two images from one file.
#
#   dev      what docker-compose.yml builds: dev extras, editable install.
#   serving  what Oracle and Modal run: no dev extras, the embedder BAKED IN,
#            the Hub switched off, and a healthcheck.
#
# Why bake the embedder: measured on 2026-09-21, the first request after a start
# paid 60.8 s loading bge-small - 17.95 s of it revalidating an already-cached
# model against the Hugging Face Hub over 29 requests. A baked model with
# HF_HUB_OFFLINE=1 cannot do that.
#
#   docker build --target serving -t rag-wikipedia-api backend
#
# arm64 (Oracle A1) and amd64 (Modal, laptops) both work: python:slim, and the
# torch CPU wheels, publish both.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base
WORKDIR /app
RUN pip install --no-cache-dir uv
# CPU-only torch wheels: no GPU in this container, and CUDA wheels triple the size
ENV UV_TORCH_BACKEND=cpu
# install deps from the manifest first so source edits don't invalidate this layer
COPY pyproject.toml .

FROM base AS dev
RUN uv pip install --system -r pyproject.toml --extra dev
COPY . .
RUN uv pip install --system --no-deps -e .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS serving
RUN uv pip install --system -r pyproject.toml
COPY . .
RUN uv pip install --system --no-deps -e .

ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')" \
 && chmod -R a+rX /opt/hf
# The model is in the image: never ask the Hub at run time.
ENV HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONUNBUFFERED=1

# Runs as a normal user: nothing here needs root, and /opt/hf is read-only to it.
RUN useradd --create-home --uid 10001 rag
USER rag

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
