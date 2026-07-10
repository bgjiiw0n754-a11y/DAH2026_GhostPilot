# Agent 작업 로그

> 코딩 에이전트(Claude)가 이 프로젝트에서 수행한 **작업 이력**을 시간순으로 기록한다.
> 각 항목 상단에 "일시 + 핵심 주제·이유"를 적고, 아래에 실행 명령·수정 파일·결과·다음 작업을
> 상세히 남긴다.
>
> **여기는 "무엇을 했는지"만 기록한다.** 의사결정에 영향을 주는 기술적 발견(왜 그런
> 현상이 생겼는지, 설계를 바꿔야 하는지, 보고서 표현을 조심해야 하는지)은
> `TECHNICAL_FINDINGS.md`에 별도로 기록하고, 여기서는 "→ Finding NNN 참고"로만 링크한다.

---

## 2026-07-05 18:12 — Git 연동 + SITL 실측 검증 착수

**주제/이유**: CODE_WALKTHROUGH.md 로드맵 P0 항목("실제 ArduPilot SITL 미검증")을 메우기
위해 macOS에 ArduPilot SITL을 처음부터 구축하고 공격→방어 파이프라인을 실제로 구동.

**실행 내역**
1. Git 연동
   - 기존 작업 폴더는 git repo가 아니었고 파일이 이미 있어서, 하위 폴더 `2026_DAH/`에
     private 저장소(`github.com/huiju0919/2026_DAH`) 새로 클론.
   - `gh` CLI를 Homebrew 없이 `~/bin`에 직접 설치 → 브라우저 device-flow 로그인
     (`jaedol2023-oss` 계정) → `gh auth setup-git`.
   - 결과: `main` 브랜치, `origin` remote 정상 연결.
2. 코드 구조 파악 + 오프라인 데모 재현
   - venv 생성, `numpy/scikit-learn/matplotlib` 설치.
   - `gen_synthetic_data.py → demo_offline.py → evaluate_metrics.py → demo_stateful.py`
     순서로 실행, CODE_WALKTHROUGH.md 8번 표 수치(F1 0.818→0.902 등)와 일치 확인.
   - 팀 역할분담 다이어그램 검증: `main_defense.py:228`에서 Layer2 4개 탐지기가 실제로
     OR 결합됨을 코드로 확인. (앵커는 이미 `AbsoluteDriftTracker`로 구현 완료 상태라는
     점을 짚어줌 — "앵커 신규 구현"이 아니라 "계획 경로 기반 확장"이 남은 일)
3. ArduPilot SITL 빌드 (macOS arm64)
   - Homebrew 설치(사용자가 직접 sudo 입력 — 에이전트는 PATH 등록만 처리).
   - `git clone --recurse-submodules https://github.com/ArduPilot/ardupilot ~/ardupilot`
     (~1.9GB, git repo 밖 홈 디렉토리 — 경로에 한글/공백 있으면 빌드 스크립트 깨짐).
   - `Tools/environment_install/install-prereqs-mac.sh` 실행 (pyenv·STM32 툴체인 skip).
   - `./waf configure --board sitl && ./waf copter` → `build/sitl/bin/arducopter` 빌드
     성공 (2분 12초, 1423개 파일).
   - MAVProxy 1.8.70 → 1.8.74로 업그레이드 (`--retries` 옵션 미지원 문제 해결).
4. SITL 포트 구조 정리
   - `sim_vehicle.py`는 인스턴스당 출력 포트를 기본 1개(14550)만 연다
     (`Tools/autotest/sim_vehicle.py:1110` 확인). 여러 프로세스가 동시에 같은 포트를
     bind하면 `Address already in use`.
   - `-m "--out udp:127.0.0.1:14551 --out udp:127.0.0.1:14552"`로 재기동해 포트 3개 확보:
     14550=방어, 14551=공격, 14552=이착륙/이동 제어용.
5. 코드 버그 수정 (상세 원인·영향은 → **Finding 003**)
   - `src/utils.py` `FeatureExtractor.extract()`: `seq.mseq` → `seq.seq`
   - `src/utils.py` `FakeMsg._H`: `mseq` 필드명 → `seq`로 통일
   - `src/defense_layer1.py` `check()`: `header.mseq` → `header.seq`
   - `src/attack_agent.py` `ghost_spoof()`: `int(time.time()*1000)` →
     `int(time.time()*1000) % (2**32)` (uint32 오버플로우 방지)
   - 수정 후 `demo_offline.py` 재실행 → 기존 결과와 동일, 회귀 없음 확인.
6. SITL 실측 절차
   - headless SITL 기동, pymavlink로 arm→GUIDED→takeoff→waypoint 반복 전송하는 임시
     제어 스크립트(`/tmp/sitl_autopilot.py`, `/tmp/sitl_fly_pattern.py` — **저장소 밖,
     git에 미포함**)로 실제 비행 유도.
   - 중간에 takeoff ACK 확인 없이 진행하다 실제로는 이륙 실패한 채 정지 상태였던 것을
     뒤늦게 발견 → ARM/TAKEOFF ACK·고도 변화를 직접 확인하는 방식으로 재확인 후 해결.
   - `collect_baseline.py`로 실비행 텔레메트리 479개 수집
     (`data/normal_flight_sitl.csv`) → `defense_layer2.py --train`으로 재학습.
   - `main_defense.py`(14550)를 무공격 상태로 띄워 안정성 확인 → 간헐적 오탐 발견
     (→ **Finding 002**).
   - `attack_agent.py --mode ghost`(14551)로 실제 위조 공격 실행 → 60스텝/누적 66.6m까지
     크래시 없이 정상 실행됨(버그 수정 후).
   - 포트 14550 원시 트래픽을 실시간 감시하는 별도 스크립트로 공격 주입 시점 대조 →
     위조값이 방어 쪽에 전혀 도달하지 않음 확인 (→ **Finding 001**, 이번 세션 핵심 결론).

**결과 요약**
- ArduPilot SITL이 macOS에서 정상 빌드·구동됨. 앞으로 반복 검증 가능한 환경 확보.
- 진짜 코드 버그 2건 수정 완료(mseq/seq, time_boot_ms 오버플로우).
- **결론: 현재 구현의 `ghost_spoof()`(GLOBAL_POSITION_INT 기반)는 실제 SITL에서 방어
  쪽에 전혀 반영되지 않는다 — 공격이 사실상 no-op.** (Finding 001, OPEN)
- 부가 발견: 실비행 데이터로 재학습한 IsolationForest가 간헐적 오탐을 일으켜 4상태
  머신이 NORMAL로 복귀 못 함. (Finding 002, OPEN)

**다음 작업**
- [ ] `ghost_spoof()`를 `GPS_INPUT`/`HIL_GPS` 기반으로 재작성 (Finding 001 조치)
- [ ] IsolationForest 실비행 오탐 튜닝 (Finding 002 조치)
- [ ] `/tmp/sitl_autopilot.py`, `/tmp/sitl_fly_pattern.py`를 정식 스크립트로 다듬어
      저장소에 반영할지 논의 (예: `src/sitl_flight_helper.py`)
- [ ] 수정한 파일(`utils.py`, `defense_layer1.py`, `attack_agent.py`) 커밋 여부 확인
- [ ] SITL 포트 분리 이슈를 README의 "SITL 실전 실행" 섹션에 반영할지 논의

**SITL 프로세스 상태 (세션 종료 시점)**: `arducopter`/`mavproxy`/이동 제어 스크립트가
계속 백그라운드에서 실행 중이었음(사용자에게 종료 여부 확인 대기).

---

## 2026-07-05 ~19:00 — git 정리, SITL 버그 수정 커밋/푸시

**주제/이유**: 팀 의사결정(기본안 A 채택) 전, SITL 테스트 부산물이 git 작업 트리를
오염시킨 상태를 정리하고 실제 버그 수정만 커밋.

**실행 내역**
- SITL 실측 중 `models/isoforest.pkl`(실비행 데이터로 재학습됨)과 `data/normal_flight.csv`
  (재실행 산출물이 커밋 원본과 달라짐)가 git 작업트리에서 modified 상태인 것을 발견.
- `git restore`로 위 2개 + `results/fig_*.png`, `metrics_summary.csv`, `stateful_loop.csv`
  (반복 실행 부산물) 전부 원상복구.
- `src/utils.py`, `src/defense_layer1.py`, `src/attack_agent.py` 3개 파일만 스테이징 →
  커밋 `b9e9b42` "Fix SITL compatibility bugs" → `origin/main` 푸시.
- `docs/AGENT_LOG.md`, `docs/TECHNICAL_FINDINGS.md`, `venv/`, `.DS_Store`류, SITL 테스트
  부산물(`data/normal_flight_sitl.csv`, `results/defense_status_sitl.json`)은 이번 커밋에서
  의도적으로 제외 (문서는 검수 후 별도 커밋 예정).

**결과 요약**
- 원격 저장소에 SITL 호환성 버그 수정 3건만 깨끗하게 반영됨. 오프라인 데모용 데이터/모델/
  결과 파일은 커밋 시점 기준 원본 그대로 유지.

**다음 작업**
- [ ] `docs/AGENT_LOG.md`, `docs/TECHNICAL_FINDINGS.md` 검수 후 별도 커밋

---

## 2026-07-06 00:38 — 팀 의사결정(기본안 A) 확정, 오프라인 데모 최종 고정(A1) 완료

**주제/이유**: 팀 논의 결과 "기본안 A"(오프라인 데모를 예선 본체로 확정, SITL은 최소
PoC만 진행)로 확정. 이에 따른 실행 계획(사진 체크리스트 A1~A6, 진행 순서는
A1→A5→A3→A2→A6, A4는 보류)에 착수하며, 첫 단계로 어제 수정한 SITL
호환성 버그 3건이 오프라인 데모 수치에 영향이 없는지 최종 확인.

