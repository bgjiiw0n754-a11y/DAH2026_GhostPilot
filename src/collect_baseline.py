"""
collect_baseline.py — 정상 비행 데이터 수집기
DAH 2026 Ghost Pilot 프로젝트

ArduPilot SITL에서 정상 비행 텔레메트리를 수집해 CSV로 저장한다.
이 데이터로 Isolation Forest 방어 모델을 학습한다.

사용법:
  1. 터미널 1: sim_vehicle.py -v ArduCopter --console --map
  2. SITL 콘솔에서 드론을 이륙시키고 여기저기 비행시킨다 (mode guided, arm, takeoff 등)
  3. 터미널 2: python3 collect_baseline.py --duration 300
     → 5분간 정상 비행 데이터 수집

수집이 잘 되려면 드론이 실제로 움직여야 한다. SITL 콘솔에서:
  mode guided
  arm throttle
  takeoff 20
  이후 지도에서 우클릭 → Fly to Here 로 여기저기 이동
"""

import argparse
import csv
import time

import pymavlink.mavutil as mavutil

from utils import FeatureExtractor, Log, ensure_src_cwd


def collect(target, duration, output):
    Log.info(f"SITL 연결 시도: {target}")
    conn = mavutil.mavlink_connection(target)
    conn.wait_heartbeat()
    Log.info(f"연결 성공! SYS_ID={conn.target_system}")

    extractor = FeatureExtractor()
    rows = []
    start = time.time()
    last_report = start

    Log.info(f"{duration}초 동안 정상 비행 데이터 수집 시작...")
    Log.info("(SITL에서 드론을 이륙시키고 움직여 주세요)")

    while time.time() - start < duration:
        msg = conn.recv_match(
            type="GLOBAL_POSITION_INT", blocking=True, timeout=2
        )
        if msg is None:
            continue

        features = extractor.extract(msg)
        if features is not None:
            rows.append(features)

        # 10초마다 진행 상황 보고
        if time.time() - last_report >= 10:
            elapsed = int(time.time() - start)
            Log.info(f"수집 중... {elapsed}s / {duration}s  (샘플 {len(rows)}개)")
            last_report = time.time()

    # CSV 저장
    with open(output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(FeatureExtractor.FEATURE_NAMES)
        writer.writerows(rows)

    Log.info(f"수집 완료! 총 {len(rows)}개 샘플 → {output}")
    if len(rows) < 100:
        Log.alert("샘플이 100개 미만입니다. 드론을 더 오래/많이 움직여 재수집을 권장합니다.")


if __name__ == "__main__":
    ensure_src_cwd()
    parser = argparse.ArgumentParser(description="정상 비행 데이터 수집기")
    parser.add_argument("--target", default="udp:127.0.0.1:14550",
                        help="MAVLink 연결 대상 (기본: udp:127.0.0.1:14550)")
    parser.add_argument("--duration", type=int, default=300,
                        help="수집 시간(초) (기본: 300)")
    parser.add_argument("--output", default="../data/normal_flight.csv",
                        help="출력 CSV 경로")
    args = parser.parse_args()

    collect(args.target, args.duration, args.output)
