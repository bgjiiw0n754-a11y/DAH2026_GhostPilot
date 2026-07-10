# 기술적 발견 기록 (Technical Findings)

> 단순 작업 로그가 아니라, **프로젝트 의사결정에 영향을 주는 기술적 발견**만 기록한다.
> (예: 특정 MAVLink 메시지가 SITL에 반영되지 않음, 특정 탐지 방식이 오탐을 유발함,
> 구현 방식을 피벗해야 함, 보고서 표현을 조심해야 하는 사항 등)
> 단순 버그 수정·명령 실행 내역은 `AGENT_LOG.md`에 기록한다.
>
> 상태값: **OPEN**(미해결, 다음 작업 필요) / **VERIFIED**(원인·영향 확인, 조치는 미완)
> / **CLOSED**(조치 완료 또는 참고용으로 종결)

---

## Finding 001 — GLOBAL_POSITION_INT 기반 위치 위조가 실제 SITL/FC에 반영되지 않음

### 발견
실제 ArduPilot SITL 환경에서 `attack_agent.py`의 `ghost_spoof()`가 60스텝(누적 66.6m)까지
위조 `GLOBAL_POSITION_INT`를 크래시 없이 끝까지 주입했다. 하지만 방어 쪽 포트(14550)에서
실시간으로 수신되는 원시 위치값을 별도 스크립트로 직접 대조한 결과, 이 위조값이 방어가
받는 스트림에 **전혀 나타나지 않았다**. 방어는 공격 실행 시점 전후로 시종일관 드론의
실제(진짜) 위치만 관측했다.

### 원인 해석
`GLOBAL_POSITION_INT`는 비행체(FC)가 자신의 위치를 GCS에 보고하는 **출력 전용
(telemetry-out)** 메시지다. 외부 프로세스(공격 스크립트)가 같은 메시지 타입을 흉내 내어
주입해도, ArduPilot의 비행 로직이나 MAVProxy 라우팅이 이를 "새로운 참값"으로 받아들여
재전송해주지 않는다. 즉 이 채널은 FC→GCS 단방향 보고용이며, GCS/컴패니언→FC 방향의
"위치 갱신 명령"으로 설계된 채널이 아니다. 오프라인 데모(`FakeMsg`)는 실제 MAVLink
라우팅 계층을 거치지 않고 Python 객체를 함수에 직접 넘겨 검증했기 때문에, 이 문제가
지금까지 드러나지 않았다.

### 영향
지금까지 보고서/CODE_WALKTHROUGH.md에 기록된 모든 정량 지표(F1 0.902 등)는 "위조가
실제로 반영된다"는 전제 위에서 나온 **오프라인** 결과이며, 그 전제 자체가 실제 SITL에서는
성립하지 않는다. 방어 로직(Layer2 4종 탐지기 + 4상태 머신) 설계 자체는 문제가 없지만,
"공격이 실제로 위치를 속인다"는 시나리오의 **재현 방법**이 잘못됐다. SITL 기반 데모를
만들거나 "실제 공격이 성립한다"고 주장하려면 이 문제부터 해결해야 한다.

### 결정
`ghost_spoof()`의 주입 메시지를 `GLOBAL_POSITION_INT`에서 **`GPS_INPUT` 또는 `HIL_GPS`**로
교체한다 (두 메시지는 FC의 EKF 입력 경로로 설계된 메시지 — 이 시점에는 설계상 근거일 뿐,
실측 검증 전이었음. **`GPS_INPUT`은 아래 "업데이트 2"에서 1차 관찰됨(완료 아님), `HIL_GPS`는
미검증**). 이 교체 전까지는 "SITL에서 공격이 실제로 성공했다"고 주장하지 않는다. 오프라인
`FakeMsg` 데모는 "탐지 로직 자체의 알고리즘 성능"을 보여주는 용도로는 계속 유효하지만,
"실제 공격이 통했다"는 근거로는 쓰지 않는다.

### 보고서 반영 문장 (원본 — PoC 이전 작성, 아래 "업데이트 2"의 최신 문장을 대신 사용할 것)
> "본 프로젝트의 위치 위조 탐지 로직(다중 신호 + 4상태 히스테리시스)은 재현 시뮬레이션
> 데이터 기반으로 검증되었으며, 실제 GPS 입력 채널(GPS_INPUT/HIL_GPS)을 통한 실기 SITL
> 위조 주입 검증은 현재 진행 중이다."
>
> (피해야 할 표현: "SITL에서 공격이 실제로 성공/재현되었다" — 아직 사실이 아님)

### 상태
**OPEN**

### 업데이트 (2026-07-06)
팀 논의 결과 "기본안 A"로 확정: 예선 본체는 오프라인(FakeMsg) 데모로 확정하고, 이
Finding의 조치(`GPS_INPUT`/`HIL_GPS`로 교체)는 전체 재작성이 아니라 **최소 PoC**(위치
입력 반영 여부만 확인)로 스코프를 좁혀 진행한다. PoC 성공 시 보고서에 "SITL 1차 PoC
확인"으로 추가하고, 실패하거나 시간 부족 시 이 Finding을 본선 고도화 과제로 넘긴 채
예선은 오프라인 데모 중심으로 마감한다.

### 업데이트 2 (2026-07-06) — GPS_INPUT 최소 PoC 1차 관찰 (완료 아님)

> 근거 등급: [실제 검증됨]=직접 실행·관찰 / [파일 기준 확인됨]=소스코드 읽기 /
> [추정]=근거 있는 가설 / [미확인]=아직 확인 못 함.

