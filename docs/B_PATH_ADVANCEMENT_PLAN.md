# B-Path Advancement Plan

## 0. Objective

B-path must not end as another ambiguous "it worked, but only under a hidden
condition" experiment.

The goal is to produce one clean, reproducible, and honestly scoped result:

```text
Under a declared post-access MAVLink GPS-input condition, a kinematically
consistent GPS_INPUT sequence contaminates the FC/EKF position estimate, and
we verify whether that contamination reaches mission-level behavior.
```

If mission-level behavior does not change, that is also a valid result:

```text
B-path remains a GPS_INPUT reflection PoC, not a mission-impact attack.
```

## 1. Hard Separation Of Claims

### Claim A: Already Proven

```text
GPS_INPUT direct-link can be reflected in GLOBAL_POSITION_INT in ArduPilot SITL
when MAVLink GPS input is enabled.
```

This is a reflection/input-path result.

### Claim B: Not Proven Yet

```text
The attack changes AUTO, RTL, geofence, landing, or other mission decisions.
```

This needs a separate mission-impact experiment.

### Claim C: Not In Scope

```text
The project remotely hacks arbitrary enemy drones.
The project bypasses encryption, MAVLink signing, or authentication.
The project discovers a new GCS/companion zero-day.
The project performs real RF GNSS spoofing or jamming.
```

Do not use these claims.

## 2. No-Excuse Rules

Before running any B-path experiment, these facts must be recorded in the log
and result CSV:

- repo commit or working-tree diff summary
- ArduPilot path
- ArduPilot commit
- SITL binary path
- exact launch command
- target connection string
- whether the path is FC direct-link, MAVProxy output, MAVProxy input, or
  companion bridge
- `GPS1_TYPE`
- `GPS_TYPE`
- `SIM_GPS1_ENABLE`
- `gps_id`
- `fix_type`
- `satellites`
- `hacc`, `vacc`, `sacc`
- baseline latitude, longitude, altitude
- warmup duration
- injection interval
- requested step size
- requested bearing
- consistency profile
- max speed
- max acceleration
- total iterations
- verification message types observed

If any of these are missing, the result cannot be used as final evidence.

## 3. Experiments To Run

### E0 - Environment Lock

Purpose:

```text
Prove that the SITL environment is exactly the one being claimed.
```

Must capture:

- ArduPilot commit
- SITL binary hash or timestamp
- parameter snapshot before injection
- parameter snapshot after setup
- opened TCP/UDP ports
- whether MAVProxy is used
- whether direct FC link is used

Acceptance:

```text
All required environment fields are present in the result bundle.
```

Failure:

```text
Any hidden parameter, hidden route, or unknown SITL build remains.
```

### E1 - Negative Control: GLOBAL_POSITION_INT

Purpose:

```text
Show the old A-path does not contaminate FC navigation input.
```

Run:

```text
GLOBAL_POSITION_INT injection under the same SITL environment.
```

Expected:

```text
FC/EKF official position does not follow the injected final telemetry.
```

Why needed:

```text
It prevents mixing "GCS/log spoofing" with "FC input spoofing."
```

### E2 - Negative Control: MAVProxy UDP GPS_INPUT

Purpose:

```text
Show that the standard MAVProxy UDP path is not silently being used as the
successful path.
```

Run:

```text
GPS_INPUT sent to the MAVProxy UDP input/output path used by normal GCS-style
traffic.
```

Expected:

```text
GPS_INPUT does not reach FC navigation input in the default tested MAVProxy
configuration.
```

Why needed:

```text
It prevents claiming "UDP/GCS path works" when only FC direct-link works.
```

### E3 - Positive Control: FC Direct GPS_INPUT

Purpose:

```text
Reproduce the known B-path result cleanly.
```

Run:

```text
GPS_INPUT -> FC direct-link -> FC/EKF -> GLOBAL_POSITION_INT
```

Required success metrics:

- `observed_count`
- `reflected_count`
- `reflection_rate = reflected_count / observed_count`
- `final_target_drift_m`
- `final_official_drift_m`
- `mean_target_error_m`
- `max_target_error_m`

Acceptance:

