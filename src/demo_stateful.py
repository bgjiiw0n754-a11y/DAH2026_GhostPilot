"""
demo_stateful.py — 4상태 방어 vs 단일 윈도우 방어 (before/after)
DAH 2026 Ghost Pilot 프로젝트

적응형 공격(백오프 회피)을 상대로 두 방어를 나란히 돌려 비교한다.

  OLD : 윈도우 드리프트 + 무상태 결정 트리 (P0 버전)
        → 공격이 백오프하면 윈도우 순변위가 임계 아래로 떨어져 '회피' 발생
  NEW : 절대 이탈 추적 + 4상태 히스테리시스 머신 (P1)
        → 한 번 ATTACK으로 확정되면 백오프해도 상태를 내리지 않음 → 회피 차단

두 경우 모두 공격은 '자신이 상대하는 방어의 탐지 여부'에 적응한다(공정 비교).

산출물: ../results/stateful_loop.csv  (mode 컬럼으로 OLD/NEW 구분)

사용법:
  python3 demo_stateful.py
  python3 plot_agentic.py     # fig_stateful.png 포함 생성
"""

import csv
import os
import time

import numpy as np

from defense_layer1 import RuleBasedDefense
import config
from defense_layer2 import (
    AnomalyDetector, CumulativeDriftDetector, AbsoluteDriftTracker,
    KinematicConsistencyDetector)
from main_defense import response_decision, DefenseStateMachine, state_action
from utils import FeatureExtractor, Log, haversine_m, ensure_src_cwd, FakeMsg

BASE_LAT, BASE_LON = 37.5665, 126.9780
OUT_CSV = "../results/stateful_loop.csv"
DECEIVE_M = 15.0   # '이 이상 벌어지면 실질적 기만 상태'로 보는 기준(회피 판정용)


# FakeMsg는 utils.py 공용 정의 사용 (B4)


def _load_train():
    train = []
    with open("../data/normal_flight.csv") as f:
        reader = csv.reader(f)
        next(reader)
        for r in reader:
            train.append([float(x) for x in r])
    return np.array(train)


def run_loop(mode, train):
    """
    mode='OLD' 또는 'NEW'. 해당 방어에 적응하는 공격 폐루프를 80스텝 돌리고
    스텝별 기록 리스트를 반환한다.
    """
    np.random.seed(7)   # 두 모드가 동일한 실제-지터 시퀀스를 보도록 고정

    layer1 = RuleBasedDefense()
    layer2 = AnomalyDetector()
    layer2.model.fit(train)
    layer2.trained = True
    drift = CumulativeDriftDetector()
    extractor = FeatureExtractor(fixed_dt=1.0)

    absdrift = AbsoluteDriftTracker()
    kin = KinematicConsistencyDetector()   # (C1) 위치-속도 교차검증
    sm = DefenseStateMachine()
    prev_state = "NORMAL"

    spoof_delta = 0.0
    spoof_step = 0.00001
    STEP_MIN, STEP_MAX = 0.000002, 0.00002
    detecting = False
    rows = []

    for i in range(80):
        # 적응형 공격: 직전 방어 반응(detecting)에 따라 속도 조절
        spoof_step = (max(spoof_step * 0.5, STEP_MIN) if detecting
                      else min(spoof_step * 1.05, STEP_MAX))
        spoof_delta += spoof_step

        perc_lat = BASE_LAT + spoof_delta   # GCS 인식(위조 주입)
        perc_lon = BASE_LON + spoof_delta
        msg = FakeMsg("GLOBAL_POSITION_INT",
                      lat=int(perc_lat * 1e7), lon=int(perc_lon * 1e7),
                      alt=20000, vx=0, vy=0, vz=0, seq=20 + i, sys_id=1)

        # ── 방어 공통 특징 계산 ──
        l1_result, _ = layer1.check(msg)
        feat = extractor.extract(msg)
        iso = False
        win_det, win_m = False, 0.0
        abs_over, abs_m = False, 0.0
        kin_bad = False
        if feat is not None:
            iso, _ = layer2.detect(feat)
            win_det, win_m = drift.update(perc_lat, perc_lon)
            abs_over, abs_m = absdrift.update(perc_lat, perc_lon)
            # 위조는 vx=vy=0이므로 추측항법과 어긋난다 (dt=1.0 고정)
            kin_bad, _ = kin.update(perc_lat, perc_lon, 0.0, 0.0, 1.0)

        if mode == "OLD":
            # 윈도우 드리프트 + 무상태 결정 트리
            det_raw = iso or win_det
            action, _ = response_decision(l1_result, det_raw, "FLYING")
            detecting = det_raw
            state = "-"
        else:
            # 절대 이탈 + 운동학 잔차 + 4상태 히스테리시스 (DefenseAgent와 동일)
            anomaly = iso or win_det or abs_over or kin_bad
            state = sm.update(anomaly, abs_over, l1_result == "BLOCK")
            action, _ = state_action(state, l1_result)
            if state == "NORMAL" and prev_state != "NORMAL":
                absdrift.set_anchor(perc_lat, perc_lon)
                kin.reset(perc_lat, perc_lon)
            prev_state = state
            detecting = (state != "NORMAL")

        cum_m = spoof_delta * 111000
        rows.append({
            "mode": mode,
            "step": i + 1,
            "spoof_step_m": round(spoof_step * 111000, 3),
            "cum_drift_m": round(cum_m, 1),
            "window_net_m": round(win_m, 1),
            "abs_drift_m": round(abs_m, 1),
            "detecting": int(detecting),
            "state": state,
            "action": action,
        })

    return rows


def summarize(rows, mode):
    """첫 탐지 시점과 '기만 중 미탐지(회피)' 스텝 수를 집계."""
    first = next((r["step"] for r in rows if r["detecting"]), None)
    # 실질 기만(cum_drift > DECEIVE_M) 상태인데 방어가 놓친 스텝 수
    evaded = sum(1 for r in rows
                 if r["cum_drift_m"] > DECEIVE_M and not r["detecting"])
    final_drift = rows[-1]["cum_drift_m"]
    return first, evaded, final_drift


def main():
    ensure_src_cwd()
    Log.info("=" * 64)
    Log.info("4상태 방어(NEW) vs 단일 윈도우 방어(OLD) — 적응형 공격 상대 비교")
    Log.info("=" * 64)

    train = _load_train()
    old_rows = run_loop("OLD", train)
    new_rows = run_loop("NEW", train)

    of, oe, od = summarize(old_rows, "OLD")
    nf, ne, nd = summarize(new_rows, "NEW")

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(old_rows[0].keys()))
        writer.writeheader()
        writer.writerows(old_rows)
        writer.writerows(new_rows)

    def line(tag, first, evaded, drift):
        fd = f"{first}스텝" if first else "미탐지"
        Log.info(f"  {tag}: 첫탐지 {fd:>7} | 기만중 미탐지(회피) {evaded:2d}스텝 | "
                 f"최종 이탈 {drift:.1f}m")

    Log.info("-" * 64)
    line("OLD(윈도우+무상태)  ", of, oe, od)
    line("NEW(다중신호+4상태)", nf, ne, nd)
    Log.info("-" * 64)
    Log.info(f"  → 회피 스텝: OLD {oe} → NEW {ne}  "
             f"(백오프 회피가 {oe - ne}스텝 감소)")
    Log.info(f"  로그 저장: {OUT_CSV}")
    Log.info("=" * 64)


if __name__ == "__main__":
    main()
