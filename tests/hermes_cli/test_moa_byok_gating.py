"""BYOK gating for MoA presets in the model picker.

A preset whose reference/aggregator models are paid must be greyed out /
refused when the user has no usable key for that provider, while free presets
(``:free`` models) stay selectable. Covers the shared classifier in
``model_access_policy`` and its wiring into the picker row builder.
"""

from __future__ import annotations

import hermes_cli.model_access_policy as policy


FREE_PRESET = {
    "reference_models": [{"provider": "openrouter", "model": "tencent/hy3:free"}],
    "aggregator": {"provider": "openrouter", "model": "tencent/hy3:free"},
}
BYOK_PRESET = {
    "reference_models": [{"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"}],
    "aggregator": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"},
}


def test_slot_requires_key_paid_vs_free():
    assert policy.slot_requires_key("openrouter", "deepseek/deepseek-v4-flash") is True
    assert policy.slot_requires_key("openrouter", "tencent/hy3:free") is False
    # Virtual/empty providers never gate.
    assert policy.slot_requires_key("moa", "byok-code") is False
    assert policy.slot_requires_key("", "whatever") is False


def test_moa_preset_missing_key_reports_provider(monkeypatch):
    monkeypatch.setattr(policy, "_usable_key_present", lambda provider: False)
    assert policy.moa_preset_missing_key(BYOK_PRESET) == "openrouter"
    # A free preset is runnable even with no key.
    assert policy.moa_preset_missing_key(FREE_PRESET) is None


def test_moa_preset_runnable_when_key_present(monkeypatch):
    monkeypatch.setattr(policy, "_usable_key_present", lambda provider: True)
    assert policy.moa_preset_missing_key(BYOK_PRESET) is None


def test_unavailable_moa_presets_lists_only_blocked(monkeypatch):
    monkeypatch.setattr(policy, "_usable_key_present", lambda provider: False)
    cfg = {"presets": {"free-max": FREE_PRESET, "byok-code": BYOK_PRESET}}
    assert policy.unavailable_moa_presets(cfg) == ["byok-code"]


def test_unavailable_moa_presets_fails_open_on_bad_input():
    assert policy.unavailable_moa_presets(None) == []
    assert policy.unavailable_moa_presets({"presets": "nope"}) == []


def test_moa_picker_row_greys_out_byok_without_key(monkeypatch):
    import hermes_cli.config as config_mod
    from hermes_cli import inventory

    monkeypatch.setattr(policy, "_usable_key_present", lambda provider: False)
    # ``_moa_provider_row`` imports load_config locally, so patch the source.
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda *a, **k: {
            "moa": {
                "default_preset": "free-max",
                "presets": {"free-max": FREE_PRESET, "byok-code": BYOK_PRESET},
            }
        },
    )

    row = inventory._moa_provider_row()
    assert row is not None
    assert set(row["models"]) == {"free-max", "byok-code"}
    assert row["unavailable_models"] == ["byok-code"]
