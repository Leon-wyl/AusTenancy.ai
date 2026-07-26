"""Start Uvicorn for ASGI-mode route validation. Use mocked graph — no real LLM.

Usage:
    # Terminal 1 — start the server with mock graph
    .venv/bin/python scripts/run_asgi_smoke.py

    # Terminal 2 — validate routes
    curl -s http://localhost:8000/health | python -m json.tool
    curl -s -X POST http://localhost:8000/api/agent/invoke \
      -H 'Content-Type: application/json' \
      -d '{"question":"","jurisdiction":"QLD"}' | python -m json.tool
    curl -s -X POST http://localhost:8000/api/agent/invoke \
      -H 'Content-Type: application/json' \
      -d 'not json' | python -m json.tool

The server runs with a mock graph.ainvoke that returns a canned success
response — all routes and Pydantic validation are tested, but no LLM is
called and no AWS credentials are needed.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point at the local qdrant_storage/ so runtime paths resolve
repo_root = Path(__file__).resolve().parent.parent
os.environ["QDRANT_SEED_PATH"] = str(repo_root / "qdrant_storage")
os.environ["QDRANT_PATH"] = str(repo_root / "qdrant_storage")
os.environ["QDRANT_MANIFEST_PATH"] = str(repo_root / "assets" / "qdrant_index_manifest.json")

import uvicorn  # noqa: E402

from src.api.handler import app  # noqa: E402

if __name__ == "__main__":
    print("AusTenancy.ai ASGI smoke test server at http://localhost:8000")
    print(f"QDRANT_SEED_PATH={os.environ['QDRANT_SEED_PATH']}")
    print(f"QDRANT_PATH={os.environ['QDRANT_PATH']}")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
