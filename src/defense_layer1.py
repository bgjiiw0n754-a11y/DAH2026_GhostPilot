"""
defense_layer1.py — 방어 Layer 1 (규칙 기반 즉각 차단)
DAH 2026 Ghost Pilot 프로젝트

전문가가 정의한 명시적 규칙으로 공격을 즉각 탐지·차단한다.
빠르고 가볍지만, 알려진 패턴만 잡을 수 있다.

탐지 규칙:
  Rule 1: 허가되지 않은 SYS_ID    → BLOCK
  Rule 2: SEQ 번호 급변           → ALERT
  Rule 3: 비행 중 위험 명령       → BLOCK
  Rule 4: 위치 순간이동(111m+)    → ALERT (급격한 GPS 스푸핑)
  Rule 5: 물리적으로 불가능한 속도 → BLOCK

주의: 점진적 위조(Ghost Pilot 핵심 공격)는 한 번에 1m씩만 이동하므로
      Rule 4에 걸리지 않는다. 이것이 Layer 2(AI)가 필요한 이유다.
"""

import config
from utils import haversine_m, Log


class RuleBasedDefense:
    # 설정값 (config.py 중앙 설정에서 가져옴)
    ALLOWED_SYS_IDS = config.ALLOWED_SYS_IDS      # 허가된 GCS SYS_ID
    SEQ_JUMP_THRESHOLD = config.SEQ_JUMP_THRESHOLD  # SEQ 급변 임계값
    POSITION_JUMP_M = config.POSITION_JUMP_M      # 위치 순간이동 임계값 (m)
    MAX_SPEED_MS = config.MAX_SPEED_MS            # 물리적 최대 속도 (m/s)
    DANGER_COMMANDS = config.DANGER_COMMANDS      # 비행 중 금지 명령 (ARM_DISARM)
    FLYING_ALT_M = config.FLYING_ALT_M            # 이 고도 이상이면 비행 중

    def __init__(self):
        self.last_seq = None
        self.last_lat = None
        self.last_lon = None
        # (C2) 비행 상태는 하드코딩이 아니라 텔레메트리에서 유도한다.
        self._armed = True              # HEARTBEAT에서 갱신 (미지 시 보수적으로 True)
        self._last_alt = None           # GLOBAL_POSITION_INT 고도(m)
        self.is_flying = True           # _armed and 고도>임계 로 갱신
        self.stats = {"PASS": 0, "ALERT": 0, "BLOCK": 0}

    def _update_flight_state(self):
        """(C2) armed 여부와 고도로 '비행 중' 여부를 유도한다."""
        alt_ok = (self._last_alt is None) or (self._last_alt > self.FLYING_ALT_M)
        self.is_flying = self._armed and alt_ok

    def check(self, msg):
        """
        MAVLink 메시지를 검사해 (판정, 사유) 반환.
        판정: "PASS" | "ALERT" | "BLOCK"
        """
        result, reason = "PASS", None

        # 헤더에서 SYS_ID, SEQ 추출
        header = getattr(msg, "_header", None)
        sys_id = header.srcSystem if header else None
        seq = header.seq if header else None

        # (C2) HEARTBEAT로 armed 상태 갱신 → 비행 상태 유도
        if msg.get_type() == "HEARTBEAT":
            base_mode = getattr(msg, "base_mode", 0)
            self._armed = bool(base_mode & 128)   # MAV_MODE_FLAG_SAFETY_ARMED
            self._update_flight_state()

        # ── Rule 1: 허가되지 않은 SYS_ID ──
        if sys_id is not None and sys_id not in self.ALLOWED_SYS_IDS:
            result, reason = "BLOCK", f"허가되지 않은 SYS_ID={sys_id}"
            self._record(result)
            return result, reason

        # ── Rule 2: SEQ 번호 급변 ──
        if seq is not None and self.last_seq is not None:
            delta = (seq - self.last_seq) % 256
            if delta > self.SEQ_JUMP_THRESHOLD:
                result, reason = "ALERT", f"SEQ 급변 (delta={delta})"
        if seq is not None:
            self.last_seq = seq

        # ── Rule 3: 비행 중 위험 명령 ──
        if msg.get_type() == "COMMAND_LONG":
            if msg.command in self.DANGER_COMMANDS and self.is_flying:
                result, reason = "BLOCK", \
                    f"비행 중 위험 명령 (command={msg.command})"
                self._record(result)
                return result, reason

        # ── Rule 4 & 5: 위치·속도 검사 ──
        if msg.get_type() == "GLOBAL_POSITION_INT":
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            vx = msg.vx / 100.0
            vy = msg.vy / 100.0
            speed = (vx ** 2 + vy ** 2) ** 0.5

            # (C2) 고도로 비행 상태 갱신 (relative_alt 우선, 없으면 alt)
            rel = getattr(msg, "relative_alt", None)
            self._last_alt = (rel if rel is not None else msg.alt) / 1000.0
            self._update_flight_state()

            # Rule 5: 속도 초과
            if speed > self.MAX_SPEED_MS:
                result, reason = "BLOCK", f"속도 초과 ({speed:.1f} m/s)"
                self._record(result)
                return result, reason

            # Rule 4: 위치 순간이동
            if self.last_lat is not None:
                jump = haversine_m(self.last_lat, self.last_lon, lat, lon)
                if jump > self.POSITION_JUMP_M:
                    result, reason = "ALERT", f"위치 순간이동 ({jump:.0f}m)"
            self.last_lat, self.last_lon = lat, lon

        self._record(result)
        return result, reason

    def _record(self, result):
        self.stats[result] = self.stats.get(result, 0) + 1

    def report(self):
        Log.info(f"[Layer1 통계] PASS={self.stats['PASS']} "
                 f"ALERT={self.stats['ALERT']} BLOCK={self.stats['BLOCK']}")


# 단독 테스트용
if __name__ == "__main__":
    import pymavlink.mavutil as mavutil

    Log.info("Layer 1 단독 테스트 시작")
    conn = mavutil.mavlink_connection("udp:127.0.0.1:14550")
    conn.wait_heartbeat()
    Log.defense(f"연결됨 SYS_ID={conn.target_system}")

    guard = RuleBasedDefense()
    while True:
        msg = conn.recv_match(blocking=True, timeout=2)
        if msg is None:
            continue
        result, reason = guard.check(msg)
        if result == "BLOCK":
            Log.block(f"{reason}")
        elif result == "ALERT":
            Log.alert(f"{reason}")
