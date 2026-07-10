"""
main_defense.py — 통합 방어 에이전트
DAH 2026 Ghost Pilot 프로젝트

Layer 1 (규칙 기반) + Layer 2 (Isolation Forest) + 대응 결정 트리를 통합한다.
Ghost Pilot 방어 AI의 핵심 실행 파일이다.

동작 흐름:
  MAVLink 수신
    → Layer 1 규칙 검사 (허가 SYS_ID·SEQ·비행중 위험명령·순간이동·속도)
        · BLOCK → 즉시 확정(ATTACK)
    → Layer 2 다중 신호 (위치 메시지)
        · Isolation Forest        : 단일 스텝 이상
        · 누적 드리프트(윈도우)    : 빠른 점진 위조
        · 절대 이탈(고정 앵커)      : 백오프 회피 차단
        · 운동학 잔차(위치-속도)    : 정상 기동 오탐 없이 위조 탐지
    → 4상태 히스테리시스 머신 (NORMAL/SUSPICIOUS/ATTACK/RECOVERY)
        · 연속 확증으로 오탐 억제, 절대 이탈 잔존 시 상태 유지
    → 상태별 대응 (PASS / ALERT / SWITCH / HOVER / RTL)

사용법:
  python3 main_defense.py
  python3 main_defense.py --log ../results/detection_log.csv
  python3 main_defense.py --status ../results/defense_status.json   # 적응형 폐루프
"""

import argparse
import csv
import json
import os
import time

import config
from utils import FeatureExtractor, Log, ensure_src_cwd
from defense_layer1 import RuleBasedDefense
from defense_layer2 import (
    AnomalyDetector, CumulativeDriftDetector, AbsoluteDriftTracker,
    KinematicConsistencyDetector)


# ──────────────────────────────────────────────────────────────
# 4상태 히스테리시스 머신 (백오프 회피 차단)
# ──────────────────────────────────────────────────────────────
class DefenseStateMachine:
    """
    NORMAL → SUSPICIOUS → ATTACK → RECOVERY 4상태를 히스테리시스로 전이한다.

    설계 의도:
      · 단발 이상으로 즉시 대응하지 않아 오탐(False Positive)을 억제한다.
      · 한 번 ATTACK으로 확정되면, 공격이 백오프해 윈도우 드리프트를
        임계 아래로 낮춰도 '절대 이탈(abs_over)'이 남아있는 한 상태를
        내리지 않는다 → 적응형 공격의 회피를 차단.
      · 충분히 오래 정상이어야(연속 clean) 단계적으로 완화·복귀한다.

    입력(매 메시지):
      anomaly   : 이상 관측 여부 (IsoForest OR 윈도우 드리프트 OR 절대 이탈)
      abs_over  : 절대 누적 이탈이 하드 임계 초과 (백오프로도 사라지지 않음)
      l1_block  : Layer1 치명 규칙 위반 (즉시 ATTACK)
    """

    def __init__(self, confirm=config.SM_CONFIRM, clear=config.SM_CLEAR,
                 recover=config.SM_RECOVER):
        self.state = "NORMAL"
        self.confirm = confirm   # SUSPICIOUS→ATTACK 필요한 연속 이상 횟수
        self.clear = clear       # 상태 완화에 필요한 연속 정상 횟수
        self.recover = recover   # RECOVERY→NORMAL 쿨다운(연속 정상)
        self.anom_streak = 0
        self.clean_streak = 0

    def update(self, anomaly, abs_over, l1_block):
        if anomaly:
            self.anom_streak += 1
            self.clean_streak = 0
        else:
            self.clean_streak += 1
            self.anom_streak = 0

        s = self.state
        if l1_block:
            self.state = "ATTACK"                       # 치명 위반 즉시 확정
        elif s == "NORMAL":
            if anomaly:
                self.state = "SUSPICIOUS"
        elif s == "SUSPICIOUS":
            if self.anom_streak >= self.confirm or abs_over:
                self.state = "ATTACK"                   # 연속 확증 or 절대 이탈
            elif self.clean_streak >= self.clear:
                self.state = "NORMAL"
        elif s == "ATTACK":
            if self.clean_streak >= self.clear and not abs_over:
                self.state = "RECOVERY"                 # 정상 회복 시작
        elif s == "RECOVERY":
            if anomaly or abs_over:
                self.state = "ATTACK"                   # 재악화 → 빠른 재승급
            elif self.clean_streak >= self.recover:
                self.state = "NORMAL"                   # 쿨다운 통과 → 복귀
        return self.state


