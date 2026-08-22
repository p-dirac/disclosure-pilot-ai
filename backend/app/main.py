"""
Financial Report Generator - FastAPI Backend
"""
import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from app.api import auth, report_10q, report_10k
from app.core.database import engine
from app.core.config import settings
import logging
import sys

app = FastAPI(title="Disclosure Pilot AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = logging.getLogger()

def initLogger():
    """Initialize logger with file handler
    """      
    try:
        print("App initLogger")
        #
        formatter = logging.Formatter(fmt="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d - %(funcName)s()] %(message)s", datefmt='%Y-%m-%d %H:%M:%S')
        #
        # log to stdout
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        #
        # to record to logger, must include logger.setLevel
        logger.setLevel(logging.INFO)
    except Exception as e:
        print(f"Control.initLogger raised an exception: {e}")
        raise Exception(f"Control.initLogger exception: {str(e)}") 

initLogger()
# NOTE: DB_HOST / OLLAMA_HOST / OLLAMA_BASE_URL no longer need to be a LAN
# IP address. That requirement was specific to the old Podman container
# deployment, where "localhost" inside a container resolves to the
# container itself rather than the Windows host running Postgres/Ollama.
# Now that containers have been removed in favor of running natively on
# Windows, localhost/127.0.0.1 is the correct, expected value.
db_host = os.environ.get('DB_HOST', 'localhost')
logger.info(f"DB_HOST: {db_host}")
#
ollama_host = os.getenv("OLLAMA_HOST", "localhost")
logger.info(f"OLLAMA_HOST: {ollama_host}")

ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
logger.info(f"OLLAMA_BASE_URL: {ollama_url}")

appio_dir   = settings.WIN_APPIO_DIR
logger.info(f"WIN_APPIO_DIR: {appio_dir}")

input_dir   = settings.DATA_USER_INPUT_DIR
logger.info(f"DATA_USER_INPUT_DIR: {input_dir}")

reports_dir   = settings.REPORTS_DIR
logger.info(f"REPORTS_DIR: {reports_dir}")

data_10k_dir   = settings.DATA_10K_DIR
logger.info(f"DATA_10K_DIR: {data_10k_dir}")

base_10q_dir   = settings.BASE_10Q 
logger.info(f"BASE_10Q: {base_10q_dir}")

# ── API routers (registered first — before the SPA catch-all) ─────────────────
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(report_10q.router, prefix="/api/10q", tags=["10-Q"])
app.include_router(report_10k.router, prefix="/api/10k", tags=["10-K"])

# ── Serve React frontend ──────────────────────────────────────────────────────
# Inside the container the Vite build lands at /app/frontend/dist.
# Override with FRONTEND_DIST env var if needed.
# During local dev (Vite on port 5173) this directory won't exist and the
# block is skipped — the Vite dev server handles the frontend instead.
FRONTEND_DIST = os.environ.get("FRONTEND_DIST", "/app/frontend/dist")
FRONTEND_INDEX = os.path.join(FRONTEND_DIST, "index.html")


def _index_response() -> FileResponse:
    """Return the React index.html — the login page on first load."""
    return FileResponse(FRONTEND_INDEX, media_type="text/html")


if os.path.isdir(FRONTEND_DIST):
    # Serve Vite-generated /assets/* (JS / CSS bundles)
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    async def serve_root():
        """Serve the SPA shell (React login page)."""
        return _index_response()

    @app.get("/{full_path:path}", response_class=FileResponse, include_in_schema=False)
    async def serve_spa(full_path: str):
        """
        SPA catch-all.
        - If the path maps to a real file in dist/ (e.g. favicon.ico), serve it.
        - Otherwise return index.html so React Router handles client-side routing.
        - API paths are never intercepted (router registration order guarantees
          /api/* routes are matched first, but we guard here too).
        """
        if full_path.startswith("api/"):
            return HTMLResponse(status_code=404, content="Not found")

        candidate = os.path.join(FRONTEND_DIST, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)

        return _index_response()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
