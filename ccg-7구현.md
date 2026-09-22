# CCG-7 PoC — 정적 CBOM 결합 · 정규화 · 정책 게이트 · PQC 전환 우선순위

논문 **「PQC 전환 우선순위 결정을 위한 지속적 암호 거버넌스 프레임워크: CycloneDX CBOM 1.6·1.7 표현력 분석을 중심으로」**
5절 "적용 및 검토"를 위한 최소 구현입니다. CCG-7 의 7단계 중 **①정적 CBOM 생성 → ②정규화·crypto-ref 중복제거 → ⑤정책 판정 → ⑥우선순위**
만 구현했고, ③동적(런타임) 증거와 ④3상태 분포 분석은 범위 밖입니다(사용성 상태는 정적 근거만으로 `provided/inferred` 를 근사).

```
targets/<repo>  ──cdxgen(cbom, 1.7)───────────┐
                ──collectors/cert_collector.py │ (1.6 과 1.7 두 표현으로 발행)   ┌ out/ccg7/report.md   (표 1~6)
                ──collectors/naive_scanner.py ─┴─▶ python3 -m ccg7 run ─────────▶│ out/ccg7/priority.csv
                   (선택) cbomkit-theia(1.6)        정규화→crypto-ref→중복제거    │ out/ccg7/findings.json
                                                    →신뢰도 융합→정책→우선순위     └ out/ccg7/assets.normalized.json
```

## 1. 빠른 실행

```bash
# 의존성: Node 22 + @cdxgen/cdxgen 13.1 (cbom 명령 포함), Python 3.11 + cryptography pyyaml jsonschema
npm i -g @cdxgen/cdxgen
pip install cryptography pyyaml jsonschema

bash scripts/00_fetch_targets.sh      # NodeBB / Ghost / verdaccio 얕은 clone
bash scripts/run_all.sh               # 생성 → 스키마 검증 → 파이프라인 → 표
python3 tests/test_pipeline.py        # 단위 시험 4개
```

주의: cdxgen 의 CBOM 은 **`cbom` 별칭 실행 파일**(또는 `cdxgen --include-crypto`)로 만들어야 합니다.
`cdxgen cbom …` 처럼 쓰면 "cbom" 이 경로로 해석되어 빈 BOM 이 나옵니다. `-t js` 를 명시하면 단일 언어 경로로 빠져
암호 자산 수집 단계가 실행되지 않으므로 **프로젝트 타입은 자동 감지**에 맡깁니다.

## 2. 구성

| 경로 | 역할 | CCG-7 대응 |
|---|---|---|
| `collectors/cert_collector.py` | 저장소의 X.509 인증서·개인키 파일 → CBOM (`--spec-version 1.6/1.7`) | ④ 설정·인증서면. cbomkit-theia 대체(형식 호환) |
| `collectors/naive_scanner.py` | 정규식 문자열 매칭 스캐너, 신뢰도 0.4 | ① 소스면(저신뢰). 중복제거·융합 시험용 + cdxgen 누락 보완 |
| `ccg7/registry.py` | 도구별 명칭 → 레지스트리 `algorithmFamily`·곡선 정규화 (`data/cryptography-defs.json` 공식 사본 + `data/registry_min.json` 별칭표) | §4.1 명칭 정규화 |
| `ccg7/normalize.py` | 1.6/1.7 적재, crypto-ref 합성, 3단계 병합(동일 crypto-ref → 파일 해시 → 파라미터 포섭), 충돌 시 고신뢰 채택·반증 보존, `C = min(C_max, 1−Π(1−c_i))`, provided/inferred/observed | §4.1~4.3 |
| `ccg7/policy.py` + `data/policy.yaml` | CM-01 SHA-1, CM-02 RSA<2048, QV-01 고전 공개키만 사용 (+참고 CM-03 MD5, CM-04 만료 인증서) | §6.1 위험 이원 분류, §6.3 게이트 등급 |
| `ccg7/priority.py` + `data/overlay.example.csv` | 기본형 `100·C·(0.25Q+0.20E+0.20D+0.15B+0.10M+0.10S)`, 확장형 `…·U÷A_norm` | §6.2 |
| `ccg7/report.py` | 표 1~6 (Markdown) + CSV/JSON | 5절 표 |
| `scripts/validate_schema.py` | 공식 JSON Schema(1.6/1.7)로 구문성 검증, `--as 1.6` 로 1.7 문서의 하위호환 실험 | 품질 4차원 중 구문성 |
| `scripts/01_mac_extra_tools.sh` | (선택) cbomkit-theia 로 nginx/Keycloak 이미지·디렉터리 1.6 CBOM 추가 | ④ 실제 도구 |

