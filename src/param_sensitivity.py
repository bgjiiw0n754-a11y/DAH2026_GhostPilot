"""
param_sensitivity.py — A3 파라미터 민감도 분석 (재현 가능한 산출 스크립트)
DAH 2026 Ghost Pilot 프로젝트

★ 성격 (중요):
  · 이것은 **오프라인 / 합성(FakeMsg 계열) 피처레벨** 민감도 분석이다.
    SITL 실측·실비행 결과가 아니다. 보고서에서 SITL/실비행 결과처럼 쓰면 안 된다.
  · 결론 프레이밍: "파라미터를 바꿔도 무조건 완벽 방어"가 아니라,
    "파라미터 변화에 따른 결과가 **예측 가능**하고, 누적 드리프트와 절대이탈이
    **상보적으로 작동**한다"로 서술한다.

목적:
  방어 성능이 config.py의 특정 값에서만 우연히 맞는 게 아니라, 파라미터 범위에서
  예측 가능하게 변하는지 확인한다. AGENT_LOG.md 2026-07-06 17:18 항목의 표 4개를
  이 스크립트로 재현하고 CSV로 남긴다.

산출 조건 (공통):
  - 데이터: ../data/normal_flight.csv (gen_synthetic_data.py 산출, 합성 정상비행 1000행,
    8피처 lat_rate,lon_rate,alt,vx,vy,vz,seq_delta,pos_jump_m).
  - split: 앞 70%(700) train / 뒤 30%(300) test  (실험 ①에만 사용).
  - seed: np.random.seed(42)  (실험 ①의 랜덤 test 샘플 추출에만 영향).
  - 탐지기: defense_layer2.py의 CumulativeDriftDetector/AbsoluteDriftTracker,
    sklearn IsolationForest. 파라미터만 인자로 스윕(기존 코드 미변경).
  - ⚠️ 결정성: 실험 ②③④의 합성 공격은 결정적(고정 시작점 직선 드리프트)이라
    trial 수와 무관하게 결과 동일(기하 관계). "100%/0%"는 통계가 아니라 결정적 결과.
    실험 ①만 랜덤성이 있어 seed가 수치 재현을 좌우.

사용법:
  cd src && python3 param_sensitivity.py
  → ../results/param_sensitivity.csv 생성 + 콘솔 표 출력
"""

import csv

import numpy as np
from sklearn.ensemble import IsolationForest

from defense_layer2 import CumulativeDriftDetector, AbsoluteDriftTracker
from utils import ensure_src_cwd

DATA_PATH = "../data/normal_flight.csv"
OUT_PATH = "../results/param_sensitivity.csv"

# 실험 ②③④ 합성 드리프트의 고정 시작점 (임의의 기준 좌표)
BASE_LAT, BASE_LON = 37.5665, 126.9780


def load_csv(path):
    rows = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r)  # header
        for row in r:
            rows.append([float(x) for x in row])
    return np.array(rows)


def make_spoof(sample, drift_m):
    """정상 샘플에 피처레벨 위조를 흉내 (run_experiments.py와 동일 규약)."""
    s = sample.copy()
    s[7] = drift_m               # pos_jump_m
    s[0] = drift_m / 111000 / 0.5  # lat_rate 소폭 변화
    return s


def exp1_contamination(train, test, csv_rows):
    """[표 A3-①] IsolationForest contamination 민감도."""
    cond = ("data=normal_flight.csv; split=70/30; seed=42; "
            "FP=test 이상비율; 탐지=drift별 200회 랜덤 test 샘플 위조 예측")
    print("=" * 66)
    print("[A3-①] IsolationForest contamination 민감도")
    print(f"{'contam':>8} | {'정상오탐율':>9} | {'1m':>6} | {'5m':>6} | {'10m':>6}")
    for c in [0.01, 0.03, 0.05, 0.10, 0.15]:
        m = IsolationForest(n_estimators=100, contamination=c, random_state=42)
        m.fit(train)
        fp = float(np.mean(m.predict(test) == -1) * 100)
        det = {}
        for d in (1.0, 5.0, 10.0):
            cnt = 0
            for _ in range(200):
                b = test[np.random.randint(len(test))]
                if m.predict(make_spoof(b, d).reshape(1, -1))[0] == -1:
                    cnt += 1
            det[d] = cnt / 200 * 100
        print(f"{c:8.2f} | {fp:8.1f}% | {det[1.0]:5.1f}% | {det[5.0]:5.1f}% | {det[10.0]:5.1f}%")
        csv_rows.append(["A3-1_IForest_contamination", "IsolationForest",
                         f"contamination={c}", "정상오탐율(%)", f"{fp:.1f}", cond])
        for d in (1.0, 5.0, 10.0):
            csv_rows.append(["A3-1_IForest_contamination", "IsolationForest",
                             f"contamination={c}", f"{int(d)}m위조탐지율(%)",
                             f"{det[d]:.1f}", cond])


def _straight_drift_detect(detector, step_m, max_steps):
    """고정 시작점에서 step_m/스텝 직선 드리프트 → 탐지 스텝(없으면 None)."""
    lat = BASE_LAT
    step_deg = step_m / 111000.0
    for s in range(max_steps):
        lat += step_deg
        is_anom, _ = detector.update(lat, BASE_LON)
        if is_anom:
            return s + 1
    return None


