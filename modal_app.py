"""T15: the FastAPI app on Modal. Deploy: `uv run modal deploy modal_app.py`.

Needs a Modal secret named `aimo-api` with VALIO_API_KEY, DB_URL (MariaDB Cloud, `?ssl=true`) and
CORS_ORIGINS (the Vercel URL). Without DB_URL the app falls back to in-memory stores.
The image downloads the data files and builds the SQLite catalog (data/catalog.db) at build time.
"""
import modal

secret = modal.Secret.from_name("aimo-api")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_sync()
    # data files (87 MB) are downloaded at build time; this layer is cached until the script changes
    .add_local_file("scripts/download_data.py", "/root/scripts/download_data.py", copy=True)
    .run_commands("cd /root && python scripts/download_data.py", secrets=[secret])
    # copy=True so the catalog can be built from this code at build time (P1's loader -> /root/data/catalog.db)
    .add_local_python_source("app", "contracts", copy=True, ignore=["**/__pycache__/**"])  # keeps fixtures/*.json
    .run_commands("cd /root && python -m app.catalog.loader")
)
app = modal.App("aimo-trusted-trade", image=image)


# max_containers=1: approve/reject are serialised by an in-process lock (see app/orders/service.py)
# min_containers=1: keep one warm for the demo; a cold start + first cloud-DB connections take ~10 s
@app.function(secrets=[secret], min_containers=1, max_containers=1)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def api():
    from app.main import app as fastapi_app

    return fastapi_app