**실행 내역**
- `cd src && python gen_synthetic_data.py` → 정상 비행 합성 데이터 1000개 재생성
  (`data/normal_flight.csv`). 이 스크립트는 `np.random.seed(42)`로 시드가 고정돼 있어
  재실행 자체는 결정적이지만(반복 실행 시 서로 동일), **현재 git에 커밋된 원본 산출물과는
  달라짐**(일부 산출물·세부 수치에서 차이 발생, 원인 미확정). 이번 검증 후 원본은
  `git restore`로 되돌려놓음(아래 "정정" 항목 참고).
- `python demo_offline.py` → 4개 시나리오(정상 비행 / 명령 인젝션 / 위장 GCS / 점진적
  위조) 전부 기존과 동일하게 재현됨 확인.
- `python evaluate_metrics.py` → OLD/NEW 혼동행렬·정밀도/재현율·F1·MTTD·스텝별 탐지·
  운동학 오탐율 등 핵심 결론(F1 개선, 미탐지 0/12, 백오프 회피 차단, 정상 기동 오탐율 0%)은
  CODE_WALKTHROUGH.md 8번 표와 동일하게 재현됨. 단, 위 산출물 차이로 인해 세부 수치는 원본과
  완전히 같지 않음(예: OLD F1 0.818→0.817, OLD MTTD 4.6→4.8 — 반올림 수준의 작은 차이이며
  결론에는 영향 없음).
- `python demo_stateful.py` → 적응형 백오프 대결에서 NEW가 OLD 대비 회피 스텝
  52→0으로 재현됨 확인. 최종 이탈량도 큰 틀에서 동일(위와 같은 산출물 차이로 소수점 차이
  있을 수 있음).

**결과 요약**
- **A1 완료.** SITL 호환성 버그 수정(mseq→seq, time_boot_ms 오버플로우 방지)이 오프라인
  데모의 **핵심 결론**에 영향을 주지 않음을 전체 파이프라인 재실행으로 확인. 이 시점
  기준으로 오프라인 데모를 "예선 본체"로 확정함.
- Finding 003(오프라인 검증의 한계)에서 남겨뒀던 "회귀 없음 확인"이 `demo_offline.py`
  단독 실행 수준이었는데, 이번에 전체 파이프라인(gen_synthetic_data→demo_offline→
  evaluate_metrics→demo_stateful)으로 범위를 넓혀 재확인함.
- **정정(2026-07-06)**: 위 재실행 과정에서 `gen_synthetic_data.py`가 원본과 다른 산출물을
  덮어써서 `data/normal_flight.csv`, `results/fig_confusion.png`, `results/fig_kinematic.png`,
  `results/fig_metrics.png`, `results/metrics_summary.csv`, `results/stateful_loop.csv` 6개
  파일이 git 작업트리에서 modified 상태가 됐던 것을 확인하고 `git restore`로 원상복구함.
  처음에 "정확히 일치"라고 기록했던 것은 부정확한 표현이었음(위 실행 내역에서 정정).

**다음 작업**
- [ ] A5: SITL 최소 PoC (진행 중) — `GPS_INPUT` 최소 PoC **1차 관찰 성공**(위치 출력에
      반영되는 경로로 보이나 추가 재현성 검증 필요). `HIL_GPS`는 미검증 후보로 남음
      (아래 항목 참고)
- [ ] A3: 파라미터 민감도 분석 (`run_experiments.py` 활용, drift threshold/window size/
      contamination/위조 속도별 스윕)
- [ ] A2: 결과물 정리 — A1/A5/A3 결과를 모아 보고서 팀에 전달할 형태로 정리
- [ ] (보류) A4: 데모 영상 시나리오 — 팀장 확인(영상 범위: 핵심 흐름 vs 전체 구현 현황)
      대기 중

---

## 2026-07-06 01:53 — A5(진행 중): GPS_INPUT 최소 PoC 1차 관찰, Finding 001 방향 예비 확인

> ⚠️ **이 항목은 "완료"가 아니라 "1차 관찰" 단계다.** GPS_INPUT은 위치 출력에 반영되는
> 경로로 보이나 재현성·축 불일치·주입 조건·방어 연결 검증이 아직 부족하다. HIL_GPS는
> 아예 미검증. 아래 기록에는 [실제 검증됨]/[파일 기준 확인됨]/[추정]/[미확인] 근거 등급을
> 붙인다.

**주제/이유**: Finding 001에서 제안한 두 후보(`GPS_INPUT`/`HIL_GPS`) 중 `GPS_INPUT`이
`GLOBAL_POSITION_INT`와 달리 실제로 SITL 위치 출력에 반영되는지 최소 PoC로 1차 관찰. 전체
`ghost_spoof()` 재작성은 아니고, 별도의 독립적인 테스트 스크립트로만 관찰(코드베이스 변경 없음).

**실행 내역**
- 포트 14551(공격 역할)로 연결해 `PARAM_REQUEST_READ`로 `GPS_TYPE` 파라미터를 조회했으나
  응답 없음 → 원인 확인 결과, 이 ArduPilot 버전은 파라미터명이 `GPS_TYPE`이 아니라
  **`GPS1_TYPE`**로 변경되어 있었음(`PARAM_REQUEST_LIST`로 전체 1382개 파라미터를 덤프해서
  확인). 이름을 잘못 알고 있었던 것이 원인이며, 라우팅 문제가 아니었음(별도로
  `COMMAND_LONG`(MAV_CMD_REQUEST_MESSAGE) 요청 → `COMMAND_ACK` 정상 수신을 확인해
  attacker→FC 업링크 자체는 살아있음을 검증).
- **[파일 기준 확인됨]** ArduPilot 소스(`~/ardupilot/libraries/AP_GPS/AP_GPS_MAV.cpp`)에서
  `GPS_INPUT` 메시지는 `GPS_TYPE=14`(MAV 백엔드)일 때만 처리되는 것으로 확인(코드 읽기).
  **[실제 검증됨]** `PARAM_SET`으로 `GPS1_TYPE=14` 설정 → `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN`
  으로 SITL 재부팅(같은 프로세스 내 소프트 리부트, 실제 프로세스 재시작 아님) → 재연결 후
  `GPS1_TYPE=14` 유지됨을 PARAM 재조회로 확인.
- **[실제 검증됨]** 최초 1회성 `gps_input_send()` 단발 주입은 반영 안 됨 → 5Hz 연속
  스트리밍으로 바꾸자 반영됨(단발 vs 연속의 임계 주기·원인은 **[미확인]**).
- **[실제 검증됨(1회 관찰)]** 위조 좌표(실제 위치에서 위도 +0.001도, 약 111m 북쪽)를 5Hz로
  10초간 연속 주입하며 포트 14551에서 관찰 → 위치 출력이 이동함. 단 **[미확인]** 의도한
  축(위도)이 아니라 경도 축이 약 0.00049도(약 44m) 움직였고, 원인 불명(EKF 블렌딩/좌표
  처리 관련은 **[추정]**일 뿐). 반복 재현·조건 통제는 아직 안 함.
- **[실제 검증됨(1회 관찰)]** Finding 001 원 테스트와 동일 방식 교차 관찰: 공격 포트(14551)
  주입 + 방어 포트(14550)에서 별도 프로세스로 `GLOBAL_POSITION_INT` 실시간 관찰 → 방어
  포트에서도 위치 출력이 이동함(주입 시작 시 이동, 중단 후 서서히 복귀). `GLOBAL_POSITION_INT`
  주입(반영 안 됨, Finding 001 원 테스트)과는 반대 방향의 관찰 결과.

**결과 요약 (A5 진행 중 — "완료" 아님)**
- **GPS_INPUT 최소 PoC 1차 관찰 성공.** `GPS1_TYPE=14` + 5Hz 연속 스트리밍 조건에서 GPS_INPUT
  주입이 방어 포트(14550)의 위치 출력에까지 반영되는 것으로 **1회 관찰**됨. `GLOBAL_POSITION_INT`
  (Finding 001)와 달리 **위치 출력에 반영되는 경로로 보이나, 추가 재현성 검증이 필요**하다.
- **아직 A5 완료가 아니다.** 재현성(반복/재시작 후), 축 불일치 원인, 주입 주기·offset 의존성,
  단발 불가 이유, 주입 시작/중단 대비 방어 포트 변화의 시간적 대응, `ghost_spoof()` 통합 가능
  여부, 방어 탐지 결과 연결 가능 여부가 모두 미검증 상태.
- **`HIL_GPS`는 이번에 시도하지 않음 — 완전 미검증 후보로 남음.** GPS_INPUT의 관찰 결과를
  HIL_GPS에 옮겨 쓰지 말 것(코드 경로 자체가 다름: `hil_gps_send`).
- SITL은 `GPS1_TYPE=14`로 설정된 채로 유지 중(원복 여부는 팀 논의 필요).

**다음 작업 (A5 마무리용 추가 검증)**
- [x] TECHNICAL_FINDINGS.md Finding 001 상태 업데이트 (OPEN 유지, GPS_INPUT 1차 관찰/
      HIL_GPS 미검증 구분해서 반영)
- [ ] GPS_INPUT 재현성: 동일 조건 반복, SITL 재시작/파라미터 재설정 후 재현 여부
- [ ] 위도 offset인데 경도가 움직인 원인 규명
- [ ] offset 크기·주입 주기(1/2/5/10Hz)별 반영 양상, 단발 불가 이유
- [ ] 주입 시작/중단 시점 ↔ 14550 GLOBAL_POSITION_INT 변화의 시간적 대응
- [ ] `attack_agent`/`ghost_spoof` 경로와 통합 가능 여부(설계 검토, 코드 변경은 별도 요청)
- [ ] 방어 에이전트 탐지 결과와 연결 가능 여부
- [ ] `HIL_GPS` 별도 최소 PoC 검증 (GPS_INPUT과 분리 기록)
- [ ] SITL `GPS1_TYPE`을 1(원래 값)로 되돌릴지 결정
- [ ] A3: 파라미터 민감도 분석 진행

---

## 2026-07-06 03:25 — ⚠️ A5 재검증 중 교란변수 발견: 이전 "1차 관찰"이 오염됐을 수 있음

