"""Stealth scoring for local SITL GPS_INPUT trajectories."""

from __future__ import annotations


BUDGET_LIMITS = {
    "strict": {"speed": 1.5, "accel": 0.6, "jumps": 0, "error": 2.0, "fix": 0.95},
    "normal": {"speed": 2.5, "accel": 1.0, "jumps": 0, "error": 3.0, "fix": 0.90},
    "loose": {"speed": 4.0, "accel": 2.0, "jumps": 2, "error": 5.0, "fix": 0.80},
}


def as_float(value, default: float = 0.0) -> float:
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def budget_limits(name: str) -> dict:
    return dict(BUDGET_LIMITS.get(name, BUDGET_LIMITS["normal"]))


def stealth_score(metrics: dict, budget: str = "normal") -> dict:
    limits = budget_limits(budget)
    speed = as_float(metrics.get("commanded_speed_max_mps"))
    accel = as_float(metrics.get("commanded_accel_max_mps2"))
    jumps = as_float(metrics.get("abrupt_jump_count"))
    error = as_float(metrics.get("mean_target_error_m"))
    final_drift = as_float(metrics.get("final_target_drift_m"))
    fix = as_float(metrics.get("gps_fix_stability"), 1.0)

    speed_penalty = max(0.0, speed / limits["speed"] - 1.0)
    accel_penalty = max(0.0, accel / limits["accel"] - 1.0)
    jump_penalty = max(0.0, jumps - limits["jumps"])
    error_penalty = max(0.0, error / limits["error"] - 1.0)
    fix_penalty = max(0.0, limits["fix"] - fix) * 5.0
    drift_penalty = final_drift / 50.0
    cost = (
        drift_penalty
        + speed_penalty * 2.0
        + accel_penalty * 2.0
        + jump_penalty
        + error_penalty
        + fix_penalty
    )
    return {
        "stealth_budget": budget,
        "stealth_score": round(cost, 6),
        "speed_violation": int(speed > limits["speed"]),
        "accel_violation": int(accel > limits["accel"]),
        "jump_violation": int(jumps > limits["jumps"]),
        "error_violation": int(error > limits["error"]),
        "fix_violation": int(fix < limits["fix"]),
        "constraint_violations": int(speed > limits["speed"])
        + int(accel > limits["accel"])
        + int(jumps > limits["jumps"])
        + int(error > limits["error"])
        + int(fix < limits["fix"]),
    }


def improved_over(baseline: dict, candidate: dict) -> bool:
    if candidate.get("decision_changed") and not baseline.get("decision_changed"):
        return True
    return (
        candidate.get("decision_changed") == baseline.get("decision_changed")
        and as_float(candidate.get("stealth_score"), 9999.0)
        < as_float(baseline.get("stealth_score"), 9999.0)
    )

