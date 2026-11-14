"""Shared MariaDB connection args from a DB_URL like mysql://user:pass@host:3306/db?ssl=true"""
from urllib.parse import parse_qs, unquote, urlparse

import certifi


def connect_args(url: str) -> dict:
    u = urlparse(url)
    args = dict(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
                password=unquote(u.password or ""), database=u.path.lstrip("/"), autocommit=True, charset="utf8mb4")
    if parse_qs(u.query).get("ssl", ["false"])[0].lower() in ("1", "true", "required"):
        args["ssl"] = {"ca": certifi.where()}  # pymysql only verifies the server cert when given a CA
    return args