**주제/이유**: A5를 제대로 마무리하기 위한 추가 검증(재현성 등)을 시작하면서, 실험의
기본 전제(주입이 없으면 위치가 정지해 있어야 함)부터 확인했더니 **드론이 주입 없이도
스스로 움직이고 있었음**을 발견.

**실행 내역**
- **[실제 검증됨]** `GPS_INPUT` 주입을 **전혀 하지 않은 상태**로 포트 14551에서 8초간 위치
  관찰 → 경도축 약 39m, 고도 20.0→21.9m 스스로 이동. 드론은 `armed=True`, `custom_mode=4`
  (GUIDED) 상태.
- **[실제 검증됨]** 원인: 어제 띄운 이동 제어 스크립트 `/tmp/sitl_fly_pattern.py`(PID 3036,
  포트 14552 점유)가 **여전히 드론을 자율 패턴 비행시키는 중**. `ps`로 프로세스 생존 확인.
- **[추정]** 어제(01:53) "GPS_INPUT 주입하니 위치가 이동했다"는 관찰은 이 자율 비행과
  **분리되지 않았음** → 관찰된 이동이 주입 효과인지 fly_pattern 비행인지 구분 불가.
  어제 기록한 "위도 offset을 줬는데 경도가 움직임"도 이 자율 비행(동서 왕복)으로 설명될
  수 있음(경도 왕복 패턴이 관측됨: 어제는 서쪽, 오늘은 동쪽 이동).

**결과 요약**
- **어제의 "GPS_INPUT 1차 관찰 성공"은 교란변수(자율 비행)로 오염됐을 수 있어, 그 자체를
  근거로 삼을 수 없다.** 깨끗한 재검증이 필요.
- 깨끗한 실험을 하려면 드론을 정지(호버) 상태로 만들어야 함 → `fly_pattern` 스크립트
  (PID 3036)를 멈추거나, 드론을 LOITER/BRAKE로 전환해야 함. **둘 다 SITL 런타임 상태를
  바꾸는 조작이라, 진행 전 사용자 확인 대기.**

**다음 작업 (사용자 결정 대기)**
- [x] `fly_pattern` 정지 방법 결정: (a) PID 3036 kill → 사용자 승인, 아래 03:42 항목에서 실행
- [ ] 정지 후 GPS_INPUT 주입을 처음부터 다시 관찰 → 아래 03:42 항목에서 실행
- [ ] 그 뒤에야 재현성/축/주기/offset 등 나머지 검증 진행

---

## 2026-07-06 03:42 — A5 깨끗한 재검증: GPS_INPUT은 현재 설정에서 위치에 영향 없음 (어제 관찰 = 교란)

**주제/이유**: fly_pattern 교란을 제거한 뒤 GPS_INPUT을 처음부터 다시 검증. 사용자 지정
순서(PID 확인→종료→정지 baseline→깨끗할 때만 재검증→시간적 대응→…)를 따름.

**실행 내역**
- **[실제 검증됨]** PID 3036이 `/tmp/sitl_fly_pattern.py 999999`임을 `ps`로 확인 후 종료.
  포트 14552 해제 확인.
- **[실제 검증됨]** 주입 없는 정지 baseline 30초 관찰 → 위도/경도 0.0m, 고도 20.00m 고정.
  **fly_pattern이 어제 교란의 원인이었음이 확정됨**(제거하니 드론이 완전히 정지).
- **[실제 검증됨]** 깨끗한 정지 상태에서 GPS_INPUT(순수 위도 +0.001도≈+111m, 경도 불변)을
  5Hz로 주입: P1 주입전 8s / P2 주입중 15s / P3 주입후 12s. **세 구간 모두 위도·경도·고도
  0.0m 이동.** 공격 포트(14551)·방어 포트(14550) 양쪽 동일. → **정지 드론에서 GPS_INPUT
  주입은 위치를 전혀 움직이지 못함.**
- **[실제 검증됨]** 관련 파라미터·GPS 상태 관측(값 자체는 직접 조회/관찰함):
  - `GPS1_TYPE=14`(MAV 백엔드) — 설정됨.
  - `SIM_GPS1_ENABLE=1` — 시뮬레이터 자체 GPS 활성 상태.
  - `EK3_SRC1_POSXY=3`(EKF 위치 소스 = GPS).
  - 주입 없이도 `GPS_RAW_INT`가 fix_type=6·위성10·안정 위치를 계속 보고.
- **[추정/가능 원인 — 미검증]** 시뮬 GPS가 권위 있는 위치를 EKF에 계속 공급해서, 외부
  GPS_INPUT(111m 벗어난 값)이 무시되거나 EKF innovation 게이팅에 걸리는 것일 수 있음.
  **단 이는 아직 가설이며, EKF source selection·GPS instance 상태·관련 로그로 확인되기
  전까지 [실제 검증됨]이 아니다.**

**결과 요약 (A5 진행 중 — 어제 관찰 뒤집힘)**
- **[실제 검증됨]** 어제(01:53)의 "GPS_INPUT 1차 관찰"은 fly_pattern 자율비행 교란과
  분리되지 않았고, 교란 제거 후 깨끗한 정지 상태에서는 GPS_INPUT 주입이 위치에 아무 영향을
  못 줬다(0.0m).
- **[실제 검증됨]** 현재 SITL 기본 설정(GPS1_TYPE=14, SIM_GPS1_ENABLE=1)에서 GPS_INPUT 주입
  시 위치 변화가 관찰되지 않았다. **[추정]** 이를 실효화하려면 시뮬 GPS를 끄는 등 조건
  변경이 필요할 수 있으며(가설), 그 방향은 "공격자가 실제 GPS를 완전히 대체"하는 다른(더
  강한) 위협 모델이라 진행 전 사용자 확인 필요.
- Finding 001의 원래 결론(`GLOBAL_POSITION_INT`는 반영 안 됨)은 그대로 유효. `GPS_INPUT`이
  그 대안이라는 주장은 **현재로선 근거 부족** — 위 조건에서 재검증/추가 설정 필요.

**다음 작업**
- [x] A안: `SIM_GPS1_ENABLE=0` 통제 환경에서 GPS_INPUT 재검증 → 아래 10:24 항목
- [x] HIL_GPS 별도 검증 → 아래 10:24 항목

---

## 2026-07-06 10:24 — A안(시뮬 GPS 끈 통제 환경) 조건분리 실험: GPS_INPUT·HIL_GPS 둘 다 미반영

**주제/이유**: "성공 찾기"가 아니라 **조건 분리 실험**. 시뮬 GPS를 꺼서 GPS_INPUT을 유일
GPS 소스가 되게 한 통제 환경에서, GPS_INPUT/HIL_GPS 주입이 실제로 GPS fix·위치에 반영되는지
분리 관찰. (표현: "기본 SITL에서 성공"이 아니라 "시뮬 GPS를 끈 통제 환경에서 경로 반영 여부
확인"으로 기재)

**1) 변경 전 파라미터 스냅샷 [실제 검증됨] (복원 기준)**
```
GPS1_TYPE=14  GPS2_TYPE=0  GPS_PRIMARY=0  GPS_AUTO_SWITCH=1  GPS_AUTO_CONFIG=1
SIM_GPS1_ENABLE=1  SIM_GPS2_ENABLE=0  SIM_GPS1_TYPE=1
EK3_SRC1_POSXY=3  EK3_SRC1_VELXY=3  EK3_SRC1_POSZ=1  AHRS_EKF_TYPE=3
```
→ 원복 시 `SIM_GPS1_ENABLE=1`로 되돌리면 됨(GPS1_TYPE=14는 A5 시작 때 바꾼 값이라
별도 판단).

**2) 실험 절차·결과 [실제 검증됨]**
- `SIM_GPS1_ENABLE=0` 설정 후 재부팅 → 파라미터 유지 확인(`SIM_GPS1_ENABLE=0`, `GPS1_TYPE=14`).
- **주입 없는 baseline**: `GPS_RAW_INT` fix_type 6→**1(fix 없음)**, sats=3, 위치가 마지막
  값에 **얼어붙음**(EKF가 갱신할 GPS 없음). 드론은 GPS 상실로 `custom_mode=9`(LAND) 전환.
- **GPS_INPUT 주입**(위도 +0.0005≈+55m, 5Hz, 주입전6s/중20s/후10s): **세 구간 모두
  fix_type=1 유지, 위치 0.0m 이동.** 공격 포트(14551)·방어 포트(14550) 동일. → 시뮬 GPS를
  꺼도 GPS_INPUT이 fix를 회복시키지도 위치를 움직이지도 못함.
- **HIL_GPS 주입**(동일 목표, 5Hz) — **GPS_INPUT과 별도 항목**: 마찬가지로 fix_type=1 유지,
  위치 0.0m 이동. 전혀 반영 안 됨.

**3) 소스 확인 [파일 기준 확인됨]**
- `GCS_Common.cpp:4510`: `GPS_INPUT`은 코드상 `AP::gps().handle_msg()`로 라우팅됨(즉 FC에
  도달만 하면 GPS 드라이버가 처리하도록 되어 있음).
- `AP_GPS_MAV.cpp`: `GPS_INPUT` 케이스만 존재, **`HIL_GPS` 케이스 없음** → HIL_GPS는 이 GPS
  드라이버 경로로는 처리되지 않음(별도 HIL/SITL 경로 필요).

**결과 요약**
- **[실제 검증됨] GPS_INPUT은 시뮬 GPS를 끈 통제 환경에서도 미반영.** (기본 설정에서도,
  통제 환경에서도 위치 변화 없음)
- **[실제 검증됨] HIL_GPS도 미반영** (GPS_INPUT과 분리해서 별도 확인). 소스상 GPS 드라이버가
  HIL_GPS를 처리하지 않는 것과 일치.
- **[추정/가능 원인 — 미확정]** GPS_INPUT은 코드상 GPS 드라이버로 라우팅되도록 되어 있는데도
  반영이 안 되므로, 병목은 (a) MAVProxy가 외부(14551)에서 온 GPS_INPUT을 master(FC)로
  포워딩하지 않거나, (b) `AP_GPS_MAV`의 `state.instance != packet.gps_id` 불일치, (c) EKF
  게이팅 중 하나로 좁혀짐 — **아직 어느 것인지 확정 못 함**(직접 master 주입 경로 미확보).
