"""Declarative base shared by every ORM entity."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for canonical application persistence models."""
