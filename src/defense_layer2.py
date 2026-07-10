"""
defense_layer2.py — 방어 Layer 2 (Isolation Forest 이상 탐지)
DAH 2026 Ghost Pilot 프로젝트

정상 비행 데이터로 학습한 Isolation Forest로 이상을 탐지한다.
규칙 기반(Layer 1)이 못 잡는 '점진적 위조'를 잡는 것이 핵심 역할이다.

Isolation Forest를 선택한 이유:
  - 학습이 매우 빠름 (수 초)
  - 정상 데이터만으로 비지도 학습 (레이블 불필요)
  - 코드가 간결 (4일 일정에 적합)
  - LSTM 대비 구현·튜닝 부담이 작음

사용법:
  # 학습
  python3 defense_layer2.py --train --data ../data/normal_flight.csv

  # 단독 테스트 (실시간 탐지)
  python3 defense_layer2.py --detect
"""

import argparse
import csv
import math
import pickle

import numpy as np
from sklearn.ensemble import IsolationForest

import config
from utils import FeatureExtractor, Log, haversine_m, ensure_src_cwd

MODEL_PATH = "../models/isoforest.pkl"


class AnomalyDetector:
    def __init__(self):
        # contamination: 정상 데이터 중 이상치 비율 가정 (5%)
        self.model = IsolationForest(
            n_estimators=config.ISO_N_ESTIMATORS,
            contamination=config.ISO_CONTAMINATION,
            random_state=config.ISO_RANDOM_STATE,
        )
        self.trained = False

    def train(self, csv_path):
        """정상 비행 CSV로 학습"""
        Log.info(f"학습 데이터 로딩: {csv_path}")
        data = []
        with open(csv_path, newline="") as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 스킵
            for row in reader:
                data.append([float(x) for x in row])

        if len(data) < 50:
            Log.alert(f"데이터가 너무 적습니다 ({len(data)}개). "
                      f"최소 50개 이상 권장.")

        X = np.array(data)
        Log.info(f"학습 시작... (샘플 {len(X)}개, 피처 {X.shape[1]}개)")
        self.model.fit(X)
        self.trained = True
        Log.info("학습 완료!")
        return self

    def detect(self, feature_vector):
        """
        피처 벡터 하나를 검사.
        반환: (is_anomaly: bool, score: float)
        score가 낮을수록(음수) 이상. 정상은 양수.
        """
        if not self.trained:
            raise RuntimeError("모델이 학습되지 않았습니다.")
        X = np.array([feature_vector])
        pred = self.model.predict(X)[0]           # 1=정상, -1=이상
        score = float(self.model.decision_function(X)[0])
        return pred == -1, score

    def save(self, path=MODEL_PATH):
        with open(path, "wb") as f:
            pickle.dump(self.model, f)
        Log.info(f"모델 저장: {path}")

    def load(self, path=MODEL_PATH):
        with open(path, "rb") as f:
            self.model = pickle.load(f)
        self.trained = True
        Log.info(f"모델 로딩: {path}")
        return self


class CumulativeDriftDetector:
    """
    누적 드리프트 탐지기 — 점진적 위조(Ghost Pilot)의 결정적 대응.

    Isolation Forest는 '한 스텝'만 보므로 1m 위조를 놓친다.
    이 탐지기는 위치가 '같은 방향으로 계속 누적' 이동하는 패턴을 추적한다.

    핵심 원리:
      - 정상 비행: 위치가 기준점 주변에서 진동 → 순 변위가 작다
      - 점진적 위조: 한 방향으로만 계속 이동 → 순 변위가 계속 커진다

    윈도우 시작점과 현재점 사이의 '직선 거리(순 변위)'가
    임계값을 넘으면 탐지한다. 위치 좌표를 직접 받으므로 시간 왜곡에 강건하다.
    """

    def __init__(self, window=config.DRIFT_WINDOW,
                 drift_threshold_m=config.DRIFT_THRESHOLD_M):
        self.window = window
        self.threshold = drift_threshold_m
        self.positions = []   # [(lat, lon), ...]

    def update(self, lat, lon):
        """실제 위치 좌표(lat, lon)를 받아 누적 드리프트 검사"""
        self.positions.append((lat, lon))
        if len(self.positions) > self.window:
            self.positions.pop(0)

        if len(self.positions) < self.window:
            return False, 0.0

        # 윈도우 시작점 → 현재점 사이 직선 거리 (순 변위)
        start_lat, start_lon = self.positions[0]
        cur_lat, cur_lon = self.positions[-1]
        net_drift = haversine_m(start_lat, start_lon, cur_lat, cur_lon)

        return net_drift > self.threshold, net_drift


