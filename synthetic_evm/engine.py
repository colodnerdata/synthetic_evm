"""Latent work-package planning and simulation engine.

The public dataset intentionally contains only project totals, WBS totals, and
incremental WBS/month PV-EV-AC. Work packages exist only inside this module.

The engine works in two phases:

1. Build all latent work packages, including their schedule time warps ``g(t)``.
2. Add simple zero-lag Finish-to-Start logic for selected WBS archetypes and
   propagate dependency-constrained actual starts.
3. Simulate monthly PV/EV/AC only through the resulting project horizon.

That means the caller never chooses an arbitrary reporting horizon. A project
runs through month 60 at minimum so its entire PMB is present, and extends only
as far as needed when EV finishes late.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Tuple

import numpy as np

from .config import (
    G_RIGHT_TAIL,
    SPREAD_PROFILES,
    WBS_LOGIC_PROFILES,
    WP_DURATION_MONTHS,
    WP_DURATION_PROBS,
    ScenarioConfig,
)


@dataclass
class WorkPackagePlan:
    """Internal parameters for one latent work package.

    ``predecessor_idx`` is an optional zero-lag Finish-to-Start predecessor
    within the containing WBS. ``actual_start`` is resolved after the full WBS
    network is built so predecessor delay can propagate into successor work.
    Both are latent simulation details and never enter the public dataset.
    """

    bac: float
    planned_start: int  # zero-based month index
    duration: int
    evt: str
    resource_weights: np.ndarray
    pv_weights: np.ndarray
    g_kind: str
    g_params: Mapping[str, float]
    cost_factor: float
    predecessor_idx: int | None = None
    actual_start: int | None = None  # zero-based; resolved after FS links are assigned


@dataclass
class WbsPlan:
    """Latent work packages for one WBS element."""

    bac: float
    logic_type: str
    work_packages: List[WorkPackagePlan]

    @property
    def ev_completion_month(self) -> int:
        """One-based month in which this WBS earns its final EV."""
        return max(work_package_completion_month(wp) for wp in self.work_packages)


@dataclass
class WbsSimulation:
    """Aggregated incremental monthly values produced by latent WPs."""

    pv: np.ndarray
    ev: np.ndarray
    ac: np.ndarray


def _normalize(values: np.ndarray) -> np.ndarray:
    """Return non-negative weights normalized to 1.0."""
    x = np.clip(np.asarray(values, dtype=float), 0.0, None)
    total = x.sum()
    if total <= 0:
        return np.full(len(x), 1.0 / len(x))
    return x / total


def _choice_from_probs(rng: np.random.Generator, probs: Mapping[str, float]) -> str:
    """Choose a label using a dictionary of probability weights."""
    names = list(probs)
    p = np.array([probs[name] for name in names], dtype=float)
    p /= p.sum()
    return str(rng.choice(names, p=p))


def partition_bac(
    total_bac: float,
    n_wbs: int,
    method: str,
    rng: np.random.Generator,
) -> np.ndarray:
    """Partition project BAC across WBS elements using several concentration styles."""
    if method == "balanced":
        weights = rng.dirichlet(np.full(n_wbs, 10.0))
    elif method == "moderate":
        weights = rng.dirichlet(np.full(n_wbs, 2.5))
    elif method == "concentrated":
        weights = rng.dirichlet(np.full(n_wbs, 0.70))
    elif method == "dominant_wbs":
        dominant_idx = int(rng.integers(0, n_wbs))
        dominant_share = float(rng.uniform(0.30, 0.45))
        rest = rng.dirichlet(np.full(n_wbs - 1, 1.8)) * (1.0 - dominant_share)
        weights = np.empty(n_wbs, dtype=float)
        weights[dominant_idx] = dominant_share
        weights[np.arange(n_wbs) != dominant_idx] = rest
    else:
        raise ValueError(f"Unknown BAC partition method: {method}")

    return total_bac * weights / weights.sum()


def make_wbs_windows(
    n_wbs: int,
    plan_months: int,
    rng: np.random.Generator,
) -> List[Tuple[int, int]]:
    """Create overlapping planned WBS windows spanning the full baseline."""
    nominal_starts = np.linspace(0, int(plan_months * 0.68), n_wbs)
    windows: List[Tuple[int, int]] = []

    for i, nominal in enumerate(nominal_starts):
        start = int(np.clip(round(nominal) + int(rng.integers(-3, 4)), 0, plan_months - 1))
        if i == 0:
            start = 0

        remaining = plan_months - start
        min_span = min(10, remaining)
        max_span = min(30, remaining)
        span = remaining if max_span <= min_span else int(rng.integers(min_span, max_span + 1))
        end = min(plan_months - 1, start + span - 1)
        windows.append((start, end))

    # Make the PMB actually reach the stated project finish.
    last_start, _ = windows[-1]
    windows[-1] = (last_start, plan_months - 1)
    return windows


def _spread_weights(duration: int, profile: str, rng: np.random.Generator) -> np.ndarray:
    """Generate the WP's underlying planned resource/budget consumption profile."""
    if duration == 1:
        return np.array([1.0])

    x = np.linspace(0.0, 1.0, duration)
    if profile == "uniform":
        weights = np.ones(duration)
    elif profile == "front_loaded":
        weights = np.exp(-float(rng.uniform(1.2, 2.8)) * x)
    elif profile == "back_loaded":
        weights = np.exp(float(rng.uniform(1.2, 2.8)) * x)
    elif profile == "middle_peak":
        center = float(rng.uniform(0.42, 0.58))
        width = float(rng.uniform(0.16, 0.28))
        weights = np.exp(-0.5 * ((x - center) / width) ** 2)
    elif profile == "two_peak":
        c1 = float(rng.uniform(0.20, 0.38))
        c2 = float(rng.uniform(0.62, 0.82))
        w1 = float(rng.uniform(0.10, 0.20))
        w2 = float(rng.uniform(0.10, 0.20))
        mix = float(rng.uniform(0.35, 0.65))
        weights = (
            mix * np.exp(-0.5 * ((x - c1) / w1) ** 2)
            + (1.0 - mix) * np.exp(-0.5 * ((x - c2) / w2) ** 2)
        )
    else:
        raise ValueError(f"Unknown spread profile: {profile}")

    # Avoid mathematically pristine tails while preserving the broad shape.
    weights += 0.03 * weights.mean()
    return _normalize(weights)


