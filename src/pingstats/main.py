"""ASGI entry point for Uvicorn."""

from pingstats.app import create_app

app = create_app()
