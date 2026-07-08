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


def slot_requires_key(provider: str, model: str) -> bool:
    """Return True when a single MoA slot points at a paid model on a provider
    that needs an API key.

    Free-tier providers (openrouter/nous/novita) only need a key for their
    *paid* models — a ``:free`` model is fine without one. Any other real
    ``api_key`` provider needs a key for everything it serves.
    """
    pslug = str(provider or "").strip().lower()
    if not pslug or pslug == "moa":
        return False
    if looks_like_free_model(model):
        return False
    if pslug in _FREE_WITHOUT_KEY_PROVIDERS:
        return True
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY

        pconfig = PROVIDER_REGISTRY.get(pslug)
        return pconfig is not None and getattr(pconfig, "auth_type", None) == "api_key"
    except Exception:
        return False


def _iter_preset_slots(preset: Any):
    """Yield every (provider, model) slot of a MoA preset (references + aggregator)."""
    if not isinstance(preset, dict):
        return
    slots = list(preset.get("reference_models") or [])
    aggregator = preset.get("aggregator")
    if isinstance(aggregator, dict):
        slots.append(aggregator)
    for slot in slots:
        if isinstance(slot, dict):
            yield str(slot.get("provider") or ""), str(slot.get("model") or "")


def _usable_key_present(provider: str) -> bool:
    """Return True when a usable API key is resolvable for ``provider``."""
    pslug = str(provider or "").strip().lower()
    if not pslug:
        return False
    try:
        from hermes_cli.auth import (
            PROVIDER_REGISTRY,
            _resolve_api_key_provider_secret,
            has_usable_secret,
        )

        pconfig = PROVIDER_REGISTRY.get(pslug)
        if pconfig is not None:
            token, _source = _resolve_api_key_provider_secret(pslug, pconfig)
            if has_usable_secret(token):
                return True
    except Exception:
        pass
    # Some free-tier providers (openrouter/nous/novita) aren't registered as
    # api_key providers in PROVIDER_REGISTRY, so the lookup above finds nothing
    # even when the gateway resolves them at call time from a conventional
    # ``<PROVIDER>_API_KEY`` environment variable. Fall back to that so
    # runnability detection matches the gateway — otherwise a paid MoA preset
    # on such a provider would be reported unrunnable and blocked despite a key
    # being configured.
    import os

    env_var = f"{pslug.upper().replace('-', '_')}_API_KEY"
    return bool(os.environ.get(env_var, "").strip())


def moa_preset_missing_key(preset: Any) -> str | None:
    """Return the provider slug a preset needs a key for but lacks, else None.

    ``None`` means the preset is runnable with the current credentials (either
    it uses only free models, or every paid provider it references has a usable
    key). A non-empty return is the first blocking provider — handy for both
    picker gating and error messages.
    """
    for provider, model in _iter_preset_slots(preset):
        if slot_requires_key(provider, model) and not _usable_key_present(provider):
            return str(provider).strip().lower()
    return None


def unavailable_moa_presets(moa_config: Any) -> list[str]:
    """Return the sorted names of MoA presets that can't run without a key.

    Fails open: any unexpected error yields an empty list so the picker never
    hides a preset it shouldn't.
    """
    try:
        presets = (moa_config or {}).get("presets")
        if not isinstance(presets, dict):
            return []
        blocked = [
            str(name)
            for name, preset in presets.items()
            if moa_preset_missing_key(preset) is not None
        ]
        return sorted(blocked)
    except Exception:
        return []
