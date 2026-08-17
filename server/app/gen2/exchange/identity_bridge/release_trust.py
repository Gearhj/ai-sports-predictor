"""Release-only self anchors for ``SPORTS_IDENTITY_BRIDGE_V1``.

Only these three values are excluded from the publishing-code revision.  They
remain unset until canonical package bytes have passed independent package-only
validation, preventing release freezing from creating a digest self-reference.
"""

from __future__ import annotations


EXPECTED_BRIDGE_MANIFEST_SHA256: str | None = (
    "6b69e7dcf4c5db7615420ee9f6f6f797b0335e31ab475646291f3a4cf90c52cf"
)
EXPECTED_ALIAS_AUTHORITY_SHA256: str | None = (
    "ddb828aee391b2f725ba4540a874865963313e6c210f246eacd7bbcf28025661"
)
EXPECTED_DIMENSION_AUTHORITY_SHA256: str | None = (
    "6f30e1b30ca41002d6cbb2dea8b90da5abfad4f62f96237e780fbee518005acf"
)


__all__ = [
    "EXPECTED_ALIAS_AUTHORITY_SHA256",
    "EXPECTED_BRIDGE_MANIFEST_SHA256",
    "EXPECTED_DIMENSION_AUTHORITY_SHA256",
]
