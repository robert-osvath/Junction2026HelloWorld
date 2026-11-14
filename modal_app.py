"""T15: the FastAPI app on Modal.

    uv run modal run modal_app.py::build_catalog   # data + catalog + embeddings into the volume
    uv run modal deploy modal_app.py                # the API

Needs a Modal secret named `aimo-api` with VALIO_API_KEY, DB_URL (MariaDB Cloud, `?ssl=true`),
CORS_ORIGINS (the Vercel URL) and GEMINI_API_KEY (vector search). Without DB_URL the app falls back to
in-memory stores; without GEMINI_API_KEY or embeddings, search is text-only.

The catalog (data files + data/catalog.db with embeddings) lives on the `aimo-catalog` volume, mounted at
/root/data, which is the code's default data dir. Embedding is incremental (only changed products), so
rebuilds after the first (~4 min) take seconds.
"""
import modal

secret = modal.Secret.from_name("aimo-api")
catalog = modal.Volume.from_name("aimo-catalog", create_if_missing=True)
DATA = "/root/data"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_sync()
    .add_local_file("scripts/download_data.py", "/root/scripts/download_data.py")
    .add_local_python_source("app", "contracts", ignore=["**/__pycache__/**"])  # keeps contracts/fixtures/*.json
)
app = modal.App("aimo-trusted-trade", image=image)


@app.function(secrets=[secret], volumes={DATA: catalog}, timeout=1800)
def build_catalog(redownload: bool = False) -> None:
    import os
    import subprocess

    def run(*cmd, check=True):
        return subprocess.run(cmd, cwd="/root", check=check)  # separate processes: DB files closed before commit

    if redownload or not os.path.exists(f"{DATA}/products.json"):
        run("python", "scripts/download_data.py")
    run("python", "-m", "app.catalog.loader")  # keeps product_embeddings (text_hash decides re-embedding)
    # embeddings are optional (search falls back to text-only): a Gemini outage must not block deploys
    if run("python", "-m", "app.catalog.embed", check=False).returncode:
        print("WARNING: embedding incomplete; search stays text-only for unembedded products (re-run to resume)")
    catalog.commit()


# max_containers=1: approve/reject are serialised by an in-process lock (see app/orders/service.py)
# min_containers=1: keep one warm for the demo; a cold start + first cloud-DB connections take ~10 s
# shortcut: containers see the volume as of their start; after a catalog rebuild with no code change,
# run `modal app stop aimo-trusted-trade -y` before deploying so the warm container picks it up
@app.function(secrets=[secret], volumes={DATA: catalog}, min_containers=1, max_containers=1)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def api():
    from app.main import app as fastapi_app

    return fastapi_app
