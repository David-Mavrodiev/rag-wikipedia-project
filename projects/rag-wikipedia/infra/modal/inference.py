"""Generation on a GPU, scaled to zero: the fast half of the two-host design.

    modal secret create rag-inference GATEWAY_TOKEN=$(openssl rand -hex 32)
    modal deploy infra/modal/inference.py

Oracle serves every request for free and never goes cold, but it has no GPU, and
generation is 98.5% of an answer (measured). This is where an answer goes when
waiting would hurt - and it costs nothing while nobody is asking.

Cost levers, all here (docs/online-deployment.md §6):
* min_containers=0  - never pay for idle. An always-on T4 is about $580/month.
* max_containers=1  - a burst queues instead of burning the month's credit. This
  is a demo, not a product; JobHack uses 2 because it has paying users.
* scaledown_window  - 120 s by day, 60 s at night (Europe/Paris), set by the
  `retune` cron below. Every burst is billed as cold start + work + this window,
  so 120 s keeps a reading session warm and 300 s would spend twice as fast.
* The model is BAKED INTO THE IMAGE: a cold start loads weights from the image
  and never downloads them.
* OLLAMA_NUM_PARALLEL with @modal.concurrent: one warm GPU serves several
  visitors at once, which is what makes a shared demo cheap.

NOT yet exercised against a live Modal account: check the first `modal deploy`
output before relying on it.
"""

from __future__ import annotations

import os
import subprocess

import modal

GPU = os.environ.get("RAG_MODAL_GPU", "T4")
MODEL = os.environ.get("RAG_MODAL_MODEL", "llama3.2:3b")
DAY_WINDOW_S, NIGHT_WINDOW_S = 120, 60

image = (
    modal.Image.from_registry("ollama/ollama:0.12.3", add_python="3.12")
    .entrypoint([])
    .run_commands(
        # Bake the weights: `ollama serve` in the background just long enough to pull.
        f"(ollama serve &) && sleep 8 && ollama pull {MODEL} && pkill ollama || true",
    )
    .env({"RAG_MODAL_MODEL": MODEL})
)

app = modal.App("rag-wikipedia-inference")


@app.function(
    image=image,
    gpu=GPU,
    min_containers=0,
    max_containers=1,
    scaledown_window=DAY_WINDOW_S,
    timeout=600,
    secrets=[modal.Secret.from_name("rag-inference")],
)
@modal.concurrent(max_inputs=4)
@modal.web_server(port=11434, startup_timeout=300)
def serve() -> None:
    """Ollama itself, on the GPU. The API keeps retrieval and the refusal gate."""
    subprocess.Popen(
        ["ollama", "serve"],
        env=os.environ | {
            "OLLAMA_HOST": "0.0.0.0:11434",
            "OLLAMA_NUM_PARALLEL": "4",
            # Keep the model resident for as long as the container lives: the
            # container itself is what scales to zero.
            "OLLAMA_KEEP_ALIVE": "-1",
        },
    )


@app.function(schedule=modal.Cron("0 8,23 * * *", timezone="Europe/Paris"))
def retune() -> None:
    """Day/night scaledown window: warm while people read, frugal overnight."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hour = datetime.now(ZoneInfo("Europe/Paris")).hour
    window = DAY_WINDOW_S if 8 <= hour < 23 else NIGHT_WINDOW_S
    modal.Function.from_name("rag-wikipedia-inference", "serve").update_autoscaler(
        scaledown_window=window
    )
