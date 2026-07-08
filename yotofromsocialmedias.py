"""YotoFromSocialMedias — application entry point.

Backend business logic lives in the ``app`` package (``app.routers`` holds the
FastAPI routes; the ``app.yoto_client`` module owns the Yoto REST/upload client).
This file only wires the entry point: it imports the FastAPI instance and serves
it. Run from the repo root so ``templates/``, ``static/`` and ``data/`` resolve.

    python yotofromsocialmedias.py
    # or: uvicorn yotofromsocialmedias:app --host 0.0.0.0 --port 8081
"""
import uvicorn

from app.routers import app  # FastAPI instance

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081, reload=False)
