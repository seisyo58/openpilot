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
  waiting for its response (imperial: 2 km/h, as the 5 km/h step is metric-only).
  OEM button steps can still overshoot this target.
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

46 tests (37 ICBM + 9 parking/PPP) and 9 subtests passed at that revision.
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

59 tests (50 ICBM + 9 parking/PPP) and 9 subtests passed at that revision; Ruff
and diff checks passed. Current counts are in the T-feedback validation section below.

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
Low-level Nissan CAN code is not changed. Standstill bypasses this pacing; since
the 2026-11-28 hardening, planner mode suppresses ICBM RES at standstill (see below).

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

## ADAS-request T feedback (0x2B0)

CAN 0x2B0 (decimal 688, bus 1) carries the stock Nissan ADAS accel/brake request.
The smooth request value is a 12-bit quantity decoded as
`T = (b[2] << 4) | (b[1] & 0x0F)` from `ADAS_REQUEST_HI`/`ADAS_REQUEST_LO` on the
`PROPILOT_REQUEST` message. Larger T = more braking. Reference points: ~2530 ≈
0 m/s², ~2270 ≈ +0.8 m/s², ~2800 ≈ −0.8 m/s², idle ≈ 2595, friction-brake
marker ≈ 3415, i.e. roughly 340 counts per m/s². 0x2B0 was not previously defined
in the Leaf DBC; the generator source now carries it so the runtime-generated
`nissan_leaf_2018_generated` DBC exposes the message.

### Receive path, missing and stale 0x2B0

Only NISSAN_LEAF / NISSAN_LEAF_IC on stock longitudinal (`leaf_stock_long`) read
0x2B0; other Nissans neither register nor publish it. It is registered on the cam
parser up front with `freq = nan` (`ignore_alive`), so it never counts toward
`can_valid`: a missing, paused or stopped 0x2B0 cannot cause a CAN error or
disengagement. `CarStateExt` publishes `CarStateSP.leafAdasRequestT` only when the
last 0x2B0 frame is at most 100 ms older than the parser's latest update
(`LEAF_ADAS_REQUEST_MAX_AGE_NS`); a message that was never received or has gone
stale is published as 0. It never repeats a frozen last value. selfdrived passes
`carStateSP` to ICBM only when it passes `all_checks` (alive, frequency, valid,
where valid mirrors `canValid`) and is at most 50 ms old, the same pattern as
`planner_data_valid`. Otherwise it passes nothing, which ICBM treats as T = 0.

### T validity

T counts as valid in a frame only when carStateSP is fresh and T > 0. A reference
is taken only from T that has been valid continuously for the whole 0.3 s trend
window (`LEAF_T_TREND_WINDOW`) up to and including the acknowledgement frame.
If T is invalid in any frame during the settle, that settle's reference is
dropped and the settle runs its full length, even if T becomes valid again.

### Confirmation (early release)

A press counts as acknowledged only when the OEM setpoint cluster moves in the
requested direction. A move in the opposite direction ends the request as an
external change: there is no reference and the full settle applies.

At the acknowledgement, the pre-press trend of T is fitted over the trend window
with a Theil-Sen (median pairwise slope) line. The press-attributable response at
a later frame is the movement in the expected direction (a decrease press expects
T to rise, an increase press expects T to fall) beyond that trend, extrapolated
from the acknowledgement:

    response = sign · (T − T0) − drift · (frames since ack)

- `sign` is +1 for a decrease press and −1 for an increase press.
- `drift` is the pre-press slope in the expected direction, clamped at ≥ 0. An
  opposite trend is never credited to the press: T that was falling and merely
  levels off after a SET− does not confirm.
- `T0` is the directionally higher of the measured T and the fitted trend value
  at the acknowledgement.

Each of these choices can only make confirmation harder than the old absolute
check. The median fit means the plateau of the previous press's response, which
sits in the next window as a step, is not mistaken for a ramp.

The next press is released early only when all of these hold:

- the reference exists;
- T is valid in every frame since the acknowledgement;
- at least `LEAF_T_MIN_SETTLE` (0.3 s) has passed since the acknowledgement;
- the response is at least `LEAF_T_CONFIRM_DELTA` (30 counts, ≈ 0.09 m/s²) for
  `LEAF_T_CONFIRM_HOLD` (0.05 s) of consecutive frames.

The reference is cleared on acknowledgement timeout, target or direction change,
not-ready (driver button, disengagement, invalid plan), standstill, and when the
settle ends, whether released early or expired. A new press therefore always starts
from a fresh reference.

As a result, steady ambient T movement that was already under way before the
press does not confirm, for example the OEM reacting to a slowing lead or a
grade. In the REVIEW S2 scenario (T +2 counts per frame), the press rate equals
the no-T behaviour, where it was previously ~3×. A response that starts after the
acknowledgement does confirm, including on top of such a ramp. Two kinds of
movement remain indistinguishable from a response: an unrelated movement that
begins just after the acknowledgement, and a trend that accelerates during the
settle. Logs must show how often these occur.

When T is unavailable, invalid, or never confirms, the timing is identical to
the previous fixed behaviour: the full `LEAF_SETTLE_TIME` (1.0 s) settle is
observed and there is no early release. This is receive-only. Which buttons are
sent, the targets, and all non-Leaf behaviour are unchanged.

`LEAF_T_CONFIRM_DELTA`, `LEAF_T_MIN_SETTLE`, `LEAF_T_TREND_WINDOW`,
`LEAF_T_CONFIRM_HOLD` and the 100 ms / 50 ms freshness limits are initial,
uncalibrated values to be tuned from logs. The 0x2B0 rate and its presence in
every ignition/ICC state on both fingerprints have not been verified on the car.

### Timing base

Leaf pacing counts frames (one per `run()` call, nominally 100 Hz) instead of
accumulating floating-point seconds, so 0.3 s means exactly 30 frames. If frames
are dropped, the waits get longer, which is the conservative direction.

### Standstill (planner mode)

Pacing is bypassed at standstill. In planner mode (`use_planner`), ICBM never
emits RES (`increase`) while `CS.standstill` is set, because RES while held
launches the Leaf. SET− at standstill and all non-planner standstill behaviour
are unchanged.

### Validation (2026-11-28 hardening)

84 ICBM tests, 15 carstate/0x2B0 tests, and 9 parking/PPP tests with 9 subtests
passed. The carstate tests drive the real Nissan `CarInterface.update()` with
synthetic CAN. They check that `canValid` stays true and T = 0 without 0x2B0, and
that T drops to 0 after 0x2B0 stops while `canValid` stays true. They also check
the decode bit positions and that non-Leaf Nissans do not read 0x2B0.

```sh
PYTHONPATH=.:opendbc_repo:msgq_repo .venv/bin/python -m pytest -q \
  openpilot/sunnypilot/selfdrive/car/tests/test_planner_icbm.py \
  opendbc_repo/opendbc/sunnypilot/car/nissan/tests/test_carstate_ext.py \
  openpilot/sunnypilot/selfdrive/selfdrived/tests/test_parking.py \
  opendbc_repo/opendbc/sunnypilot/car/nissan/tests/test_parking.py
```

These are simulations with modelled OEM acknowledgement and T responses. They
are not evidence of real T noise levels or of real vehicle behaviour.