def state_action(state, l1_result):
    """
    (실제 통합 방어의 대응 매핑) 4상태 머신의 상태 → 대응 행동.
    DefenseAgent(SITL) · demo_stateful · evaluate_metrics의 NEW 방어가 쓴다.
    치명 규칙 위반은 상태와 무관하게 RTL.
    """
    if l1_result == "BLOCK":
        return "RTL", "규칙 위반(치명) — 안전 귀환"
    return {
        "NORMAL":     ("PASS",   None),
        "SUSPICIOUS": ("ALERT",  "의심 — 경보·명령 보류·기준 동결"),
        "ATTACK":     ("SWITCH", "공격 확정 — GPS 차단·IMU 항법 전환"),
        "RECOVERY":   ("HOVER",  "복구 검증 — 호버링 후 재동기화 대기"),
    }[state]


# ──────────────────────────────────────────────────────────────
# 대응 결정 트리 (RL 대신 규칙 기반 결정)
# ──────────────────────────────────────────────────────────────
def response_decision(layer1_result, layer2_anomaly, flight_state="FLYING"):
    """
    (기본·무상태 대응 매핑) 단일 이상 신호를 바로 행동으로 매핑한다.

    사용처: demo_offline(기초 흐름 시연), demo_adaptive(적응형 공격을 '윈도우
    단독' 기준 방어에 대해 시연 — 약점 노출용). 실제 통합 방어(다중 신호 +
    히스테리시스)의 대응은 state_action()을 쓴다.

    반환 행동:
      RTL    : 안전 귀환 (가장 강한 대응)
      HOVER  : 호버링 + 운용자 확인 대기
      SWITCH : 통신 채널 전환 (GPS 차단, IMU 항법)
      ALERT  : 경보만 발생
      PASS   : 정상 통과
    """
    # 1. Layer 1이 BLOCK → 가장 강한 대응
    if layer1_result == "BLOCK":
        return "RTL", "규칙 위반(치명) — 안전 귀환"

    # 2. Layer 1 ALERT + Layer 2 이상 → 복합 공격 의심
    if layer1_result == "ALERT" and layer2_anomaly:
        return "HOVER", "복합 공격 의심 — 호버링 후 확인 대기"

    # 3. Layer 2만 이상 → 점진적 위조(Ghost Pilot) 의심
    if layer2_anomaly:
        if flight_state == "FLYING":
            return "SWITCH", "점진적 위조 의심 — GPS 차단, IMU 항법 전환"
        return "ALERT", "이상 패턴 — 경보"

    # 4. Layer 1 ALERT만 → 경보
    if layer1_result == "ALERT":
        return "ALERT", "규칙 경보"

    return "PASS", None


