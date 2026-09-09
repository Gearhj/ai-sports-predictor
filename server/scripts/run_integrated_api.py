"""Serve the integrated AISP API for the primary frontend, owning ONE scheduled job.

    python -m scripts.run_integrated_api            # 127.0.0.1:8018

WHY THIS EXISTS
---------------
The supervised 8010 process runs pre-merge code with the full scheduler
(daily pipeline, finalization, shadow experiments, NFL bundle import, market
observers). It cannot be restarted from an unelevated shell. The primary
frontend on 5173 therefore reads the integrated code from this process.

This process is deliberately NOT a second owner of those jobs: it disables
every job family and keeps only the flag-gated research-artifact consumer
(``consume-model-exchange-research-artifacts``), which imports COMPLETED local
Model Exchange artifacts. It makes no provider request and executes no model.

RETIRING IT
-----------
When the supervised 8010 process is restarted onto the merged code with
``MODEL_EXCHANGE_AUTO_IMPORT_ENABLED=true`` in the root ``.env``, stop this
process and repoint ``.env.local`` to 8010; the consumer job then has one owner
again with no code change.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
os.chdir(SERVER)
sys.path.insert(0, str(SERVER))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# Single-purpose scheduler owner: research consumer only.
os.environ["SCHEDULER_ENABLED"] = "true"
os.environ["SCHEDULER_CORE_JOBS_ENABLED"] = "false"
os.environ["MODEL_EXCHANGE_AUTO_IMPORT_ENABLED"] = "true"
os.environ.setdefault("MODEL_EXCHANGE_IMPORT_INTERVAL_MINUTES", "15")
# Every other job family stays with the supervised process.
os.environ["EVENT_RELATIVE_SCHEDULER_ENABLED"] = "false"
os.environ["LIVE_SHADOW_ENABLED"] = "false"
os.environ["NFL_SHADOW_ENABLED"] = "false"
os.environ["NFL_SHADOW_PRODUCER_ENABLED"] = "false"
os.environ["NFL_SHADOW_ODDS_ENABLED"] = "false"
os.environ["ODDS_POLLING_ENABLED"] = "false"
os.environ["PREDICTION_MARKET_OBSERVER_ENABLED"] = "false"
os.environ["CORS_ORIGINS"] = "http://localhost:5173,http://127.0.0.1:5173"

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("AISP_INTEGRATED_PORT", "8018")),
                log_level="warning")
