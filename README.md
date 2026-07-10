# Ghost Pilot — DAH 2026 AI 드론 공격·방어 통합 시스템

보호 미적용 SITL MAVLink 환경에서 "유령 조종사(Ghost Pilot)" 공격과,
이를 실시간으로 탐지·차단·복구하는 AI 방어 에이전트를 구현한다.

핵심 아이디어는 **점진적 위조(gradual spoofing)** 다.
공격자가 한 번에 1m씩만 위치를 조작하면 규칙 기반 탐지를 우회할 수 있다.
이 "탐지 임계값 아래로 숨는" 공격을 **누적 드리프트 추적**으로 잡아내는 것이
본 프로젝트의 방어 핵심이다.

SITL 실측 기준으로 `GLOBAL_POSITION_INT` 단독 송신은 FC의 GPS/항법 입력으로
반영되지 않는 legacy 비교 경로다. 현재 확인된 입력 피벗은 `GPS_INPUT`
(`GPS1_TYPE=14`, MAVLink GPS) direct-link PoC이며, 표준 MAVProxy UDP 입력
경유는 미전달이다. `HIL_GPS`는 이 ArduPilot ref에서 지원 제거되어 실효
경로로 쓰지 않는다.

선행연구, 명칭 충돌, GNSS deception/MAVLink injection 결합 모델, B안 SITL 검증
결과는 `docs/GHOSTPILOT_GNSS_DECEPTION.md`에 정리한다.

---

## 프로젝트 구조

```
DAH2026_GhostPilot/
├── README.md
├── requirements.txt
├── server.py                 # 폐루프 시뮬레이터 FastAPI 백엔드
├── ghostpilot_ui.html        # 폐루프 시뮬레이터 브라우저 UI
├── src/
│   ├── config.py             # 모든 임계값·상수 중앙 설정 (단일 출처)
│   ├── utils.py              # 공통: 피처 추출, 거리 계산, 로그, 환경 보정
│   ├── collect_baseline.py   # SITL에서 정상 비행 데이터 수집
│   ├── gen_synthetic_data.py # (테스트용) 합성 정상 데이터 생성
│   ├── attack_agent.py       # 공격: 스니핑 / 명령 인젝션 / 점진적 위조
│   ├── defense_layer1.py     # 방어 L1: 규칙 기반 즉각 차단
│   ├── defense_layer2.py     # 방어 L2: Isolation Forest + 누적 드리프트
│   ├── main_defense.py       # 방어 통합 실행 (L1+L2+대응 결정)
│   ├── demo_offline.py       # SITL 없이 전체 흐름 재현 (검증용)
│   ├── demo_adaptive.py      # 적응형 공격 폐루프 데모 (공격↔방어 상호 적응)
│   ├── demo_stateful.py      # 4상태 방어 vs 단일 윈도우 방어 (before/after)
│   ├── run_experiments.py    # 정량 실험 자동화
│   ├── param_sensitivity.py  # A3 파라미터 민감도 분석(재현 스크립트, 오프라인)
│   ├── evaluate_metrics.py   # 혼동행렬·정밀도/재현율/F1·MTTD 평가
│   ├── plot_results.py       # 실험 결과 그래프 생성
│   └── plot_agentic.py       # 에이전트 폐루프 그림(상태 분리·톱니) 생성
├── scripts/
│   ├── prepare_ardupilot_sitl.sh         # ArduPilot clone/submodule/SITL build 자동 준비
│   ├── sitl_latest_gps_input_smoke.sh    # B안 GPS_INPUT 반영 smoke test
│   └── sitl_gps_input_smoke.sh           # 구형 dronekit-sitl 비교용 smoke test
├── models/
│   └── isoforest.pkl         # 학습된 Isolation Forest 모델
├── data/
│   └── normal_flight.csv     # 정상 비행 데이터
├── results/
│   ├── experiment_results.csv
│   ├── param_sensitivity.csv
│   ├── adaptive_loop.csv
│   ├── stateful_loop.csv
│   ├── metrics_summary.csv
│   ├── fig_detection_comparison.png
│   ├── fig_summary.png
│   ├── fig_divergence_map.png
│   ├── fig_divergence_time.png
│   ├── fig_adaptive_saw.png
│   ├── fig_stateful.png
│   ├── fig_confusion.png
│   ├── fig_metrics.png
│   └── fig_kinematic.png
└── docs/
    ├── GHOSTPILOT_GNSS_DECEPTION.md
    ├── demo_video_link.txt
    ├── AGENT_LOG.md
    ├── TECHNICAL_FINDINGS.md
    ├── REPORT_HANDOFF.md
    └── GhostPilot_SITL_Pivot_Explainer.html
```

---

## 설치

```bash
pip install -r requirements.txt
pip install fastapi uvicorn
```