- 이전 "[추정] 시뮬 GPS 우선"은 이번에 **약화됨**: 시뮬 GPS가 없어도 GPS_INPUT이 GPS 소스로
  등록조차 안 되므로, 원인은 그보다 앞단(전달/수용)일 가능성이 큼.

**피벗 방향 (사용자 지정 step 9대로 기록)**
- GPS_INPUT/HIL_GPS는 현재 메시지/파라미터/라우팅 구성에서 **미반영** → **SIM 파라미터 경로로
  피벗 검토 권장.** 특히 SITL 내장 GPS 글리치/오프셋 파라미터(`SIM_GPS1_GLTCH_X/Y/Z`,
  `SIM_GPS1_POS_*` 등)는 MAVProxy 포워딩을 거치지 않고 시뮬 GPS 자체를 틀어 위치 위조를
  재현하는 SITL-네이티브 경로여서 유력해 보임 **[추정, 미검증]**. 또는 master 링크에 GPS_INPUT을
  직접 주입하는 경로 확보.

**현재 SITL 상태 (주의)**
- `SIM_GPS1_ENABLE=0`으로 둔 상태 → 드론이 GPS 없이 LAND 모드. **정상 baseline으로 되돌리려면
  `SIM_GPS1_ENABLE=1` 복원 + 재부팅 필요.** 복원 여부는 사용자 확인 대기.

**다음 작업 (사용자 결정 대기)**
- [ ] SITL 원복(`SIM_GPS1_ENABLE=1`) 실행 여부
- [ ] 피벗 경로 선택: (a) `SIM_GPS1_GLTCH/POS` 파라미터로 SITL-네이티브 위조 재현, (b) master
      직접 주입 경로 확보, (c) 여기서 A5를 "SITL 실효 경로 미확보"로 정리하고 본선 이관
- [ ] A3(파라미터 민감도) 등 나머지 A-작업 재개 여부

---

## 2026-07-06 10:45 — ⚠️ SITL 원복 시도 중 MAVProxy 텔레메트리 링크 wedge

**주제/이유**: 사용자 요청으로 `SIM_GPS1_ENABLE=1` 원복 + 재부팅을 실행했는데, 재부팅
직후부터 SITL 텔레메트리가 어느 포트로도 흐르지 않게 됨. 원인·상태 기록.

**실행 내역**
- **[실제 검증됨]** `SIM_GPS1_ENABLE=1` 설정은 성공(PARAM echo 확인) 후 재부팅 명령 전송.
- **[실제 검증됨]** 재부팅 후 14550/14551/14552 세 UDP 출력 포트 모두 raw 소켓으로 3초간
  **0바이트 수신**. pymavlink `wait_heartbeat`도 hang.
- **[실제 검증됨]** 프로세스는 살아 있음: `arducopter`(PID 72350) 7.4% CPU로 구동 중,
  `mavproxy`(72342)도 생존, master TCP(127.0.0.1:5760) ESTABLISHED 유지. 즉 프로세스
  죽음이 아니라 **MAVProxy가 텔레메트리를 UDP 출력으로 포워딩하지 못하는 상태**.
- **[추정/가능 원인 — 미확정]** `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` 소프트 재부팅이
  MAVProxy↔FC master 링크를 stale 상태로 만든 것으로 보임(TCP는 ESTABLISHED로 남았지만
  실제 데이터 흐름 끊김). 앞선 재부팅들(GPS1_TYPE, SIM off/on)은 우연히 정상 복귀했음.

**결과 요약**
- **[실제 검증됨] 현재 SITL은 파라미터상으론 baseline(SIM_GPS1_ENABLE=1)로 복원됐으나,
  MAVProxy 텔레메트리 링크가 wedge되어 실사용 불가 상태.**
- 깨끗한 baseline으로 되돌리는 확실한 방법은 **sim_vehicle 세션 재시작**(arducopter+mavproxy
  kill 후 재기동). 이는 더 큰 조작이라 진행 전 사용자 확인 필요. 재기동 시 저장된 파라미터
  (`GPS1_TYPE=14`, `SIM_GPS1_ENABLE=1`)가 로드됨.

**다음 작업 (사용자 결정 대기)**
- [ ] sim_vehicle 재시작으로 SITL 복구할지 (추천). 재시작 시 `GPS1_TYPE`을 1로 되돌릴지도
      함께 결정
- [ ] 복구 후 SIM_GPS1_* 파라미터 경로(피벗 a) 실험 진행 여부

---

## 2026-07-06 12:55 — SITL 세션 재시작 + SIM_GPS1_* 경로 실험(센서 모델 조작 PoC)

**주제/이유**: wedge된 SITL을 깨끗이 재시작하고(사용자 승인), baseline 확인 후 MAVProxy
주입 경로를 우회하는 SIM_GPS1_* 파라미터 경로를 통제 실험. **목적은 "공격 성공"이 아니라
SITL 센서 모델 조작으로 방어 포트에 위조 위치를 만들 수 있는지 분리 확인.**

**실행 내역**
- **[실제 검증됨]** 기존 세션(arducopter/mavproxy/sim_vehicle) 종료 후 동일 명령으로 재기동
  (`sim_vehicle.py -v ArduCopter -N -w ...`, `-w`=EEPROM 초기화). 재부팅 소프트 명령이 아닌
  프로세스 재시작이라 wedge 해소.
- **[실제 검증됨]** 재시작 후: 14550/14551/14552 텔레메트리 정상, `GPS1_TYPE=1`(원복),
  `SIM_GPS1_ENABLE=1`(유지). baseline 20초 위치 0.00m 정지, fix_type=6·위성10. (드론은 갓
  부팅·지상·disarmed 상태)
- **[파일 기준 확인됨]** 소스(`SIM_GPS.cpp:566-569`): `d.latitude += glitch.x` 등 —
  `SIM_GPS1_GLTCH_X/Y/Z`는 시뮬 GPS 출력 lat/lon(도)/alt(m)에 직접 더해지는 오프셋.
- **[실제 검증됨]** `SIM_GPS1_GLTCH_X=0.00005`(≈+5.5m 위도) 적용 실험(14550 방어 포트 관찰):
  - **RAW GPS**(`GPS_RAW_INT`) 위도가 +5.6m로 이동(적용 후 약 5초 GPS 지연 뒤 나타남).
  - **EKF 추정치**(`GLOBAL_POSITION_INT`)는 급격한 스텝을 초기에는 게이팅(0m 유지)하다가,
    오프셋이 지속되자 결국 +5.6m로 반영됨.
  - GLTCH_X=0으로 제거하니 RAW·EKF 모두 홈(0.0m)으로 **가역적으로 복귀**(GPS 지연으로 인한
    잔상 후 완전 복귀 확인).

**결과 요약**
- **[실제 검증됨] SIM_GPS1_GLTCH_X 경로는 시뮬 GPS 센서값과(지속 시) EKF 추정치·방어 포트
  관측값까지 위조 위치로 이동시킬 수 있음. 가역적.** MAVLink 주입(GLOBAL_POSITION_INT/
  GPS_INPUT/HIL_GPS)이 현재 구성에서 미반영이던 것과 대조됨.
- **[실제 검증됨] EKF는 급격한 5.6m 스텝을 초기 게이팅** → 프로젝트의 핵심 논지(급격한 위조는
  걸리고, 점진적 위조라야 회피)와 정성적으로 부합하는 관측(정량 검증은 아님).
- **⚠️ 성격 구분(중요, 과대해석 금지)**: `SIM_GPS1_GLTCH`는 **시뮬레이터 내부 파라미터**로,
  외부 공격자가 네트워크로 보낼 수 있는 메시지가 아니다. 따라서 이건 **"실제 공격 채널"이
  아니라 "SITL 센서 모델을 조작해 방어에 위조 위치를 주입하는 테스트 도구"**다. 방어
  에이전트에 스푸핑된 위치를 먹여 탐지 로직을 SITL에서 시험하는 용도로는 유효하지만,
  "공격이 성립한다"는 근거로 쓰면 안 된다.
- **미확인/한계**: (1) 드론이 지상·disarmed 상태 관측 — 비행 중 EKF 거동은 다를 수 있음,
  (2) EKF 게이팅→수용 전환 임계·시점 정량 미측정, (3) 점진(1m/스텝) 글리치로 게이팅 회피가
  실제 되는지 미검증, (4) GPS 지연(~5s) 정확한 값 미측정.

**표현 기준(지킴)**: "MAVLink 기반 GLOBAL_POSITION_INT/GPS_INPUT/HIL_GPS는 현재 구성에서
실효 경로 미확보 / SIM_GPS1_* 경로는 SITL 센서 모델 조작 PoC로 분리". "GPS_INPUT 성공"·
"SITL Ghost 공격 성공"·"실제 GPS spoofing 구현"으로 쓰지 않음.

**다음 작업 (사용자 결정 대기)**
- [ ] SIM_GPS1_* 경로를 예선/보고서에서 어떻게 포지셔닝할지 (테스트 도구 vs 위협 재현) 결정
- [ ] 점진적 글리치(1m/스텝)로 EKF 게이팅 회피가 되는지 추가 실험할지
- [ ] 비행(armed) 상태에서의 거동 확인할지
- [ ] 아니면 여기서 A5를 정리(MAVLink 경로 미확보 + SIM 경로는 테스트 도구로 확인)하고
      A3/문서화로 넘어갈지
- [ ] SITL을 이대로 둘지(현재 GLTCH=0, baseline 정상) / 종료할지

---

## 2026-07-06 13:38 — A5 원인 분리(GPS_INPUT): FC 직접 도달 시 반영됨, MAVProxy 경유가 병목

**주제/이유**: A5 핵심(=3번 GLOBAL_POSITION_INT 실패 원인 + 대체 입력 경로)을 좁히기 위해
사용자 지정 3개(① master 직접 주입 시에도 미반영인지 ② gps_id/instance/필드 문제인지
③ HIL_GPS 처리 경로 코드/로그 기준 짧게)만 확인. SIM_GPS1_* 추가 실험은 보류.