```text
observed_count >= 10
reflection_rate >= 0.70
final_official_drift_m >= 0.60 * final_target_drift_m
mean_target_error_m <= max(2.0m, 0.50 * mean_target_drift_m)
```

Failure:

```text
Not enough observations, low reflection rate, or official position does not
track the injected target.
```

### E4 - EKF-Consistent Sequence: Linear vs Smooth

Purpose:

```text
Check whether a kinematically smoother GPS_INPUT sequence is more credible than
a naive linear step sequence.
```

Compare:

```text
linear
ekf-smooth
```

Both must use the same:

- SITL build
- parameter setup
- baseline
- total duration
- final intended drift
- bearing
- gps_id
- verification logic

Metrics:

- reflection rate
- mean target error
- max target error
- commanded speed range
- commanded acceleration range
- abrupt jump count
- GPS fix stability
- GPS_RAW_INT availability
- GLOBAL_POSITION_INT availability

Acceptance:

```text
ekf-smooth is accepted only if it is at least as reflective as linear and has
lower abruptness, lower target error, or cleaner speed/acceleration behavior.
```

Failure:

```text
If ekf-smooth only changes code style but produces no measurable improvement,
do not present it as a stronger attack.
```

### E5 - Post-Access Companion Bridge

Purpose:

```text
Model a realistic B-path access condition without pretending to remotely hack
the drone.
```

Chain:

```text
local companion/relay process
-> local JSON attack plan
-> trusted MAVLink GPS_INPUT path
-> FC/EKF
-> official position output
```

Required statement:

```text
This is a post-access payload model. It assumes the companion/relay position is
already trusted. It does not obtain that access.
```

Acceptance:

```text
The bridge reproduces E3/E4 reflection metrics using a local plan file and a
declared trusted link.
```

Failure:

```text
If it only wraps the direct-link command without clearer chain evidence, present
it as a convenience wrapper, not a stronger access model.
```

### E6 - Mission Impact: One Minimal Decision

Purpose:

```text
Move beyond "position output changed" and test whether mission behavior changes.
```

Pick one first, not all at once:

```text
geofence
RTL
AUTO waypoint
landing/home bias
```

Recommended first target:

```text
geofence
```

Reason:

```text
Geofence has a clearer position-dependent decision boundary than full AUTO
mission behavior and is easier to interpret.
```

Required comparison:

```text
baseline mission
same mission + linear GPS_INPUT
same mission + ekf-smooth GPS_INPUT
```

Metrics:

- decision changed: yes/no
- time to decision change
- position drift at decision change
- final position drift
- target error
- mode/status messages around the event

Acceptance:

```text
The injected position causes a reproducible mission-level decision difference
under the declared setup.
```

Failure:

```text
If official position changes but mission behavior does not, B-path remains a
navigation-output contamination PoC, not a mission-impact attack.
```

## 4. Result Bundle

Each experiment must write one result folder:

```text
results/b_path/<timestamp>_<experiment_name>/
  README.md
  env.json
  params_before.txt
  params_after.txt
  injection.csv
  telemetry.csv
  sitl.log
  verdict.json
```

Minimum `verdict.json` fields:

```json
{
  "experiment": "E3_direct_gps_input",
  "claim_tested": "GPS_INPUT direct-link reflection",
  "verdict": "pass",
  "reason": "reflection_rate >= 0.70 and final drift tracked",
  "not_claimed": [
    "remote exploit",
    "mission takeover",
    "MAVLink signing bypass"
  ]
}
```

## 5. Folder Strategy

Do not create a new top-level folder for B-path.

Use:

```text
DAH2026_GhostPilot/DAH2026_GhostPilot/
  src/
    attack_agent.py
    companion_gps_bridge.py
  scripts/
    sitl_latest_gps_input_smoke.sh
    sitl_companion_post_access_smoke.sh
  docs/
    B_PATH_ADVANCEMENT_PLAN.md
    GHOSTPILOT_GNSS_DECEPTION.md
    TECHNICAL_FINDINGS.md
  results/
    b_path/
```

Reason:

```text
B-path is GhostPilot/ArduPilot SITL work.
C-path is separate because it is a mission-flow inference simulator.
```