**[실제 검증됨(1회 관찰)]** 독립 테스트 스크립트(`ghost_spoof()` 코드 자체는 미변경)로
`GPS_INPUT` 메시지를 주입한 결과, 방어 포트(14550)에서 관측되는 위치 출력
(`GLOBAL_POSITION_INT`)이 이동하는 것을 **1회 관찰**했다. 조건은 (1) 차량 파라미터
`GPS1_TYPE`을 14(MAV 백엔드)로 설정 후 재부팅, (2) 단발 주입이 아니라 5Hz로 연속 스트리밍.
이 두 조건에서 정지해 있던 위치가 주입 시작과 함께 이동하기 시작했고 주입을 멈추자 서서히
원래 위치로 복귀했다. 같은 방식으로 테스트했던 `GLOBAL_POSITION_INT`(전혀 반영 안 됨)와는
반대 방향의 관찰 결과다.

**[미확인] 아직 확인되지 않은 것들** (그래서 "성공"이 아니라 "1차 관찰"이다):
- 재현성: 동일 조건 반복, SITL 재시작/파라미터 재설정 후에도 재현되는지 미확인.
- 의도한 축(위도 offset)이 아니라 경도 축이 움직인 원인 미확인(EKF 블렌딩/좌표 처리 관련은
  **[추정]**일 뿐).
- offset 크기·주입 주기(1/2/5/10Hz) 의존성, 단발 주입이 왜 안 되는지 미확인.
- 주입 시작/중단 시점과 방어 포트 변화의 시간적 대응 정량화 미확인.
- `ghost_spoof()` 통합 가능 여부, 방어 탐지 결과와의 연결 여부 미확인.

**[미확인] `HIL_GPS`**: 이번 PoC에서 **전혀 시도하지 않았다.** 코드 경로 자체가 다르므로
(`hil_gps_send`) GPS_INPUT의 관찰 결과를 HIL_GPS에 옮겨 쓰면 안 된다. 완전 미검증 후보.

**[실제 검증됨] ⚠️ 교란변수 발견 (2026-07-06 03:25)**: 위 "1차 관찰"의 실험 조건을 재점검한
결과, 관찰 당시 드론이 **주입과 무관하게 스스로 비행 중**이었음이 드러났다. 어제 띄운
이동 제어 스크립트(`/tmp/sitl_fly_pattern.py`, 포트 14552)가 계속 살아 있어 드론을 자율
패턴 비행시키고 있었고, **주입을 전혀 하지 않은 상태에서도 8초간 경도축 약 39m·고도 약 2m가
움직였다.** 따라서 위 "1차 관찰"에서 본 위치 이동이 GPS_INPUT 주입 효과인지 자율 비행인지
**분리되지 않는다.** 경도 축이 움직인 현상도 이 자율 비행(동서 왕복)으로 설명될 수 있다.

**[실제 검증됨] 깨끗한 재검증 결과 (2026-07-06 03:42) — 어제 관찰 뒤집힘**: fly_pattern을
종료(PID 3036 kill)하니 드론이 완전히 정지(30초간 0.0m)했다. 이 깨끗한 정지 상태에서
GPS_INPUT(순수 위도 +111m, 5Hz)을 주입전(8s)/주입중(15s)/주입후(12s)로 나눠 관찰한 결과,
**세 구간 모두 위치가 0.0m — GPS_INPUT 주입이 위치에 아무 영향을 주지 못했다**(공격 포트
14551·방어 포트 14550 동일). 즉 **어제의 "1차 관찰"은 fly_pattern 자율비행에 의한 착시였다.**

**[실제 검증됨] 관련 상태 관측**(값 자체는 직접 조회/관찰): `GPS1_TYPE=14`(MAV 백엔드)로
설정됨, `SIM_GPS1_ENABLE=1`(시뮬 자체 GPS 활성), `EK3_SRC1_POSXY=3`(EKF 위치 소스=GPS),
주입 없이도 `GPS_RAW_INT`가 fix_type=6·위성10·안정 위치를 계속 보고.
**[추정/가능 원인 — 미검증]** 시뮬 GPS가 권위 있는 위치를 계속 공급해서 외부 GPS_INPUT
(111m 벗어난 값)이 무시되거나 EKF innovation 게이팅에 걸리는 것일 수 있다. **단 이는
가설이며, EKF source selection·GPS instance 상태·관련 로그로 확인되기 전까지 [실제 검증됨]이
아니다.**

**중간 결론 (재수정)**: **[실제 검증됨] 현재 SITL 기본 설정에서는 `GPS_INPUT` 주입 시 위치
변화가 관찰되지 않았다.** `GLOBAL_POSITION_INT`(Finding 001 원 결론)와 마찬가지로, 지금
조건의 `GPS_INPUT` 역시 Ghost 공격의 실효 경로로 보기 어렵다. **[추정]** GPS_INPUT을
실효화하려면 시뮬 GPS를 끄는(`SIM_GPS1_ENABLE=0`) 등 조건 변경이 필요할 수 있으며(가설),
그렇게 하면 "공격자가 실제 GPS를 완전히 대체"하는 **다른(더 강한) 위협 모델**이 된다 — 이
방향을 예선 스토리로 채택할지는 팀 판단 필요. 재현성·축·주기·offset·`HIL_GPS`·`ghost_spoof`
통합 검증은 "GPS_INPUT이 실제로 반영되는 설정"을 먼저 찾은 뒤에야 의미가 있으므로 그 전까지
보류.

### 업데이트 3 (2026-07-06) — 시뮬 GPS 끈 통제 환경 실험: GPS_INPUT·HIL_GPS 둘 다 미반영

가설("시뮬 GPS가 우선해서 GPS_INPUT이 무시됨")을 검증하려고 `SIM_GPS1_ENABLE=0`으로 시뮬
GPS를 끄고 재부팅한 통제 환경에서 재실험했다(변경 전 파라미터 스냅샷은 AGENT_LOG 10:24 항목에
기록, 복원 기준).

