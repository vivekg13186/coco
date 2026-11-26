"""Coco engine: REST API, SQLite store and run worker around coco-core."""
from .api import create_app
from .settings import Settings

__version__ = "0.1.0"
__all__ = ["create_app", "Settings"]
