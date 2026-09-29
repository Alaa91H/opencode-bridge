"""Owner-scoped model and reasoning-level selection over the live OpenCode catalog.

The service is the single seam that turns a short Telegram button payload into a
validated ``provider/model`` + reasoning variant. It never invents identifiers:
every model and every variant offered to a user is read back from current live
catalog metadata, so a stale or paid model can never be selected from a button.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bridge.domain.policies.user_preferences import UserPreferences
from model_catalog import (
    MAX_PERFORMANCE_VARIANTS,
    model_variant_ids,
    ranked_zen_general_model_ids,
)

log = logging.getLogger("opencode_bridge.model_selection")
AUTO_SELECTION_INDEX = 0
MAX_PICKER_MODELS = 40

VARIANT_LABELS = {
    "max": "Max — أقصى استدلال",
    "xhigh": "Xhigh — استدلال عالٍ جدًا",
    "high": "High — استدلال عالٍ",
    "medium": "Medium — استدلال متوسط",
    "low": "Low — استدلال منخفض",
    "minimal": "Minimal — أقل استدلال",
    "none": "None — بدون استدلال",
}
MODEL_LABELS = {
    "none": "بدون استدلال إضافي",
    "minimal": "أقل استدلال",
    "low": "استدلال منخفض",
    "medium": "استدلال متوسط",
    "high": "استدلال عالٍ",
    "xhigh": "استدلال عالٍ جدًا",
    "max": "أقصى استدلال",
}


class ModelSelectionError(RuntimeError):
    """Raised when a requested selection is not available in the live catalog."""


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    label: str
    variants: tuple[str, ...]


@dataclass(frozen=True)
class VariantChoice:
    model_id: str
    variant_id: str | None
    label: str
    strongest_variant: str | None


@dataclass(frozen=True)
class ModelSelectionView:
    """Everything a renderer needs to draw the picker for one owner."""

    current_model: str | None
    current_variant: str | None
    preferred_model: str | None
    pinned: bool
    choices: tuple[ModelChoice, ...]

    def model_at(self, index: int) -> ModelChoice | None:
        if not 0 <= index < len(self.choices):
            return None
        return self.choices[index]

    def variant_choice(self, model_index: int, variant_index: int) -> VariantChoice | None:
        choice = self.model_at(model_index)
        if choice is None:
            return None
        if not 0 <= variant_index <= len(choice.variants):
            return None
        variant_id = None if variant_index == 0 else choice.variants[variant_index - 1]
        return VariantChoice(
            model_id=choice.model_id,
            variant_id=variant_id,
            label=VARIANT_LABELS.get((variant_id or "").casefold(), variant_id or "الافتراضي"),
            strongest_variant=choice.variants[0] if choice.variants else None,
        )


def _display_label(qualified_model_id: str) -> str:
    model_id = qualified_model_id.split("/", 1)[-1] if "/" in qualified_model_id else qualified_model_id
    return model_id.replace("-contributor-free", "").strip("-") or qualified_model_id


def _ordered_variants(qualified_model_id: str, providers: object) -> tuple[str, ...]:
    """Order live variants strongest-first without inventing any identifier."""
    available = {value.casefold(): value for value in model_variant_ids(providers, qualified_model_id)}
    ordered: list[str] = []
    for preferred in MAX_PERFORMANCE_VARIANTS:
        match = available.pop(preferred, None)
        if match is not None:
            ordered.append(match)
    ordered.extend(available[key] for key in sorted(available))
    return tuple(ordered)


class ModelSelectionService:
    def __init__(
        self,
        *,
        catalog_provider,
        preferences,
        session_model_reader,
        session_model_writer,
        variant_resolver,
        audit_write=None,
    ) -> None:
        self.catalog_provider = catalog_provider
        self.preferences = preferences
        self.session_model_reader = session_model_reader
        self.session_model_writer = session_model_writer
        self.variant_resolver = variant_resolver
        self.audit_write = audit_write

    async def pinned_model(self, owner_id: str) -> str | None:
        preferences = await self.preferences.get(owner_id)
        return preferences.model_preference if preferences.model_pinned else None

    async def _resolve_selection(self, owner_id: str) -> str | None:
        session = await self.session_model_reader(owner_id)
        current = getattr(session, "model", None) if session is not None else None
        if current:
            return str(current)
        preferences = await self.preferences.get(owner_id)
        return preferences.model_preference

    async def view(self, owner_id: str) -> ModelSelectionView:
        providers = await self.catalog_provider()
        current_model = await self._resolve_selection(owner_id)
        current_variant = None
        if current_model:
            current_variant = await self.variant_resolver(owner_id, current_model)
        preferences = await self.preferences.get(owner_id)
        ranked = ranked_zen_general_model_ids(providers)[:MAX_PICKER_MODELS]
        choices = tuple(
            ModelChoice(
                model_id=model_id,
                label=_display_label(model_id),
                variants=_ordered_variants(model_id, providers),
            )
            for model_id in ranked
        )
        return ModelSelectionView(
            current_model=current_model,
            current_variant=current_variant,
            preferred_model=preferences.model_preference,
            pinned=preferences.model_pinned,
            choices=choices,
        )

    async def auto(self, owner_id: str) -> ModelSelectionView:
        """Clear any owner pin and fall back to the automatic catalog choice."""
        preferences = await self.preferences.get(owner_id)
        await self.preferences.update(
            owner_id,
            model_preference=None,
            model_variant=None,
            model_pinned=False,
        )
        automatic = await self._automatic_model(frozenset())
        if automatic is not None:
            await self._apply_model(owner_id, automatic, None, reason="user_selected_auto")
        self._audit(
            "model_selection_auto",
            "cleared",
            owner_id=owner_id,
            details={"cleared_model": preferences.model_preference},
        )
        return await self.view(owner_id)

    async def select_model(self, owner_id: str, model_index: int) -> ModelSelectionView:
        providers = await self.catalog_provider()
        ranked = ranked_zen_general_model_ids(providers)[:MAX_PICKER_MODELS]
        if not 0 <= model_index < len(ranked):
            raise ModelSelectionError("this model is no longer offered by the live catalog")
        model_id = ranked[model_index]
        preferences = await self.preferences.get(owner_id)
        variants = _ordered_variants(model_id, providers)
        # Keep an already chosen level when the model is still valid for it.
        variant = preferences.model_variant if preferences.model_variant in variants else None
        await self._persist(owner_id, model_id, variant, pinned=True, reason="user_selected_model")
        return await self.view(owner_id)

    async def select_variant(self, owner_id: str, model_index: int, variant_index: int) -> ModelSelectionView:
        providers = await self.catalog_provider()
        ranked = ranked_zen_general_model_ids(providers)[:MAX_PICKER_MODELS]
        if not 0 <= model_index < len(ranked):
            raise ModelSelectionError("this model is no longer offered by the live catalog")
        model_id = ranked[model_index]
        variants = _ordered_variants(model_id, providers)
        if variant_index == AUTO_SELECTION_INDEX:
            variant = None
        elif 0 <= variant_index - 1 < len(variants):
            variant = variants[variant_index - 1]
        else:
            raise ModelSelectionError("this reasoning level is no longer offered by the live catalog")
        await self._persist(owner_id, model_id, variant, pinned=True, reason="user_selected_variant")
        return await self.view(owner_id)

    async def _automatic_model(self, excluded: frozenset[str]) -> str | None:
        providers = await self.catalog_provider()
        return next(
            (model_id for model_id in ranked_zen_general_model_ids(providers) if model_id not in excluded),
            None,
        )

    async def _apply_model(self, owner_id: str, model_id: str, previous_model: str | None, *, reason: str) -> None:
        session = await self.session_model_reader(owner_id)
        if session is not None:
            await self.session_model_writer(owner_id, session.opencode_session_id, model_id)
        self._audit(
            "model_selection_applied",
            "changed",
            owner_id=owner_id,
            details={"from_model": previous_model, "to_model": model_id, "reason": reason},
        )

    async def _persist(
        self,
        owner_id: str,
        model_id: str,
        variant: str | None,
        *,
        pinned: bool,
        reason: str,
    ) -> None:
        preferences = await self.preferences.get(owner_id)
        previous_model = preferences.model_preference
        if not pinned:
            await self.preferences.update(
                owner_id,
                model_preference=model_id,
                model_variant=variant,
                model_pinned=False,
            )
        else:
            await self.preferences.update(
                owner_id,
                model_preference=model_id,
                model_variant=variant,
                model_pinned=True,
            )
        if previous_model != model_id:
            await self._apply_model(owner_id, model_id, previous_model, reason=reason)

    def _audit(self, event: str, outcome: str, *, owner_id: str, details: dict) -> None:
        if self.audit_write is None:
            return
        try:
            self.audit_write(event, outcome, actor_id=owner_id, details=details)
        except Exception as exc:  # audit must never break selection
            log.info("تعذر تسجيل اختيار النموذج: %s", type(exc).__name__)


__all__ = [
    "AUTO_SELECTION_INDEX",
    "MODEL_LABELS",
    "VARIANT_LABELS",
    "ModelChoice",
    "ModelSelectionError",
    "ModelSelectionService",
    "ModelSelectionView",
    "UserPreferences",
    "VariantChoice",
]