- **[실제 검증됨]** 시뮬 GPS를 끄자 주입 없는 baseline에서 `GPS_RAW_INT` fix_type=1(fix 없음),
  위치 얼어붙음, 드론 LAND 전환.
- **[실제 검증됨]** 이 통제 환경에서 `GPS_INPUT`(위도 +55m, 5Hz) 주입 → fix_type 여전히 1,
  위치 0.0m. **시뮬 GPS를 꺼도 GPS_INPUT이 GPS 소스로 등록조차 안 됨.**
- **[실제 검증됨]** `HIL_GPS`도 **별도로** 동일 조건 시험 → fix_type 1, 위치 0.0m. 미반영.
  (GPS_INPUT 결과와 섞지 말 것 — 둘 다 독립적으로 미반영 확인.)
- **[파일 기준 확인됨]** 소스: `GPS_INPUT`은 `GCS_Common.cpp`에서 GPS 드라이버로 라우팅되도록
  되어 있으나(도달 시), `AP_GPS_MAV.cpp`에는 `HIL_GPS` 핸들러가 없음(HIL_GPS는 별도 경로).

**중간 결론 (재재수정)**: 이전 "[추정] 시뮬 GPS 우선"은 **약화됨** — 시뮬 GPS가 없어도
GPS_INPUT이 반영 안 되므로 병목은 그보다 앞단(전달/수용)일 가능성이 큼. **[추정]** 후보:
(a) MAVProxy가 14551→master로 GPS_INPUT 미포워딩, (b) `state.instance != gps_id` 불일치,
(c) EKF 게이팅 — 아직 확정 못 함. **결론적으로 `GPS_INPUT`·`HIL_GPS` 둘 다 현재 메시지/파라미터/
라우팅 구성에서 SITL 위치 위조 경로로 확인되지 않았다.** → SIM 파라미터 경로
(`SIM_GPS1_GLTCH_*`/`SIM_GPS1_POS_*` 등 SITL-네이티브 GPS 오프셋) 또는 master 직접 주입으로
피벗 검토 필요 **[추정, 미검증]**.

