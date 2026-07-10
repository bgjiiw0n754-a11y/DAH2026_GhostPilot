# GhostPilot: MAVLink-Based GNSS Deception Emulation

## Prior Art First

This project is not a zero-prior-art idea. It combines two existing research
directions:

- UAV GNSS/GPS spoofing research, such as
  [GPS Spoofing and Takeover Attacks on UAVs](https://www.usenix.org/conference/usenixsecurity22/presentation/sathaye)
  and
  [Unmanned Aircraft Capture and Control via GPS Spoofing](https://rnl.ae.utexas.edu/images/stories/files/papers/unmannedCapture.pdf).
- MAVLink false message injection and detection research, such as
  [MUVIDS: False MAVLink Injection Attack Detection](https://www.ndss-symposium.org/ndss-paper/auto-draft-96/).

There is also an unrelated public project already using the name
[GhostPilot](https://github.com/amsach/GhostPilot). That project is a
GPS-denied navigation stack based on Visual SLAM, ROS 2, Nav2, and AI
components. It is not the same attack idea as this project, but the name can
cause confusion. For that reason, this project should be described with a
specific subtitle:

```text
GhostPilot: MAVLink-Based GNSS Deception Emulation
```

or:

```text
GhostPilot: False GPS Sensor Injection over MAVLink
```

## Core Idea

GhostPilot models a navigation deception attack against UAV/UGV systems that
trust GNSS-derived position. The project does not transmit real spoofed GNSS
radio signals. Instead, it safely emulates the result of a deceived GNSS
receiver inside SITL.

The key chain is:

```text
deceptive GNSS position scenario
-> MAVLink GPS_INPUT sensor message
-> ArduPilot FC/EKF position estimate
-> official GLOBAL_POSITION_INT output changes
-> mission, route, log, or detection behavior can be evaluated
```

This makes the project a bridge between two domains:

- GNSS deception: the vehicle receives a believable but false position.
- MAVLink false message injection: the false sensor value is delivered through
  a MAVLink message path in a controlled test environment.

## What GNSS Deception Means Here

GNSS includes GPS, Galileo, GLONASS, BeiDou, and similar satellite navigation
systems. GNSS deception is different from simple jamming.

```text
GNSS jamming
-> receiver loses navigation signal
-> vehicle knows navigation quality is degraded
```

```text
GNSS deception / spoofing
-> receiver sees a believable but false position
-> vehicle may continue navigating with a wrong position estimate
```

GhostPilot focuses on the second case. The interesting failure mode is not
"the vehicle has no GPS." The interesting failure mode is "the vehicle still
believes it has GPS, but the position is slowly wrong."

## Attack Model

The attacker does not claim to break every drone or every GPS receiver. The
assumption is narrower and must stay explicit:

- The experiment runs in ArduPilot SITL or an equivalent MAVLink GPS-input
  configuration.
- The flight controller is configured to accept MAVLink GPS input, for example
  `GPS1_TYPE=14` in ArduPilot.
- MAVLink signing/authentication is absent, disabled, or the attacker already
  has access to a trusted input path.
- The injected position changes gradually, for example one meter per step,
  rather than jumping hundreds of meters at once.

This is a strong-assumption attack model, but it is still useful. It does not
claim that arbitrary deployed drones accept `GPS_INPUT` from the network by
default. It uses `GPS_INPUT` as a safe SITL substitute for the output of a
deceived GNSS receiver.

Relevant protocol and implementation references:

- MAVLink defines
  [`GPS_INPUT`](https://mavlink.io/en/messages/common.html#GPS_INPUT) as a GPS
  sensor input message.
- MAVLink defines
  [`GLOBAL_POSITION_INT`](https://mavlink.io/en/messages/common.html#GLOBAL_POSITION_INT)
  as a filtered global position output.
- ArduPilot documents MAVLink GPS input through
  [GPSInput](https://ardupilot.org/mavproxy/docs/modules/GPSInput.html).

## Why GLOBAL_POSITION_INT Is Not The Injection Point

`GLOBAL_POSITION_INT` is an output report. It is the flight controller saying,
"this is my current filtered global position." Sending another
`GLOBAL_POSITION_INT` packet from the side can confuse a log collector, GCS
view, or external stream analyzer, but it does not make the flight controller
use that packet as GPS input.

That legacy path has this identity:

```text
fake final-position telemetry
-> possible GCS/log/analyzer confusion
-> FC navigation input is not changed
```

GhostPilot's stronger SITL path is different:

```text
fake GPS sensor input
-> FC/EKF accepts it as navigation input
-> official GLOBAL_POSITION_INT changes afterward
```

So in the corrected design, `GLOBAL_POSITION_INT` is not the main attack
surface. It is the observable output used to verify that the GPS input
deception reached the flight controller's position estimate.

## B-Path Implementation In This Repository

The GPS-input path has been implemented inside the Git repository, not only in
an external scratch directory.

Relevant files:

- `src/attack_agent.py`
  - adds `--mode ghost-gps`
  - supports `--gps-engine gps-input`
  - supports `--verify`
  - injects gradual GPS drift through MAVLink `GPS_INPUT`
  - verifies whether the official `GLOBAL_POSITION_INT` output follows the
    injected target
- `scripts/sitl_latest_gps_input_smoke.sh`
  - runs the ArduPilot SITL binary used for the B-path smoke test
  - automatically calls `scripts/prepare_ardupilot_sitl.sh` if the binary does
    not exist
  - boots SITL with `GPS1_TYPE 14` and `GPS_TYPE 14`
  - creates `.venv` and installs `requirements.txt` if needed
  - executes the `ghost-gps` attack path
  - prints the official position reflection result
- `scripts/prepare_ardupilot_sitl.sh`
  - clones ArduPilot if needed
  - checks out the tested ref
    `5152cde4046b6c0bac5de44fc5d8d0caa925f041` by default
  - initializes submodules
  - runs `./waf configure --board sitl`
  - runs `./waf copter`
- `scripts/sitl_gps_input_smoke.sh`
  - kept as a legacy comparison script for older `dronekit-sitl` behavior
- `src/test_attack_agent_gps_input.py`
  - unit tests for coordinate offsets, bearing conversion, `GPS_INPUT`
    payloads, and `HIL_GPS` payloads

The ArduPilot source tree itself remains an external dependency. It is prepared
by script under `.external/ardupilot` by default, and that directory is ignored
by Git. This repository contains the reproducible build script and the smoke
test, not a vendored copy of ArduPilot.

## Verified Result

The corrected B-path was tested against a locally built ArduPilot SITL binary
from commit `5152cde4046b6c0bac5de44fc5d8d0caa925f041`.

Observed result:

```text
GPS_INPUT -> FC/EKF -> GLOBAL_POSITION_INT
observed=15 reflected=15
```

That means the injected GPS input was reflected in the flight controller's
official global position output under the MAVLink GPS-input configuration.

The old `GLOBAL_POSITION_INT`-only path did not provide that result. It sent
extra final-position telemetry but did not change the flight controller's own
navigation input.

The `HIL_GPS` path was also tested in the same latest-SITL setup and did not
reflect under that configuration:

```text
HIL_GPS -> FC/EKF -> GLOBAL_POSITION_INT
observed=16 reflected=0
```

Therefore the primary PoC path is `GPS_INPUT`, not `HIL_GPS`.

## Attack Scenarios

### Slow Drift

The injected GPS position changes by a small amount each step, such as one
meter. The goal is to avoid looking like an obvious teleport while creating
cumulative navigation error.

### Route Deviation

The vehicle believes it is following the planned route, while the estimated
position is slowly biased sideways.

### Return-To-Home Deception

If the current position or home-related navigation state is polluted, return
logic can be evaluated under deceptive location assumptions.

### Geofence Boundary Deception

The estimated position can be biased so the vehicle appears inside or outside a
virtual boundary incorrectly.

## What This Project Claims

This project claims:

- It emulates GNSS deception in SITL through MAVLink GPS sensor injection.
- It shows that `GPS_INPUT` can affect ArduPilot's official position output
  when MAVLink GPS input is selected.
- It separates the real injection point (`GPS_INPUT`) from the observable
  output (`GLOBAL_POSITION_INT`).
- It provides a controlled environment for testing slow-drift location
  deception against UAV/UGV navigation logic and telemetry analysis.

## What This Project Does Not Claim

This project does not claim:

- It transmits real spoofed GNSS RF signals.
- It breaks all commercial drones.
- It bypasses MAVLink signing or encryption.
- It changes the flight controller's navigation state by merely sending
  `GLOBAL_POSITION_INT`.
- It proves that default physical-GPS vehicles trust MAVLink `GPS_INPUT`
  without configuration.

## Recommended Positioning

The clean technical positioning is:

```text
GhostPilot is a SITL testbed for MAVLink-based GNSS deception emulation.
It injects false GPS sensor input through GPS_INPUT and verifies whether the
flight controller's official position estimate changes.
```

That positioning keeps the project connected to real UAV/UGV GNSS deception
research without overstating the PoC as a universal real-world GPS takeover.
