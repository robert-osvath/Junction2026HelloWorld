"""T15: the FastAPI app on Modal. Deploy: `uv run modal deploy modal_app.py`.

Needs a Modal secret named `aimo-api` with VALIO_API_KEY, DB_URL (MariaDB Cloud, `?ssl=true`) and
CORS_ORIGINS (the Vercel URL). Without DB_URL the app falls back to in-memory stores.
"""
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_sync()
    .add_local_python_source("app", "contracts", ignore=["**/__pycache__/**"])  # keep contracts/fixtures/*.json
)
app = modal.App("aimo-trusted-trade", image=image)


# max_containers=1: approve/reject are serialised by an in-process lock (see app/orders/service.py)
@app.function(secrets=[modal.Secret.from_name("aimo-api")], max_containers=1)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def api():
    from app.main import app as fastapi_app

    return fastapi_app
