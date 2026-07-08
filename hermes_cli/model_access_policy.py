"""Shared model access policy helpers for free-vs-paid gating.

Policy:
- If a provider requires an API key and no usable key is present, only models
  that are clearly free can be used.
- Paid models must be blocked from selection/execution until a key is present.
"""

from __future__ import annotations

from typing import Any


_FREE_MODEL_HINTS: tuple[str, ...] = (
    ":free",
    "free-chat",
    "free-max",
    "/free",
)
_FREE_WITHOUT_KEY_PROVIDERS: set[str] = {"openrouter", "nous", "novita"}


def looks_like_free_model(model_id: str) -> bool:
    mid = str(model_id or "").strip().lower()
    if not mid:
        return False
    return any(hint in mid for hint in _FREE_MODEL_HINTS)


def pricing_marks_free(pricing_entry: Any) -> bool:
    if not isinstance(pricing_entry, dict):
        return False
    if isinstance(pricing_entry.get("free"), bool):
        return bool(pricing_entry.get("free"))
    try:
        prompt = float(pricing_entry.get("prompt", "1"))
        completion = float(pricing_entry.get("completion", "1"))
        return prompt == 0 and completion == 0
    except (TypeError, ValueError):
        return False


def enforce_paid_model_requires_key(
    *,
    provider: str,
    model: str,
    api_key_present: bool,
) -> None:
    """Raise ValueError when a paid model is selected without an API key."""
    pslug = str(provider or "").strip().lower()
    if pslug not in _FREE_WITHOUT_KEY_PROVIDERS:
        return
    if api_key_present:
        return
    if looks_like_free_model(model):
        return
    raise ValueError(
        f"Model '{model}' on provider '{provider}' requires an API key. "
        "Choose a free-chat/free-max/:free model or add a provider key."
    )