### 보고서 반영 문장 (업데이트 3 기준 — 최신·보수적 버전)
> "위치 위조 탐지 로직은 재현 시뮬레이션 데이터로 검증되었다. 실제 SITL에서 GPS_INPUT·
> HIL_GPS 메시지를 통한 위치 위조 주입을 시도했으나, 현재 메시지/파라미터/라우팅 구성에서는
> 차량 위치 추정에 반영되지 않음을 확인했다. SITL-네이티브 GPS 위조 경로(시뮬레이터 GPS
> 오프셋 파라미터 등)를 통한 재현은 후속(본선) 과제로 남긴다."
>
> (피해야 할 표현: "SITL에서 Ghost 공격 성공", "GPS_INPUT 검증 완료/성공", "FC 항법 입력
> 조작 성공" — GPS_INPUT·HIL_GPS 모두 현재 구성에서 미반영으로 확인됨. 어제자 "1차 관찰
> 성공"은 자율비행 교란에 의한 착시로 뒤집혔음.)

### 업데이트 4 (2026-07-06) — SIM_GPS1_* 경로(시뮬 센서 모델 조작)는 위치 위조가 반영됨 (단, 공격 채널 아님)

MAVLink 주입 경로(위 업데이트 3)와 **완전히 분리된 별개 경로**로, SITL 내장 GPS 글리치
파라미터를 시험했다. **이 결과를 MAVLink 경로(GPS_INPUT/HIL_GPS) 결과와 섞지 말 것.**

- **[파일 기준 확인됨]** `SIM_GPS.cpp`: `SIM_GPS1_GLTCH_X/Y/Z`는 시뮬 GPS 출력 lat/lon(도)·
  alt(m)에 직접 더해지는 오프셋(`d.latitude += glitch.x`).
- **[실제 검증됨]** `SIM_GPS1_GLTCH_X=0.00005`(≈+5.5m) 적용 시 RAW GPS(`GPS_RAW_INT`) 위도가
  +5.6m로 이동(약 5초 GPS 지연 후). EKF 추정치는 급격한 스텝을 초기 게이팅하다 지속되자
  +5.6m로 반영. 방어 포트(14550) 관측값에도 나타남. GLTCH=0으로 **가역 복귀** 확인.
- **[실제 검증됨]** MAVLink 주입(업데이트 3, 미반영)과 대조적으로, 이 경로는 방어가 보는
  위치를 실제로 위조할 수 있음.

**⚠️ 성격 구분 (보고서에서 반드시 지킬 것)**: `SIM_GPS1_GLTCH`는 **시뮬레이터 내부
파라미터**이지 외부 공격자가 네트워크로 주입할 수 있는 메시지가 아니다. 따라서 이것은
**"실제 공격 채널"이 아니라 "SITL 센서 모델을 조작해 방어 에이전트에 위조 위치를 먹이는
테스트 도구(스푸핑 시나리오 생성기)"**다. 방어 탐지 로직을 SITL에서 시험·시연하는 용도로는
유효하나, "공격이 성립/성공했다"는 근거로 쓰면 안 된다.

**[실제 검증됨] 부수 관측**: EKF가 급격한 5.6m 스텝을 초기 게이팅한 것은 "급격한 위조는
걸리고 점진적 위조라야 회피"라는 프로젝트 핵심 논지와 정성적으로 부합(정량 검증은 아님).

**[미확인] 한계**: 드론 지상·disarmed 관측(비행 중 거동 미확인), EKF 게이팅→수용 임계·시점
미측정, 점진(1m/스텝) 글리치의 게이팅 회피 미검증, GPS 지연 정확값 미측정.

### 상태값 요약 (Finding 001, 2026-07-06 기준)
- MAVLink `GLOBAL_POSITION_INT` 주입: **미반영 [실제 검증됨]** (출력 상태 메시지 계열)
- MAVLink `GPS_INPUT` 주입: **경로에 따라 다름 [실제 검증됨]** — MAVProxy UDP 출력(14551)
  경유 시 미반영, **FC 직접 링크(tcp:5762) 경유 시 반영됨**(아래 업데이트 5)
- MAVLink `HIL_GPS` 주입: **미반영 [파일 기준 확인됨]** — 펌웨어에서 제거됨(ReleaseNotes
  PR:28593), 처리 경로 없음
- SITL `SIM_GPS1_GLTCH_*` 파라미터: **위치 위조 반영됨 [실제 검증됨]** — 단 공격 채널이
  아니라 시뮬 조작(테스트 도구)
- MAVLink 경로 미반영 원인: **MAVProxy가 GPS_INPUT을 FC로 미포워딩 [실제 검증됨]**(직접 링크
  경유는 반영되므로 전달 단계 문제로 확인; EKF 게이팅/instance 문제 아님)

### 업데이트 5 (2026-07-06) — GPS_INPUT 원인 규명: FC 직접 도달 시 반영, MAVProxy 경유가 병목

사용자 지정 3개 범위(① master 직접 주입 ② gps_id/instance/필드 ③ HIL_GPS 코드 확인)로
GPS_INPUT 미반영 원인을 분리했다.

- **[실제 검증됨]** arducopter가 FC 직접 TCP 포트(5760/5762/5763)를 LISTEN. **5762로 FC에
  직접 GPS_INPUT을 주입**(GPS1_TYPE=14, SIM_GPS1_ENABLE=0, gps_id=0, 5Hz)하니 fix 1→3(3D),
  sats 0→12, raw GPS·EKF 위치가 주입 목표값으로 추종. → **GPS_INPUT은 FC 항법 입력으로 실제
  반영되는 유효 경로다.**
- **[실제 검증됨]** 동일 조건에서 gps_id=1로 바꾸면 거부(fix 1, sats 0). 소스의
  `state.instance != packet.gps_id`(instance 0) 체크와 일치 → **gps_id=0 필수**.
- **[실제 검증됨]** 파라미터·gps_id·SIM 설정이 모두 같고 경로만 달랐다(14551 MAVProxy vs 5762
  직접). 직접=반영, MAVProxy=미반영 → **원인은 MAVProxy가 GPS_INPUT을 FC master로 포워딩하지
  않는 전달 단계 문제.** (이전 "[추정] 시뮬 GPS 우선/EKF 게이팅"은 기각.)
- **[파일 기준 확인됨]** HIL_GPS는 펌웨어 제거(ReleaseNotes PR:28593) — 사용 불가.

**표현 주의(중요)**: 이건 "GPS_INPUT 채널이 FC 항법 입력에 반영된다"는 **메커니즘 확인**이지
"Ghost 공격 성공/완료"가 아니다. 공격 시나리오 통합(`ghost_spoof` 연결, 점진 위조, 방어 탐지
연결)은 미착수. 또한 실제 배포에서 공격자가 FC serial 링크에 직접 접근하는 것은 별개 전제
(접근성)이며 그 현실성은 미검토 — 보고서에서 "MAVLink GPS_INPUT으로 FC 위치 입력을 바꿀 수
있음(직접 링크 조건)"까지만 주장하고 "원격 공격 성공"으로 확대하지 말 것.

**[미확인]**: MAVProxy 포워딩 옵션으로 UDP 경유도 되는지, armed 비행 중 거동, 점진 위조
회피 재현, ghost_spoof 통합.

### 업데이트 6 (2026-07-06) — MAVProxy 기본 UDP 출력은 GPS_INPUT 미전달 (같은 세션 A/B 확정)

동일 세션에서 MAVProxy를 건강히 유지한 채(재부팅 없이 `GPS1_TYPE` 라이브 전환 활용) A/B
비교: `GPS1_TYPE=14`/`SIM_GPS1_ENABLE=0`(fix=1) 상태에서 **GPS_INPUT을 MAVProxy UDP
포트(14551)로 주입 → fix 계속 1(미전달)**. 직접 링크(5762) 주입 시 fix 3D였던 것과 대조.

- **[실제 검증됨]** 기본 MAVProxy UDP 출력 경유로는 GPS_INPUT이 FC master까지 역방향
  전달되지 않음. → **GPS_INPUT은 FC master/직접 링크 또는 별도 MitM/companion 경로가 필요.**
- **[추정]** COMMAND_LONG(target_system 지정)은 MAVProxy가 포워딩했으나 GPS_INPUT(targetless)은
  안 됨 → targetless 메시지 라우팅 차이로 보이나, 비표준 MAVProxy 포워딩 옵션 강제는 미시도.

### 업데이트 7 (2026-07-06) — B-path runner 재검증: MAVProxy UDP 동작은 구성 의존

`src/b_path_experiment.py`로 E0~E6를 결과 번들 형태로 재검증했다. 이 과정에서 업데이트 6의
"MAVProxy UDP는 GPS_INPUT을 전달하지 않는다"는 표현은 **일반 결론으로 쓰면 안 됨**이 확인됐다.

- **[실제 검증됨]** E1: `GLOBAL_POSITION_INT` inbound 주입은 같은 SITL 환경에서 FC/EKF 위치
  입력을 오염시키지 않았다. 결과 번들:
  `results/b_path/20260706T163526_548952Z_E1_global_position_int_negative/`
- **[실제 검증됨]** E3: FC direct `GPS_INPUT`은 공식 `GLOBAL_POSITION_INT` 출력에 반영됐다.
  결과 번들: `results/b_path/20260706T163718_121237Z_E3_fc_direct_gps_input_positive/`
- **[실제 검증됨]** E2: 이번 runner의 MAVProxy 구성
  (`mavproxy.py --master=tcp:127.0.0.1:5760 --out=udp:127.0.0.1:14550`,
  pymavlink `udpin:127.0.0.1:14550`)에서는 UDP endpoint로 보낸 `GPS_INPUT`이 FC/EKF 위치에
  반영됐다(`reflection_rate=1.0`). 결과 번들:
  `results/b_path/20260706T163903_030624Z_E2_mavproxy_udp_gps_input_negative/`

**수정된 결론**: `GLOBAL_POSITION_INT`는 여전히 출력 telemetry라 FC 입력 오염 경로가 아니다.
반면 `GPS_INPUT`은 FC가 MAVLink GPS 입력을 신뢰하는 설정에서 실제 항법 입력이 될 수 있다.
다만 "MAVProxy UDP가 항상 막는다/항상 통과한다"가 아니라, 사용한 MAVProxy 포트·방향·endpoint
구성에 따라 달라진다. 보고서에서는 **GPS_INPUT이 trusted MAVLink input path에 도달하면 위치가
바뀐다**고 쓰고, 특정 UDP/MAVProxy 구성을 일반화하지 않는다.

### 업데이트 8 (2026-07-06) — E4/E5/E6 결과: post-access navigation-deception payload로 격상 가능

동일 runner 기준으로 다음이 확인됐다.

- **[실제 검증됨]** E4: `linear`와 `ekf-smooth`가 같은 최종 drift를 사용했고 둘 다 반영됐다.
  `ekf-smooth`는 반영률을 유지하면서 abrupt jump를 줄였다.
  - linear: `results/b_path/20260706T163954_662068Z_E4_linear_gps_input/`
  - ekf-smooth: `results/b_path/20260706T164019_982977Z_E4_ekf_smooth_gps_input/`
- **[실제 검증됨]** E5: companion/relay post-access 모델도 같은 reflection 기준을 통과했다.
  결과 번들: `results/b_path/20260706T164059_713186Z_E5_companion_post_access_bridge/`
- **[실제 검증됨]** E6: geofence 최소 mission-impact 실험에서 baseline은 breach가 없었고,
  `linear`/`ekf-smooth` GPS_INPUT에서는 `FENCE_STATUS.breach_status != 0` 기준의 decision
  change가 관찰됐다. 결과 번들:
  `results/b_path/20260706T164416_269335Z_E6_geofence_mission_impact/`

**최신 표현 기준**: E3+E4+E6가 통과했으므로 B-path는 이제 "GPS_INPUT direct-link 숫자 반영"만이
아니라, **허가된 ArduPilot SITL에서 post-access navigation-deception payload가 geofence 같은
위치 의존 decision에 영향을 줄 수 있음**까지 주장할 수 있다. 단, 이것은 여전히 접근권한
획득·원격 침투·MAVLink signing 우회·RF GNSS spoofing/jamming·실기 탈취를 증명한 것이 아니다.

### 업데이트 9 (2026-07-06) — Adaptive 고도화: geofence 최소 교란은 성공, AUTO waypoint는 실패

`src/b_path_experiment.py`에 E7~E10 advanced runner를 추가하고 실행했다.

- **[실제 검증됨] E7 route matrix**: 9개 route/message cell을 시험했다.
  `GPS_INPUT`은 FC direct, MAVProxy UDP, companion-labelled trusted path 3개 모두에서 반영됐다.
  `GLOBAL_POSITION_INT`는 0/3, `HIL_GPS`는 0/3 반영. 결과 번들:
  `results/b_path/20260706T222750_745673Z_E7_route_matrix/`
- **[실제 검증됨] E8 adaptive geofence**: `max_speed=2.5m/s`, `max_accel=1.0m/s^2` 기본값에서
  adaptive payload가 geofence breach를 유도했다. baseline은 breach 없음. adaptive
  `minimum_breach_drift_m=18.7289m`, linear 비교군 `final_target_drift_m=29.9663m`.
  결과 번들: `results/b_path/20260706T223735_241593Z_E8_adaptive_geofence/`
- **[실제 검증됨] E9 AUTO waypoint**: GPS_INPUT으로 위치 출력과 geofence breach는 생겼지만,
  `MISSION_CURRENT` 기준 AUTO waypoint 조기 진행은 관찰되지 않았다. 결과 번들:
  `results/b_path/20260706T223941_086117Z_E9_auto_waypoint_reach/`
- **[실제 검증됨] E10 summary**: 최종 classification은
  **`adaptive geofence-deception payload`**. 결과 번들:
  `results/b_path/20260706T224434_991443Z_E10_summary/`

**최신 표현 기준**: B-path는 이제 "post-access navigation-deception payload"에서 한 단계 올라가,
**허가된 ArduPilot SITL에서 telemetry feedback을 이용해 고정 linear run보다 작은 drift로
geofence decision을 유도하는 adaptive geofence-deception payload**라고 주장할 수 있다.
단, AUTO waypoint mission takeover는 실패했으므로 **full mission takeover** 또는 **AUTO mission
takeover** 표현은 쓰지 않는다.
- **[실제 검증됨] 운영 메모**: 소프트 재부팅(`PREFLIGHT_REBOOT_SHUTDOWN`)은 매번 MAVProxy를
  wedge시킴(직접 링크는 무사). `GPS1_TYPE`/`SIM_GPS1_ENABLE`은 재부팅 없이 라이브 전환 가능
  (fix가 <1s 내 반응) → 향후 SITL 실험은 라이브 파라미터 전환으로 wedge 회피 권장.

**A5 상태**: 원인 규명 종결(진행 중→정리 단계). 예선 서사 = 오프라인(GLOBAL_POSITION_INT
재현) → SITL 차이 인지(출력 메시지라 미반영) → GPS_INPUT 피벗(직접 도달 시 반영) → 접근 지점
(표준 UDP 출력 미전달, 직접/MitM 필요). 통합·점진·방어 end-to-end는 본선 과제.

### 업데이트 7 (2026-07-06) — GPS_INPUT direct-link 반영 PoC 재현성 확인 + 라이브 전환 정정

- **[정정]** 업데이트 6의 "GPS1_TYPE 라이브 전환 가능"은 부정확했다. 라이브로 `GPS1_TYPE=14`를
  넣으면 `SIM_GPS1_ENABLE=0` 때문에 fix가 1로 떨어질 뿐, **MAV GPS 백엔드는 활성화되지 않는다**
  (재부팅 필요). 따라서 GPS_INPUT 반영 재현에는 **GPS1_TYPE=14 설정 후 재부팅**이 필수.
- **[실제 검증됨] 재현성**: 재부팅으로 MAV 백엔드 활성화 후, 직접 링크(5762) GPS_INPUT 주입을
  동일 조건 2회 반복 → 둘 다 fix 1→3, sats 0→12, RAW 위치=주입 목표(+33.3m), 주입 시작 0.2s
  내 전환, 중단 시 복구. **작은 offset(+3.3m)도 반영.** 별도 직접 링크(5763)에서도 동일 관측
  (위조 항법 상태가 전 링크로 브로드캐스트됨).
- **한계**: FC direct-link 조건 한정 / MAVProxy UDP 기본 출력 미전달 / 14550 방어 포트 직접
  확인은 이 세션에서 미완(재부팅이 MAVProxy wedge, 5763로 대체 관측) / ghost_spoof 미통합 /
  점진·백오프 미연결 / 방어 end-to-end SITL 미검증 / armed 비행 중 거동 미확인.
- **표현**: "GPS_INPUT direct-link 기반 항법 입력 반영 PoC"로만 기술. Ghost 공격 성공/완성 아님.

### 업데이트 8 (2026-07-06) — 14550 방어 포트 도달성 확인 (방어 입력 스트림 도달성, 탐지 검증 아님)

재부팅 wedge 회피를 위해 sim_vehicle `-P GPS1_TYPE=14 -P SIM_GPS1_ENABLE=0`로 **부팅 프리셋**
(임시 파일 불필요) → MAVProxy 정상 + MAV 백엔드 부팅 활성 상태 확보. 이 상태에서 GPS_INPUT을
FC 직접 링크(5762)로 주입하고 **14550(방어 에이전트 입력 스트림) 관찰**:

- **[실제 검증됨/성공]** 주입중 14550에서 `GPS_RAW_INT` fix 1→3·sats 0→12, `GLOBAL_POSITION_INT`
  위치가 주입 목표(+33.3m)로 이동(주입 0.5s 후 fix 전환), 주입 중단 시 fix 1 복구. → **위조된
  항법 상태가 방어가 실제로 받는 출력 스트림(14550)까지 전파됨.**
- **성격**: 이 단계는 **방어 입력 스트림 도달성 확인**이지 **방어 탐지 성능 검증이 아니다**
  (방어 에이전트가 이 위조를 탐지하는지는 별개, end-to-end 미검증).
- **비대칭 주의**: 주입은 FC 직접 링크(5762)에서, 관찰은 output 포트(14550)에서. 공격자가 표준
  MAVProxy UDP **입력**(예: 14551)으로 GPS_INPUT을 밀어넣는 것은 여전히 FC 미전달(업데이트 6).

**상태표 갱신**: `GPS_INPUT` = FC 직접 링크(master/5762 등) 주입 시 항법 입력 반영 + 방어 포트
(14550) 전파까지 확인 [실제 검증됨]. 표준 MAVProxy UDP 입력 경유는 미전달. A5 정리 완료.

### ★ A5 최종 정리 (확정 — 보고서 기준, 2026-07-06)

> 아래 7개 항목이 A5의 **확정 결론**이다. 보고서/발표는 이 프레이밍을 따른다.

1. **오프라인 데모**: FC가 없는 FakeMsg/합성 텔레메트리 환경이므로 `GLOBAL_POSITION_INT`
   기반 Ghost 공격이 재현됨.
2. **실제 SITL**: `GLOBAL_POSITION_INT`는 출력 상태 메시지라 FC 항법 입력에 반영되지 않음.
3. **피벗**: 입력 계열로 피벗했고, `GPS_INPUT`은 **FC direct-link 조건**에서 fix/raw GPS/EKF
   위치에 반영됨(재현성·작은 offset 확인, gps_id=0 필수).
4. **도달성**: GPS_INPUT 주입 결과가 MAVProxy output **14550에서도 관찰**되어, 방어 에이전트
   입력 스트림까지 도달함을 확인.
5. **성격 한정**: 이는 **방어 탐지 성능 검증이 아니라 도달성(입력 스트림 도달) 확인**임.
6. **경계**: 표준 MAVProxy UDP **입력** 경유는 미전달 / `HIL_GPS`는 현재 펌웨어에서 제외
   (제거됨) / `SIM_GPS1_*`는 공격 채널이 아니라 SITL 센서 모델 테스트 도구로 분리.
7. **본선 과제**: `ghost_spoof` 통합, 점진 편향/백오프 연결, 방어 end-to-end SITL 검증,
   armed 비행 거동.

**금지 표현(유지)**: "SITL Ghost 공격 완전 성공", "실제 GPS spoofing 구현", "실제 드론 탈취",
"ghost_spoof 통합 완료", "방어 end-to-end 검증 완료".

---

## Finding 002 — 실비행 데이터로 학습한 IsolationForest가 간헐적 오탐을 유발해 4상태 히스테리시스가 NORMAL로 복귀하지 못함

### 발견
실제 SITL 비행에서 수집한 479개 샘플로 `defense_layer2.py`의 IsolationForest를 재학습한
뒤, **공격이 전혀 없는 상태**에서도 약 5스텝 중 1번꼴로 이상탐지(`l2_anomaly=True`)가
발생했다. 4상태 머신은 `SM_CLEAR=5`회 연속 정상 관측이 있어야 SUSPICIOUS→NORMAL로
내려가는데, 이 조건을 채우지 못해 **공격 없이도 영구히 SUSPICIOUS/ALERT 상태**에 머물렀다.

### 원인 해석
합성 데이터(`gen_synthetic_data.py`) 대비 실비행 데이터는 표본 수가 적고(479개) 노이즈
특성이 다르다. `contamination=0.05` 설정과 실제 8차원 피처 분포 사이의 불일치로,
IsolationForest가 정상 범주 안의 미세한 변동에도 과민하게 반응하는 것으로 추정된다
(확정 원인 분석은 아직 안 함).

### 영향
오프라인 검증(합성 데이터)에서 보고된 "정상 기동 오탐율 0%"는 **실비행 데이터에서는
재현되지 않는다**. 방어 로직을 그대로 실비행에 투입하면 상시 ALERT 상태가 되어 운용자가
경보를 신뢰하지 않게 될 위험(경보 피로, false alarm fatigue)이 있다.

### 결정
코드는 아직 수정하지 않음(근본 원인 미확정). 실비행 데이터 양을 늘려 재학습하거나,
`config.py`의 `SM_CLEAR` / `ISO_CONTAMINATION` 등을 실비행 분포에 맞게 재튜닝하는 작업이
필요하다. 우선순위는 Finding 001(공격 자체가 실제로 성립해야 탐지 검증도 의미가 있음)
다음으로 둔다.

### 보고서 반영 문장
> "실비행 데이터 기반 재학습 시 이상탐지 민감도 재조정이 필요함을 확인했으며, 관련
> 파라미터 튜닝은 후속 과제로 진행할 예정이다."
>
> (피해야 할 표현: "정상 기동 오탐율 0%"를 실비행 환경에도 그대로 적용되는 것처럼 서술)

### 상태
**OPEN**

---

## Finding 003 — 오프라인(FakeMsg) 검증 경로는 실제 MAVLink 인코딩 오류를 잡아내지 못한다

### 발견
실제 SITL 연동 과정에서 코드 결함 2건을 발견했다:
1. `utils.py`/`defense_layer1.py`가 실제 MAVLink 헤더 속성명(`.seq`)이 아닌 존재하지 않는
   속성(`.mseq`)을 참조 → 실제 텔레메트리 수신 즉시 `AttributeError` 크래시.
2. `attack_agent.py`가 uint32 필드(`time_boot_ms`)에 유닉스 epoch 밀리초(~1.8조)를 그대로
   대입 → `struct.error` 오버플로우로 위조 주입 첫 스텝에서 크래시.

두 버그 모두 오프라인 데모(`FakeMsg` 경로)에서는 해당 실행 경로 자체가 한 번도 돌아간
적이 없어 지금까지 발견되지 않았다.

### 원인 해석
`FakeMsg`(`utils.py`)는 실제 pymavlink 메시지 인코딩·헤더 구조를 흉내만 낸 Python mock
객체이고, 실제 struct pack이나 실제 MAVLink 헤더 필드명을 거치지 않는다. 따라서
"오프라인 데모 통과"는 방어/공격 **로직의 알고리즘적 정합성**만 검증하며, 실제 MAVLink
프로토콜 인코딩과의 호환성은 전혀 검증하지 못한다.

### 영향
"오프라인 검증 완료"라는 표현이 "실제 환경에서 동작 확인됨"으로 오독될 위험이 있다.
이번처럼 실제 실행 경로에만 존재하는 크래시 버그가 코드베이스에 임의로 더 남아있을
가능성을 배제할 수 없다.

### 결정
두 버그는 이미 수정 완료(각각 1줄 수정, `demo_offline.py` 재실행으로 기존 동작에
회귀 없음 확인). 앞으로 새로운 MAVLink 관련 코드를 추가할 때는 최소 1회 실제 SITL
연결 테스트를 거치는 것을 원칙으로 한다.

### 보고서 반영 문장
> "오프라인 시뮬레이션(FakeMsg 기반)과 실제 SITL 연동 테스트를 병행 검증했으며, 그
> 과정에서 실제 MAVLink 프로토콜 인코딩 관련 결함 2건을 발견·수정했다."
>
> (피해야 할 표현: 오프라인 지표만으로 "실전 검증 완료"라고 서술)

### 상태
**CLOSED** (버그 자체는 수정 완료. "오프라인 검증에는 이런 종류의 한계가 있다"는 교훈은
계속 유효하므로 참고용으로 남겨둔다.)

### 업데이트 (2026-07-06)
"기본안 A" 확정에 따라 오프라인 데모를 예선 본체로 최종 고정하기 위해, `demo_offline.py`
단독 재실행이 아니라 `gen_synthetic_data.py → demo_offline.py → evaluate_metrics.py →
demo_stateful.py` 전체 파이프라인으로 회귀 검증 범위를 넓혔다. F1 개선(NEW 0.902), 미탐지
스트림 0/12, 적응형 백오프 회피 0스텝 등 **핵심 결론**은 CODE_WALKTHROUGH.md 기록과
동일하게 재현됨을 확인. 단, `gen_synthetic_data.py`는 시드(42)가 고정돼 있음에도 재실행
결과가 git에 커밋된 원본 산출물과 완전히 같지는 않았고(일부 산출물·세부 수치 차이,
원인 미확정), 이로 인해 OLD F1 0.818→0.817, MTTD 4.6→4.8처럼 일부 세부 수치에 반올림
수준의 차이가 생겼다(결론에는 영향 없음). 재실행으로 덮어써진 6개 산출물
(`normal_flight.csv`, `fig_*.png` 3종, `metrics_summary.csv`, `stateful_loop.csv`)은
`git restore`로 원본 상태로 복구함. 이로써 "버그 수정이 오프라인 예선 본체의 핵심 결론에
영향 없음"이 단일 스크립트가 아닌 전체 파이프라인 기준으로 확인됨.

---

## Finding 004 — B-path real-advanced planner 구현은 성공했지만 mission-impact 확장은 geofence에 머무름

### 발견
`GPS_INPUT` B-path를 단순 주입 스크립트에서 planner/optimizer/matrix 구조로 확장했다.
추가된 구성은 mission decision detector, stealth scoring, route scoring, automatic objective
selection, claim classifier다.

최신 증거 번들:

```text
E11: results/b_path/20260707T010445_557499Z_E11_mission_decision_matrix/
E12: results/b_path/20260707T011205_772112Z_E12_stealth_optimizer/
E13: results/b_path/20260707T011423_872299Z_E13_route_relaxation/
E14: results/b_path/20260707T011607_827891Z_E14_auto_attack_planner/
E15: results/b_path/20260707T011642_237696Z_E15_mission_impact_full_run/
E16: results/b_path/20260707T011642_256681Z_E16_claim_classifier/
E17: results/b_path/20260707T011642_274206Z_E17_final_summary/
```

### 결과
실행 결과는 다음과 같다.

```text
geofence: pass
auto-waypoint: fail
RTL: fail
land: fail
failsafe: fail
stealth optimizer: pass
route relaxation: pass in local SITL for fc-direct, MAVProxy UDP, companion label
auto planner: pass, selected geofence and executed it
final strict classification: fail, adaptive geofence-deception payload only
```

E12에서 linear와 stealth-opt 모두 geofence impact를 만들었고, stealth-opt는 normal budget에서
stealth score를 7.499438에서 0.479061로 낮췄다. E13에서는 같은 geofence payload가
fc-direct, MAVProxy UDP, companion-labeled post-access route에서 모두 반영됐다.

### 결정
코드 수준의 고도화는 완료됐지만, 엄격한 의미의 mission-impact 확장은 아직 성공하지 못했다.
보고서/발표에서는 다음처럼 한정한다.

```text
허가된 ArduPilot SITL에서, post-access GPS_INPUT payload가 planner/stealth optimizer/route
selection을 통해 geofence decision을 유도할 수 있다.
```

### 금지 표현
다음 표현은 최신 E11-E17 결과로도 뒷받침되지 않는다.

```text
AUTO waypoint takeover
RTL takeover
LAND takeover
failsafe induction
full mission takeover
real vehicle takeover
remote access or credential/signing bypass
```

### 상태
**OPEN** — geofence 외 decision impact는 후속 과제.

---

## Finding 005 — AI 제외 후 MAVLink write-access payload matrix는 전 payload 통과

### 발견
E11-E17의 AI/planner 중심 주장은 최종 본론에서 제외하고, 같은 허가된 ArduPilot SITL에서
MAVLink write access가 이미 있는 post-access 조건의 payload를 deterministic matrix로
재정리했다.

최종 통합 실행:

```text
.venv/bin/python src/b_path_experiment.py --experiment all-payloads --install-mavproxy \
  --warmup-sec 4 --iterations 12 --max-drift-m 30 --stealth-budget normal \
  --routes all --payload all
```

최신 증거 번들:

```text
E19: results/b_path/20260707T024455_949208Z_E19_mode_command/
E20: results/b_path/20260707T025130_250985Z_E20_mission_edit/
E21: results/b_path/20260707T025208_174522Z_E21_param_edit/
E22: results/b_path/20260707T025231_390703Z_E22_telemetry_deception/
E18: results/b_path/20260707T025255_406828Z_E18_payload_matrix/
E23: results/b_path/20260707T025255_422162Z_E23_attack_surface_summary/
```

### 결과
전 payload가 pass로 정리됐다.

```text
GPS_INPUT: position-estimate/geofence deception confirmed from existing GPS_INPUT evidence.
MODE_COMMAND: RTL/LAND/BRAKE/LOITER mode changes confirmed by HEARTBEAT mode.
MISSION_EDIT: mission upload ACK and readback_count=2 confirmed.
PARAM_EDIT: FENCE_RADIUS, FENCE_ACTION, FS_EKF_ACTION, WP_SPD changed and read back.
TELEMETRY_DECEPTION: GLOBAL_POSITION_INT remained telemetry/log deception, not FC input.
```

중요한 수정도 있었다. 기존 E20은 `mission_clear_all` ACK를 mission upload ACK로 오인할 수
있었다. 이 버그를 고쳐 mission item 전송 뒤의 ACK와 readback을 확인하도록 바꿨고, 그 뒤
E20이 통과했다. E21은 구명칭 `WPNAV_SPEED`가 이 빌드에서 응답하지 않아 실패했는데, 최신
Copter 파라미터인 `WP_SPD`를 후보 탐색으로 선택하게 바꾼 뒤 통과했다.

### 결정
보고서의 B-path 본론은 다음으로 제한한다.

```text
허가된 ArduPilot SITL에서 MAVLink write access가 이미 노출된 post-access 상황을 가정하고,
GPS_INPUT, mode command, mission edit, parameter edit, telemetry deception payload의
임무 영향과 전제조건을 deterministic matrix로 실험·분류했다.
```

### 금지 표현
다음은 이번 결과로도 주장하지 않는다.

```text
remote exploit
credential bypass
MAVLink signing bypass
RF GNSS spoofing or jamming
payload/weapon control
closed-network intrusion
malware deployment
real vehicle takeover
```

### 상태
**CLOSED** — AI 제외 deterministic payload matrix 구현 및 통합 실행 완료.