def exp2_cumulative(csv_rows):
    """[표 A3-②] 누적 드리프트 window×threshold 민감도 (결정적)."""
    cond = ("합성 1m/스텝 직선 드리프트(최대 80스텝); 결정적(trial 무관); "
            "탐지=net_drift>threshold")
    print("=" * 66)
    print("[A3-②] 누적 드리프트 window×threshold (1m/스텝, 결정적)")
    print(f"{'window':>7} {'thr(m)':>7} | {'탐지':>5} | {'탐지스텝':>7}")
    for win in [15, 20, 30]:
        for thr in [10.0, 15.0, 20.0, 25.0]:
            det = CumulativeDriftDetector(window=win, drift_threshold_m=thr)
            step = _straight_drift_detect(det, 1.0, 80)
            caught = step is not None
            print(f"{win:7d} {thr:7.1f} | {'O' if caught else 'X':>5} | "
                  f"{step if caught else '-':>7}")
            csv_rows.append(["A3-2_CumulativeDrift", "CumulativeDriftDetector",
                             f"window={win},threshold_m={thr}", "탐지여부",
                             "탐지" if caught else "미탐지", cond])
            csv_rows.append(["A3-2_CumulativeDrift", "CumulativeDriftDetector",
                             f"window={win},threshold_m={thr}", "탐지스텝",
                             str(step) if caught else "", cond])


def exp3_absolute(csv_rows):
    """[표 A3-③] 절대이탈 threshold 민감도 (결정적)."""
    cond = ("합성 1m/스텝 직선 드리프트(최대 80스텝); warmup=3; 결정적; "
            "탐지=앵커대비 절대거리>threshold")
    print("=" * 66)
    print("[A3-③] 절대이탈(AbsoluteDriftTracker) threshold (1m/스텝, 결정적)")
    print(f"{'thr(m)':>7} | {'탐지':>5} | {'탐지스텝':>7}")
    for thr in [15.0, 20.0, 25.0, 30.0, 40.0]:
        det = AbsoluteDriftTracker(abs_threshold_m=thr, warmup=3)
        step = _straight_drift_detect(det, 1.0, 80)
        caught = step is not None
        print(f"{thr:7.1f} | {'O' if caught else 'X':>5} | {step if caught else '-':>7}")
        csv_rows.append(["A3-3_AbsoluteDrift", "AbsoluteDriftTracker",
                         f"abs_threshold_m={thr},warmup=3", "탐지여부",
                         "탐지" if caught else "미탐지", cond])
        csv_rows.append(["A3-3_AbsoluteDrift", "AbsoluteDriftTracker",
                         f"abs_threshold_m={thr},warmup=3", "탐지스텝",
                         str(step) if caught else "", cond])


def exp4_speed(csv_rows):
    """[표 A3-④] 위조 속도별 — 누적 vs 절대 상보성 (config 기본값, 결정적)."""
    cond = ("config 기본값(누적 win=20/thr=15, 절대 thr=25/warmup=3); "
            "속도별 직선 드리프트(최대 120스텝); 결정적")
    print("=" * 66)
    print("[A3-④] 속도별 누적 vs 절대 상보성 (config 기본값, 결정적)")
    print(f"{'스텝(m)':>7} | {'누적탐지':>7} {'누적스텝':>7} | {'절대탐지':>7} {'절대스텝':>7}")
    for step_m in [0.3, 0.5, 1.0, 2.0, 5.0]:
        cd_step = _straight_drift_detect(CumulativeDriftDetector(), step_m, 120)
        ad_step = _straight_drift_detect(AbsoluteDriftTracker(), step_m, 120)
        cdc, adc = cd_step is not None, ad_step is not None
        print(f"{step_m:7.1f} | {'O' if cdc else 'X':>7} {cd_step if cdc else '-':>7} | "
              f"{'O' if adc else 'X':>7} {ad_step if adc else '-':>7}")
        csv_rows.append(["A3-4_SpeedSweep", "CumulativeDriftDetector",
                         f"step_m={step_m}", "누적_탐지스텝",
                         str(cd_step) if cdc else "미탐지", cond])
        csv_rows.append(["A3-4_SpeedSweep", "AbsoluteDriftTracker",
                         f"step_m={step_m}", "절대_탐지스텝",
                         str(ad_step) if adc else "미탐지", cond])


def main():
    ensure_src_cwd()
    np.random.seed(42)   # 실험 ①의 랜덤 test 샘플 추출 재현용
    data = load_csv(DATA_PATH)
    n = int(len(data) * 0.7)
    train, test = data[:n], data[n:]
    print(f"데이터 {len(data)}행 | train {len(train)} / test {len(test)} | seed=42")
    print("※ 오프라인/합성 피처레벨 민감도 분석 (SITL/실비행 아님)")

    csv_rows = []
    exp1_contamination(train, test, csv_rows)   # 랜덤 → 먼저 실행(seed 순서 고정)
    exp2_cumulative(csv_rows)
    exp3_absolute(csv_rows)
    exp4_speed(csv_rows)

    with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["experiment", "detector", "parameters", "metric", "value",
                    "condition"])
        w.writerows(csv_rows)

    print("=" * 66)
    print(f"CSV 저장: {OUT_PATH}  ({len(csv_rows)}행)")
    print("해석: 파라미터 변화에 따라 결과가 예측 가능하게 변하고, 누적 드리프트와")
    print("      절대이탈이 상보적으로 작동함(느린 위조는 절대이탈이 커버). '무조건")
    print("      완벽 방어'가 아님.")


if __name__ == "__main__":
    main()
