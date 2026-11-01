# Leaf planner → ICBM

Applies to NISSAN_LEAF and NISSAN_LEAF_IC with existing ICBM enabled and
stock longitudinal control. Other vehicles retain the existing target selection.
No new CAN sender, brake command, or changes to Nissan PPP interlocks.
The stopped-lead advisory is a separate branch and is not included here.

## Target selection

ICBM reads longitudinalPlan.aTarget, the final acceleration selection from
AI/e2e, lead MPC, or cruise. Enter planner deceleration at <= -0.3 m/s² and
release at >= -0.1 m/s² to avoid switching at a single threshold.

The previous vEgo + aTarget * 3s conversion has been removed. Instead a
persistent set-speed cap starts at the acknowledged OEM setting (bounded by
the existing SP target). Compare aTarget with measured carState.aEgo:

- If braking is insufficient by more than 0.15 m/s², lower the cap at a rate
  proportional to the acceleration error, limited to 2 m/s per second.
- Never request more than one 5 km/h step below the acknowledged OEM setting while
  waiting for its response. OEM button steps can still overshoot this target.
- If already braking enough, raise the cap to at least the current OEM setting
  to stop further downward button requests. Excess braking allows gradual
  upward correction of the internal cap, limited to 0.5 m/s per second.
  Metric Leaf RES output is inhibited while planner deceleration remains active.
- After the deceleration request clears, release the cap at 0.5 m/s per second
  rather than jumping straight back to the user target.

The final target is the lower of this cap and longitudinalPlanSP.vTarget.
Existing curve/map/speed-limit targets can still lower it immediately; the
recovery rate only constrains the planner cap. Existing units/minima remain
30 km/h or 20 mph. These gains and thresholds are provisional, not a calibrated
model of OEM ACC response. Acceleration sensor delay, quantized buttons and
vehicle dynamics still affect tracking; this is not direct acceleration control.

Both plan messages and the source model must be fresh within 0.25 seconds.
Invalid/nonfinite inputs, driver operations and disengagement stop output and
reset cap state. Recovery uses the existing 0.4-second activation delay.
No changes to Nissan CAN transmission or PPP interlocks.

Cruise button timing helpers were moved to a lightweight shared module so
controller tests do not require native Params libraries. ICBM timer dictionaries
are now private to each instance.

## Validation (2026-11-01 feedback revision)

46 tests and 9 subtests passed, including existing parking/PPP tests.
New checks cover sufficient actual deceleration, falling ego speed without a
falling set-speed target, bounded pending reductions, release rate and threshold
hysteresis. Existing tests cover driver priority, invalid/stale input, platform
scope, curve bounds and units/minima. Ruff and diff checks passed.

Offline comparisons used route 00000053--44ff2a74c1 segments 7 and 8 with identical
recorded inputs. At monotonic 1299.8s the old target was 30 km/h and the new target
42 km/h; at 1361.8s the old target was 38 km/h and the new target 54 km/h.
No CAN is sent by replay. Vehicle acceleration/setpoint feedback comes from the
old implementation's recorded drive, so this is not a closed-loop simulation
or evidence of improved real vehicle behavior. Device deployment and real
response of this revision remain unverified.

```sh
PYTHONPATH=.:opendbc_repo:msgq_repo .venv/bin/python -m pytest -q \
  openpilot/sunnypilot/selfdrive/car/tests/test_planner_icbm.py \
  openpilot/sunnypilot/selfdrive/selfdrived/tests/test_parking.py \
  opendbc_repo/opendbc/sunnypilot/car/nissan/tests/test_parking.py

PYTHONPATH=.:opendbc_repo .venv/bin/python \
  tools/scripts/replay_planner_icbm.py /path/to/rlog.zst > decisions.jsonl
```

The replay tool processes one segment, assumes metric display, starts with an
empty ICBM state, and compares planner-connected and existing target selection.
Raw logs are kept outside the repository.

## Metric Leaf 5 km/h revision (2026-11-01)

59 tests and 9 subtests passed; Ruff and diff checks passed.

All metric Leaf targets, including SP curve/map/limit targets and planner caps,
are rounded down to a multiple of 5 km/h, bounded at 30 km/h. A 0.0001 km/h
tolerance handles Float32 representation of exact multiples; it is not a driving
deadband. Other platforms and imperial target rounding remain unchanged.

For moving metric Leaf only, a button request ends on any acknowledged setpoint
change, on target-direction cancellation, or after a 0.3s timeout. After it ends,
wait 1s before another request. An increase must additionally be requested
continuously for 1s, and is inhibited while planner deceleration is active.
Targets can keep updating during settling, but no buttons are requested. The
low-level Nissan CAN sender still emits its existing 60ms press/release pulses;
this pacing is not a guarantee of exactly one physical press if ACK is delayed.
No standstill RES/set-speed handling or low-level Nissan CAN code is changed.

A CAN-level test using the actual Nissan button sender and packer, with a
simulated 40ms OEM acknowledgement, requests 57 km/h from 60 and sends one SET,
settles at 55, and sends no RES during five seconds of simulation.

Offline replay of route 00000054--40ca87611f with the same recorded inputs:

| Interval (JST) | Old requested direction reversals | New reversals |
| --- | ---: | ---: |
| 11:39:20–35 | 83 | 0 |
| 11:40:30–37.55 | 58 | 0 |

RES request samples during aTarget <= -0.3 in the second interval decrease
from 12 to zero. These are high-level request changes, not counts of transmitted
CAN clicks. Recorded OEM setpoints/acceleration still come from the old drive;
this does not simulate the revised vehicle response or prove collision avoidance.
The downward rounding can select a target up to almost 5 km/h below the original
curve target. Real response and the settling interval require validation.
