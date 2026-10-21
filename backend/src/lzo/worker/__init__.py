"""Durable jobs. Domain modules register handlers explicitly at process startup."""
from lzo.worker.service import register_handler

__all__ = ["register_handler"]
