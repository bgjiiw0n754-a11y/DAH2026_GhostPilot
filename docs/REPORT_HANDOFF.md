# 보고서 인계 요약 (A2 결과물 정리)

> **용도**: 예선 보고서 작성팀이 한 파일만 보면 되도록 A1/A5/A3 결과를 요약한 인계용
> 문서다. 상세 실행 이력은 [AGENT_LOG.md](AGENT_LOG.md), 기술적 발견·의사결정은
> [TECHNICAL_FINDINGS.md](TECHNICAL_FINDINGS.md) 참고.
> **작성 기준일**: 2026-07-06. 수치는 커밋된 오프라인 산출본 기준.
>
> ⚠️ 이 문서의 수치는 **오프라인/합성(FakeMsg) 데이터** 기준과 **SITL 관찰** 기준이 섞이지
> 않도록 각 절에 범위를 명시했다. 보고서에서도 두 범위를 섞지 말 것.

---

## 1. A1 — 오프라인 데모 최종 지표 (예선 본체)

**범위**: FakeMsg/합성 텔레메트리 기반 오프라인 데모. 실제 FC 없음. `src/`에서
`gen_synthetic_data.py → demo_offline.py → evaluate_metrics.py → demo_stateful.py`로 재현.

**핵심 정량 지표** (OLD=윈도우+무상태 / NEW=다중신호+4상태):

| 지표 | OLD | NEW |
|---|---|---|
| 정밀도 / 재현율 / F1 | 1.00 / 0.69 / **0.818** | 1.00 / 0.82 / **0.902** |
| 미탐지 스트림 | 3 / 12 | **0 / 12** |
| 0.5m/스텝(느린 위조) 탐지 | 0 / 3 | **3 / 3** |
| 공정 MTTD(둘 다 탐지분) | 4.6스텝 | 7.4스텝(히스테리시스 비용) |
| 정상 기동 오탐율 | 100% | **0%**(운동학 교차검증) |
| 적응형 백오프 회피 미탐지 | 52스텝 | **0스텝** |
| 적응형 최종 이탈량 | 55.7m | **31.1m** |

**한 줄 요약**: NEW는 탐지가 +2~3스텝 늦어지는 대신(견고성 비용), 오탐 0을 유지하며 놓치는
공격이 없어진다. 특히 느린 위조·백오프 회피를 완전히 차단.

**주의**: 이 표는 오프라인 결과다. `gen_synthetic_data.py`는 시드(42) 고정이지만 재실행 환경에
따라 세부 수치가 반올림 수준에서 달라질 수 있음(핵심 결론 불변, TECHNICAL_FINDINGS 003 참고).
재현 스크립트: `src/demo_offline.py`, `src/evaluate_metrics.py`, `src/demo_stateful.py`.

---

## 2. A5 — SITL 피벗 PoC 요약

**범위**: 실제 ArduPilot SITL(FC 존재) 관찰. 방어 탐지 성능 검증이 아니라 **입력 경로/도달성
확인**.

1. **오프라인**: FC 없는 FakeMsg 환경이라 `GLOBAL_POSITION_INT` 기반 Ghost 공격이 재현됨.
2. **SITL 차이**: 실제 FC에서 `GLOBAL_POSITION_INT`는 FC가 GPS/IMU/EKF로 계산해 내보내는
   **출력 상태 메시지**라, 외부에서 같은 메시지를 보내도 FC 항법 상태에 반영되지 않음.
3. **피벗**: 입력 계열 `GPS_INPUT`으로 전환. **FC direct-link 조건**(SITL TCP 5762 등)에서
   주입 시 GPS fix(1→3D)·raw GPS·EKF 위치가 주입 목표로 반영됨(재현성 2회+작은 offset 확인,
   `gps_id=0` 필수).
4. **방어 입력 스트림 도달성**: 그 위조 위치가 MAVProxy output **14550**(방어 에이전트 입력
   스트림)에서도 관찰됨. → 위조가 방어가 받는 스트림까지 도달.
5. **성격 한정**: 이는 **도달성 확인**이지 방어 탐지 성능 검증이 아님.
6. **경계**: 표준 MAVProxy UDP **입력** 경유는 FC에 미전달(주입은 direct-link, 관찰은 output
   포트라는 비대칭). `HIL_GPS`는 현재 펌웨어에서 제거되어 제외. `SIM_GPS1_*`는 외부 공격
   채널이 아니라 SITL 센서 모델 테스트 도구로 분리.
7. **본선 과제**: `ghost_spoof` 통합, 점진 편향/적응형 백오프 연결, 방어 end-to-end SITL 검증,
   armed 비행 중 거동.

**대표 문장(보고서용)**: "실제 FC 환경에서는 공격 엔진을 `GLOBAL_POSITION_INT` 출력 위조에서
`GPS_INPUT` 같은 항법 입력 계열로 피벗해야 하며, direct-link 조건에서 FC 항법 입력 반영 및
방어 입력 스트림(14550) 도달성을 확인했다. 다만 이는 피벗 PoC이며 공격 시나리오 통합·방어
end-to-end 검증은 본선 과제다."

**보조 설명 자료**: [GhostPilot_SITL_Pivot_Explainer.html](GhostPilot_SITL_Pivot_Explainer.html)
— 오프라인에서는 왜 `GLOBAL_POSITION_INT`가 먹히고, SITL에서는 왜 안 됐고, 왜 `GPS_INPUT`으로
피벗했는지 팀 내부 공유용으로 설명한 정적 HTML.

