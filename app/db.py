"""Shared MariaDB connection args from a DB_URL like mysql://user:pass@host:3306/db?ssl=true"""
import threading
from contextlib import contextmanager
from urllib.parse import parse_qs, unquote, urlparse

import certifi
import pymysql


def connect_args(url: str) -> dict:
    scheme, sep, rest = url.partition("://")
    if not sep or scheme not in ("mysql", "mariadb"):
        raise ValueError("DB_URL must look like mysql://user:pass@host:port/db")
    # split credentials at the LAST '@' so raw passwords containing @ # ? / : still parse
    creds, _, hostpart = rest.rpartition("@")
    user, _, password = creds.partition(":")
    u = urlparse(f"{scheme}://{hostpart}")
    if not u.hostname:
        raise ValueError("DB_URL has no host")  # never fall back to pymysql's default localhost
    args = dict(host=u.hostname, port=u.port or 3306, user=unquote(user), password=unquote(password),
                database=u.path.lstrip("/"), autocommit=True, charset="utf8mb4")
    if parse_qs(u.query).get("ssl", ["false"])[0].lower() in ("1", "true", "required"):
        args["ssl"] = {"ca": certifi.where()}  # pymysql only verifies the server cert when given a CA
    return args


_local = threading.local()


@contextmanager
def pooled(args: dict):
    """One reused connection per thread per DB. A new TLS connection to a cloud DB costs ~1.5 s."""
    key = (args["host"], args["port"], args["user"], args["database"])
    conns = _local.__dict__.setdefault("conns", {})
    c = conns.get(key)
    if c is not None:
        try:
            c.ping(reconnect=False)
        except pymysql.Error:
            c = None  # server closed it (idle timeout, restart): reconnect below
    if c is None:
        c = conns[key] = pymysql.connect(**args)
    try:
        yield c
    except BaseException:
        conns.pop(key, None)  # unknown state after an error: drop it, next call reconnects
        c.close()
        raise
