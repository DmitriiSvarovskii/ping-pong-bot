"""Deployment guard for Uvicorn's optional WebSocket runtime."""

import websockets
from uvicorn.config import Config


def test_uvicorn_auto_selects_installed_websocket_protocol() -> None:
    """Fail before deployment when no Uvicorn WebSocket backend is installed."""
    assert websockets.__version__

    config = Config("pingstats.main:app", ws="auto")
    config.load()

    protocol = config.ws_protocol_class
    assert protocol is not None
    assert protocol.__module__.startswith("uvicorn.protocols.websockets.")