**실행 내역**
- **[파일 기준 확인됨] (③ HIL_GPS)**: `AP_GPS_MAV.cpp`는 `GPS_INPUT`만 처리(HIL_GPS 케이스
  없음). `ArduCopter/ReleaseNotes.txt`: **"HIL_GPS message support removed (PR:28593)"**. 즉
  이 ArduPilot 버전에서 HIL_GPS는 **펌웨어에서 제거**되어 처리 경로가 없음. (AP_GPS_MAV.cpp:40
  주석은 낡은 잔재.) → HIL_GPS는 더 파지 않음.
- **[실제 검증됨] FC 직접 TCP 포트 발견**: arducopter가 5760(serial0, MAVProxy 연결)·5762
  (serial1)·5763(serial2)을 LISTEN. 5762로 FC에 직접 연결(heartbeat OK) → MAVProxy 우회 주입
  경로 확보.
- **[실제 검증됨] (① 직접 주입 + ② gps_id)**: `GPS1_TYPE=14`, `SIM_GPS1_ENABLE=0`(GPS_INPUT을
  유일 소스로) 설정·재부팅 후, 5762 직접 링크로 GPS_INPUT을 5Hz 주입(스트림은
  `SET_MESSAGE_INTERVAL`로 요청):
  - 주입 없음: fix=1(없음), sats=0.
  - **gps_id=0 주입: fix 1→3(3D), sats 0→12, raw GPS 위도 = 주입 목표(-35.3630000),
    EKF 위도도 목표로 추종(-35.3630002).** → GPS_INPUT이 FC 항법 입력으로 실제 반영됨.
  - gps_id=1 주입: fix 다시 1, sats 0 → **거부**. 소스의 `state.instance != packet.gps_id`
    (instance 0) 체크와 일치. **gps_id는 0이어야 함.**

**결과 요약 (A5 진행 중 — 원인 규명 진전)**
- **[실제 검증됨] GPS_INPUT은 "불가"가 아니다.** FC에 직접 도달하는 경로(5762)에서는 위치
  입력으로 반영됨(fix 3D 전환, raw·EKF 위치가 주입값 추종). gps_id=0 필수.
- **[실제 검증됨] 기존 "MAVProxy 포트(14551) 경유 미반영"의 원인 = MAVProxy가 GPS_INPUT을
  FC master로 포워딩하지 않기 때문.** 파라미터·gps_id·SIM 설정 모두 동일하고 경로만 달랐는데
  (14551 MAVProxy vs 5762 직접), 직접 경로는 반영·MAVProxy 경로는 미반영. → 이전 [추정]이
  이제 원인으로 확인됨(EKF 게이팅/instance 문제가 아니라 전달 단계 문제였음).
- **[파일 기준 확인됨] HIL_GPS는 펌웨어 제거로 사용 불가.**
- **[실제 검증됨] GLOBAL_POSITION_INT는 여전히 미반영**(출력 상태 메시지 계열, 변동 없음).
- **표현 주의(지킴)**: 이건 "GPS_INPUT 채널이 FC 항법 입력에 반영된다"는 **메커니즘 확인**이지,
  "Ghost 공격 성공/완료"가 아니다. 공격 시나리오 통합(ghost_spoof 연결, 점진 위조, 방어 탐지
  연결)은 아직 안 함. 또한 실제 배포 환경에서 공격자가 FC serial(5762)에 직접 붙는 것은
  별개 전제(접근성)라 그 현실성은 미검토.

**미확인/남은 것**
- MAVProxy가 GPS_INPUT을 포워딩하도록 설정(예: `--mav10`/포워딩 옵션)하면 UDP 경유로도
  되는지 미확인.
- 비행(armed) 중 GPS_INPUT 반영·EKF 게이팅 거동 미확인.
- 점진(1m/스텝) GPS_INPUT으로 탐지 회피가 SITL에서 재현되는지 미확인.
- `ghost_spoof()` 통합/방어 연결 미착수.

**SITL 상태**: 실험 위해 `GPS1_TYPE=14`, `SIM_GPS1_ENABLE=0`로 바꾼 상태(비정상 baseline).
→ 아래에서 sim_vehicle 재시작으로 정상 baseline(`GPS1_TYPE=1`, `SIM_GPS1_ENABLE=1`) 복원.

**다음 작업 (사용자 결정 대기)**
- [ ] A5 정리 방향: "GPS_INPUT은 FC 직접 도달 시 반영, MAVProxy 기본 출력은 미포워딩이 원인"을
      결론으로 A5 마무리할지 / MAVProxy 포워딩 설정까지 확인할지
- [ ] 공격 현실성(공격자가 FC 입력 채널에 접근 가능한가) 논의를 보고서에 어떻게 담을지
- [ ] A3(파라미터 민감도)/문서화로 이동 여부

---

## 2026-07-06 14:06 — A5 MAVProxy 포워딩 확인: 기본 UDP 출력은 GPS_INPUT 미전달 (A5 원인 규명 종결)

**주제/이유**: A5 마지막 조각 — 공격자가 표준 GCS 포트(MAVProxy UDP)로 GPS_INPUT을 보내도
FC까지 전달되는지 확인. 이 답이 있어야 "GPS_INPUT이 된다"를 넘어 "어느 접근 지점에서
주입해야 하는지"를 설명할 수 있음.

**실행 내역**
- **[실제 검증됨] 운영 발견**: `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` 소프트 재부팅은 매번
  MAVProxy 텔레메트리를 wedge시킴(직접 링크 5762는 무사). 또 sim_vehicle 세션에서 mavproxy만
  kill하면 슈퍼바이저가 arducopter까지 내림. → 재부팅/부분 kill 회피 필요.
- **[실제 검증됨] 회피책 발견**: `GPS1_TYPE`/`SIM_GPS1_ENABLE` 변경은 **재부팅 없이 라이브로
  적용됨**(GPS1_TYPE=14 설정 0.6초 후 fix 6→1, 원복 시 0.9초 후 6 회복). → wedge 없이 GPS
  백엔드 전환 가능. 이 방법으로 MAVProxy를 건강하게 유지한 채 테스트함.
- **[실제 검증됨] 핵심 테스트**: SITL 재기동(MAVProxy 정상) → 라이브로 `GPS1_TYPE=14`,
  `SIM_GPS1_ENABLE=0` 설정(fix=1) → **GPS_INPUT을 MAVProxy UDP 포트(14551)로 5Hz 주입**,
  직접 링크(5762)로 관찰 → **fix 계속 1, 위치 변화 없음.** (직접 링크 5762 주입 시엔 fix
  3D였던 것과 대조.)

**결과 요약 (A5 원인 규명 종결)**
- **[실제 검증됨] 기본 MAVProxy UDP 출력(14551/14552) 경유로는 GPS_INPUT이 FC master까지
  역방향 전달되지 않는다.** GPS_INPUT은 **FC master/직접 링크(예: SITL 5762) 또는 별도
  MitM/companion 경로**를 통해야 FC 항법 입력으로 반영됨.
- **[미확인]** MAVProxy의 비표준 포워딩 모듈/옵션으로 targetless 메시지(GPS_INPUT)를
  강제 포워딩하도록 만들 수 있는지는 시도하지 않음(표준 설정 기준 결론). 참고: 앞서
  COMMAND_LONG(target_system 지정)은 MAVProxy가 포워딩했음 → targetless 메시지 라우팅
  차이로 **[추정]**되나 확정 아님.

**A5 종합 (예선 서사용, 보수적)**
- **오프라인**: FakeMsg/합성 텔레메트리 기반 `GLOBAL_POSITION_INT` Ghost 공격 재현(FC 없음).
- **SITL 차이 인지**: 실제 FC 있는 SITL에서 `GLOBAL_POSITION_INT`는 **출력 상태 메시지**라
  항법 입력에 미반영 → 오프라인과 SITL의 근본 차이 확인.
- **SITL 피벗**: 입력 계열 `GPS_INPUT`으로 피벗 → **FC에 직접 도달하면 항법 입력으로 반영됨**
  (fix 3D, raw·EKF 위치 추종, gps_id=0 필수).
- **접근 지점**: 표준 MAVProxy UDP 출력으로는 GPS_INPUT 미전달 → 공격자는 FC 직접 링크 또는
  MitM/companion 위치가 필요.
- **제외**: `HIL_GPS`(펌웨어 제거), `SIM_GPS1_*`(반영되나 외부 공격 채널 아님, 방어 검증용
  센서 모델 조작 도구로 분리).
- **미완료(본선/후속)**: `ghost_spoof` 통합, 점진 편향 로직 연결, 방어 end-to-end SITL 검증,
  armed 비행 중 거동, MAVProxy 포워딩 옵션.
- **금지 표현 준수**: "SITL Ghost 공격 완전 성공", "실제 드론 탈취", "실제 GPS spoofing 구현"
  으로 쓰지 않음.

**SITL 상태**: baseline 라이브 복원 완료(`GPS1_TYPE=1`, `SIM_GPS1_ENABLE=1`, fix=6·위성10,
재부팅 불필요). MAVProxy 정상. SITL 유지 중.

**다음 작업**
- [ ] A5를 위 "A5 종합"으로 정리 마무리(사용자 확인 후)
- [ ] A3(파라미터 민감도 분석)로 이동
- [ ] SITL 유지/종료 결정

---

## 2026-07-06 15:23 — A5 재현성 보강: GPS_INPUT direct-link 항법 입력 반영 PoC

**주제/이유**: A5를 바로 닫지 않고, GLOBAL_POSITION_INT 실패 이후 **GPS_INPUT direct-link가
FC 항법 입력으로 반영되는 피벗 가능성을 재현성 있게** 확인. (목표는 Ghost 공격 완성이 아님.)