B안 SITL PoC는 `scripts/prepare_ardupilot_sitl.sh`가 ArduPilot clone, submodule
초기화, SITL configure/build를 수행한다. 기본 ref는 이 프로젝트에서 검증한
ArduPilot commit `5152cde4046b6c0bac5de44fc5d8d0caa925f041`이다.

---

## 폐루프 시뮬레이터 (브라우저 UI)

공격자↔방어자가 실시간으로 반응하는 폐루프를 브라우저에서 시각화한다.
Python 백엔드가 실제 방어 모듈(`defense_layer1/2`, `main_defense`)을 직접 실행하고
결과를 WebSocket으로 프론트엔드에 스트리밍한다.

### 동작 구조

```
브라우저 (ghostpilot_ui.html)
    │  WebSocket  ws://localhost:8000/ws
    ▼
server.py (FastAPI)
    │  import
    ▼
src/defense_layer1.py   ← 실제 방어 로직 실행
src/defense_layer2.py
src/main_defense.py
```

### 실행 순서

**최초 1회만 실행:**

```bash
# 1. 프로젝트 루트로 이동
cd DAH2026_GhostPilot

# 2. 합성 정상 데이터 생성
cd src
python gen_synthetic_data.py      # → ../data/normal_flight.csv

# 3. Isolation Forest 모델 학습
python defense_layer2.py --train --data ../data/normal_flight.csv
#                                        → ../models/isoforest.pkl
cd ..
```

**매번 실행:**

```bash
# 4. 서버 실행 (프로젝트 루트에서)
cd DAH2026_GhostPilot
python server.py
```

```
# 5. 브라우저 접속
http://localhost:8000
```

헤더에 **"서버 연결됨"** 초록 배지가 뜨면 정상 연결된 것이다.

### 시뮬레이터 기능

| 패널 | 내용 |
|------|------|
| **MAP** | 실제 드론 위치(초록)와 GCS 인식 위치(빨강)가 벌어지는 경로 |
| **DRONE HUD** | 드론 1인칭 시점, 기만량에 따라 글리치·경보 표시 |
| **FORMATION** | 6기 다이아몬드 편대에서 LEAD 드론이 이탈·복귀하는 장면 |
| **차트** | 누적 기만량·윈도우 드리프트·공격 스텝 크기 실시간 그래프 |
| **공격자 패널** | 백오프/가속 판단, 탐지 여부, 이벤트 피드 |
| **4상태 머신** | NORMAL → SUSPICIOUS → ATTACK → RECOVERY 전이 실시간 표시 |

### 모드 설명

- **실제 방어 모듈**: `src/` 폴더의 Python 코드 직접 실행
- **내장 시뮬레이션**: `src/` 로드 실패 시 자동 fallback (동일 로직 내장)
- `models/isoforest.pkl` 있으면 Isolation Forest도 실제로 동작

---

## 빠른 시작 — SITL 없이 검증 (1분)

전체 공격·방어 흐름을 한 번에 확인하려면:

```bash
cd src
python gen_synthetic_data.py   # 합성 정상 데이터 생성
python demo_offline.py         # 공격→탐지→대응 흐름 재현
```

출력 예시:
```
[시나리오 1] 정상 비행       → 대응 없음 (오탐 0)
[시나리오 2] 명령 인젝션     → Layer 1 즉시 차단 (RTL)
[시나리오 3] 위장 GCS       → Layer 1 즉시 차단 (RTL)
[시나리오 4] 점진적 위조     → 누적 드리프트 탐지 (GPS 차단)
```

---

## 정량 실험 실행

```bash
cd src
python run_experiments.py --data ../data/normal_flight.csv
python plot_results.py
```

결과는 `results/`에 CSV와 그래프로 저장된다.

### 파라미터 민감도 분석 (A3, 오프라인/합성)

```bash
cd src
python param_sensitivity.py    # → ../results/param_sensitivity.csv
```

---

## 적응형 공격 폐루프 데모

```bash
cd src
python demo_adaptive.py    # 적응형 폐루프 → results/adaptive_loop.csv
python demo_stateful.py    # 4상태 방어 비교 → results/stateful_loop.csv
python plot_agentic.py     # 보고서용 그림 4장 생성
```

생성되는 핵심 그림:
- `fig_divergence_map.png` — 실제 경로 vs GCS 인식 경로(상태 분리 = 기만)
- `fig_divergence_time.png` — 상태 이탈량의 시간 변화 + 첫 탐지(MTTD) 시점
- `fig_adaptive_saw.png` — 탐지되면 백오프, 안 걸리면 가속하는 위조 속도 톱니
- `fig_stateful.png` — 4상태 히스테리시스 방어가 백오프 회피를 차단하는 효과

### 방어 강화: 다중 신호 + 4상태 히스테리시스

단일 윈도우 드리프트 탐지는 공격이 백오프하면 순변위가 임계 아래로 떨어져
회피가 가능하다. 이를 세 가지로 막는다:

- **절대 이탈 추적**(`AbsoluteDriftTracker`): 고정 앵커 대비 총 이탈을 추적 →
  백오프해도 이미 벌어진 기만은 사라지지 않는다.
- **운동학 잔차 교차검증**(`KinematicConsistencyDetector`): 보고 속도를 적분한
  추측항법과 보고 위치의 잔차를 추적 → 위치·속도 불일치(위조)를 잡되,
  **정상 기동은 오탐하지 않는다**.
- **4상태 머신**(`DefenseStateMachine`): NORMAL→SUSPICIOUS→ATTACK→RECOVERY.
  연속 확증으로 오탐 억제, ATTACK 확정 후 절대 이탈이 남는 한 상태 유지.

### 정량 지표 평가

```bash
cd src
python evaluate_metrics.py   # metrics_summary.csv, fig_confusion/metrics/kinematic.png
```

| | 정밀도 | 재현율 | F1 | 미탐지 스트림 | 공정 MTTD |
|---|---|---|---|---|---|
| OLD (윈도우+무상태) | 1.00 | 0.69 | 0.818 | 3 / 12 | 4.6 |
| **NEW (다중신호+4상태)** | 1.00 | **0.82** | **0.902** | **0 / 12** | 7.4 |

---

## SITL 실전 실행 (3-터미널 구성)

> ⚠️ **SITL 검증 상태**: `--mode ghost`의 `GLOBAL_POSITION_INT` 위조는 오프라인/FakeMsg
> 환경에서만 반영된다. 실제 SITL FC에서는 항법 입력에 반영되지 않는다.
> SITL 위치 위조는 `GPS_INPUT` direct-link 피벗 PoC까지만 확인됐다.
> 상세: [docs/REPORT_HANDOFF.md](docs/REPORT_HANDOFF.md)

### ArduPilot SITL 자동 준비

```bash
bash scripts/prepare_ardupilot_sitl.sh
```

### B안 GPS_INPUT smoke test

```bash
bash scripts/sitl_latest_gps_input_smoke.sh
```

### 3-터미널 데모

**터미널 1 — SITL 드론:**
```bash
cd ardupilot/ArduCopter
sim_vehicle.py -v ArduCopter --console --map
```

**터미널 2 — 방어 에이전트:**
```bash
cd src
python collect_baseline.py --duration 300   # 최초 1회
python defense_layer2.py --train            # 최초 1회
python main_defense.py --log ../results/detection_log.csv
```

**터미널 3 — 공격 에이전트:**
```bash
cd src
python attack_agent.py --mode ghost-gps --set-gps-type --verify \
                       --iterations 60 --step-m 1.0 --interval 0.5
```

---

## 시스템 아키텍처

```
MAVLink 수신
    │
    ▼
[Layer 1] 규칙 기반 즉각 차단
    · 알 수 없는 SYS_ID       → BLOCK
    · 비행 중 ARM_DISARM      → BLOCK
    · 위치 순간이동(111m+)    → ALERT
    · SEQ 급변               → ALERT
    │  (PASS/ALERT는 Layer 2로)
    ▼
[Layer 2] AI 이상 탐지
    · Isolation Forest       → 단일 스텝 이상 탐지
    · 누적 드리프트 추적       → 점진적 위조 탐지 (핵심)
    · 절대 이탈 추적           → 백오프 회피 차단
    · 운동학 잔차 교차검증     → 정상 기동 오탐 방지
    │
    ▼
[4상태 히스테리시스 머신]
    NORMAL → SUSPICIOUS → ATTACK → RECOVERY
    │
    ▼
[대응 결정]
    · BLOCK          → RTL (안전 귀환)
    · 복합 공격       → HOVER (호버링+확인)
    · 점진적 위조     → SWITCH (GPS 차단, IMU 전환)
    · 경보만         → ALERT
```

---

## 왜 두 개의 탐지 계층이 필요한가

| 공격 유형 | Layer 1 (규칙) | Layer 2 IsoForest | Layer 2 Drift |
|---------|:---:|:---:|:---:|
| 명령 인젝션 | 차단 | — | — |
| 위장 GCS (SYS_ID) | 차단 | — | — |
| 급격한 위조 (150m) | 탐지 | 탐지 | 탐지 |
| **점진적 위조 (1m씩)** | **못 잡음** | **못 잡음** | **탐지** |

점진적 위조는 규칙 기반과 단일 스텝 이상탐지를 모두 우회한다.
"같은 방향으로 계속 누적되는" 패턴을 추적하는 드리프트 탐지기만이 이를 잡는다.

---

## 주의

본 코드는 DAH 2026 대회 및 교육 목적의 시뮬레이션 전용이다.
모든 공격 코드는 로컬 SITL 시뮬레이터(127.0.0.1)를 대상으로만 실행한다.
실제 드론이나 타인의 시스템에 대한 무단 사용은 법적 책임을 진다.
