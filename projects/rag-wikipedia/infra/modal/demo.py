"""The public demo: the whole RAG in one scale-to-zero container.

    modal volume create rag-wikipedia-corpus
    modal volume put rag-wikipedia-corpus wikipedia.snapshot /wikipedia.snapshot
    modal run infra/modal/demo.py::restore_corpus      # once
    modal deploy infra/modal/demo.py

Four processes, one container, because they are one request's worth of work and
talking over localhost beats talking over the internet:

    Qdrant    the 87,173-chunk corpus, on a Modal Volume (332 MB measured)
    Redis     so the existing rate limiter works unchanged
    Ollama    llama3.2:3b, BAKED INTO THE IMAGE so a cold start never downloads
    FastAPI   the app itself, retrieval + refusal gate + generation

The frontend is NOT here: it is static, it lives on Vercel, and it loads in
under a second whether or not this container is awake. That split is the point -
a visitor sees the product and the published numbers immediately, and only the
answer waits for the GPU.

Cost levers (docs/online-deployment.md §6), all visible here:
* min_containers=0  - idle costs nothing. An always-on T4 is about $580/month.
* max_containers=1  - a burst queues instead of burning the month's credit.
* scaledown_window  - 120 s by day, 60 s at night (Europe/Paris), set by `retune`.
  Each burst is billed as cold start + work + this window.
* the model is in the image, so a cold start is weights-from-disk, not a download.

NOT yet run against a live Modal account.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import modal

# Only needed while the image is being defined, on the laptop. Inside the
# container this file is /root/demo.py, which has no parents[2] - and Modal
# re-imports the module there, so a bare parents[2] crashes every function.
_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[2] if len(_HERE.parents) > 2 else Path("/srv")
MODEL = os.environ.get("RAG_MODAL_MODEL", "llama3.2:3b")
GPU = os.environ.get("RAG_MODAL_GPU", "T4")
COLLECTION = "wikipedia"
EXPECTED_POINTS = 87173
DAY_WINDOW_S, NIGHT_WINDOW_S = 120, 60
QDRANT_VERSION = "v1.18.3"

corpus = modal.Volume.from_name("rag-wikipedia-corpus", create_if_missing=True)

image = (
    # Ollama's image already carries the server and the GPU runtime; everything
    # else is added on top so there is one image to cold-start, not three.
    modal.Image.from_registry("ollama/ollama:0.12.3", add_python="3.12")
    .entrypoint([])
    .apt_install("redis-server", "curl", "ca-certificates")
    .run_commands(
        # Qdrant: the released binary, not a second container.
        f"curl -sSfL https://github.com/qdrant/qdrant/releases/download/{QDRANT_VERSION}"
        f"/qdrant-x86_64-unknown-linux-gnu.tar.gz -o /tmp/qdrant.tar.gz",
        "tar -xzf /tmp/qdrant.tar.gz -C /usr/local/bin && rm /tmp/qdrant.tar.gz",
        # The weights, baked: a cold start loads them from the image.
        f"(ollama serve &) && sleep 8 && ollama pull {MODEL} && pkill ollama || true",
    )
    .env({"UV_TORCH_BACKEND": "cpu", "HF_HOME": "/opt/hf", "HF_HUB_DISABLE_TELEMETRY": "1"})
    # The SERVING subset of backend/pyproject.toml: no prefect, no datasets -
    # those belong to ingestion, which does not run here, and they would make
    # the image (and so every cold start) bigger for nothing.
    #
    # The subset is a deliberate choice and therefore a drift risk: the first
    # deploy answered /health and returned 500 on /query because `ollama` - the
    # client, not the server in the base image - was missing from this list. If
    # it drifts again, the symptom is a ModuleNotFoundError in the app logs.
    .uv_pip_install(
        "fastapi>=0.111", "uvicorn[standard]>=0.30", "httpx>=0.27", "pydantic>=2.9",
        "pydantic-settings>=2.3", "qdrant-client>=1.12,<2", "sentence-transformers>=5.4,<6",
        "torch>=2.4", "redis>=5.0", "tiktoken>=0.7", "ollama>=0.2",
    )
    # The embedder too: measured, a cold load from the Hub cost 60.8 s.
    .run_commands(
        "python -c \"from sentence_transformers import SentenceTransformer;"
        " SentenceTransformer('BAAI/bge-small-en-v1.5')\"",
    )
    .env({"HF_HUB_OFFLINE": "1"})
    .add_local_dir(ROOT / "backend", "/srv/backend")
    .add_local_dir(ROOT / "scripts", "/srv/scripts")
)

app = modal.App("rag-wikipedia-demo")


def _start_qdrant(storage: str = "/corpus/storage", snapshot: str | None = None) -> None:
    """Qdrant on localhost, its storage on the Volume."""
    env = os.environ | {
        "QDRANT__STORAGE__STORAGE_PATH": storage,
        "QDRANT__STORAGE__SNAPSHOTS_PATH": "/corpus/snapshots",
        "QDRANT__SERVICE__HTTP_PORT": "6333",
    }
    args = ["qdrant"]
    if snapshot:
        # Qdrant restores a snapshot at startup when given path:collection.
        args += ["--snapshot", f"{snapshot}:{COLLECTION}", "--force-snapshot"]
    subprocess.Popen(args, env=env, cwd="/usr/local/bin")


def _wait_for(url: str, seconds: int = 300) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


@app.function(image=image, volumes={"/corpus": corpus}, cpu=2, memory=8192, timeout=1800)
def restore_corpus(snapshot: str = "/corpus/wikipedia.snapshot") -> dict:
    """Run once, after `modal volume put`: unpack the snapshot into the Volume."""
    import sys

    sys.path.insert(0, "/srv")
    _start_qdrant(snapshot=snapshot)
    if not _wait_for("http://127.0.0.1:6333/readyz", 900):
        return {"ok": False, "error": "qdrant did not come up"}

    from scripts.corpus_snapshot import verify  # noqa: PLC0415

    code = verify("http://127.0.0.1:6333", COLLECTION, EXPECTED_POINTS)
    corpus.commit()
    return {"ok": code == 0}


@app.function(
    image=image,
    gpu=GPU,
    volumes={"/corpus": corpus},
    cpu=2,
    memory=8192,
    min_containers=0,
    max_containers=1,
    scaledown_window=DAY_WINDOW_S,
    timeout=900,
    secrets=[modal.Secret.from_name("rag-demo")],
)
@modal.concurrent(max_inputs=4)
@modal.web_server(port=8000, startup_timeout=600)
def serve() -> None:
    """Qdrant, Redis, Ollama and the API - in that order, because each needs the last."""
    _start_qdrant()
    subprocess.Popen(["redis-server", "--save", "", "--appendonly", "no", "--bind", "127.0.0.1"])
    subprocess.Popen(
        ["ollama", "serve"],
        env=os.environ | {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_KEEP_ALIVE": "-1",
                          "OLLAMA_NUM_PARALLEL": "4"},
    )
    _wait_for("http://127.0.0.1:6333/readyz", 300)

    subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"],
        cwd="/srv/backend",
        env=os.environ | {
            "PYTHONPATH": "/srv/backend",
            "QDRANT_URL": "http://127.0.0.1:6333",
            "OLLAMA_URL": "http://127.0.0.1:11434",
            "RATE_LIMIT_REDIS_URL": "redis://127.0.0.1:6379/0",
            "COLLECTION": COLLECTION,
            "PROFILE": "real",
            "LLM_MODEL": MODEL,
            "WARMUP_ON_STARTUP": "true",
            # The demo's budget per visitor: a question every few seconds is
            # fine, a loop is not. One container, so this is the whole service.
            "RATE_LIMIT_ENABLED": "true",
            "RATE_LIMIT_QUERY_PER_MINUTE": os.environ.get("RATE_LIMIT_QUERY_PER_MINUTE", "6"),
            "RATE_LIMIT_QUERY_BURST": os.environ.get("RATE_LIMIT_QUERY_BURST", "10"),
            # Vercel serves the page; this is the only origin allowed to call the API.
            "ALLOWED_ORIGINS": os.environ["ALLOWED_ORIGINS"],
            # The auto-correcting audit does not exist in production.
            "QUALITY_ADMIN_TOKEN": "",
        },
    )


@app.function(schedule=modal.Cron("0 8,23 * * *", timezone="Europe/Paris"))
def retune() -> None:
    """Warm while people read, frugal overnight."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hour = datetime.now(ZoneInfo("Europe/Paris")).hour
    window = DAY_WINDOW_S if 8 <= hour < 23 else NIGHT_WINDOW_S
    modal.Function.from_name("rag-wikipedia-demo", "serve").update_autoscaler(
        scaledown_window=window
    )
