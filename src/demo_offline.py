"""
demo_offline.py — 오프라인 통합 데모 (SITL 없이 공방 흐름 시연)
DAH 2026 Ghost Pilot 프로젝트

SITL 연결 없이도 공격→탐지→대응의 전체 흐름을 보여준다.
개발 초기 검증용이자, SITL 세팅 전 팀원들이 로직을 이해하는 용도.

실제 대회 데모는 SITL + attack_agent + main_defense 3개 터미널로 진행한다.
이 스크립트는 그 흐름을 한 프로세스로 압축해 재현한다.

주의: 여기 방어는 기초 구성(규칙 + 누적 드리프트)으로 '흐름'만 시연한다.
전체 다중신호(절대이탈·운동학) + 4상태 방어는 main_defense.py / demo_stateful.py
참조. 정량 성능은 evaluate_metrics.py.

사용법:
  python3 demo_offline.py
"""

import time
import numpy as np

from defense_layer1 import RuleBasedDefense
from defense_layer2 import AnomalyDetector, CumulativeDriftDetector
from main_defense import response_decision
from utils import Log, ensure_src_cwd, FakeMsg

np.random.seed(7)


# FakeMsg는 utils.py 공용 정의 사용 (B4)


def simulate():
    ensure_src_cwd()
    Log.info("=" * 60)
    Log.info("Ghost Pilot 오프라인 통합 데모")
    Log.info("SITL 없이 공격→탐지→대응 흐름을 재현합니다.")
    Log.info("=" * 60)
    time.sleep(1)

    # 방어 시스템 구성 (합성 데이터로 즉석 학습)
    import csv
    train = []
    with open("../data/normal_flight.csv") as f:
        reader = csv.reader(f)
        next(reader)
        for r in reader:
            train.append([float(x) for x in r])
    train = np.array(train)

    layer1 = RuleBasedDefense()
    layer2 = AnomalyDetector()
    layer2.model.fit(train)
    layer2.trained = True
    drift = CumulativeDriftDetector(window=20, drift_threshold_m=15.0)

    Log.defense("방어 에이전트 준비 완료 (Layer1 규칙 + Layer2 이상탐지 + 드리프트)")
    time.sleep(1)

    # ────────────────────────────────────────────────
    # 시나리오 1: 정상 비행 (탐지 없어야 함)
    # ────────────────────────────────────────────────
    Log.info("\n──── [시나리오 1] 정상 비행 ────")
    base_lat, base_lon = 37.5665, 126.9780
    # 정상 비행: 정지비행(호버) — GPS 잡음 수준(약 0.3m)의 미세 진동만.
    # 속도(vx,vy)와 위치가 일치해야 정상 통과한다(호버이므로 속도≈0).
    for i in range(25):
        lat = base_lat + np.random.normal(0, 0.000003)
        lon = base_lon + np.random.normal(0, 0.000003)
        msg = FakeMsg("GLOBAL_POSITION_INT",
                      lat=int(lat * 1e7), lon=int(lon * 1e7),
                      alt=20000, vx=0, vy=0, vz=0, seq=i, sys_id=1)
        _process(msg, layer1, layer2, drift, base_lat, base_lon)
        time.sleep(0.05)
    Log.defense("→ 정상 비행 구간: 대응 없음 (정상 통과) ✓")
    time.sleep(1)

    # ────────────────────────────────────────────────
    # 시나리오 2: 직접 명령 인젝션 (Layer 1이 잡아야 함)
    # ────────────────────────────────────────────────
    Log.info("\n──── [시나리오 2] 직접 명령 인젝션 공격 ────")
    Log.attack("공격자: 비행 중 ARM_DISARM(모터 정지) 명령 주입!")
    attack_msg = FakeMsg("COMMAND_LONG", command=400, sys_id=1, seq=10)
    _process(attack_msg, layer1, layer2, drift, base_lat, base_lon)
    time.sleep(1)

    # ────────────────────────────────────────────────
    # 시나리오 3: 알 수 없는 SYS_ID (Layer 1이 잡아야 함)
    # ────────────────────────────────────────────────
    Log.info("\n──── [시나리오 3] 위장 GCS 명령 (알 수 없는 SYS_ID) ────")
    Log.attack("공격자: SYS_ID=99로 위장해 명령 전송!")
    fake_gcs = FakeMsg("COMMAND_LONG", command=176, sys_id=99, seq=11)
    _process(fake_gcs, layer1, layer2, drift, base_lat, base_lon)
    time.sleep(1)

    # ────────────────────────────────────────────────
    # 시나리오 4: 점진적 위조 (Ghost Pilot 핵심 — 드리프트가 잡아야 함)
    # ────────────────────────────────────────────────
    Log.info("\n──── [시나리오 4] 점진적 위조 (Ghost Pilot 핵심 공격) ────")
    Log.attack("공격자: 한 번에 1m씩만 위조 — 규칙 기반 우회 시도")
    # 드리프트 탐지기 초기화 (이전 시나리오 상태 제거)
    drift.positions.clear()
    drift_delta = 0.0
    caught_at = None
    for i in range(30):
        drift_delta += 0.00001  # 스텝당 약 1.1m
        lat = base_lat + drift_delta
        lon = base_lon + drift_delta
        msg = FakeMsg("GLOBAL_POSITION_INT",
                      lat=int(lat * 1e7), lon=int(lon * 1e7),
                      alt=20000, vx=0, vy=0, vz=0, seq=20 + i, sys_id=1)
        action = _process(msg, layer1, layer2, drift, base_lat, base_lon,
                          quiet=True)
        if action in ("SWITCH", "HOVER") and caught_at is None:
            caught_at = i + 1
            Log.block(f"→ 점진적 위조 탐지! (스텝 {caught_at}회차, "
                      f"누적 이탈 {drift_delta*111000:.1f}m)")
            Log.alert(f"→ 대응: GPS 차단 + IMU 항법 전환")
            break
        time.sleep(0.15)

    if caught_at is None:
        Log.alert("→ 30스텝 내 미탐지 (임계값 조정 필요)")

    Log.info("\n" + "=" * 60)
    Log.info("데모 종료. 핵심 결과:")
    Log.info("  · 직접 공격(명령 인젝션, 위장 GCS) → Layer 1 즉시 차단")
    Log.info("  · 점진적 위조(1m씩) → 규칙 우회하지만 누적 드리프트가 탐지")
    Log.info("=" * 60)
    layer1.report()


