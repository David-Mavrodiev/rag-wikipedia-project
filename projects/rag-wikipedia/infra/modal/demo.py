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
# eval/judge_accuracy.py names its reports after the set FILE ("real" lives in
# real_claims.jsonl.gz), so reading one back needs the stem, not the flag.
SET_STEMS = {"real": "real_claims", "synthetic": "synthetic"}
EXPECTED_POINTS = 87173
DAY_WINDOW_S, NIGHT_WINDOW_S = 120, 60
QDRANT_VERSION = "v1.18.3"

corpus = modal.Volume.from_name("rag-wikipedia-corpus", create_if_missing=True)

base_image = (
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
        "pyjwt[crypto]>=2.9,<3",
    )
    # The embedder too: measured, a cold load from the Hub cost 60.8 s.
    .run_commands(
        "python -c \"from sentence_transformers import SentenceTransformer;"
        " SentenceTransformer('BAAI/bge-small-en-v1.5')\"",
    )
    .env({"HF_HUB_OFFLINE": "1"})
)


def _with_sources(built: modal.Image) -> modal.Image:
    """Local files go on LAST. Modal forbids a build step after add_local_*,
    and adding them last is also what keeps an edit here from rebuilding the
    whole image."""
    return (
        built
        .add_local_dir(ROOT / "backend", "/srv/backend")
        .add_local_dir(ROOT / "scripts", "/srv/scripts")
    )


def _deploy_sha() -> str:
    """The commit being deployed, read on the laptop while the image is defined.

    The container gets the working tree without its `.git`, so `/quality` served
    a provenance block whose git_sha was null - a live metric belonging to no
    commit. Empty outside a checkout (including when Modal re-imports this file
    in the container, where the baked value is already in the environment).
    """
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


# Last layer before the local files, so a new commit rebuilds nothing that
# costs anything. `serve` passes os.environ through, so the API inherits it.
image = _with_sources(base_image.env({"RAG_GIT_SHA": _deploy_sha()}))

reports = modal.Volume.from_name("rag-wikipedia-reports", create_if_missing=True)

# The evaluation image is the serving image plus the judge's weights, branched
# from the BASE rather than from the serving image. Separate on purpose: the
# NLI model is ~700 MB, and baking it into the image the DEMO cold-starts from
# would make every first visitor wait for weights no answer needs.
# HF_HUB_OFFLINE is lifted for the download and put back afterwards.
eval_image = _with_sources(
    base_image.env({"HF_HUB_OFFLINE": "0"})
    .run_commands(
        "python -c \"from sentence_transformers import CrossEncoder;"
        " CrossEncoder('cross-encoder/nli-deberta-v3-base')\"",
    )
    .env({"HF_HUB_OFFLINE": "1"})
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
        # serving.app is the composition root: the API, plus authentication when
        # AUTH_MODE and CLERK_ISSUER are set. With neither, it is app.main
        # unchanged - so this line is safe before Clerk exists.
        ["uvicorn", "serving.app:app", "--host", "0.0.0.0", "--port", "8000",
         "--no-access-log"],
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
            # Authentication: off until the Clerk secret carries an issuer.
            "AUTH_MODE": os.environ.get("AUTH_MODE", "off"),
            "CLERK_ISSUER": os.environ.get("CLERK_ISSUER", ""),
            "CLERK_AUDIENCE": os.environ.get("CLERK_AUDIENCE", ""),
        },
    )


# cpu=8 and two hours, measured: torch here is the CPU build (UV_TORCH_BACKEND=cpu
# in the base image), and scoring the real set's two settings took about 45
# minutes on 4 CPUs - four settings did not fit in an hour and the run was killed
# with the synthetic half unfinished. A CUDA wheel would be faster and is a 2.5 GB
# layer for a job that runs twice a month.
@app.function(image=eval_image, volumes={"/reports": reports},
               cpu=8, memory=8192, timeout=7200, max_containers=1)
def judge(sets: str = "real,synthetic", split: str = "test",
          assemble: str = "0,3", git_sha: str = "") -> dict:
    """Score the groundedness JUDGE against hand labels - no GPU, no corpus.

    The judge decides whether a claim is supported, and the published
    groundedness rate is only as good as that decision. This reads the committed
    labelled sets and runs the NLI model on CPU: no Qdrant, no Ollama, no GPU, so
    a full comparison costs cents and cannot be confused with an end-to-end run.

    Every (set x assemble) combination runs in ONE container, because the cost
    here is loading the model, not scoring 57 claims:

        modal run infra/modal/demo.py::judge --split test --git-sha $SHA

    The laptop can do this too. It stopped being able to while 16 GB of RAM was
    otherwise occupied, and the point of the number is that it exists.
    """
    import json
    import subprocess

    out_dir = Path("/reports/judge")
    results, failures = {}, []
    for which in [s.strip() for s in sets.split(",") if s.strip()]:
        for k in [a.strip() for a in assemble.split(",") if a.strip()]:
            args = ["python", "eval/judge_accuracy.py", "--set", which,
                    "--split", split, "--assemble", k, "--out-dir", str(out_dir)]
            done = subprocess.run(
                args, cwd="/srv/backend", check=False, capture_output=True, text=True,
                env=os.environ | {"PYTHONPATH": "/srv/backend",
                                  "PYTHONIOENCODING": "utf-8",
                                  "RAG_GIT_SHA": git_sha},
            )
            key = f"{which}.{split}.assemble{k}"
            if done.returncode != 0:
                failures.append({"combo": key, "stderr_tail": done.stderr[-1500:]})
                continue
            report = out_dir / f"accuracy.{SET_STEMS.get(which, which)}.{split}.assemble{k}.json"
            results[key] = (json.loads(report.read_text(encoding="utf-8"))
                            if report.exists() else {"stdout_tail": done.stdout[-1500:]})
            # Per combination, not at the end: the first run of this hit the
            # timeout mid-set, and an uncommitted report is a run paid for twice.
            reports.commit()
            print(f"committed {key}")
    return {"ok": not failures, "failures": failures, "results": results}


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


