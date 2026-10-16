"""Configuration invariants.

``synthetic_evm/config.py`` is the file users edit to tune the portfolio, so
these checks keep an edit self-consistent: probabilities that must be
distributions are, ranges are ordered, and every scenario speaks the same
vocabulary of schedule behaviors the engine understands.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from synthetic_evm import SCENARIOS, config
from tests.helpers import G_KINDS


def test_wp_duration_mix_is_a_probability_distribution_over_whole_months():
    assert len(config.WP_DURATION_MONTHS) == len(config.WP_DURATION_PROBS)
    assert math.isclose(sum(config.WP_DURATION_PROBS), 1.0, abs_tol=1e-9)
    assert all(p > 0 for p in config.WP_DURATION_PROBS)
    assert all(isinstance(m, int) and m >= 1 for m in config.WP_DURATION_MONTHS)


def test_tail_specs_name_the_four_delay_draws_and_are_right_skewed():
    assert set(config.G_RIGHT_TAIL) == {
        "minor_delay",
        "slow_slip",
        "recover_early_slip",
        "stall_length",
    }
    for name, spec in config.G_RIGHT_TAIL.items():
        # Beta(a, b) with a < b puts most mass near the low end.
        assert 0 < spec["a"] < spec["b"], name
        assert spec["low"] >= 0 and spec["span"] > 0, name


def test_wbs_logic_profiles_include_the_unlinked_archetype():
    profiles = config.WBS_LOGIC_PROFILES
    assert profiles["parallel"]["link_probability"] == 0.0
    assert all(p["weight"] >= 0 for p in profiles.values())
    assert sum(p["weight"] for p in profiles.values()) > 0
    assert all(0.0 <= p["link_probability"] <= 1.0 for p in profiles.values())


def test_every_scenario_names_exactly_the_known_schedule_behaviors():
    for name, cfg in SCENARIOS.items():
        assert set(cfg.g_probs) == set(G_KINDS), name
        assert all(p >= 0 for p in cfg.g_probs.values()), name
        assert sum(cfg.g_probs.values()) > 0, name


def test_scenario_parameters_are_in_valid_ranges():
    for name, cfg in SCENARIOS.items():
        shock_low, shock_high = cfg.shock_range
        rework_low, rework_high = cfg.rework_range
        assert cfg.g_tail_scale > 0, name
        assert cfg.cost_mean > 0 and cfg.cost_sd >= 0, name
        assert cfg.shock_events >= 0 and 0 <= shock_low <= shock_high, name
        assert cfg.drift_sd >= 0, name
        assert 0 <= cfg.rework_prob <= 1 and 0 <= rework_low <= rework_high, name
        assert cfg.recovery_premium >= 0, name


def test_tail_scale_ordering_matches_the_scenario_descriptions():
    nominal = SCENARIOS["nominal"].g_tail_scale
    assert SCENARIOS["combined_overrun"].g_tail_scale > nominal
    assert SCENARIOS["schedule_slip"].g_tail_scale > nominal
    assert SCENARIOS["ahead_of_plan"].g_tail_scale < nominal
    assert SCENARIOS["efficient_execution"].g_tail_scale < nominal


def test_scenario_config_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SCENARIOS["nominal"].g_tail_scale = 2.0