## 6. Final Decision Rules

### If E3 passes but E6 fails

Final positioning:

```text
B-path is a post-access GPS_INPUT reflection/input-contamination PoC.
```

Use as supporting work, not the main attack.

### If E3, E4, and E6 pass

Final positioning:

```text
B-path is a post-access cyber-physical navigation-deception payload that can
affect at least one mission-level position-dependent decision in SITL.
```

This is strong enough to present as a serious B-path escalation.

### If E3 fails

Final positioning:

```text
B-path is not stable enough for final claims.
```

Do not rely on B-path for the main story.

### If only companion bridge passes

Final positioning:

```text
The bridge is only a wrapper around an assumed trusted path.
```

Do not claim access-path realism unless there is chain evidence beyond wrapping
the same direct-link call.

## 7. Work Order

Run in this exact order:

1. E0 environment lock
2. E1 GLOBAL_POSITION_INT negative control
3. E2 MAVProxy UDP GPS_INPUT negative control
4. E3 FC direct GPS_INPUT positive control
5. E4 linear vs ekf-smooth comparison
6. E5 companion bridge reproduction
7. E6 one mission-impact test

Do not skip E1/E2. They are what prevent later ambiguity.

## 8. Current Draft Status

Draft code already exists for:

- `ekf-smooth` GPS_INPUT generation
- local post-access companion bridge
- companion SITL smoke script
- helper unit tests

One preliminary run produced:

```text
companion bridge smoke: observed=19 reflected=16
```

This is not yet final evidence because the full E0-E6 result bundle was not
captured.

## 9. Summary

The project should only advance B-path if it can survive this question:

```text
Exactly which path worked, under exactly which parameters, and did it change
mission behavior or only official telemetry?
```

If that question cannot be answered from files alone, the experiment is not
finished.

## 10. Execution Result - 2026-07-06 UTC

Unified runner:

```text
src/b_path_experiment.py
```

Unit tests:

```text
src/test_b_path_experiment.py
```

Validation commands passed:

```text
python -m py_compile src/attack_agent.py src/companion_gps_bridge.py src/b_path_experiment.py
python -m unittest src/test_attack_agent_gps_input.py src/test_b_path_experiment.py
```

Evidence bundles:

| Experiment | Verdict | Bundle |
|---|---:|---|
| E0 environment lock | pass | `results/b_path/20260706T163443_071791Z_E0_environment_lock/` |
| E1 GLOBAL_POSITION_INT negative | pass | `results/b_path/20260706T163526_548952Z_E1_global_position_int_negative/` |
| E2 MAVProxy UDP negative | fail | `results/b_path/20260706T163903_030624Z_E2_mavproxy_udp_gps_input_negative/` |
| E3 FC direct GPS_INPUT positive | pass | `results/b_path/20260706T163718_121237Z_E3_fc_direct_gps_input_positive/` |
| E4 linear GPS_INPUT | pass | `results/b_path/20260706T163954_662068Z_E4_linear_gps_input/` |
| E4 ekf-smooth GPS_INPUT | pass | `results/b_path/20260706T164019_982977Z_E4_ekf_smooth_gps_input/` |
| E5 companion post-access bridge | pass | `results/b_path/20260706T164059_713186Z_E5_companion_post_access_bridge/` |
| E6 geofence mission impact | pass | `results/b_path/20260706T164416_269335Z_E6_geofence_mission_impact/` |

Important correction:

```text
E2 did not pass as a negative control. In this local MAVProxy configuration,
GPS_INPUT sent through the UDP GCS-style endpoint was reflected by FC/EKF
(reflection_rate=1.0). Therefore the project must not claim that MAVProxy UDP
always blocks GPS_INPUT. The safer conclusion is: GLOBAL_POSITION_INT does not
contaminate FC input, while GPS_INPUT works when it reaches a trusted FC input
path; MAVProxy UDP behavior is configuration-dependent.
```

Final B-path classification from these bundles:

```text
post-access navigation-deception payload in authorized ArduPilot SITL
```

Reason:

```text
E3, E4, E5, and E6 passed. The injected GPS_INPUT sequence changed official
position output and caused a geofence decision change in SITL. This still does
not prove remote access, signing bypass, RF GNSS spoofing, RF jamming, or real
vehicle takeover.
```

## 11. Advanced Execution Result - 2026-07-06 UTC

Advanced runner additions:

```text
E7_route_matrix
E8_adaptive_geofence
E9_auto_waypoint_reach
E10_summary
```

Validation commands passed:

```text
python -m py_compile src/attack_agent.py src/companion_gps_bridge.py src/b_path_experiment.py
python -m unittest src/test_attack_agent_gps_input.py src/test_b_path_experiment.py
```

Evidence bundles:

| Experiment | Verdict | Bundle |
|---|---:|---|
| E7 route/message matrix | pass | `results/b_path/20260706T222750_745673Z_E7_route_matrix/` |
| E8 adaptive geofence | pass | `results/b_path/20260706T223735_241593Z_E8_adaptive_geofence/` |
| E9 AUTO waypoint reach | fail | `results/b_path/20260706T223941_086117Z_E9_auto_waypoint_reach/` |
| E10 advanced summary | pass | `results/b_path/20260706T224434_991443Z_E10_summary/` |

Advanced result:

```text
adaptive geofence-deception payload
```

Evidence:

```text
E7: 9 route/message cells tested. GPS_INPUT reflected in 3/3 tested routes.
GLOBAL_POSITION_INT reflected in 0/3. HIL_GPS reflected in 0/3.

E8: baseline had no geofence breach. Adaptive GPS_INPUT found a geofence breach
with minimum_breach_drift_m=18.7289m under max_speed=2.5m/s and
max_accel=1.0m/s^2. Linear comparison ran to final_target_drift_m=29.9663m.

E9: AUTO waypoint mission-current did not advance earlier under GPS_INPUT.
Do not claim AUTO waypoint mission takeover.
```

Final advanced claim:

```text
허가된 ArduPilot SITL에서, post-access GPS_INPUT payload가 텔레메트리 피드백을 이용해
고정 linear run보다 작은 drift로 geofence decision을 유도할 수 있다.
```

Still not claimed:

```text
remote exploit, credential bypass, MAVLink signing bypass, RF GNSS spoofing,
RF jamming, real vehicle takeover, full mission takeover
```

## 12. Real Advanced Execution Result - 2026-07-07 UTC

Implemented additions:

```text
src/bpath/mission_decisions.py
src/bpath/stealth.py
src/bpath/routes.py
src/bpath/planner.py
E11_mission_decision_matrix
E12_stealth_optimizer
E13_route_relaxation
E14_auto_attack_planner
E15_mission_impact_full_run
E16_claim_classifier
E17_final_summary
```

Validation commands passed:

```text
.venv/bin/python -m py_compile src/attack_agent.py src/companion_gps_bridge.py src/b_path_experiment.py src/bpath/*.py
.venv/bin/python -m unittest src/test_attack_agent_gps_input.py src/test_b_path_experiment.py
```

Evidence bundles:

| Experiment | Verdict | Bundle |
|---|---:|---|
| E11 mission decision matrix | fail | `results/b_path/20260707T010445_557499Z_E11_mission_decision_matrix/` |
| E12 stealth optimizer | pass | `results/b_path/20260707T011205_772112Z_E12_stealth_optimizer/` |
| E13 route relaxation | pass | `results/b_path/20260707T011423_872299Z_E13_route_relaxation/` |
| E14 auto attack planner | pass | `results/b_path/20260707T011607_827891Z_E14_auto_attack_planner/` |
| E15 mission impact full run | fail | `results/b_path/20260707T011642_237696Z_E15_mission_impact_full_run/` |
| E16 claim classifier | pass | `results/b_path/20260707T011642_256681Z_E16_claim_classifier/` |
| E17 final summary | fail | `results/b_path/20260707T011642_274206Z_E17_final_summary/` |

Result:

```text
The planner/optimizer/route-matrix implementation executes end to end, but the
measured mission-impact result is still geofence-only.
```

Key measured facts:

```text
E11: geofence changed under stealth-opt GPS_INPUT while baseline did not.
auto-waypoint, RTL, land, and failsafe did not show an isolated decision change.

E12: linear and stealth-opt both produced geofence impact. stealth-opt reduced
the measured stealth score from 7.499438 to 0.479061 under the normal budget.

E13: the selected geofence payload reproduced through fc-direct, MAVProxy UDP,
and companion-labeled post-access routes in this local SITL setup.

E14: --objective auto produced attack_plan.json and selected geofence from
planner_trace.csv, then executed it successfully.

E15/E17: the stricter real-advanced target failed because no non-geofence
mission decision succeeded.
```

Current bounded claim after E11-E17:

```text
허가된 ArduPilot SITL에서, post-access GPS_INPUT payload가 planner/stealth
optimizer/route selection을 통해 geofence decision을 유도할 수 있다. 단,
이번 실행은 waypoint, RTL, land, failsafe decision deception을 입증하지 못했다.
```

## 13. AI-Excluded MAVLink Payload Matrix Result - 2026-07-07 UTC

The final B-path direction was narrowed again: remove the AI/planner claim from
the main result and classify deterministic MAVLink payloads under the same
authorized local SITL condition.

Final claim:

```text
허가된 ArduPilot SITL에서 MAVLink write access가 이미 노출된 post-access
상황을 가정하고, GPS_INPUT, mode command, mission edit, parameter edit,
telemetry deception payload의 임무 영향과 전제조건을 deterministic matrix로
실험·분류했다.
```

Implemented additions:

```text
E18_payload_matrix
E19_mode_command
E20_mission_edit
E21_param_edit
E22_telemetry_deception
E23_attack_surface_summary
--payload gps-input|mode-command|mission-edit|param-edit|telemetry-deception|all
--experiment all-payloads
payload_matrix.csv
mission_impact_matrix.csv
precondition_matrix.csv
attack_surface_summary.json
```

Validation commands passed:

```text
.venv/bin/python -m py_compile src/attack_agent.py src/companion_gps_bridge.py src/b_path_experiment.py src/bpath/*.py
.venv/bin/python -m unittest src/test_attack_agent_gps_input.py src/test_b_path_experiment.py
```

Final all-payloads evidence bundles:

| Experiment | Verdict | Bundle |
|---|---:|---|
| E19 mode command | pass | `results/b_path/20260707T024455_949208Z_E19_mode_command/` |
| E20 mission edit | pass | `results/b_path/20260707T025130_250985Z_E20_mission_edit/` |
| E21 parameter edit | pass | `results/b_path/20260707T025208_174522Z_E21_param_edit/` |
| E22 telemetry deception | pass | `results/b_path/20260707T025231_390703Z_E22_telemetry_deception/` |
| E18 payload matrix | pass | `results/b_path/20260707T025255_406828Z_E18_payload_matrix/` |
| E23 attack surface summary | pass | `results/b_path/20260707T025255_422162Z_E23_attack_surface_summary/` |

Measured payload results:

| Payload | Confirmed effect | Important boundary |
|---|---|---|
| `GPS_INPUT` | FC/EKF position estimate and geofence decision impact | Requires a trusted GPS_INPUT path to the FC; this is not remote access or RF GNSS spoofing. |
| `MODE_COMMAND` | RTL/LAND/BRAKE/LOITER HEARTBEAT mode changes observed | This is stronger than indirect GPS deception when MAVLink write access already exists. |
| `MISSION_EDIT` | Mission upload ACK and readback count 2 confirmed | Mission list modification was confirmed; this does not claim full autonomous mission takeover. |
| `PARAM_EDIT` | Fence/action/failsafe/speed parameters changed and read back | Latest Copter uses `WP_SPD`; old `WPNAV_SPEED` did not respond in this build. |
| `TELEMETRY_DECEPTION` | `GLOBAL_POSITION_INT` injection stayed telemetry/log deception only | Not FC navigation input contamination; keep separate from GPS spoofing. |

Important implementation corrections:

```text
mission_clear_all ACK is no longer accepted as mission upload ACK.
Mission edit only passes after mission item upload and readback.
Parameter edit selects an existing speed parameter from candidates; this build
selected WP_SPD.
```

Still not claimed:

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