**중요 선행 발견 [실제 검증됨]**: `GPS1_TYPE=14` **라이브 전환은 MAV GPS 백엔드를 활성화하지
못함**. 라이브 전환 시 fix가 1로 떨어지는 것은 `SIM_GPS1_ENABLE=0`(시뮬 GPS off) 때문일 뿐,
MAV 백엔드는 부팅 시 초기화가 필요. → GPS_INPUT 반영을 재현하려면 **`GPS1_TYPE=14` 설정 후
재부팅**이 필요(이전 14:06의 "라이브로 먹힘" 해석을 이 항목에서 정정: fix 하락 ≠ 백엔드 활성화).

**실행 내역 (재부팅으로 MAV 백엔드 활성화 후, 5762 직접 주입 / 5762·5763 직접 관찰)**
- 조건: `GPS1_TYPE=14`, `SIM_GPS1_ENABLE=0`(재부팅 반영), gps_id=0, 5Hz, 홈 좌표
  (-35.3632622,149.1652376) 앵커.
- **[실제 검증됨] 재현성(동일 조건 2회)**:
  | 시행 | 주입전 | 주입중 | raw 위도Δ | 5763(별도관찰) | 첫 fix≥3 | 주입후 |
  |---|---|---|---|---|---|---|
  | T1 (+33m) | fix1 sats0 | **fix3 sats12** | **+33.3m** | fix3 rawΔ+33.3m EKFΔ+33.3m | 0.2s | fix1 복구 |
  | T2 (반복) | fix1 sats0 | **fix3 sats12** | **+33.3m** | fix3 rawΔ+33.3m EKFΔ+33.3m | 0.2s | fix1 복구 |
  → 두 시행 동일. 재현성 확인.
- **[실제 검증됨] 작은 offset**: T3 (+3.3m) → fix3, rawΔ=+3.3m, EKFΔ=+3.3m. 작은 편향도 반영.
- **[실제 검증됨] 시간적 대응**: 주입 시작 후 0.2~0.4초 내 fix 1→3 전환, raw·EKF 위치가 주입
  목표로 이동. 주입 중단 시 fix 3→1, raw 위치 무효화(복구). EKF는 fix 상실 후 마지막 위치를
  잠시 유지(dead-reckoning)하다 이탈.
- **[실제 검증됨] 별도 관찰자 전파**: 주입은 5762, 관찰은 별도 링크 5763에서도 fix3·동일 위조
  위치 확인 → FC가 위조된 항법 상태를 **연결된 모든 링크로 브로드캐스트**함을 시사.
- **아티팩트 주의**: 주입전/후 GPS_RAW의 위도가 0으로 보고되어(fix 없음) 홈 대비 Δ가 약
  +3,925km로 찍히는 것은 "유효 위치 없음"의 표기 아티팩트이지 실제 위치가 아님.

**결과 요약 (A5 — "GPS_INPUT direct-link 기반 항법 입력 반영 PoC")**
- **GPS_INPUT을 FC 직접 링크로 주입하면 fix_type(1→3), 위성수(0→12), RAW GPS 위치, EKF/
  GLOBAL_POSITION_INT 위치가 주입 목표로 반영되며, 이 동작이 반복 재현되고 작은 offset에도
  성립함을 확인함.** 별도 링크(5763)에서도 동일 관측.
- **표현 준수**: 이건 "GPS_INPUT direct-link 기반 항법 입력 반영 PoC"일 뿐. "SITL Ghost 공격
  성공/GPS_INPUT 공격 완성/실제 GPS spoofing/실제 드론 탈취/ghost_spoof 통합 완료"가 아님.

**한계 (명시)**
- **FC direct-link 조건**: SITL 직접 TCP(5762/5763) 또는 master 링크에서만 반영. 실제 배포에서
  공격자가 이 링크에 접근 가능한지는 별개 전제(현실성 미검토).
- **MAVProxy UDP 기본 출력 경유 미반영**(업데이트 14:06): 표준 GCS 포트로는 전달 안 됨.
- **14550 방어 포트 직접 관측은 이 세션에서 못 함**: 재부팅이 MAVProxy를 wedge시켜 14550 사망.
  대신 별도 FC 직접 링크(5763)로 전파 확인. 건강한 MAVProxy 세션이라면 동일 브로드캐스트가
  14550에도 도달할 것으로 보이나(GLOBAL_POSITION_INT/GPS_RAW_INT는 전 링크 브로드캐스트),
  14550에서의 직접 확인은 **미완**.
- **ghost_spoof 미통합**, **점진 편향/백오프 로직 미연결**, **방어 end-to-end SITL 미검증**,
  **armed 비행 중 거동 미확인**.

**SITL 상태**: 재부팅으로 MAVProxy wedge 상태. → 아래에서 sim_vehicle 재시작으로 정상 baseline
(`GPS1_TYPE=1`, `SIM_GPS1_ENABLE=1`) 복원 예정.

**다음 작업 (사용자 결정 대기)**
- [x] A5 정리 마무리하고 A3로 이동할지 → 아래 16:10 항목에서 14550 확인 후 마무리
- [x] 건강한 MAVProxy 세션에서 14550 방어 포트 직접 확인 → 아래 16:10 항목에서 완료

---

## 2026-07-06 16:10 — A5 마무리: 14550 방어 포트 도달성 확인 (GPS_INPUT direct-link 전파)

**주제/이유**: GPS_INPUT direct-link로 FC/EKF에 반영된 위치 변화가 **방어 에이전트가 붙는
MAVProxy output 포트(14550)까지 실제로 전달되는지** 확인. (이 단계는 방어 **탐지 성능 검증이
아니라 방어 입력 스트림 도달성 확인**임.)

**환경 준비 (임시 파일 없이)**
- **[실제 검증됨]** sim_vehicle의 `-P PARAM=VALUE` 플래그로 부팅 시 파라미터 프리셋 →
  **임시 파라미터 파일 생성 불필요**(당초 예고했던 scratchpad `.parm` 파일은 만들지 않음).
- 재기동 명령: `sim_vehicle.py -v ArduCopter -N -w -P GPS1_TYPE=14 -P SIM_GPS1_ENABLE=0
  -m "--daemon --out udp:...14551 --out udp:...14552"`.
- 결과: **MAVProxy 정상(14550/14551 살아있음) + GPS1_TYPE=14 부팅 적용 + fix=1(GPS_INPUT
  대기)**. 재부팅 wedge 문제를 부팅 프리셋으로 회피 → 14550 관측 가능.

**실행 내역 (5762 FC 직접 주입 / 14550 방어 포트 관찰)**
- 조건: gps_id=0, 5Hz, 홈 좌표(-35.3632622,149.1652376) 앵커, 목표 위도Δ=+33.3m.
- **[실제 검증됨] 결과 (14550 관찰)**:
  | 구간 | fix/sats | GPS_RAW 위도Δ | GLOBAL_POS 위도Δ | 14550 첫 fix≥3 |
  |---|---|---|---|---|
  | 주입전 | 1/0 | (무효, fix없음) | (무효) | — |
  | **주입중** | **3/12** | **+33.3m(주입 목표)** | **+33.3m(EKF 수렴)** | 주입 0.5s 후 |
  | 주입후 | 1/0 | (무효) | +32.9m→이탈 | — |
- 즉 **GPS_INPUT을 FC 직접 링크(5762)로 주입하니, 방어 포트(14550)에서 GPS_RAW_INT와
  GLOBAL_POSITION_INT 모두 fix 1→3·위치 +33.3m로 직접 관찰됨.** 주입 중단 시 fix 1로 복구.

**결과 요약 (A5 최종)**
- **[실제 검증됨/성공] GPS_INPUT direct-link 주입으로 FC 항법 입력에 반영되고, MAVProxy output
  14550(방어 에이전트 입력 스트림)에서도 위치 변화가 직접 관찰됨.** → 위조된 항법 상태가
  방어가 실제로 받는 스트림까지 도달함을 확인.
- **성격 명시**: 이 단계는 **방어 탐지 성능 검증이 아니라 방어 입력 스트림 도달성 확인**이다.
  방어 에이전트가 이 위조를 탐지하는지는 별개(end-to-end 미검증).

**한계 (명시)**
- FC direct-link 조건(5762)에서 주입해야 함(표준 MAVProxy UDP **입력** 경유 GPS_INPUT은
  여전히 FC 미전달). 즉 "방어가 받는 출력 스트림(14550)"에는 전파되지만, "공격자가 14551
  같은 표준 UDP로 밀어넣는 것"은 안 됨 — 주입은 직접 링크, 관찰은 output 포트라는 비대칭.
- ghost_spoof 미통합 / 점진 편향·백오프 미연결 / 방어 end-to-end SITL 미검증 / armed 비행 중
  거동 미확인.
- 실제 배포에서 공격자의 FC 직접 링크 접근 현실성 미검토.
- **금지 표현 준수**: "SITL Ghost 공격 완전 성공 / 실제 GPS spoofing 구현 / 실제 드론 탈취 /
  ghost_spoof 통합 완료 / 방어 end-to-end 검증 완료"로 쓰지 않음.

**A5 최종 종합 (예선 서사)**
1. 오프라인: FakeMsg/합성 텔레메트리 기반 `GLOBAL_POSITION_INT` Ghost 공격 재현(FC 없음).
2. SITL 차이 인지: 실제 FC에서 `GLOBAL_POSITION_INT`는 출력 상태 메시지라 항법 입력 미반영.
3. SITL 피벗: `GPS_INPUT`(direct-link)은 FC 항법 입력으로 반영(fix 3D, 위치 추종, gps_id=0,
   재현성·작은 offset 확인).
4. 방어 도달성: 그 위조 위치가 방어 포트(14550) 입력 스트림까지 전파됨을 확인.
5. 접근 지점: 표준 MAVProxy UDP 출력으로 공격자가 GPS_INPUT을 밀어넣는 경로는 미전달 →
   FC 직접 링크/MitM/companion 필요.
6. 제외: HIL_GPS(펌웨어 제거), SIM_GPS1_*(반영되나 공격 채널 아닌 방어 검증용 도구).
7. 미완(본선): ghost_spoof 통합, 점진 편향/백오프 연결, 방어 end-to-end, armed 비행 거동.

**SITL 상태**: 실험 위해 GPS1_TYPE=14/SIM off 부팅 상태. → 아래에서 정상 baseline 복원 예정.
A3(파라미터 민감도)는 오프라인 분석이라 SITL 불필요.