def _process(msg, layer1, layer2, drift, base_lat, base_lon, quiet=False):
    """단일 메시지를 방어 파이프라인에 통과시키고 대응 반환"""
    from utils import FeatureExtractor
    if not hasattr(_process, "extractor"):
        # 데모는 sleep 간격이 짧으므로 dt를 1초로 고정 (시간 왜곡 방지)
        _process.extractor = FeatureExtractor(fixed_dt=1.0)

    l1_result, l1_reason = layer1.check(msg)

    l2_anomaly = False
    drift_detected, drift_m = False, 0.0
    if msg.get_type() == "GLOBAL_POSITION_INT":
        feat = _process.extractor.extract(msg)
        if feat is not None:
            l2_anomaly, _ = layer2.detect(feat)
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            drift_detected, drift_m = drift.update(lat, lon)

    l2_final = l2_anomaly or drift_detected
    action, reason = response_decision(l1_result, l2_final, "FLYING")

    if not quiet and action != "PASS":
        if action == "RTL":
            Log.block(f"[대응:RTL] {reason}")
        elif action == "HOVER":
            Log.block(f"[대응:HOVER] {reason}")
        elif action == "SWITCH":
            Log.alert(f"[대응:SWITCH] {reason}")
        elif action == "ALERT":
            Log.alert(f"[대응:ALERT] {reason}")

    return action


if __name__ == "__main__":
    simulate()
