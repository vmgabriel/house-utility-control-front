"""Identity infrastructure layer: the DRF-backed auth adapter."""

from .repository import DRFAuthRepository

__all__ = ["DRFAuthRepository"]
