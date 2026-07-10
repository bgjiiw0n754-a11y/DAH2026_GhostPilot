"""
demo_adaptive.py — 적응형 공격 폐루프 오프라인 데모 (Ghost Pilot)
DAH 2026 Ghost Pilot 프로젝트

공격↔방어가 서로 반응하는 '살아있는 판단'을 SITL 없이 한 프로세스로 재현한다.
  · 공격: 직전 방어 반응을 보고 위조 속도를 조절 (탐지되면 절반으로 백오프,
          안 걸리면 조금씩 가속)
  · 방어: 매 스텝 누적 드리프트/이상 탐지로 판정

이 데모는 attack_agent(--adaptive) + main_defense(--status)의 폐루프를
파일 대신 '직접 콜백'으로 압축한 것이다. 로직은 동일하다.

핵심 관전 포인트:
  탐지 → 공격 백오프(스텝↓) → 드리프트 임계 아래로 → 미탐지 →
  공격 가속(스텝↑) → 재탐지 …  이 톱니(sawtooth)가 반복된다.
  = 고정 스크립트가 아니라 상호 적응하는 에이전트라는 증거.

주의: 여기 방어는 '윈도우 드리프트 단독(기준선)'이라 적응형 공격의 회피가
드러난다(약점 노출용). 이 회피를 막는 다중신호+4상태 강화 방어는
demo_stateful.py / main_defense.py가 보여준다.

산출물: ../results/adaptive_loop.csv  (P0-2 그래프의 입력)

사용법:
  python3 demo_adaptive.py
"""

import csv
import os
import time

import numpy as np

from defense_layer1 import RuleBasedDefense
from defense_layer2 import AnomalyDetector, CumulativeDriftDetector
from main_defense import response_decision
from utils import FeatureExtractor, Log, haversine_m, ensure_src_cwd, FakeMsg

np.random.seed(7)

BASE_LAT, BASE_LON = 37.5665, 126.9780
OUT_CSV = "../results/adaptive_loop.csv"


# FakeMsg는 utils.py 공용 정의 사용 (B4)


def build_defense():
    """합성 정상 데이터로 방어 파이프라인을 즉석 구성한다."""
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
    # 데모는 sleep 간격이 짧으므로 dt를 1초로 고정 (시간 왜곡 방지)
    extractor = FeatureExtractor(fixed_dt=1.0)
    return layer1, layer2, drift, extractor


def defense_step(msg, layer1, layer2, drift, extractor):
    """단일 메시지를 방어에 통과시켜 (action, detecting, drift_m) 반환."""
    l1_result, _ = layer1.check(msg)

    l2_anomaly = False
    drift_detected, drift_m = False, 0.0
    if msg.get_type() == "GLOBAL_POSITION_INT":
        feat = extractor.extract(msg)
        if feat is not None:
            l2_anomaly, _ = layer2.detect(feat)
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            drift_detected, drift_m = drift.update(lat, lon)

    detecting = l2_anomaly or drift_detected
    action, _ = response_decision(l1_result, detecting, "FLYING")
    return action, detecting, drift_m


def main():
    ensure_src_cwd()
    Log.info("=" * 62)
    Log.info("Ghost Pilot — 적응형 공격 폐루프 데모 (공격↔방어 상호 적응)")
    Log.info("=" * 62)

    layer1, layer2, drift, extractor = build_defense()
    Log.defense("방어 준비 완료 (Layer1 규칙 + Layer2 이상탐지 + 누적 드리프트)")
    time.sleep(0.8)

    # 공격 상태
    spoof_delta = 0.0
    spoof_step = 0.00001      # 스텝당 약 1.1m (초기값)
    STEP_MIN = 0.000002       # 백오프 하한 (약 0.22m)
    STEP_MAX = 0.00002        # 가속 상한 (약 2.2m)
    detecting = False         # 직전 방어 반응 (폐루프 입력)

    backoffs = 0
    first_detect_step = None
    rows = []

    Log.attack("공격 시작: 방어 반응을 보며 위조 속도를 실시간 조절한다.")
    time.sleep(0.8)

    for i in range(80):
        # ── 적응형 판단: 직전 방어 반응을 보고 이번 스텝 크기 결정 ──
        if detecting:
            spoof_step = max(spoof_step * 0.5, STEP_MIN)   # 탐지됨 → 백오프
            backoffs += 1
            adapt = "백오프"
        else:
            spoof_step = min(spoof_step * 1.05, STEP_MAX)  # 안 걸림 → 가속
            adapt = "가속"

        # ── 위조 주입 ──
        spoof_delta += spoof_step
        # 실제 상태: 드론은 지정 지점에서 정지비행(loiter) — 미세 진동만 있음
        real_lat = BASE_LAT + np.random.normal(0, 0.000003)   # 약 0.3m 지터
        real_lon = BASE_LON + np.random.normal(0, 0.000003)
        # GCS 인식 상태: 공격자가 주입한 위조로 위치가 한 방향으로 누적 이탈
        perc_lat = BASE_LAT + spoof_delta
        perc_lon = BASE_LON + spoof_delta
        msg = FakeMsg("GLOBAL_POSITION_INT",
                      lat=int(perc_lat * 1e7), lon=int(perc_lon * 1e7),
                      alt=20000, vx=0, vy=0, vz=0, seq=20 + i, sys_id=1)

        # ── 방어 판정 (다음 루프의 폐루프 입력이 된다) ──
        action, detecting, drift_m = defense_step(
            msg, layer1, layer2, drift, extractor)

        step_m = spoof_step * 111000
        cum_m = spoof_delta * 111000
        # 실제 상태와 GCS 인식 상태의 벌어진 거리 (= 기만의 크기)
        divergence_m = haversine_m(real_lat, real_lon, perc_lat, perc_lon)
        if detecting and first_detect_step is None:
            first_detect_step = i + 1

        mark = "  ← 탐지" if detecting else ""
        Log.attack(
            f"[{i+1:2d}] 스텝={step_m:4.2f}m({adapt})  누적이탈={cum_m:6.1f}m  "
            f"드리프트창={drift_m:5.1f}m  대응={action}{mark}")

        rows.append({
            "step": i + 1,
            "spoof_step_m": round(step_m, 3),
            "cum_drift_m": round(cum_m, 1),
            "window_net_m": round(drift_m, 1),
            "divergence_m": round(divergence_m, 1),
            "real_lat": round(real_lat, 7),
            "real_lon": round(real_lon, 7),
            "perc_lat": round(perc_lat, 7),
            "perc_lon": round(perc_lon, 7),
            "detecting": int(detecting),
            "action": action,
            "adapt": adapt,
        })
        time.sleep(0.05)

    # ── 결과 저장 ──
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    Log.info("=" * 62)
    Log.info("폐루프 데모 종료 — 핵심 결과:")
    fd = f"{first_detect_step}스텝" if first_detect_step else "미탐지"
    Log.info(f"  · 첫 탐지 시점(MTTD, 스텝) : {fd}")
    Log.info(f"  · 공격 백오프 횟수          : {backoffs}회")
    Log.info(f"  · 최종 누적 이탈량          : {rows[-1]['cum_drift_m']}m")
    Log.info(f"  · 로그 저장                 : {OUT_CSV}")
    Log.info("  → 탐지→백오프→미탐지→가속→재탐지 반복 = 상호 적응(에이전트성) 증거")
    Log.info("=" * 62)


if __name__ == "__main__":
    main()