class AbsoluteDriftTracker:
    """
    고정 앵커(정상 상태에서 잡은 기준 위치) 대비 '절대 누적 이탈'을 추적한다.

    CumulativeDriftDetector(슬라이딩 윈도우)는 공격이 백오프하면 윈도우 순변위가
    임계값 아래로 떨어져 회피가 가능하다(적응형 공격이 방어를 따돌리는 지점).
    이 추적기는 앵커를 '고정'하므로, 공격이 속도를 늦춰도 이미 벌어진 총
    이탈량은 사라지지 않는다. → 백오프 회피를 막는 결정적 보완.

    주의: 앵커는 방어가 NORMAL일 때만 갱신(재기준)한다. 의심/공격 중에는
    앵커를 고정해 '기준 오염(baseline poisoning)'을 막는다.
    이 방식은 드론이 정지비행/알려진 경로를 유지한다는 가정에서 유효하며,
    자유 기동 시에는 앵커를 계획 경로로 잡는 확장이 필요하다(향후 과제).
    """

    def __init__(self, abs_threshold_m=config.ABS_THRESHOLD_M,
                 warmup=config.ABS_WARMUP):
        self.threshold = abs_threshold_m
        self.warmup = warmup
        self.anchor = None      # (lat, lon)
        self._seen = 0          # (C3) 앵커 확정 전 신뢰 관측 수

    def set_anchor(self, lat, lon):
        self.anchor = (lat, lon)
        self._seen = self.warmup   # 명시적 재기준(NORMAL 재진입)은 즉시 무장

    def update(self, lat, lon):
        """현재 위치를 받아 (임계 초과 여부, 절대 이탈 m) 반환."""
        # (C3) 초기 warmup 동안은 앵커를 확정하지 않고 신뢰 관측만 쌓는다.
        #      시작 순간의 단발 이상치 하나로 앵커가 오염되는 것을 완화한다.
        if self._seen < self.warmup:
            self._seen += 1
            self.anchor = (lat, lon)   # warmup 동안 최신 위치로 갱신
            return False, 0.0
        d = haversine_m(self.anchor[0], self.anchor[1], lat, lon)
        return d > self.threshold, d


class KinematicConsistencyDetector:
    """
    (C1) 위치-속도 운동학 교차검증기.

    보고된 속도(vx=북, vy=동)를 적분한 추측항법(dead-reckoning) 위치와
    보고된 GPS 위치의 '잔차(residual)'를 추적한다.

    - 정상 비행: GPS 위치와 속도 적분이 일치 → 잔차가 작다 (정지든 기동이든).
    - 위치 위조: 위치는 움직이는데 속도는 정상(흔히 0)으로 유지 → 추측항법과
      어긋나 잔차가 누적된다 → 탐지.

    CumulativeDriftDetector(순수 위치 드리프트)와의 결정적 차이:
      드리프트 탐지는 '정상 기동(위치가 실제로 이동)'도 위조로 오탐할 수 있다.
      이 검출기는 위치와 속도가 함께 움직이면 잔차가 0이라 오탐하지 않는다.
      = 위치·속도 두 신호의 교차검증(다중 신호 방어).
    """

    def __init__(self, threshold_m=config.KINEMATIC_THRESHOLD_M):
        self.threshold = threshold_m
        self.ref_lat = None
        self.ref_lon = None
        self.dr_north_m = 0.0   # 기준점 대비 추측항법 누적 변위(m)
        self.dr_east_m = 0.0

    def reset(self, lat, lon):
        self.ref_lat, self.ref_lon = lat, lon
        self.dr_north_m = self.dr_east_m = 0.0

    def update(self, lat, lon, vx, vy, dt):
        """
        lat, lon: 보고된 위치.  vx: 북 속도(m/s), vy: 동 속도(m/s), dt: 간격(s).
        반환: (임계 초과 여부, 잔차 m)
        """
        if self.ref_lat is None:
            self.reset(lat, lon)
            return False, 0.0
        # 속도 적분 → 추측항법 변위 누적
        self.dr_north_m += vx * dt
        self.dr_east_m += vy * dt
        # 기준점 대비 '보고된 위치'의 실제 변위(m)를 남/북·동/서로 분해
        sign_n = 1.0 if lat >= self.ref_lat else -1.0
        sign_e = 1.0 if lon >= self.ref_lon else -1.0
        act_north = haversine_m(self.ref_lat, self.ref_lon, lat, self.ref_lon) * sign_n
        act_east = haversine_m(self.ref_lat, self.ref_lon, self.ref_lat, lon) * sign_e
        # 잔차 = 실제 위치 변위 − 추측항법(속도 적분) 변위
        res = math.hypot(act_north - self.dr_north_m, act_east - self.dr_east_m)
        return res > self.threshold, res


def train_main(data_path):
    detector = AnomalyDetector()
    detector.train(data_path)
    detector.save()
    Log.info("학습·저장 완료. main_defense.py로 통합 실행하세요.")


def detect_main(target):
    import pymavlink.mavutil as mavutil

    detector = AnomalyDetector().load()
    extractor = FeatureExtractor()

    conn = mavutil.mavlink_connection(target)
    conn.wait_heartbeat()
    Log.defense(f"Layer 2 단독 탐지 시작 SYS_ID={conn.target_system}")

    while True:
        msg = conn.recv_match(
            type="GLOBAL_POSITION_INT", blocking=True, timeout=2
        )
        if msg is None:
            continue
        feat = extractor.extract(msg)
        if feat is None:
            continue
        is_anomaly, score = detector.detect(feat)
        if is_anomaly:
            Log.alert(f"이상 탐지! score={score:.4f}")
        else:
            Log.defense(f"정상 score={score:.4f}")


if __name__ == "__main__":
    ensure_src_cwd()
    parser = argparse.ArgumentParser(description="Layer 2 이상 탐지")
    parser.add_argument("--train", action="store_true", help="학습 모드")
    parser.add_argument("--detect", action="store_true", help="탐지 모드")
    parser.add_argument("--data", default="../data/normal_flight.csv")
    parser.add_argument("--target", default="udp:127.0.0.1:14550")
    args = parser.parse_args()

    if args.train:
        train_main(args.data)
    elif args.detect:
        detect_main(args.target)
    else:
        print("--train 또는 --detect 중 하나를 지정하세요.")
