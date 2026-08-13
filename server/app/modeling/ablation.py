"""Predeclared feature, recent-window, and lifecycle ablation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from app.modeling.schema import FrozenFeatureSchema
from app.modeling.temporal import LabeledGame


@dataclass(frozen=True)
class AblationSpec:
    name: str
    excluded_families: tuple[str, ...] = ()
    excluded_features: tuple[str, ...] = ()
    maximum_recent_window_days: int | None = None


def apply_ablation(schema: FrozenFeatureSchema, ablation: AblationSpec) -> FrozenFeatureSchema:
    excluded_names = set(ablation.excluded_features)
    if ablation.maximum_recent_window_days is not None:
        excluded_names.update(
            feature.name
            for feature in schema.features
            if feature.recent_window_days is not None
            and feature.recent_window_days > ablation.maximum_recent_window_days
        )
    return schema.without(
        families=ablation.excluded_families,
        names=tuple(sorted(excluded_names)),
        version_suffix=ablation.name,
    )


def feature_family_ablations(
    schema: FrozenFeatureSchema,
    *,
    protected_families: Sequence[str] = ("rating", "team"),
) -> tuple[AblationSpec, ...]:
    families = tuple(dict.fromkeys(feature.family for feature in schema.features))
    protected = set(protected_families)
    return (
        AblationSpec("full"),
        *(AblationSpec(f"without_{family}", excluded_families=(family,)) for family in families if family not in protected),
    )


def single_feature_ablations(schema: FrozenFeatureSchema) -> tuple[AblationSpec, ...]:
    return tuple(
        AblationSpec(f"without_{feature.name}", excluded_features=(feature.name,))
        for feature in schema.features
    )


def recent_window_ablations(schema: FrozenFeatureSchema) -> tuple[AblationSpec, ...]:
    windows = sorted({feature.recent_window_days for feature in schema.features if feature.recent_window_days is not None})
    if not windows:
        return (AblationSpec("full"),)
    return (
        AblationSpec("without_recent", excluded_families=("recent",)),
        *(AblationSpec(f"recent_up_to_{window}d", maximum_recent_window_days=window) for window in windows),
        AblationSpec("full"),
    )


def lifecycle_ablations() -> tuple[AblationSpec, ...]:
    return (
        AblationSpec("without_lifecycle_features", excluded_families=("lifecycle",)),
        AblationSpec("full"),
    )


def lifecycle_cohorts(rows: Iterable[LabeledGame]) -> dict[str, tuple[LabeledGame, ...]]:
    grouped: dict[str, list[LabeledGame]] = {}
    for row in rows:
        grouped.setdefault(row.lifecycle, []).append(row)
    return {
        lifecycle: tuple(sorted(items, key=lambda item: (item.event_at, item.game_id)))
        for lifecycle, items in sorted(grouped.items())
    }
