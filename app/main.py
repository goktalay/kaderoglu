"""Tek sunucu, iki site: istek hangi alan adına geldiyse ona yönlendirilir.

panel.kaderoglu.com -> bilgisayar paneli (server.py)
diğer her şey       -> aile sitesi (family.py)
"""
import json
from pathlib import Path

import family
import server
import social

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text("utf-8"))
PANEL_HOSTS = {h.lower() for h in CONFIG.get("panel_hosts", ["panel.kaderoglu.com"])}
# aile sitesi: giriş zorunlu + /giris, /duvar, /social/... (app/social.py)
family_app = social.wrap(family.app)


def _host(scope) -> str:
    for k, v in scope.get("headers", []):
        if k == b"host":
            return v.decode("latin-1").split(":")[0].lower()
    return ""


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        return await family.app(scope, receive, send)
    host = _host(scope)
    target = server.app if host in PANEL_HOSTS else family_app
    await target(scope, receive, send)