# ──────────────────────────────────────────────────────────────
# 통합 방어 에이전트
# ──────────────────────────────────────────────────────────────
class DefenseAgent:
    def __init__(self, target, log_path=None, status_path=None):
        import pymavlink.mavutil as mavutil
        self.conn = mavutil.mavlink_connection(target)
        self.conn.wait_heartbeat()
        Log.defense(f"통합 방어 에이전트 시작 SYS_ID={self.conn.target_system}")

        self.layer1 = RuleBasedDefense()
        self.layer2 = AnomalyDetector().load()
        self.drift = CumulativeDriftDetector()
        self.absdrift = AbsoluteDriftTracker()
        self.kinematic = KinematicConsistencyDetector()   # (C1) 위치-속도 교차검증
        self.state_machine = DefenseStateMachine()
        self._prev_state = "NORMAL"
        self._last_t = None                               # (C1) dt 계산용
        self.extractor = FeatureExtractor()

        self.log_path = log_path
        self.log_rows = []
        self.last_anomaly = False   # 적응형 공격 시연용 (공격 에이전트가 참조)

        # 적응형 공격 폐루프용: 방어 판정을 파일로 공유해 공격이 읽는다.
        self.status_path = status_path
        if self.status_path:
            Log.defense(f"방어 상태 공유 파일: {self.status_path} "
                        f"(공격 에이전트가 이 값을 보고 백오프)")

        # 대응 통계
        self.response_stats = {}

    def run(self, duration=None):
        Log.defense("모니터링 시작. 공격을 기다립니다...")
        start = time.time()

        while True:
            if duration and time.time() - start > duration:
                break

            msg = self.conn.recv_match(blocking=True, timeout=2)
            if msg is None:
                continue

            # ── Layer 1 ──
            l1_result, l1_reason = self.layer1.check(msg)

            # ── Layer 2 (위치 메시지만) ──
            l2_anomaly, l2_score = False, 0.0
            drift_detected, drift_m = False, 0.0
            abs_over, abs_m = False, 0.0
            kin_bad, kin_m = False, 0.0
            lat = lon = None
            if msg.get_type() == "GLOBAL_POSITION_INT":
                feat = self.extractor.extract(msg)
                if feat is not None:
                    l2_anomaly, l2_score = self.layer2.detect(feat)
                    lat = msg.lat / 1e7
                    lon = msg.lon / 1e7
                    vx = msg.vx / 100.0   # 북 속도 (cm/s→m/s)
                    vy = msg.vy / 100.0   # 동 속도
                    now = time.time()
                    dt = (now - self._last_t) if self._last_t else (1.0 / config.TELEM_HZ)
                    self._last_t = now
                    # 윈도우 순변위(빠른 드리프트)
                    drift_detected, drift_m = self.drift.update(lat, lon)
                    # 고정 앵커 절대 이탈(백오프로도 사라지지 않음)
                    abs_over, abs_m = self.absdrift.update(lat, lon)
                    # (C1) 위치-속도 운동학 잔차 (정상 기동엔 무반응)
                    kin_bad, kin_m = self.kinematic.update(lat, lon, vx, vy, dt)

            # 이상 관측 = IsoForest OR 윈도우 드리프트 OR 절대 이탈 OR 운동학 잔차
            anomaly = l2_anomaly or drift_detected or abs_over or kin_bad
            self.last_anomaly = anomaly

            # ── 4상태 머신 → 대응 결정 ──
            l1_block = (l1_result == "BLOCK")
            state = self.state_machine.update(anomaly, abs_over, l1_block)
            action, action_reason = state_action(state, l1_result)

            # NORMAL로 '재진입'하는 순간에만 앵커 재기준.
            # (steady NORMAL 중 매번 갱신하면 느린 드리프트가 앵커를 끌고 가
            #  절대 이탈이 누적되지 않는다. 진입 시 1회만 잡아야 슬로우 공격도 잡힌다.)
            if lat is not None and state == "NORMAL" and self._prev_state != "NORMAL":
                self.absdrift.set_anchor(lat, lon)
                self.kinematic.reset(lat, lon)
            self._prev_state = state

            # 사유 보강
            if state == "ATTACK" and abs_over:
                action_reason = (f"공격 확정 — 절대 이탈 {abs_m:.1f}m "
                                 f"(백오프로도 회피 불가)")
            elif kin_bad and action == "SWITCH":
                action_reason = (f"위치-속도 불일치 잔차 {kin_m:.1f}m "
                                 f"— 운동학 교차검증 탐지")
            elif drift_detected and action == "SWITCH":
                action_reason = f"누적 드리프트 {drift_m:.1f}m — 점진적 위조 탐지"

            # ── 적응형 공격 폐루프: 방어 반응(상태) 공유 ──
            if self.status_path:
                self._write_status(state != "NORMAL", action, drift_m)

            # ── 로그·출력 ──
            if action != "PASS":
                self._handle_response(action, action_reason,
                                      l1_result, l1_reason,
                                      anomaly, l2_score)

            # 로그 기록
            if self.log_path:
                self.log_rows.append({
                    "time": round(time.time() - start, 2),
                    "msg_type": msg.get_type(),
                    "layer1": l1_result,
                    "layer2_isoforest": int(l2_anomaly),
                    "layer2_drift": int(drift_detected),
                    "drift_m": round(drift_m, 1),
                    "abs_drift_m": round(abs_m, 1),
                    "abs_over": int(abs_over),
                    "kin_residual_m": round(kin_m, 1),
                    "kin_bad": int(kin_bad),
                    "state": state,
                    "layer2_score": round(l2_score, 4),
                    "action": action,
                })

        self._finalize()

    def _handle_response(self, action, reason, l1_result, l1_reason,
                         l2_anomaly, l2_score):
        self.response_stats[action] = self.response_stats.get(action, 0) + 1

        detail = []
        if l1_result != "PASS":
            detail.append(f"L1={l1_result}({l1_reason})")
        if l2_anomaly:
            detail.append(f"L2=이상(score={l2_score:.3f})")
        detail_str = " | ".join(detail)

        if action == "RTL":
            Log.block(f"[대응:RTL] {reason} :: {detail_str}")
        elif action == "HOVER":
            Log.block(f"[대응:HOVER] {reason} :: {detail_str}")
        elif action == "SWITCH":
            Log.alert(f"[대응:SWITCH] {reason} :: {detail_str}")
        elif action == "ALERT":
            Log.alert(f"[대응:ALERT] {reason} :: {detail_str}")

    def _write_status(self, detecting, action, drift_m):
        """
        방어 판정을 JSON 파일로 원자적 기록.
        공격 에이전트(--adaptive)가 이 파일을 읽어 탐지 여부를 판단하고
        위조 속도를 조절한다. 이것이 공격↔방어 폐루프의 연결점이다.
        """
        data = {
            "detecting": bool(detecting),
            "action": action,
            "drift_m": round(drift_m, 1),
            "ts": time.time(),
        }
        tmp = self.status_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.status_path)   # 원자적 교체 (부분쓰기 방지)
        except OSError:
            pass   # 상태 공유 실패는 방어 본 기능을 막지 않는다

    def _finalize(self):
        Log.defense("모니터링 종료")
        self.layer1.report()
        Log.info(f"[최종 방어 상태] {self.state_machine.state}")
        Log.info(f"[대응 통계] {self.response_stats}")

        if self.log_path and self.log_rows:
            with open(self.log_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.log_rows[0].keys())
                writer.writeheader()
                writer.writerows(self.log_rows)
            Log.info(f"탐지 로그 저장: {self.log_path}")


def main():
    ensure_src_cwd()
    parser = argparse.ArgumentParser(description="통합 방어 에이전트")
    parser.add_argument("--target", default="udp:127.0.0.1:14550")
    parser.add_argument("--log", default=None, help="탐지 로그 CSV 경로")
    parser.add_argument("--duration", type=int, default=None,
                        help="실행 시간(초). 미지정 시 무한 실행")
    parser.add_argument("--status", default="../results/defense_status.json",
                        help="방어 상태 공유 파일 경로 (적응형 공격 폐루프용). "
                             "off로 지정하면 비활성화")
    args = parser.parse_args()

    status_path = None if args.status == "off" else args.status
    agent = DefenseAgent(args.target, log_path=args.log, status_path=status_path)
    try:
        agent.run(duration=args.duration)
    except KeyboardInterrupt:
        agent._finalize()


if __name__ == "__main__":
    main()
