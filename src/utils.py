"""
utils.py — 공통 유틸리티
DAH 2026 Ghost Pilot 프로젝트

MAVLink 메시지에서 피처를 추출하고, 거리 계산 등 공통 함수를 제공한다.
공격/방어 에이전트 양쪽에서 사용한다.
"""

import math
import os
import sys
import time


# ──────────────────────────────────────────────────────────────
# 실행 환경 보정 (모든 스크립트가 utils를 import하므로 여기 한 곳이면 전체 적용)
# ──────────────────────────────────────────────────────────────
# (A1) 콘솔 UTF-8 강제 — Windows 기본 콘솔(cp949)에서 한글/기호 출력 시
#      UnicodeEncodeError로 죽는 것을 방지한다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass   # 이미 UTF-8이거나 reconfigure 미지원 환경이면 그대로 둔다

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_src_cwd():
    """
    (A2) 작업 디렉터리를 이 파일이 있는 src/로 맞춘다.
    스크립트를 어느 위치에서 실행하든 '../data', '../results' 같은
    상대경로가 항상 올바르게 해석되도록 보정한다.
    각 스크립트의 main()/진입부에서 먼저 호출한다.
    """
    os.chdir(_SRC_DIR)


# ──────────────────────────────────────────────────────────────
# 오프라인 테스트용 가짜 MAVLink 메시지 (B4: 데모/평가 공용 단일 정의)
# ──────────────────────────────────────────────────────────────
class FakeMsg:
    """
    SITL 없이 방어 파이프라인을 테스트하기 위한 가짜 MAVLink 메시지.
    demo_offline / demo_adaptive / demo_stateful / evaluate_metrics 공용.

    사용 예:
      FakeMsg("GLOBAL_POSITION_INT", lat=..., lon=..., vx=0, vy=0,
              alt=20000, sys_id=1, seq=3)
    """

    def __init__(self, msg_type, **kwargs):
        self._type = msg_type
        self.__dict__.update(kwargs)

        class _H:
            srcSystem = kwargs.get("sys_id", 1)
            seq = kwargs.get("seq", 0)
        self._header = _H()

    def get_type(self):
        return self._type


# ──────────────────────────────────────────────────────────────
# 거리 계산
# ──────────────────────────────────────────────────────────────
def haversine_m(lat1, lon1, lat2, lon2):
    """
    두 GPS 좌표 사이의 거리를 미터 단위로 반환 (Haversine 공식)
    위치 순간이동(GPS 스푸핑) 탐지에 사용한다.
    """
    R = 6371000  # 지구 반지름 (m)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ──────────────────────────────────────────────────────────────
# 피처 추출기
# ──────────────────────────────────────────────────────────────
class FeatureExtractor:
    """
    MAVLink 텔레메트리 스트림에서 8개 피처를 추출한다.
    Isolation Forest 학습·추론에 사용하는 피처 벡터를 생성한다.

    피처 목록:
      0. lat_rate   : 위도 변화율 (도/초)
      1. lon_rate   : 경도 변화율 (도/초)
      2. alt        : 고도 (m)
      3. vx         : 북쪽(위도 방향) 속도 (m/s)
      4. vy         : 동쪽(경도 방향) 속도 (m/s)
      5. vz         : 수직 속도 (m/s)
      6. seq_delta  : SEQ 번호 증가량
      7. pos_jump_m : 직전 위치 대비 이동 거리 (m)
    """

    FEATURE_NAMES = [
        "lat_rate", "lon_rate", "alt",
        "vx", "vy", "vz",
        "seq_delta", "pos_jump_m",
    ]

    def __init__(self, fixed_dt=None):
        """
        fixed_dt: None이면 실제 시간(time.time())으로 변화율 계산 (SITL 실전용).
                  값을 주면 그 값을 dt로 고정 (데모·오프라인 테스트용).
                  SITL 텔레메트리는 보통 1Hz이므로 fixed_dt=1.0 권장.
        """
        self.fixed_dt = fixed_dt
        self.last_lat = None
        self.last_lon = None
        self.last_time = None
        self.last_seq = None

    def extract(self, msg):
        """
        GLOBAL_POSITION_INT 메시지에서 피처 벡터(list[float])를 추출한다.
        첫 메시지는 이전 값이 없으므로 None을 반환한다.
        """
        if msg.get_type() != "GLOBAL_POSITION_INT":
            return None

        now = time.time()
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt = msg.alt / 1000.0
        vx = msg.vx / 100.0   # cm/s → m/s
        vy = msg.vy / 100.0
        vz = msg.vz / 100.0

        # SEQ 번호 (헤더에서 추출)
        seq = getattr(msg, "_header", None)
        seq = seq.seq if seq is not None else 0

        # 첫 샘플이면 기준만 저장하고 반환하지 않음
        if self.last_time is None:
            self.last_lat, self.last_lon = lat, lon
            self.last_time, self.last_seq = now, seq
            return None

        dt = self.fixed_dt if self.fixed_dt else max(now - self.last_time, 1e-3)
        lat_rate = (lat - self.last_lat) / dt
        lon_rate = (lon - self.last_lon) / dt
        seq_delta = (seq - self.last_seq) % 256
        pos_jump = haversine_m(self.last_lat, self.last_lon, lat, lon)

        # 상태 갱신
        self.last_lat, self.last_lon = lat, lon
        self.last_time, self.last_seq = now, seq

        return [lat_rate, lon_rate, alt, vx, vy, vz, seq_delta, pos_jump]


# ──────────────────────────────────────────────────────────────
# 로그 헬퍼 (색상 출력)
# ──────────────────────────────────────────────────────────────
class Log:
    """터미널 색상 로그 — 데모 영상에서 가독성 향상"""
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"

    @staticmethod
    def attack(msg):
        print(f"{Log.RED}[ATTACK]{Log.RESET} {msg}")

    @staticmethod
    def defense(msg):
        print(f"{Log.GREEN}[DEFENSE]{Log.RESET} {msg}")

    @staticmethod
    def alert(msg):
        print(f"{Log.YELLOW}[ALERT]{Log.RESET} {msg}")

    @staticmethod
    def block(msg):
        print(f"{Log.RED}[BLOCK]{Log.RESET} {msg}")

    @staticmethod
    def info(msg):
        print(f"{Log.BLUE}[INFO]{Log.RESET} {msg}")