def _choose_evt(duration: int, rng: np.random.Generator) -> str:
    """Choose an EVT with short-duration techniques restricted to short WPs."""
    if duration == 1:
        return str(rng.choice(["0_100", "percent_complete", "loe"], p=[0.35, 0.50, 0.15]))
    if duration == 2:
        return str(
            rng.choice(
                ["50_50", "weighted_milestone", "percent_complete", "loe"],
                p=[0.25, 0.20, 0.45, 0.10],
            )
        )
    return str(
        rng.choice(
            ["weighted_milestone", "percent_complete", "loe"],
            p=[0.34, 0.54, 0.12],
        )
    )


def _planned_pv_weights(
    duration: int,
    evt: str,
    resource_weights: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Convert the WP's resource plan into its scheduled-value earning profile."""
    if evt in {"percent_complete", "loe"}:
        return resource_weights.copy()

    if evt == "0_100":
        weights = np.zeros(duration)
        weights[-1] = 1.0
        return weights

    if evt == "50_50":
        weights = np.zeros(duration)
        weights[0] = 0.5
        weights[-1] += 0.5
        return weights

    if evt == "weighted_milestone":
        if duration <= 2:
            milestone_months = np.arange(duration)
        else:
            n_milestones = int(rng.integers(2, min(5, duration) + 1))
            candidates = np.arange(duration - 1)
            take = min(n_milestones - 1, len(candidates))
            chosen = rng.choice(candidates, size=take, replace=False) if take else np.array([], dtype=int)
            milestone_months = np.sort(np.r_[chosen, duration - 1])

        milestone_weights = []
        prior = 0
        for month in milestone_months:
            planned_resource = resource_weights[prior : month + 1].sum()
            milestone_weights.append(planned_resource * float(rng.uniform(0.85, 1.15)))
            prior = month + 1

        weights = np.zeros(duration)
        weights[milestone_months] = _normalize(np.array(milestone_weights))
        return weights

    raise ValueError(f"Unknown EVT: {evt}")


def _right_tail_draw(
    name: str,
    tail_scale: float,
    rng: np.random.Generator,
    cap: float | None = None,
) -> float:
    """Draw a positive schedule effect with a long but thinning right tail.

    The beta distributions in ``G_RIGHT_TAIL`` have ``a < b``. Most draws are
    therefore near the low end, while a minority extend substantially farther
    to the right. ``tail_scale`` lets the scenario change severity without
    changing the basic shape.
    """
    spec = G_RIGHT_TAIL[name]
    value = float(spec["low"] + tail_scale * spec["span"] * rng.beta(spec["a"], spec["b"]))
    if cap is not None:
        value = min(value, cap)
    return value


def _choose_g_profile(cfg: ScenarioConfig, rng: np.random.Generator) -> Tuple[str, dict]:
    """Select and parameterize a monotone actual-progress time warp g(t).

    Delay magnitudes are deliberately right-tailed: most WPs see little or
    moderate schedule distortion, while a smaller population sees much larger
    slips. This produces the kind of asymmetric completion-time distribution
    seen in real portfolios without simply making every WP uniformly late.
    """
    kind = _choice_from_probs(rng, cfg.g_probs)

    if kind == "on_time":
        params = {}
    elif kind == "minor_delay":
        params = {
            "delay": _right_tail_draw(
                "minor_delay", cfg.g_tail_scale, rng, cap=0.75
            )
        }
    elif kind == "slow":
        # Parameterize slow work in terms of completion-time slip, then convert
        # to the linear rate used by g(t). A slip of 0.30 means the WP takes
        # roughly 1.30 times its planned duration before FS propagation.
        slip = _right_tail_draw("slow_slip", cfg.g_tail_scale, rng, cap=1.00)
        params = {"rate": float(1.0 / (1.0 + slip))}
    elif kind == "ahead":
        # Ahead-of-plan work remains possible, but the upper tail is intentionally
        # modest compared with the delay tail.
        params = {"rate": float(rng.uniform(1.05, 1.25))}
    elif kind == "recover":
        early_slip = _right_tail_draw(
            "recover_early_slip", cfg.g_tail_scale, rng, cap=1.00
        )
        params = {
            "break_x": float(rng.uniform(0.30, 0.60)),
            "early_rate": float(1.0 / (1.0 + early_slip)),
            # Recovery can claw back some delay, but not with the extremely
            # aggressive rates used in v3. That keeps completion times more
            # naturally right-shifted.
            "late_rate": float(rng.uniform(1.04, 1.32)),
        }
    elif kind == "stall":
        params = {
            "stall_start": float(rng.uniform(0.18, 0.55)),
            "stall_length": _right_tail_draw(
                "stall_length", cfg.g_tail_scale, rng, cap=0.90
            ),
            "post_rate": float(rng.uniform(0.95, 1.25)),
        }
    else:
        raise ValueError(kind)

    return kind, params


def _g_progress(x: np.ndarray, kind: str, params: Mapping[str, float]) -> np.ndarray:
    """Map normalized actual calendar time to normalized technical progress."""
    x_pos = np.maximum(np.asarray(x, dtype=float), 0.0)

    if kind == "on_time":
        u = x_pos
    elif kind == "minor_delay":
        u = np.maximum(0.0, x_pos - params["delay"])
    elif kind == "slow":
        u = params["rate"] * x_pos
    elif kind == "ahead":
        u = params["rate"] * x_pos
    elif kind == "recover":
        b = params["break_x"]
        r1 = params["early_rate"]
        r2 = params["late_rate"]
        u_at_b = r1 * b
        u = np.where(x_pos <= b, r1 * x_pos, u_at_b + r2 * (x_pos - b))
    elif kind == "stall":
        a = params["stall_start"]
        gap = params["stall_length"]
        r_after = params["post_rate"]
        u = np.where(
            x_pos <= a,
            x_pos,
            np.where(x_pos <= a + gap, a, a + r_after * (x_pos - a - gap)),
        )
    else:
        raise ValueError(kind)

    return np.clip(u, 0.0, 1.0)


def _g_completion_x(kind: str, params: Mapping[str, float]) -> float:
    """Return normalized elapsed time x at which g(x) first reaches 100%."""
    if kind == "on_time":
        return 1.0
    if kind == "minor_delay":
        return 1.0 + params["delay"]
    if kind in {"slow", "ahead"}:
        return 1.0 / params["rate"]
    if kind == "recover":
        b = params["break_x"]
        r1 = params["early_rate"]
        r2 = params["late_rate"]
        u_at_b = r1 * b
        if u_at_b >= 1.0:
            return 1.0 / r1
        return b + (1.0 - u_at_b) / r2
    if kind == "stall":
        a = params["stall_start"]
        gap = params["stall_length"]
        r_after = params["post_rate"]
        if a >= 1.0:
            return 1.0
        return a + gap + (1.0 - a) / r_after
    raise ValueError(kind)


def work_package_completion_month(wp: WorkPackagePlan) -> int:
    """Return the one-based accounting month in which the WP earns its final EV.

    A dependent WP uses its resolved ``actual_start``. This is what lets a late
    predecessor move downstream EV to the right instead of merely decorating
    the latent plan with a relationship that has no consequence.
    """
    # LOE earns exactly by passage of time and is intentionally excluded from
    # explicit FS logic below. Its g(t) therefore remains irrelevant to EV.
    completion_x = 1.0 if wp.evt == "loe" else _g_completion_x(wp.g_kind, wp.g_params)
    start = wp.planned_start if wp.actual_start is None else wp.actual_start

    elapsed_months = int(np.ceil(wp.duration * completion_x - 1e-12))
    return int(start + max(1, elapsed_months))


def _evaluate_cumulative_curve(local_weights: np.ndarray, progress: np.ndarray) -> np.ndarray:
    """Evaluate a discrete planned curve at arbitrary normalized progress."""
    duration = len(local_weights)
    x_grid = np.r_[0.0, np.arange(1, duration + 1) / duration]
    y_grid = np.r_[0.0, np.cumsum(local_weights)]
    return np.interp(np.clip(progress, 0.0, 1.0), x_grid, y_grid)


def make_period_cost_effects(
    report_months: int,
    cfg: ScenarioConfig,
    rng: np.random.Generator,
) -> np.ndarray:
    """Create project-wide monthly multiplicative AC effects."""
    drift_end = float(rng.normal(cfg.drift_mean, cfg.drift_sd))
    effects = np.linspace(1.0, 1.0 + drift_end, report_months)
    effects *= np.exp(rng.normal(0.0, 0.012, report_months))

    n_events = int(rng.poisson(cfg.shock_events))
    low, high = cfg.shock_range
    for _ in range(n_events):
        start = int(rng.integers(0, report_months))
        length = int(rng.integers(1, 4))
        shock = 1.0 + float(rng.uniform(low, high))
        effects[start : min(report_months, start + length)] *= shock

    return np.clip(effects, 0.70, 1.60)


def _make_rework_costs(
    wp_bac: float,
    active_months: np.ndarray,
    cfg: ScenarioConfig,
    report_months: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Occasionally add modest cost-only rework without extra earned value."""
    result = np.zeros(report_months)
    if len(active_months) == 0 or rng.random() >= cfg.rework_prob:
        return result

    low, high = cfg.rework_range
    total_rework = wp_bac * float(rng.uniform(low, high))
    event_month = int(rng.choice(active_months))

    # Keep the full rework amount inside the project's natural reporting life.
    # This avoids inventing a trailing month solely because a two-month cost
    # spread happened to start in the final EV month.
    max_length = max(1, report_months - event_month)
    length = min(int(rng.integers(1, 3)), max_length)
    spread = _normalize(rng.uniform(0.8, 1.2, length))
    result[event_month : event_month + length] += total_rework * spread
    return result


def _choose_wbs_logic_type(rng: np.random.Generator) -> str:
    """Choose a latent WBS schedule-logic archetype from configuration."""
    names = list(WBS_LOGIC_PROFILES)
    weights = np.array([WBS_LOGIC_PROFILES[name]["weight"] for name in names], dtype=float)
    weights /= weights.sum()
    return str(rng.choice(names, p=weights))


def _assign_fs_relationships(
    work_packages: List[WorkPackagePlan],
    logic_type: str,
    rng: np.random.Generator,
) -> None:
    """Assign simple zero-lag FS links and resolve dependency-constrained starts.

    The baseline dates are generated first. We only create an FS edge when the
    predecessor already finishes on or before the successor's planned start.
    This preserves the existing PMB instead of rescheduling it merely to force a
    network into existence. The actual schedule is then propagated through the
    selected links: a successor cannot begin before a late predecessor finishes.

    LOE WPs are excluded from the network because LOE earns by passage of time;
    treating it as discrete predecessor/successor work would create misleading
    schedule behavior.
    """
    if logic_type not in WBS_LOGIC_PROFILES:
        raise ValueError(f"Unknown WBS logic type: {logic_type}")

    link_probability = float(WBS_LOGIC_PROFILES[logic_type]["link_probability"])

    # Reset the mutable schedule fields so this function is deterministic when
    # called once on a newly built WBS and safe if reused during experimentation.
    for wp in work_packages:
        wp.predecessor_idx = None
        wp.actual_start = wp.planned_start

    if link_probability <= 0.0:
        return

    # Process by planned start. Any eligible predecessor must finish no later
    # than this start, so the resulting graph is acyclic by construction.
    order = sorted(
        range(len(work_packages)),
        key=lambda i: (work_packages[i].planned_start, work_packages[i].duration, i),
    )

    for succ_idx in order:
        succ = work_packages[succ_idx]
        if succ.evt == "loe" or rng.random() >= link_probability:
            continue

        candidates = []
        gaps = []
        for pred_idx in order:
            if pred_idx == succ_idx:
                continue
            pred = work_packages[pred_idx]
            if pred.evt == "loe":
                continue

            # A zero-lag FS successor starts in the month immediately after the
            # predecessor's planned work ends. With zero-based starts,
            # planned_start + duration is exactly that next-month index.
            pred_finish_boundary = pred.planned_start + pred.duration
#TODO: Revisit finish-start relationship boundary conditions. Check that naive planned starts are pushed only when before predecessor finish. Update documentation for clarity. Add more inline comments.
            if pred_finish_boundary <= succ.planned_start:
                candidates.append(pred_idx)
                gaps.append(succ.planned_start - pred_finish_boundary)

        if not candidates:
            continue

        # Prefer the closest sensible predecessor, while leaving enough
        # randomness that repeated seeds do not all produce identical chains.
        gap_arr = np.asarray(gaps, dtype=float)
        weights = np.exp(-gap_arr / 2.0)
        weights /= weights.sum()
        succ.predecessor_idx = int(rng.choice(candidates, p=weights))

    # Resolve actual starts in topological order. A predecessor's own g(t) and
    # any upstream FS delay are already reflected in its completion month.
    for idx in order:
        wp = work_packages[idx]
        if wp.predecessor_idx is None:
            wp.actual_start = wp.planned_start
            continue

        pred = work_packages[wp.predecessor_idx]
        pred_finish_month = work_package_completion_month(pred)
        wp.actual_start = max(wp.planned_start, pred_finish_month)


def _validate_fs_relationships(work_packages: List[WorkPackagePlan]) -> None:
    """Internal sanity checks for the simple FS network."""
    for idx, wp in enumerate(work_packages):
        if wp.actual_start is None:
            raise AssertionError("Every WP must have a resolved actual start.")
        if wp.predecessor_idx is None:
            continue
        if wp.predecessor_idx == idx:
            raise AssertionError("A WP cannot be its own predecessor.")

        pred = work_packages[wp.predecessor_idx]
        if pred.planned_start + pred.duration > wp.planned_start:
            raise AssertionError("FS predecessor violates the planned baseline order.")
        if wp.actual_start < work_package_completion_month(pred):
            raise AssertionError("FS successor starts before actual predecessor completion.")


def build_wbs_plan(
    wbs_bac: float,
    wbs_start: int,
    wbs_end: int,
    mean_wbs_bac: float,
    cfg: ScenarioConfig,
    rng: np.random.Generator,
) -> WbsPlan:
    """Generate latent WPs and their schedule/cost parameters for one WBS."""
    wbs_span = wbs_end - wbs_start + 1
    size_ratio = wbs_bac / mean_wbs_bac
    expected_wps = 7.0 + 4.5 * np.sqrt(max(size_ratio, 0.1))
    n_wps = int(np.clip(round(expected_wps + rng.normal(0, 2.0)), 5, 20))

    wp_bacs = wbs_bac * rng.dirichlet(np.full(n_wps, 1.6))
    anchors = np.linspace(wbs_start, wbs_end, n_wps)
    work_packages: List[WorkPackagePlan] = []

    for wp_idx, wp_bac in enumerate(wp_bacs):
        duration = int(rng.choice(WP_DURATION_MONTHS, p=WP_DURATION_PROBS))
        duration = min(duration, max(1, wbs_span))

        jitter = int(rng.integers(-2, 3))
        planned_start = int(round(anchors[wp_idx])) - duration // 2 + jitter
        planned_start = int(
            np.clip(planned_start, wbs_start, max(wbs_start, wbs_end - duration + 1))
        )

        evt = _choose_evt(duration, rng)
        profile = str(rng.choice(SPREAD_PROFILES))
        resource_weights = _spread_weights(duration, profile, rng)
        pv_weights = _planned_pv_weights(duration, evt, resource_weights, rng)
        g_kind, g_params = _choose_g_profile(cfg, rng)
        cost_factor = float(np.clip(rng.normal(cfg.cost_mean, cfg.cost_sd), 0.72, 1.45))

        work_packages.append(
            WorkPackagePlan(
                bac=float(wp_bac),
                planned_start=planned_start,
                duration=duration,
                evt=evt,
                resource_weights=resource_weights,
                pv_weights=pv_weights,
                g_kind=g_kind,
                g_params=g_params,
                cost_factor=cost_factor,
            )
        )

    logic_type = _choose_wbs_logic_type(rng)
    _assign_fs_relationships(work_packages, logic_type, rng)
    _validate_fs_relationships(work_packages)

    return WbsPlan(
        bac=float(wbs_bac),
        logic_type=logic_type,
        work_packages=work_packages,
    )


def _simulate_work_package(
    wp: WorkPackagePlan,
    report_months: int,
    cfg: ScenarioConfig,
    project_period_cost_effect: np.ndarray,
    rng: np.random.Generator,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate one pre-built latent WP and return incremental monthly PV, EV, and AC."""
    report_index = np.arange(report_months)

    # Planned progress remains tied to the PMB dates. Actual progress uses the
    # dependency-constrained start, so predecessor slippage can propagate into
    # downstream EV and resource consumption without moving PV.
    planned_x = (report_index - wp.planned_start + 1) / wp.duration
    planned_x[report_index < wp.planned_start] = 0.0

    actual_start = wp.planned_start if wp.actual_start is None else wp.actual_start
    actual_x = (report_index - actual_start + 1) / wp.duration
    actual_x[report_index < actual_start] = 0.0

    actual_progress = _g_progress(actual_x, wp.g_kind, wp.g_params)
    planned_progress = np.clip(planned_x, 0.0, 1.0)

    pv_cum_frac = _evaluate_cumulative_curve(wp.pv_weights, planned_progress)
    ev_cum_frac = (
        pv_cum_frac.copy()
        if wp.evt == "loe"
        else _evaluate_cumulative_curve(wp.pv_weights, actual_progress)
    )
    resource_cum_frac = _evaluate_cumulative_curve(wp.resource_weights, actual_progress)

    pv_period = np.diff(np.r_[0.0, wp.bac * pv_cum_frac])
    ev_period = np.diff(np.r_[0.0, wp.bac * ev_cum_frac])
    resource_period_budget = np.diff(np.r_[0.0, wp.bac * resource_cum_frac])

    # Faster-than-planned technical progress can carry an overtime/expediting premium.
    progress_incr = np.diff(np.r_[0.0, actual_progress])
    relative_speed = progress_incr * wp.duration
    recovery_multiplier = 1.0 + cfg.recovery_premium * np.maximum(relative_speed - 1.0, 0.0)
    wp_month_noise = np.exp(rng.normal(0.0, 0.010, report_months))

    ac_period = (
        resource_period_budget
        * wp.cost_factor
        * project_period_cost_effect
        * recovery_multiplier
        * wp_month_noise
    )

    active_months = np.flatnonzero(resource_period_budget > max(1e-9, wp.bac * 1e-8))
    ac_period += _make_rework_costs(wp.bac, active_months, cfg, report_months, rng)

    # Floating-point interpolation can create microscopic negatives at boundaries.
    return (
        np.maximum(pv_period, 0.0),
        np.maximum(ev_period, 0.0),
        np.maximum(ac_period, 0.0),
    )


def simulate_wbs(
    plan: WbsPlan,
    report_months: int,
    cfg: ScenarioConfig,
    project_period_cost_effect: np.ndarray,
    rng: np.random.Generator,
) -> WbsSimulation:
    """Simulate a pre-built WBS plan and return only WBS-level monthly totals."""
    pv = np.zeros(report_months)
    ev = np.zeros(report_months)
    ac = np.zeros(report_months)

    for wp in plan.work_packages:
        wp_pv, wp_ev, wp_ac = _simulate_work_package(
            wp=wp,
            report_months=report_months,
            cfg=cfg,
            project_period_cost_effect=project_period_cost_effect,
            rng=rng,
        )
        pv += wp_pv
        ev += wp_ev
        ac += wp_ac

    return WbsSimulation(pv=pv, ev=ev, ac=ac)
