"""Database package.

Session helpers deliberately live in :mod:`app.db.session` so importing model
metadata never attempts to load a PostgreSQL driver.
"""

from app.db.base import Base

__all__ = ["Base"]
