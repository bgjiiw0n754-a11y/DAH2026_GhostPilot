"""Route metadata for B-path local SITL experiments."""

from __future__ import annotations


ROUTES = ("fc-direct", "mavproxy-udp", "companion")


def selected_routes(value: str) -> tuple[str, ...]:
    if value == "all":
        return ROUTES
    if value not in ROUTES:
        raise ValueError(f"unknown route: {value}")
    return (value,)


def route_label(route: str) -> str:
    if route == "mavproxy-udp":
        return "mavproxy-udp-gcs-style-path"
    if route == "companion":
        return "local-companion-json-plan-to-trusted-mavlink-gps-input"
    return "fc-direct-gps-input"


def route_claim(route: str) -> str:
    if route == "companion":
        return "post-access local companion/relay GPS_INPUT path"
    if route == "mavproxy-udp":
        return "MAVProxy UDP forwarded GPS_INPUT path"
    return "FC direct GPS_INPUT path"

