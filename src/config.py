"""
config.py — Ghost Pilot 방어/공격 파라미터 중앙 설정
DAH 2026 Ghost Pilot 프로젝트

모든 임계값·상수의 단일 출처(single source of truth)다.
여기 한 곳만 바꾸면 규칙·이상탐지·상태머신 전체에 반영된다.
보고서의 '파라미터 표'도 이 파일을 그대로 옮기면 된다.
민감도 분석 시에도 이 값들만 조정한다.
"""

# ── 텔레메트리 가정 ──
TELEM_HZ = 1.0              # 텔레메트리 주기 가정 (MTTD 스텝→초 환산용)

# ── Layer 1: 규칙 기반 즉각 차단 ──
ALLOWED_SYS_IDS = {1, 255}     # 허가된 GCS SYS_ID
SEQ_JUMP_THRESHOLD = 50        # SEQ 급변 임계
POSITION_JUMP_M = 111.0        # 위치 순간이동 임계 (m)
MAX_SPEED_MS = 30.0            # 물리적 최대 속도 (m/s)
DANGER_COMMANDS = {400}        # 비행 중 금지 명령 (ARM_DISARM)
FLYING_ALT_M = 1.0            # (C2) 이 고도(m) 이상이면 '비행 중'으로 판단

# ── Layer 2: 이상 탐지 ──
ISO_N_ESTIMATORS = 100
ISO_CONTAMINATION = 0.05       # 정상 데이터 중 이상치 비율 가정
ISO_RANDOM_STATE = 42
DRIFT_WINDOW = 20             # 누적 드리프트 윈도우 크기
DRIFT_THRESHOLD_M = 15.0       # 윈도우 순변위 임계 (m)
ABS_THRESHOLD_M = 25.0         # 고정 앵커 절대 이탈 임계 (m)
ABS_WARMUP = 3                # (C3) 앵커 확정 전 신뢰 관측 수
KINEMATIC_THRESHOLD_M = 15.0   # (C1) 위치-속도(추측항법) 잔차 임계 (m)

# ── 4상태 히스테리시스 머신 ──
SM_CONFIRM = 3                # SUSPICIOUS→ATTACK 연속 이상 횟수
SM_CLEAR = 5                 # 상태 완화에 필요한 연속 정상 횟수
SM_RECOVER = 8               # RECOVERY→NORMAL 쿨다운(연속 정상)

# ── 공격 (참고 기본값, 도 단위) ──
SPOOF_STEP_DEG = 0.00001       # 스텝당 약 1.1m
SPOOF_STEP_MAX_DEG = 0.00002   # 가속 상한 약 2.2m
