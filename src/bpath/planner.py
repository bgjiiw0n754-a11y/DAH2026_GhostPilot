"""Mission-deception planner for authorized local SITL B-path experiments."""

from __future__ import annotations

from dataclasses import dataclass

from .mission_decisions import OBJECTIVES, as_float


@dataclass(frozen=True)
class ObjectiveCandidate:
    objective: str
    available: bool
    expected_impact: float
    expected_cost: float
    reason: str

    @property
    def score(self) -> float:
        if not self.available:
            return -999.0
        return self.expected_impact - self.expected_cost


def build_candidates(params: dict | None = None, prior_results: dict | None = None) -> list[ObjectiveCandidate]:
    params = params or {}
    prior_results = prior_results or {}
    fence_enabled = as_float(params.get("FENCE_ENABLE"), 1.0) >= 1.0
    candidates = [
        ObjectiveCandidate("geofence", fence_enabled, 0.95, 0.25, "fence parameters available"),
        ObjectiveCandidate("auto-waypoint", True, 0.70, 0.45, "single waypoint mission can be uploaded in SITL"),
        ObjectiveCandidate("rtl", True, 0.55, 0.55, "RTL requires mode change evidence after position event"),
        ObjectiveCandidate("land", True, 0.45, 0.60, "LAND evidence is mode/status dependent"),
        ObjectiveCandidate("failsafe", True, 0.35, 0.75, "failsafe evidence is noisy and may not trigger under bounded GPS_INPUT"),
    ]
    adjusted = []
    for item in candidates:
        prior = prior_results.get(item.objective, {})
        impact = item.expected_impact + (0.2 if prior.get("verdict") == "pass" else 0.0)
        cost = item.expected_cost + (0.25 if prior.get("verdict") == "fail" else 0.0)
        adjusted.append(ObjectiveCandidate(item.objective, item.available, impact, cost, item.reason))
    return adjusted


def choose_objective(
    requested: str,
    params: dict | None = None,
    prior_results: dict | None = None,
) -> tuple[str, list[dict]]:
    candidates = build_candidates(params, prior_results)
    trace = [
        {
            "objective": item.objective,
            "available": int(item.available),
            "expected_impact": item.expected_impact,
            "expected_cost": item.expected_cost,
            "score": item.score,
            "reason": item.reason,
            "selected": 0,
        }
        for item in candidates
    ]
    if requested != "auto":
        if requested not in OBJECTIVES:
            raise ValueError(f"unknown objective: {requested}")
        selected = requested
    else:
        selected = max(candidates, key=lambda item: item.score).objective
    for row in trace:
        row["selected"] = int(row["objective"] == selected)
    return selected, trace


def attack_plan(
    objective: str,
    route: str,
    profile: str,
    max_drift_m: float,
    stealth_budget: str,
    trace: list[dict],
) -> dict:
    return {
        "objective": objective,
        "route": route,
        "profile": profile,
        "message": "GPS_INPUT",
        "max_drift_m": max_drift_m,
        "stealth_budget": stealth_budget,
        "planner_trace": trace,
        "scope": "authorized local ArduPilot SITL post-access payload only",
        "not_claimed": [
            "remote exploit",
            "credential bypass",
            "MAVLink signing bypass",
            "RF GNSS spoofing",
            "RF jamming",
            "real vehicle takeover",
        ],
    }

