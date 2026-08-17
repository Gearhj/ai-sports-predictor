"""Revision-safe command package for the package-only bridge validator."""

from app.gen2.exchange.identity_bridge.validator import main, validate_identity_bridge

__all__ = ["main", "validate_identity_bridge"]