**다음 작업**
- [x] A3(파라미터 민감도 분석) 진행 → 아래 16:54 항목
- [x] SITL 종료 (A3에 불필요) → 완료

---

## 2026-07-06 16:54 — A3: 파라미터 민감도 분석 (오프라인, 견고성 확인)

**주제/이유**: 방어 성능이 config의 특정 값에서만 우연히 맞는 게 아니라 **파라미터 범위에서
견고한지** 확인. 탐지기들이 생성자 인자로 파라미터를 받으므로 **코드 수정·새 파일 없이
인라인으로** 값을 스윕(커밋된 `../data/normal_flight.csv` 사용, `np.random.seed(42)`).
※ 합성 피처레벨 공격 기반(오프라인 범위) — SITL/FC 무관.

**A3-① IsolationForest contamination 민감도** (train fit → test 오탐율 + 합성 위조 탐지율)
| contam | 정상 오탐율 | 1m탐지 | 5m탐지 | 10m탐지 |
|---|---|---|---|---|
| 0.01 | 0.7% | 0.0% | 4.5% | 10.0% |
| 0.03 | 2.3% | 1.0% | 42.5% | 48.0% |
| **0.05(기본)** | **4.0%** | **5.0%** | **66.0%** | **67.5%** |
| 0.10 | 8.3% | 11.5% | 100% | 100% |
| 0.15 | 11.0% | 18.0% | 100% | 100% |
→ contamination은 **오탐율↔민감도 트레이드오프 노브**. 값이 커지면 둘 다 증가(매끄러운 단조
관계, 임계적 튐 없음). **1m/스텝은 IForest 단독으로 거의 못 잡음(≤18%)** → 드리프트 탐지가
필요한 근거를 정량 재확인.

**A3-② 누적 드리프트 window×threshold 민감도** (1m/스텝 한 방향 지속, 탐지율/평균 스텝)
- 관계: 1m/스텝에서 윈도우 내 순변위 ≈ window(m). 따라서 **window > threshold(m)** 여야 탐지.
  - win15: thr10만 탐지(15스텝), thr≥15 미탐 / win20: thr10·15 탐지(20스텝), thr≥20 미탐 /
    win30: thr10·15·20·25 모두 탐지(30스텝).
- config 기본(win=20, thr=15)은 20>15로 **마진 있음** → 탐지(20스텝). "우연"이 아니라 기하
  관계에 근거한 설계 제약(window를 threshold보다 크게 두어야 함).

**A3-③ 절대이탈(AbsoluteDriftTracker) threshold 민감도** (1m/스텝, warmup=3)
| thr(m) | 15 | 20 | 25(기본) | 30 | 40 |
|---|---|---|---|---|---|
| 탐지율 | 100% | 100% | 100% | 100% | 100% |
| 평균 탐지스텝 | 18 | 23 | 28 | 33 | 43 |
→ 절대이탈은 threshold가 곧 **탐지 지연(latency) 노브**(스텝≈thr+warmup), 1m/스텝은 항상
결국 탐지. 선형·예측 가능.

**A3-④ 점진 위조 '속도'별 — 누적 vs 절대 상보성** (config 기본값)
| 스텝(m) | 누적 탐지 | 누적 스텝 | 절대 탐지 | 절대 스텝 |
|---|---|---|---|---|
| 0.3 | **0%(놓침)** | — | **100%** | 87 |
| 0.5 | **0%(놓침)** | — | **100%** | 53 |
| 1.0 | 100% | 20 | 100% | 28 |
| 2.0 | 100% | 20 | 100% | 16 |
| 5.0 | 100% | 20 | 100% | 8 |
→ **핵심 결과**: 아주 느린 위조(≤0.5m/스텝)는 누적(윈도우)이 **놓치지만**(순변위 0.5×20=10m
< 15m 임계), 절대이탈(고정앵커)이 **결국 잡는다**. 즉 두 탐지기가 **속도 스펙트럼을 상보적으로
커버** → 백오프/느린 회피 차단. 이 상보성이 우연한 튜닝이 아니라 설계 기여임을 정량 입증.

**결과 요약 (A3)**
- 모든 스윕에서 탐지율·오탐율·탐지 스텝이 파라미터에 대해 **매끄럽고 예측 가능하게** 변함
  (knife-edge/우연한 값 아님). config 기본값들은 트레이드오프의 합리적 중간대에 위치.
- 방어의 견고성 근거: (a) contamination = FP↔민감도 균형, (b) 누적 = window>threshold 기하
  마진, (c) 절대 = 지연 노브·항상 탐지, (d) 누적+절대 = 속도 스펙트럼 상보 커버.
- **한계**: 합성 피처레벨 공격·오프라인 데이터 기준(SITL/실비행 아님). 실비행 IForest 오탐
  이슈는 Finding 002 별도. 이 표들은 보고서 "파라미터 민감도" 절 근거로 사용 가능.

**산출물**: 인라인 실행 결과만(그림/CSV 파일 미생성). 보고서용 그래프·CSV가 필요하면 별도
요청 시 생성(현재는 로그의 표로 보존).

**다음 작업**
- [x] A3 표를 보고서용 형태로 정리 + 산출 조건 명시 → 아래 17:18 A2 항목
- [ ] (선택) A3 결과를 그래프/CSV로 산출할지 결정
- [ ] A2 나머지(A1/A5 결과 취합, 지표 표) 계속

---

## 2026-07-06 17:18 — A2(결과물 정리) 1단계: A3 파라미터 민감도 — 보고서용 표 + 재현 조건

**주제/이유**: A3 인라인 결과를 보고서 근거로 쓰려면 **산출 방식이 재현 가능**해야 함.
표를 보고서용으로 정리하고, 각 표의 데이터·split·seed·trial·평가 방식을 명시. (그래프/CSV
생성은 보류 — 필요 시 별도 확인.)

### 공통 재현 조건 (A3-①~④ 전부)
- **데이터**: `data/normal_flight.csv` (커밋본과 동일, 헤더 제외 1000행, 8피처
  `lat_rate,lon_rate,alt,vx,vy,vz,seq_delta,pos_jump_m`). `gen_synthetic_data.py`가 생성한
  합성 정상 비행 데이터(시드 42). ※ 실비행 데이터 아님 — 오프라인/합성 범위.
- **실행 위치/환경**: `src/`에서 프로젝트 venv, `np.random.seed(42)`.
- **탐지기**: 저장소의 `defense_layer2.py`(`CumulativeDriftDetector`, `AbsoluteDriftTracker`)와
  `sklearn.ensemble.IsolationForest`를 그대로 사용, 파라미터만 인자로 스윕.
- **⚠️ 결정성 구분**: A3-②③④의 합성 공격은 **결정적**(고정 시작점에서 일정 스텝의 직선
  드리프트)이라 trial 수와 무관하게 결과가 동일(기하 관계). "탐지율 100%/0%"는 통계가 아니라
  결정적 결과다. A3-①만 **랜덤 test 샘플 추출**이라 seed=42가 수치 재현을 좌우한다.

---
### [표 A3-①] IsolationForest contamination 민감도
**산출 조건**: train=앞 70%(700행)로 `IsolationForest(n_estimators=100, contamination=c,
random_state=42)` fit. **정상 오탐율** = test(뒤 30%, 300행) 중 이상(-1) 판정 비율.
**탐지율** = drift d∈{1,5,10}m마다 200회, 매회 test에서 랜덤 샘플 1개 뽑아 합성위조 적용
(`feat[7]=d`, `feat[0]=d/111000/0.5`) 후 예측, 이상 판정 비율. seed=42(랜덤 추출 재현).

| contamination | 정상 오탐율 | 1m 위조 탐지 | 5m 위조 탐지 | 10m 위조 탐지 |
|---|---|---|---|---|
| 0.01 | 0.7% | 0.0% | 4.5% | 10.0% |
| 0.03 | 2.3% | 1.0% | 42.5% | 48.0% |
| **0.05 (config 기본)** | **4.0%** | **5.0%** | **66.0%** | **67.5%** |
| 0.10 | 8.3% | 11.5% | 100.0% | 100.0% |
| 0.15 | 11.0% | 18.0% | 100.0% | 100.0% |

**요지**: contamination↑ → 오탐율·민감도 동반 상승(매끄러운 단조, knife-edge 없음). 1m 단일
스텝은 IForest 단독으로 거의 미탐(≤18%) → 드리프트 탐지 병행 근거.

---
### [표 A3-②] 누적 드리프트(CumulativeDriftDetector) window×threshold 민감도
**산출 조건**: 합성 공격 = 고정점(37.5665,126.9780)에서 **1m/스텝**을 한 방향으로 최대 80스텝
주입, `net_drift > threshold`면 탐지(탐지 스텝 기록). **결정적**(trial 무관 동일).

| window | threshold 10m | 15m | 20m | 25m |
|---|---|---|---|---|
| 15 | ✅ 15스텝 | ❌ | ❌ | ❌ |
| **20 (기본)** | ✅ 20스텝 | ✅ **20스텝** | ❌ | ❌ |
| 30 | ✅ 30스텝 | ✅ 30스텝 | ✅ 30스텝 | ✅ 30스텝 |

**요지**: 1m/스텝에서 윈도우 내 순변위 ≈ window(m)이므로 **window > threshold(m)** 여야 탐지.
config(win20/thr15)는 마진 확보 → 탐지(20스텝). 우연이 아니라 기하 관계 기반 설계 제약.

---
### [표 A3-③] 절대이탈(AbsoluteDriftTracker) threshold 민감도
**산출 조건**: 합성 공격 = 위와 동일(1m/스텝, 최대 80스텝), warmup=3 고정, 앵커 대비 절대거리
`> threshold`면 탐지. **결정적**.

| threshold(m) | 15 | 20 | 25 (기본) | 30 | 40 |
|---|---|---|---|---|---|
| 탐지 여부 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 탐지 스텝 | 18 | 23 | 28 | 33 | 43 |

