"""
gen_synthetic_data.py — 합성 정상 비행 데이터 생성기 (테스트용)

SITL이 없는 환경에서 코드를 검증하기 위해,
현실적인 정상 비행 텔레메트리를 흉내낸 CSV를 생성한다.

실제 대회에서는 이 파일 대신 collect_baseline.py로
SITL 실제 데이터를 수집한다. 이 스크립트는 개발·테스트 전용이다.
"""

import csv
import numpy as np

from utils import FeatureExtractor, ensure_src_cwd

ensure_src_cwd()   # 실행 위치와 무관하게 ../data 경로 보정
np.random.seed(42)

N = 1000
DT = 1.0        # 텔레메트리 주기 가정 (config.TELEM_HZ와 일치)
rows = []

# 정상 비행 시뮬레이션 (B3: 속도와 위치 변화를 '일치'시켜 내부 모순을 제거).
#   실제 정상 비행 = 정지비행(호버) + 완만한 기동의 혼합.
#   먼저 속도를 뽑고, 위치 변화(pos_jump)와 위/경도 변화율을 그 속도에서 유도한다.
#   → 학습 분포가 데모(정지/기동)와 일치하고, 위조(위치는 움직이는데 속도=0)는
#     이 분포 밖으로 벗어나 Isolation Forest도 함께 탐지에 기여한다.
for i in range(N):
    speed = abs(np.random.normal(0, 1.2))       # m/s: 대부분 정지~완만 (호버 포함)
    heading = np.random.uniform(0, 2 * np.pi)
    vx = speed * np.cos(heading)                # 북 속도
    vy = speed * np.sin(heading)                # 동 속도
    vz = np.random.normal(0, 0.2)
    # 위치 이동은 속도와 일치 + 소량의 GPS 잡음
    pos_jump = speed * DT + abs(np.random.normal(0, 0.15))
    lat_rate = vx / 111000.0                    # deg/s (속도에서 유도)
    lon_rate = vy / 111000.0
    alt = np.random.normal(20, 0.5)             # 고도 20m 유지
    seq_delta = 1                              # 정상 SEQ는 1씩 증가

    rows.append([lat_rate, lon_rate, alt, vx, vy, vz, seq_delta, pos_jump])

with open("../data/normal_flight.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(FeatureExtractor.FEATURE_NAMES)
    writer.writerows(rows)

print(f"합성 정상 비행 데이터 {N}개 생성 완료 → ../data/normal_flight.csv")