@app.function(
    image=eval_image,
    gpu=GPU,
    volumes={"/corpus": corpus, "/reports": reports},
    cpu=4,
    memory=16384,
    timeout=7200,
    max_containers=1,
)
def evaluate(suite: str = "serving_detail", engine: str = "direct",
             score: bool = True, limit: int | None = None,
             score_only: bool = False, git_sha: str = "",
             model: str = MODEL, out_dir: str = "/reports/e2e") -> dict:
    """Run the end-to-end harness here instead of on a laptop.

    The reference laptop idles at 88-91 C: a paced run of 80 cases takes about
    an hour and a half, and twice a thermal guard has stopped one outright. The
    same work on a rented T4 takes minutes, costs a few cents, and the pacing
    constraint becomes COST rather than heat - `--no-pace` below is not
    carelessness, it is the absence of a thermal problem worth pacing around.

    Reports land on the `rag-wikipedia-reports` volume, so a run survives the
    container that produced it:

        SHA=$(git rev-parse HEAD)
        modal run infra/modal/demo.py::evaluate --suite serving_detail --git-sha $SHA
        modal volume get rag-wikipedia-reports /e2e ./reports

    `--git-sha` is not decoration. The image carries the code without the
    `.git` that produced it, so the first cloud run recorded `git_sha: null`
    and its numbers could not be tied to a commit. The caller knows the sha;
    it is passed in and `app.core.quality._git_sha` falls back to it.

    `--model` answers a question a client always asks - "would you really ship a
    3B model?" - with a number instead of an opinion. Only llama3.2:3b is baked
    into the image; anything else is pulled at run time and written to its own
    `--out-dir`, so a comparison cannot overwrite the served model's report:

        modal run infra/modal/demo.py::evaluate --suite serving_detail
            --model llama3.1:8b --out-dir /reports/e2e-llama31-8b --git-sha $SHA
    """
    import json
    import subprocess

    _start_qdrant()
    subprocess.Popen(
        ["ollama", "serve"],
        env=os.environ | {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_KEEP_ALIVE": "-1"},
    )
    if not _wait_for("http://127.0.0.1:6333/readyz", 600):
        return {"ok": False, "error": "qdrant did not come up"}

    if model != MODEL:
        # Pulled, not baked: baking every model a comparison might want would
        # make the demo's cold start pay for weights no visitor needs.
        if not _wait_for("http://127.0.0.1:11434/api/version", 300):
            return {"ok": False, "error": "ollama did not come up"}
        pulled = subprocess.run(["ollama", "pull", model], check=False,
                                capture_output=True, text=True)
        if pulled.returncode != 0:
            return {"ok": False, "error": f"could not pull {model}",
                    "stderr_tail": pulled.stderr[-1500:]}

    args = ["python", "eval/run_e2e.py", "--suite", suite, "--engine", engine,
            "--out-dir", out_dir, "--no-pace"]
    if score_only:
        args.append("--score-only")
    if not score:
        args.append("--no-nli")
    if limit:
        args += ["--limit", str(limit)]

    done = subprocess.run(
        args, cwd="/srv/backend", check=False, capture_output=True, text=True,
        env=os.environ | {
            "PYTHONPATH": "/srv/backend",
            "PYTHONIOENCODING": "utf-8",
            "QDRANT_URL": "http://127.0.0.1:6333",
            "OLLAMA_URL": "http://127.0.0.1:11434",
            "COLLECTION": COLLECTION,
            "PROFILE": "real",
            "LLM_MODEL": model,
            # Provenance for a checkout-less container: both the generating and
            # the scoring side read it, so one run has one commit attached.
            "RAG_GIT_SHA": git_sha,
        },
    )
    reports.commit()

    report_path = Path(out_dir) / f"{suite}.{engine}.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    if not git_sha:
        print("WARNING: no --git-sha given; this report names no commit")
    return {
        "ok": done.returncode == 0,
        "model": model,
        "git_sha": ((report or {}).get("provenance") or {}).get("git_sha"),
        "returncode": done.returncode,
        "stdout_tail": done.stdout[-4000:],
        "stderr_tail": done.stderr[-2000:],
        "metrics": (report or {}).get("metrics"),
        "answers": (report or {}).get("answers"),
    }