**요지**: threshold = 탐지 지연(latency) 노브(스텝≈thr+warmup). 1m/스텝은 항상 결국 탐지(선형).

---
### [표 A3-④] 점진 위조 '속도'별 — 누적 vs 절대 상보성 (config 기본값)
**산출 조건**: `CumulativeDriftDetector()`(win20/thr15), `AbsoluteDriftTracker()`(thr25/warmup3)
기본값. 스텝 크기만 변화, 한 방향 최대 120스텝. **결정적**.

| 스텝 크기 | 누적 탐지 | 누적 탐지스텝 | 절대 탐지 | 절대 탐지스텝 |
|---|---|---|---|---|
| 0.3 m | ❌ 놓침 | — | ✅ | 87 |
| 0.5 m | ❌ 놓침 | — | ✅ | 53 |
| 1.0 m | ✅ | 20 | ✅ | 28 |
| 2.0 m | ✅ | 20 | ✅ | 16 |
| 5.0 m | ✅ | 20 | ✅ | 8 |

**요지(핵심 기여)**: 아주 느린 위조(≤0.5m/스텝)는 누적(윈도우)이 놓치나(순변위 0.5×20=10m<15m),
절대이탈(고정앵커)이 결국 탐지 → 두 탐지기가 속도 스펙트럼을 상보 커버(백오프/느린 회피 차단).

---
**재현 방법 메모**: 위 표는 저장소 `defense_layer2.py`/`IsolationForest`를 인자만 바꿔 호출한
인라인 스크립트로 산출(현재 파일로 저장 안 함). **필요 시** 이 조건 그대로 재현 스크립트
(`src/`에 예: `param_sensitivity.py`)와 CSV를 생성 가능 — **생성 여부는 확인 후 진행**(새 파일).

**다음 작업**
- [x] A3 재현 스크립트/CSV 생성(옵션 A) → 아래 18:05 항목
- [ ] A2 나머지: A1(오프라인 데모 지표) + A5(SITL PoC 요약) 결과를 보고서팀 전달 형태로 취합
- [ ] 지표 표(탐지율/오탐율/F1/MTTD)는 `evaluate_metrics.py` 산출본을 1차 근거로 정리

---

## 2026-07-06 18:05 — A3 재현 스크립트·CSV 생성 (옵션 A)

**주제/이유**: A3 표를 보고서 근거로 쓰기 위해 인라인 실행을 **재현 가능한 스크립트+CSV**로
남김. 기존 코드 파일은 미변경(신규 2개만 생성).

**생성물 (신규 2개)**
- `src/param_sensitivity.py` — A3-①~④를 재실행하는 독립 스크립트. `defense_layer2.py`의
  탐지기와 `sklearn.IsolationForest`를 **인자만 바꿔** 호출(기존 코드 미수정). 상단에 산출
  조건·성격(오프라인/합성, 결정성 구분) 주석.
- `results/param_sensitivity.csv` — 64행(+헤더). 컬럼: `experiment, detector, parameters,
  metric, value, condition`. 각 행에 실험 구분·파라미터·지표(탐지율/오탐율/탐지스텝)·값·
  산출 조건 포함.

**재현 검증 [실제 검증됨]**
- `cd src && python3 param_sensitivity.py` 실행 결과가 17:18 항목의 표 4개와 **정확히 일치**:
  A3-①(0.05→오탐4.0%/1m5.0%/5m66.0%/10m67.5%), A3-②(win20·thr15→20스텝, win15·thr15→미탐,
  win30·thr25→30스텝), A3-③(thr15→18·thr25→28·thr40→43), A3-④(0.3·0.5m→누적 미탐/절대
  87·53스텝, 1~5m→누적 20스텝 고정/절대 28·16·8).

**성격·표현 (스크립트 주석에도 명시)**
- 오프라인/합성 피처레벨 분석 — SITL/실비행 결과 아님.
- 결론: "파라미터를 바꿔도 무조건 완벽 방어"가 **아니라**, "파라미터 변화에 결과가 예측
  가능하고, 누적 드리프트와 절대이탈이 상보적으로 작동(느린 위조는 절대이탈이 커버)".

**다음 작업**
- [x] A2 나머지 → `docs/REPORT_HANDOFF.md`로 A1·A5·A3 취합 완료(18:xx, 파일 생성)
- [x] A6 문서 유지보수 → 아래 18:30 항목

---

## 2026-07-06 18:20 — A2 취합: REPORT_HANDOFF.md 생성

**주제/이유**: A1(오프라인 지표)+A5(SITL 피벗 PoC)+A3(민감도)를 보고서팀이 한 파일로
볼 수 있게 인계 요약 문서 생성. (신규 `docs/REPORT_HANDOFF.md` 1개만 추가, 기존 코드 미변경.)

**실행 내역**
- `docs/REPORT_HANDOFF.md` 생성. 구성: 1) A1 오프라인 최종 지표표 2) A5 7단계 요약+대표
  문장 3) A3 핵심 표+재현 파일 위치 4) 오프라인/SITL/본선 범위 구분표 5) 안전/금지 표현 기준.
- 각 절에 검증 범위(오프라인 vs SITL) 명시 → 섞이지 않게. 상세는 AGENT_LOG/TECHNICAL_FINDINGS
  링크.

**결과 요약**: A2 결과물 정리 완료(REPORT_HANDOFF.md 기준).

---

## 2026-07-06 18:30 — A6 문서 유지보수 (README 최신화 + 표현 일관성)

**주제/이유**: README 실행법 최신화, 저장소 문서 최신 상태 유지, REPORT_HANDOFF와 표현 기준
충돌 여부 확인. 문서만 수정(코드 미변경).

**실행 내역 (README.md)**
- 파일 트리 갱신: `src/param_sensitivity.py`, `results/param_sensitivity.csv`,
  `docs/AGENT_LOG.md`·`TECHNICAL_FINDINGS.md`·`REPORT_HANDOFF.md` 추가.
- "파라미터 민감도 분석(A3)" 실행법 절 추가 — "예측 가능+상보적 작동", "오프라인/합성(SITL/
  실비행 아님)" 명시.
- **SITL 실전 실행 섹션에 ⚠️ 검증 상태 주의 추가**: `attack_agent.py --mode ghost`의
  `GLOBAL_POSITION_INT` 위조는 오프라인에서만 반영, 실제 SITL FC에서는 출력 상태 메시지라
  미반영 → `GPS_INPUT` direct-link 피벗 PoC. "완전 성공 아님" 명시 + REPORT_HANDOFF/
  TECHNICAL_FINDINGS 링크. → README가 REPORT_HANDOFF와 충돌하지 않도록 정렬.
- **SITL 3-터미널 공격 예시 블록(--mode ghost/--adaptive) 보수적 수정(추가)**: 기존 주석
  "점진적 위조(누적 드리프트가 탐지)"가 SITL에서도 되는 것처럼 오해될 수 있어, (1) ghost 모드를
  "기존 GLOBAL_POSITION_INT 기반 — 오프라인/FakeMsg 검증용 또는 SITL 실패 확인용"으로 명시,
  (2) SITL 실제 위치 위조는 GPS_INPUT direct-link 피벗 PoC까지만 확인·ghost_spoof 통합은 본선
  과제라고 주석, (3) "점진적 위조 → 누적 드리프트 탐지" 검증은 오프라인 데모 절로만 한정.
  (사용자 지정 3옵션 모두 반영.)
- **적응형 폐루프(상태-공유) 블록도 보수적 수정(추가)**: 기존 "SITL 3-터미널 환경에서는 방어가
  상태를 파일로 공유하고 공격이 이를 읽는다" + `--mode ghost --adaptive --feedback-file` 예시가
  "SITL에서 adaptive ghost가 실제 위치 위조로 동작"하는 것처럼 오해될 수 있어, 표현을
  "SITL 없이 오프라인/FakeMsg에서 상태-공유 폐루프(상호적응)를 재현하는 구조"로 재정의(터미널
  → 프로세스 A/B). ⚠️ 주석으로 "이 위조는 GLOBAL_POSITION_INT 기반이라 오프라인에서만 반영,
  SITL FC 미반영(실패 확인용), 실제 SITL 위조는 GPS_INPUT direct-link PoC까지·ghost_spoof
  통합은 본선" 명시.

**표현 일관성 확인 결과**
- README에 "무조건 완벽 방어" 류 과장 없음(방어 지표는 구체 수치로 인용). A3는 "예측 가능+
  상보적"으로 유지. A5는 "GPS_INPUT direct-link 피벗 PoC"로 유지(완전 성공 표현 없음).
- AGENT_LOG/TECHNICAL_FINDINGS는 이전 항목들로 이미 최신 상태.

**범위 밖 (참고, 저장소 밖 문서)**
- `CODE_WALKTHROUGH.md`는 **저장소 밖 상위 워크스페이스**에 있어 A6(저장소 문서) 범위 밖.
  단 그 문서 8번 절의 "실제 ArduPilot SITL로는 아직 실행·검증하지 않았습니다"는 이제 사실과
  다름(SITL 검증 수행됨) → 팀이 그 문서를 쓸 때 REPORT_HANDOFF/TECHNICAL_FINDINGS로 갱신
  필요. (에이전트가 저장소 밖 파일을 임의 수정하지 않음 — 사용자 판단 대기.)

**결과 요약**: A6 완료. 저장소 문서(README + docs/) 최신화·표현 정렬 완료. A1·A5·A3·A2·A6
정리 완료. 커밋 대기 상태.

---

## 로그 작성 템플릿 (다음 항목부터 이 형식 사용)

```
## YYYY-MM-DD HH:MM — 한 줄 주제

**주제/이유**: 무엇을 왜 했는지 1~3문장.

**실행 내역**
- 실행한 명령/스크립트, 수정한 파일과 변경 요지를 항목별로.
- 새로운 기술적 발견이 나오면 본문에 적지 말고 TECHNICAL_FINDINGS.md에 Finding으로
  추가한 뒤 "(→ Finding NNN)"으로 링크만 남긴다.

**결과 요약**
- 무엇이 확인/완료됐는지.

**다음 작업**
- [ ] 남은 일 체크리스트
```
