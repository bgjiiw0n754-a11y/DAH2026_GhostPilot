"""
run_experiments.py — 탐지기 특성화(feature-level) 보조 실험
DAH 2026 Ghost Pilot 프로젝트

★ 역할 구분 (중요):
  · 이 스크립트는 개별 탐지기(Isolation Forest / 누적 드리프트)를 '피처 레벨'
    합성 입력으로 빠르게 특성화하는 보조 도구다. 스텝 크기별 탐지율 곡선처럼
    "탐지기 하나가 어떤 조작량에 반응하는가"를 보는 데 쓴다.
  · 방어 전체의 표준 성능 지표(혼동행렬·정밀도/재현율/F1·MTTD, OLD vs NEW)는
    evaluate_metrics.py가 '스트리밍(시간축) 평가'로 산출한다. 보고서의 정량
    성능은 evaluate_metrics.py를 1차 근거로 삼는다.

SITL 없이도 동작하도록 저장된 정상 데이터에 합성 공격을 주입한다.

포함 실험:
  실험 1 : 급격한 위치 조작 → Isolation Forest 탐지율
  실험 2 : 점진적 위조 스텝별 → Isolation Forest 탐지율 곡선
  실험 2B: 점진적 위조 지속   → 누적 드리프트 탐지율·평균 탐지 스텝
  실험 3 : 정상 데이터        → 오탐율(False Positive)

사용법:
  python3 run_experiments.py --data ../data/normal_flight.csv
"""

import argparse
import csv

import numpy as np

from defense_layer2 import AnomalyDetector, CumulativeDriftDetector
from utils import Log, ensure_src_cwd


def load_csv(path):
    data = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            data.append([float(x) for x in row])
    return np.array(data), header


def split_train_test(data, ratio=0.7):
    n = len(data)
    idx = int(n * ratio)
    return data[:idx], data[idx:]


def make_gradual_spoof(normal_sample, drift_m):
    """
    정상 샘플에 점진적 위조를 흉내낸 피처를 생성한다.
    pos_jump_m(피처 index 7)를 소폭 증가시킨다.
    drift_m: 스텝당 이동 거리 (m) — 작을수록 탐지 어려움
    """
    spoofed = normal_sample.copy()
    # 피처 7 = pos_jump_m, 피처 0,1 = lat/lon rate
    spoofed[7] = drift_m
    spoofed[0] = drift_m / 111000 / 0.5   # lat_rate 소폭 변화
    return spoofed


def experiment_1_injection(detector, test_data):
    """
    실험 1: 직접 명령 인젝션은 Layer 1(규칙)이 잡는 영역.
    여기서는 Layer 2 관점에서 '급격한 위치 이동(150m)'을 이상으로
    탐지하는지 확인한다.
    """
    Log.info("=" * 55)
    Log.info("실험 1: 급격한 위치 조작 탐지 (Layer 2)")
    detected = 0
    trials = 100
    for _ in range(trials):
        base = test_data[np.random.randint(len(test_data))]
        # 급격한 위조: 150m 순간이동
        attack = make_gradual_spoof(base, drift_m=150.0)
        is_anom, _ = detector.detect(attack)
        if is_anom:
            detected += 1
    rate = detected / trials * 100
    Log.info(f"  → 탐지율: {rate:.1f}% ({detected}/{trials})")
    return rate


def experiment_2_gradual(detector, test_data):
    """
    실험 2: 점진적 위조 — 스텝 크기별 탐지율.
    스텝이 작을수록(1m) 규칙 기반은 못 잡지만,
    Isolation Forest가 얼마나 잡는지 측정한다.
    """
    Log.info("=" * 55)
    Log.info("실험 2: 점진적 위조 스텝별 탐지율 (Layer 2)")
    results = {}
    trials = 100
    for drift in [1.0, 5.0, 10.0, 30.0, 50.0]:
        detected = 0
        for _ in range(trials):
            base = test_data[np.random.randint(len(test_data))]
            attack = make_gradual_spoof(base, drift_m=drift)
            is_anom, _ = detector.detect(attack)
            if is_anom:
                detected += 1
        rate = detected / trials * 100
        results[drift] = rate
        rule_based = "탐지 가능" if drift > 111 else "탐지 불가"
        Log.info(f"  스텝 {drift:5.1f}m → Layer2 탐지율 {rate:5.1f}%  "
                 f"(규칙기반: {rule_based})")
    return results


