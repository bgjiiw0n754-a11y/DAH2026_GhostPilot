"""Mission-decision detectors for authorized local SITL B-path experiments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


OBJECTIVES = ("geofence", "auto-waypoint", "rtl", "land", "failsafe")


@dataclass(frozen=True)
class DecisionEvent:
    objective: str
    event_type: str
    elapsed_s: float
    sample_index: int
    target_drift_m: float
    official_drift_m: float
    mode: str
    mission_seq: int
    status_text: str
    evidence_message_type: str


def as_float(value, default: float = 0.0) -> float:
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def row_event(row: dict, objective: str, event_type: str) -> DecisionEvent:
    return DecisionEvent(
        objective=objective,
        event_type=event_type,
        elapsed_s=as_float(row.get("elapsed_s")),
        sample_index=as_int(row.get("sample_index")),
        target_drift_m=as_float(row.get("target_drift_m")),
        official_drift_m=as_float(row.get("official_drift_m")),
        mode=str(row.get("mode", "")),
        mission_seq=as_int(row.get("mission_seq")),
        status_text=str(row.get("status_text", "")),
        evidence_message_type=str(row.get("message_type", "")),
    )


def event_to_row(event: DecisionEvent, experiment: str, case_name: str) -> dict:
    return {
        "timestamp": "",
        "experiment": experiment,
        "case": case_name,
        "event_type": event.event_type,
        "elapsed_s": event.elapsed_s,
        "sample_index": event.sample_index,
        "target_drift_m": event.target_drift_m,
        "official_drift_m": event.official_drift_m,
        "mode": event.mode,
        "mission_seq": event.mission_seq,
        "fence_breach_status": "",
        "status_text": event.status_text,
        "evidence_message_type": event.evidence_message_type,
    }


def is_geofence_breach(row: dict) -> bool:
    if row.get("message_type") != "FENCE_STATUS":
        return False
    return as_float(row.get("fence_breach_status")) != 0.0


def is_waypoint_advance(row: dict, baseline_seq: int = 0) -> bool:
    return (
        row.get("message_type") == "MISSION_CURRENT"
        and as_int(row.get("mission_seq")) > baseline_seq
    )


def mode_is(row: dict, mode: str) -> bool:
    return str(row.get("mode", "")).upper() == mode.upper()


def is_rtl_event(row: dict) -> bool:
    return row.get("message_type") == "HEARTBEAT" and mode_is(row, "RTL")


def is_land_event(row: dict) -> bool:
    text = str(row.get("status_text", "")).lower()
    return (
        row.get("message_type") == "HEARTBEAT"
        and mode_is(row, "LAND")
    ) or (
        row.get("message_type") == "STATUSTEXT" and "land" in text
    )


def is_failsafe_event(row: dict) -> bool:
    if row.get("message_type") != "STATUSTEXT":
        return False
    text = str(row.get("status_text", "")).lower()
    keywords = ("failsafe", "gps glitch", "gps failsafe", "bad gps")
    return any(keyword in text for keyword in keywords)


def detect_event(
    rows: Iterable[dict],
    objective: str,
    baseline_seq: int = 0,
    baseline_time_s: float = 0.0,
) -> DecisionEvent | None:
    for row in rows:
        if objective == "geofence" and is_geofence_breach(row):
            return row_event(row, objective, "fence_breach")
        if objective == "auto-waypoint" and is_waypoint_advance(row, baseline_seq):
            event = row_event(row, objective, "mission_advance")
            if baseline_time_s <= 0.0 or event.elapsed_s + 2.0 < baseline_time_s:
                return event
        if objective == "rtl" and is_rtl_event(row):
            return row_event(row, objective, "rtl_mode")
        if objective == "land" and is_land_event(row):
            return row_event(row, objective, "land_trigger")
        if objective == "failsafe" and is_failsafe_event(row):
            return row_event(row, objective, "failsafe_status")
    return None


def matrix_row(
    objective: str,
    case: str,
    verdict: str,
    reason: str,
    metrics: dict,
    event: DecisionEvent | None,
    bundle: str = "",
) -> dict:
    return {
        "objective": objective,
        "case": case,
        "verdict": verdict,
        "reason": reason,
        "decision_changed": int(event is not None),
        "event_type": event.event_type if event else "",
        "decision_time_s": event.elapsed_s if event else 0.0,
        "drift_at_decision_m": event.official_drift_m if event else 0.0,
        "minimum_drift_m": metrics.get("minimum_breach_drift_m", 0.0)
        or metrics.get("drift_at_decision_m", 0.0),
        "reflection_rate": metrics.get("reflection_rate", 0.0),
        "stealth_score": metrics.get("stealth_score", 0.0),
        "route": metrics.get("route", ""),
        "profile": metrics.get("profile", ""),
        "bundle": bundle,
    }
