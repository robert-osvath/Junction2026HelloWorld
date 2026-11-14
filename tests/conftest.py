import os

os.environ["DB_URL"] = ""  # app tests use the in-memory store, never the dev DB from .env