`targets.yaml` 에 대상별 CBOM 파일 목록을 적으면 어떤 도구 조합이든 병합됩니다. 도구명은 `metadata.tools.components[].name`
으로 식별하며 `ccg7/normalize.py` 의 `SOURCE_PROFILES` 에 증거면·신뢰도 상한을 둡니다(미등록 도구는 0.6).

## 3. 이번 실행 조건 (재현 정보)

| 항목 | 값 |
|---|---|
| 대상 | NodeBB `eb0f0ec` (2026-09-19), Ghost `8e2eb8a` (2026-09-18), verdaccio `6ce6075` (2026-09-20) — 모두 `--depth 1` |
| 도구 | cdxgen 13.1.0 (`cbom`, `--spec-version 1.7`), Node 22.22.2, Python 3.11.15, cryptography 46.0.7 |
| 레지스트리 | `data/cryptography-defs.json` (cdxgen 동봉, lastUpdated 2025-03-22; 79 계열·246 곡선) + 웹 레지스트리 확인분(scrypt, Argon2) |
| 스키마 | `data/schema/bom-1.6.schema.json`, `bom-1.7.schema.json` (cdxgen 동봉 공식 사본) |
| 평가 시각 | 2026-09-20 (CM-04 만료 판정 기준) |

## 4. 결과 요약 (`out/ccg7/report.md` 전문 참조)

- 입력 12개 CBOM(1.7 ×9, 1.6 ×3) **전부 공식 스키마 통과**; 1.7 문서 9개를 1.6 스키마로 검증하면 `algorithmFamily`, `ellipticCurve`,
  `certificateFileExtension`, `fingerprint`, `relatedCryptographicAssets`, 최상위 `citations` 를 쓴 **6개가 실패**(신규 필드가 없는 3개는 통과) → 1.6/1.7 병행 발행 필요의 실증.
- 원시 암호 자산 45 → 고유 crypto-ref **27** (40% 감소), occurrences 220 → 168. 다중 소스 병합 18건, 충돌 3건(cdxgen 이 `.key` 를 certificate 로 보고 → 수집기 파싱 결과 채택, cdxgen 주장은 반증으로 보존).
- 정책: CM-01 SHA-1 3, CM-02 RSA<2048 2, QV-01 고전 공개키 16 (참고: MD5 3, 만료 인증서 2).
- 우선순위 상위는 Ghost 의 members/identity 토큰용 RSA-2048·RS256/RS512 (운영 경로, 인터넷 노출). 기본형은 시험용 고정물(test fixture) 인증서를 4위·6~11위에 올리지만 확장형(U=provided 0.3)은 이를 내린다 — 기본/확장 순위 상관 Spearman ρ = 0.648.
- 인증서 입력을 1.6 표현으로 넣든 1.7 표현으로 넣든 **정규화 결과(자산·신뢰도·판정)가 동일**함을 확인.

## 5. 범위·한계

- 런타임(⑥) 증거가 없으므로 `observed` 는 0 이며 `inferred/provided` 는 경로 휴리스틱(test·example·docs 디렉터리)으로 근사한 것입니다.
- 오버레이(`overlay.example.csv`)의 B·D·E·S·A 값은 **예시값**입니다. 우선순위 절대값이 아니라 순위와 순위 변화(기본형 vs 확장형)를 보십시오.
- `naive_scanner` 는 정규식이라 오탐·누락이 있습니다. 단독 근거로 쓰지 않고 상한 0.4 로 융합에만 씁니다.
- Node/JS 대상만 다뤘습니다. Java(Keycloak 등)는 cdxgen 의 atom/evinse 또는 sonar-cryptography 가 필요합니다.