def experiment_2b_drift(test_data):
    """
    실험 2-B: 누적 드리프트 탐지기로 점진적 위조를 잡는다.
    Isolation Forest가 놓치는 1m 스텝 위조를,
    '같은 방향 누적'을 추적해 탐지하는지 확인한다.
    """
    Log.info("=" * 55)
    Log.info("실험 2-B: 누적 드리프트 탐지 (점진적 위조 결정적 대응)")
    results = {}
    trials = 50
    base_lat, base_lon = 37.5665, 126.9780
    for drift_step in [0.5, 1.0, 2.0, 5.0]:
        detected = 0
        steps_to_detect = []
        for _ in range(trials):
            drift_det = CumulativeDriftDetector()   # 임계값은 config.py 기준
            caught = False
            lat = base_lat
            step_deg = drift_step / 111000  # m → 도
            # 한 방향으로 계속 위조 (최대 60스텝)
            for s in range(60):
                lat += step_deg
                is_drift, _ = drift_det.update(lat, base_lon)
                if is_drift:
                    caught = True
                    steps_to_detect.append(s + 1)
                    break
            if caught:
                detected += 1
        rate = detected / trials * 100
        results[drift_step] = rate
        avg_steps = (sum(steps_to_detect) / len(steps_to_detect)
                     if steps_to_detect else 0)
        Log.info(f"  스텝 {drift_step:4.1f}m (한 방향 지속) → "
                 f"탐지율 {rate:5.1f}%  평균 {avg_steps:.0f}스텝 만에 탐지")
    return results


def experiment_3_false_positive(detector, test_data):
    """
    실험 3: 정상 데이터 오탐율 측정.
    정상 샘플을 이상으로 잘못 판정하는 비율.
    """
    Log.info("=" * 55)
    Log.info("실험 3: 정상 데이터 오탐율 (False Positive)")
    false_positive = 0
    for sample in test_data:
        is_anom, _ = detector.detect(sample)
        if is_anom:
            false_positive += 1
    rate = false_positive / len(test_data) * 100
    Log.info(f"  → 오탐율: {rate:.1f}% ({false_positive}/{len(test_data)})")
    return rate


def main():
    ensure_src_cwd()
    np.random.seed(42)   # 재현 가능한 수치를 위해 시드 고정
    parser = argparse.ArgumentParser(description="실험 자동화")
    parser.add_argument("--data", default="../data/normal_flight.csv")
    parser.add_argument("--output", default="../results/experiment_results.csv")
    args = parser.parse_args()

    # 데이터 로딩 및 분할
    data, _ = load_csv(args.data)
    Log.info(f"전체 데이터: {len(data)}개")
    train, test = split_train_test(data, ratio=0.7)
    Log.info(f"학습: {len(train)}개, 평가: {len(test)}개")

    # 학습
    detector = AnomalyDetector()
    detector.model.fit(train)
    detector.trained = True
    Log.info("모델 학습 완료")

    # 실험 실행
    r1 = experiment_1_injection(detector, test)
    r2 = experiment_2_gradual(detector, test)
    r2b = experiment_2b_drift(test)
    r3 = experiment_3_false_positive(detector, test)

    # 결과 저장
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["실험", "조건", "탐지방식", "탐지율(%)"])
        writer.writerow(["실험1_급격한위조", "150m", "IsolationForest", f"{r1:.1f}"])
        for drift, rate in r2.items():
            writer.writerow(["실험2_점진적위조", f"{drift}m", "IsolationForest", f"{rate:.1f}"])
        for step, rate in r2b.items():
            writer.writerow(["실험2B_점진적위조", f"{step}m지속", "누적드리프트", f"{rate:.1f}"])
        writer.writerow(["실험3_오탐율", "정상데이터", "IsolationForest", f"{r3:.1f}"])

    Log.info("=" * 55)
    Log.info(f"실험 결과 저장: {args.output}")
    Log.info("이 수치를 보고서 5번(성능 검증)에 표로 삽입하세요.")


if __name__ == "__main__":
    main()
