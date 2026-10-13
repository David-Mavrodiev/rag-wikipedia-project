"""The evaluation worker, online: the laptop stops being part of the harness.

    modal deploy infra/modal/worker.py

Today every suite runs on one laptop, paced by GPU temperature, and a 96 C guard
has stopped a run twice. The pacing constraint on Modal is cost, not heat, and a
run that takes about 1.5 h paced here takes minutes on a T4 - which is what makes
"measure every change" a habit rather than an ambition.

Functions:

  audit     nightly, on the FIXTURE corpus (2,422 vectors, committed): the same
            eval/audit.py CI runs, so a gate that moves is noticed the next
            morning rather than at the next release.
  evaluate  on demand: retrieval suites, and the end-to-end run when `e2e=True`.
            Writes its reports to a Modal volume; the caller collects them.
  rescore   on demand: run_e2e.py --score-only, the 25-minute CPU job that every
            judge change needs.
  wake      an HTTPS endpoint, bearer token, so CI or a PR comment can start one.

The GPU work goes to `rag-wikipedia-inference` (capped at one container); these
are small CPU containers and cost a fraction of a cent per run.

NOT yet exercised against a live Modal account.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[2]

image = (
    modal.Image.debian_slim(python_version="3.12")
    .env({"UV_TORCH_BACKEND": "cpu"})
    .uv_pip_install(
        "fastapi>=0.117", "httpx>=0.28", "qdrant-client>=1.12", "sentence-transformers>=3.0",
        "pydantic-settings>=2.5", "torch>=2.4",
    )
    .add_local_dir(ROOT / "backend", "/root/backend")
    .add_local_dir(ROOT / "scripts", "/root/scripts")
)

with image.imports():
    from fastapi import Request
    from fastapi.responses import JSONResponse

app = modal.App("rag-wikipedia-worker")
SECRETS = [modal.Secret.from_name("rag-worker")]
REPORTS = modal.Volume.from_name("rag-wikipedia-reports", create_if_missing=True)
VOLUMES = {"/reports": REPORTS}


def _run(args: list[str], cwd: str = "/root/backend") -> dict:
    """Run one harness command and return what it printed, with its exit code."""
    import subprocess

    env = os.environ | {"PYTHONPATH": "/root/backend", "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    REPORTS.commit()
    return {"args": args, "returncode": done.returncode,
            "stdout": done.stdout[-8000:], "stderr": done.stderr[-4000:]}


@app.function(image=image, secrets=SECRETS, volumes=VOLUMES, cpu=2, memory=4096,
              timeout=3600, schedule=modal.Cron("30 3 * * *", timezone="Europe/Paris"))
def audit() -> dict:
    """The fixture-corpus audit, nightly. Fails loudly: a moved gate is the point."""
    return _run(["python", "eval/audit.py"])


@app.function(image=image, secrets=SECRETS, volumes=VOLUMES, cpu=2, memory=8192,
              timeout=7200, max_containers=1)
def evaluate(suites: list[str] | None = None, e2e: bool = False) -> dict:
    suites = suites or ["golden", "holdout", "adversarial", "detail"]
    results = [_run(["python", "eval/run_eval.py", "--suite", suite]) for suite in suites]
    if e2e:
        results.append(_run(["python", "eval/run_e2e.py", "--suite", "serving_detail",
                             "--out-dir", "/reports/e2e", "--no-pace"]))
    return {"runs": results}


@app.function(image=image, secrets=SECRETS, volumes=VOLUMES, cpu=4, memory=8192,
              timeout=7200, max_containers=1)
def rescore(suite: str = "serving_detail", assemble: int = 0) -> dict:
    """Re-judge saved answers with the current code - no generation, no GPU."""
    args = ["python", "eval/run_e2e.py", "--suite", suite, "--score-only",
            "--out-dir", "/reports/e2e"]
    if assemble:
        args += ["--assemble", str(assemble)]
    return _run(args)


@app.function(image=image, secrets=SECRETS, cpu=0.125, memory=256)
@modal.fastapi_endpoint(method="POST")
def wake(request: Request):
    """POST with `Authorization: Bearer <WORKER_WAKE_TOKEN>`: starts a run, answers at once."""
    expected = f"Bearer {os.environ['WORKER_WAKE_TOKEN']}"
    if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
        return JSONResponse({"error": "unauthorised"}, status_code=401)
    evaluate.spawn()
    return JSONResponse({"status": "evaluating"}, status_code=202)