---

## 3. A3 — 파라미터 민감도 (핵심 표 + 재현 파일)

**범위**: 오프라인/합성 피처레벨 분석(SITL/실비행 아님). **재현 파일**:
- 스크립트: `src/param_sensitivity.py`  (실행: `cd src && python3 param_sensitivity.py`)
- 결과 CSV: `results/param_sensitivity.csv` (컬럼: experiment/detector/parameters/metric/value/condition)
- 공통 조건: data=`data/normal_flight.csv`(합성 1000행), split=70/30, seed=42.
  ⚠️ ②③④는 결정적(고정 시작점 직선 드리프트, trial 무관), ①만 랜덤(seed 좌우).

**① IsolationForest contamination** (오탐율↔민감도 트레이드오프)

| contamination | 정상 오탐율 | 1m | 5m | 10m |
|---|---|---|---|---|
| 0.01 | 0.7% | 0.0% | 4.5% | 10.0% |
| **0.05(기본)** | **4.0%** | **5.0%** | **66.0%** | **67.5%** |
| 0.15 | 11.0% | 18.0% | 100% | 100% |

**② 누적 드리프트 window×threshold** (1m/스텝): `window > threshold(m)` 여야 탐지.
config(win20/thr15)는 마진 확보(20>15) → 20스텝에 탐지. win15/thr15는 미탐.

**③ 절대이탈 threshold** (1m/스텝): threshold = 탐지 지연 노브. thr15→18스텝, thr25(기본)→28,
thr40→43. 항상 결국 탐지.

**④ 위조 속도별 — 누적 vs 절대 상보성** (config 기본값)

| 스텝(m) | 누적 탐지 | 절대 탐지 |
|---|---|---|
| 0.3 | ❌ 놓침 | ✅ 87스텝 |
| 0.5 | ❌ 놓침 | ✅ 53스텝 |
| 1.0~5.0 | ✅ 20스텝 | ✅ 28~8스텝 |

**핵심 결론**: 파라미터 변화에 따른 결과가 **예측 가능**(knife-edge 아님)하고, 아주 느린
위조(≤0.5m/스텝)는 누적 드리프트가 놓치지만 **절대이탈이 결국 커버** → 두 탐지기가 상보적으로
작동. 즉 현재 방어 설정은 "우연히 맞은 값"이 아니라 각 파라미터의 역할이 설명 가능한 구조.
(단, "무조건 완벽 방어"는 아님.)

**방어면 9개 중 A3 범위**: 1~4(규칙 기반)는 A3 제외(A1 오프라인 기능 확인). 5~7(IForest·누적·
절대)이 A3 집중 대상. 8~9(운동학·4상태)는 A3 별도 스윕 안 함(오프라인 데모/stateful 실험에서
효과 확인, 본선 민감도 후보).

---

## 4. 검증 범위 구분 (섞지 말 것)

| 구분 | 내용 |
|---|---|
| **오프라인 검증** | A1 지표 표, A3 민감도(①~④) — FakeMsg/합성 데이터. 예선 본체·정량 근거. |
| **SITL 검증** | A5 — GLOBAL_POSITION_INT 미반영 확인, GPS_INPUT direct-link 반영 및 14550 도달성 확인. 도달성/입력 경로 수준. |
| **본선 과제** | ghost_spoof 통합, 점진 편향·백오프 SITL 연결, 방어 end-to-end SITL 검증, armed 비행 거동, 방어면 8~9 민감도, 프록시·MitM 등 미구현 공격면, IForest 실비행 오탐 튜닝(Finding 002). `HIL_GPS`는 현재 ArduPilot ref에서 지원 제거되어 별도 후보로 분리. |

---

## 5. 보고서 표현 기준

**안전 표현 (사용 가능)**
- "오프라인/FakeMsg 환경에서 GLOBAL_POSITION_INT 기반 Ghost 공격과 방어 탐지를 재현·검증했다."
- "실제 SITL에서 GLOBAL_POSITION_INT는 출력 상태 메시지라 항법 입력에 반영되지 않음을 확인했다."
- "GPS_INPUT direct-link 조건에서 FC 항법 입력 반영 및 방어 입력 스트림(14550) 도달성을
  확인했다(피벗 PoC)."
- "파라미터 변화에 따른 탐지 결과가 예측 가능하며, 누적 드리프트와 절대이탈이 상보적으로
  작동한다."
- "실비행 데이터 재학습 시 IsolationForest 민감도 재조정이 필요함을 확인했다(후속 과제)."

**금지 표현 (쓰지 말 것)**
- ❌ "SITL Ghost 공격 완전 성공" / "GPS_INPUT 공격 완성"
- ❌ "실제 GPS spoofing 구현" / "실제 드론 탈취"
- ❌ "ghost_spoof 통합 완료" / "방어 end-to-end 검증 완료"
- ❌ 오프라인 지표(F1 0.902, 오탐 0% 등)를 SITL/실비행 결과처럼 서술
- ❌ A3 민감도(합성)를 "무조건 완벽 방어" 근거로 서술

---

*상세 근거: [AGENT_LOG.md](AGENT_LOG.md)(작업 이력), [TECHNICAL_FINDINGS.md](TECHNICAL_FINDINGS.md)
(Finding 001 위치 위조 경로, 002 IForest 실비행 오탐, 003 오프라인 검증 한계).*
