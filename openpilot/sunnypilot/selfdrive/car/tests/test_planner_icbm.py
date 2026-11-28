import math
from types import SimpleNamespace as NS

import pytest

from opendbc.car import structs
from opendbc.car.nissan.values import CAR
from openpilot.cereal import custom, log
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, LeafTReference, SendButtonState, car_state_sp_valid, planner_data_valid,
  LEAF_INCREASE_CONFIRM_FRAMES, LEAF_SETTLE_FRAMES, LEAF_T_CONFIRM_DELTA, LEAF_T_MIN_SETTLE_FRAMES, LEAF_T_TREND_FRAMES,
  LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES, LEAF_REVERSE_DWELL_SET_AFTER_RES_FRAMES,
)


def setup(car=CAR.NISSAN_LEAF_IC, speed=60., target=80.):
  cp = structs.CarParams(carFingerprint=car)
  controller = IntelligentCruiseButtonManagement(cp, custom.CarParamsSP(pcmCruiseSpeed=False))
  cs = structs.CarState(vEgo=speed/3.6, cruiseState={'enabled': True, 'speedCluster': target/3.6})
  cc = structs.CarControl(enabled=True)
  sp = custom.LongitudinalPlanSP(vTarget=target/3.6)
  # aTarget -1 cannot come from a_cruise (floored at -0.5): it models a model (e2e) deceleration.
  # The capnp default source is cruise, which caps the planner at the SP target.
  plan = log.LongitudinalPlan.new_message(aTarget=-1., longitudinalPlanSource='e2e')
  return controller, cs, cc, sp, plan


def tick(c, cs, cc, sp, plan, valid=True, metric=True, frames=60, CS_SP=None):
  buttons = []
  for _ in range(frames):
    c.run(cs, cc, sp, metric, plan, plan_valid=valid, CS_SP=CS_SP)
    buttons.append(c.cruise_button)
  return buttons


@pytest.mark.parametrize('car', [CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC])
def test_deceleration_reaches_button_sender(car):
  c, cs, cc, sp, plan = setup(car=car)
  assert SendButtonState.decrease in tick(c, cs, cc, sp, plan)
  assert c.v_target == 75  # one OEM step below acknowledged setting


def test_curve_and_speed_limit_target_remains_upper_bound():
  c, cs, cc, sp, plan = setup(target=40.)
  tick(c, cs, cc, sp, plan)
  assert c.v_target <= 40


@pytest.mark.parametrize('accel', [-.29, 0., 1.])
def test_no_planner_cap_without_meaningful_deceleration(accel):
  c, cs, cc, sp, plan = setup()
  plan.aTarget = accel
  tick(c, cs, cc, sp, plan)
  assert c.v_target == 80


@pytest.mark.parametrize('metric,expected', [(True, 30), (False, 20)])
def test_oem_minimum(metric, expected):
  c, cs, cc, sp, plan = setup(speed=35., target=30. if metric else 20 * 1.609344)
  plan.aTarget = -3.
  tick(c, cs, cc, sp, plan, metric=metric)
  assert c.v_target == expected


def test_other_nissan_unchanged():
  c, cs, cc, sp, plan = setup(car=CAR.NISSAN_XTRAIL)
  tick(c, cs, cc, sp, plan, valid=False)
  assert c.v_target == 80


@pytest.mark.parametrize('fault', ['stale', 'missing', 'nan', 'brake', 'gas', 'cancel', 'override', 'resume', 'disabled', 'cruise_off'])
def test_faults_and_driver_actions_stop_button_output(fault):
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan)
  assert c.cruise_button == SendButtonState.decrease
  valid = fault != 'stale'
  if fault == 'missing':
    plan = None
  elif fault == 'nan':
    plan.aTarget = math.nan
  elif fault in ('brake', 'gas'):
    setattr(cs, fault + 'Pressed', True)
  elif fault in ('cancel', 'override', 'resume'):
    setattr(cc.cruiseControl, fault, True)
  elif fault == 'disabled':
    cc.enabled = False
  elif fault == 'cruise_off':
    cs.cruiseState.enabled = False
  assert all(b == SendButtonState.none for b in tick(c, cs, cc, sp, plan, valid=valid))


def test_manual_button_has_priority():
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan)
  cs.buttonEvents = [structs.CarState.ButtonEvent(type='decelCruise', pressed=True)]
  assert tick(c, cs, cc, sp, plan, frames=1) == [SendButtonState.none]


def test_recovery_uses_existing_acceleration_and_user_target():
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan)
  cs.cruiseState.speedCluster = 49/3.6
  plan.aTarget = 0.
  # the cluster drop acknowledges the SET: RES waits out LEAF_REVERSE_DWELL_RES_AFTER_SET (300 frames)
  assert SendButtonState.increase in tick(c, cs, cc, sp, plan, frames=350)
  assert c.v_target <= 80


def test_timer_state_is_not_shared():
  c, *_ = setup()
  other, *_ = setup()
  c.cruise_button_timers[next(iter(c.cruise_button_timers))] = 10
  assert not any(other.cruise_button_timers.values())


def test_missing_plan_does_not_restore_higher_target():
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan)
  cs.cruiseState.speedCluster = 49/3.6
  assert all(b == SendButtonState.none for b in tick(c, cs, cc, sp, plan, valid=False))
  assert c.v_target < 80


def test_no_decrease_below_oem_minimum():
  c, cs, cc, sp, plan = setup(speed=20., target=30.)
  assert all(b == SendButtonState.none for b in tick(c, cs, cc, sp, plan))


def test_disabled_icbm_sends_nothing():
  c, cs, cc, sp, plan = setup()
  c.CP_SP.pcmCruiseSpeed = True
  assert all(b == SendButtonState.none for b in tick(c, cs, cc, sp, plan))


def test_existing_curve_reduction_without_ai_deceleration():
  c, cs, cc, sp, plan = setup()
  plan.aTarget = 0.
  sp.vTarget = 40/3.6
  assert SendButtonState.decrease in tick(c, cs, cc, sp, plan)
  assert c.v_target == 40


@pytest.mark.parametrize('fault', ['none', 'invalid', 'old_plan', 'old_sp', 'old_model', 'future'])
def test_source_freshness(fault):
  sm = type('SM', (dict,), {})(longitudinalPlan=NS(modelMonoTime=100_000_000_000))
  sm.logMonoTime = {'longitudinalPlan': 100_000_000_000, 'longitudinalPlanSP': 100_000_000_000}
  sm.all_checks = lambda **kw: fault != 'invalid'
  if fault == 'old_plan':
    sm.logMonoTime['longitudinalPlan'] -= 1_000_000_000
  elif fault == 'old_sp':
    sm.logMonoTime['longitudinalPlanSP'] -= 1_000_000_000
  elif fault == 'old_model':
    sm['longitudinalPlan'].modelMonoTime -= 1_000_000_000
  elif fault == 'future':
    sm.logMonoTime['longitudinalPlan'] += 1_000_000_000
  assert planner_data_valid(sm, 100.1) == (fault == 'none')


def test_sufficient_actual_deceleration_stops_lowering():
  c, cs, cc, sp, plan = setup()
  cs.aEgo = -1.2
  tick(c, cs, cc, sp, plan, frames=500)
  assert c.v_target >= 80
  assert c.cruise_button != SendButtonState.decrease


def test_falling_speed_does_not_drag_target_down():
  c, cs, cc, sp, plan = setup(target=50.)
  cs.aEgo = -1.
  for speed in [50., 45., 40., 35.]:
    cs.vEgo = speed / 3.6
    tick(c, cs, cc, sp, plan, frames=100)
    assert c.v_target == 50


def test_request_release_is_rate_limited():
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan, frames=500)
  previous = c.planner_cap
  plan.aTarget = 0.
  tick(c, cs, cc, sp, plan, frames=1)
  assert 0 < c.planner_cap - previous <= .005001


def test_pending_reduction_is_bounded_without_oem_response():
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan, frames=1000)
  assert c.planner_cap >= cs.cruiseState.speedCluster - 5/3.6 - 1e-6


def test_threshold_hysteresis_preserves_deceleration_mode():
  c, cs, cc, sp, plan = setup()
  tick(c, cs, cc, sp, plan)
  plan.aTarget = -.2
  tick(c, cs, cc, sp, plan)
  assert c.planner_decelerating
  plan.aTarget = -.09
  tick(c, cs, cc, sp, plan)
  assert not c.planner_decelerating


@pytest.mark.parametrize('target,expected', [(57., 55), (58., 55), (60., 60), (54.99, 50), (30., 30)])
def test_leaf_quantizes_all_targets(target, expected):
  c, cs, cc, sp, plan = setup(target=target)
  plan.aTarget = 0.
  tick(c, cs, cc, sp, plan)
  assert c.v_target == expected


def test_waits_after_acknowledgement():
  c, cs, cc, sp, plan = setup(target=60.)
  sp.vTarget = 45/3.6
  tick(c, cs, cc, sp, plan, frames=60)
  cs.cruiseState.speedCluster = 55/3.6
  assert all(b == SendButtonState.none for b in tick(c, cs, cc, sp, plan, frames=80))
  assert SendButtonState.decrease in tick(c, cs, cc, sp, plan, frames=60)


def test_pending_press_cannot_reverse_direction():
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget = 0.
  sp.vTarget = 55/3.6
  tick(c, cs, cc, sp, plan)
  sp.vTarget = 65/3.6
  assert all(b == SendButtonState.none for b in tick(c, cs, cc, sp, plan, frames=50))


def test_no_res_during_planner_deceleration():
  c, cs, cc, sp, plan = setup(target=60.)
  tick(c, cs, cc, sp, plan)
  cs.cruiseState.speedCluster = 55/3.6
  cs.aEgo = -1.5
  assert SendButtonState.increase not in tick(c, cs, cc, sp, plan, frames=400)


def test_button_request_times_out_without_acknowledgement():
  c, cs, cc, sp, plan = setup(target=60.)
  sp.vTarget = 50/3.6
  ds = tick(c, cs, cc, sp, plan, frames=500)
  longest = run = 0
  for b in ds:
    run = run + 1 if b == SendButtonState.decrease else 0
    longest = max(longest, run)
  assert 0 < longest <= 31


def test_nissan_can_buttons_settle_at_55_without_res_bounce():
  from opendbc.can.packer import CANPacker
  from opendbc.sunnypilot.car.nissan.icbm import IntelligentCruiseButtonManagementInterface
  from opendbc.sunnypilot.car.nissan.nissancan_ext import LEAF_CRUISE_THROTTLE_SIGNALS

  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget = 0.
  sp.vTarget = 57/3.6
  sender = IntelligentCruiseButtonManagementInterface(c.CP, c.CP_SP)
  packer = CANPacker('nissan_leaf_2018_generated')
  stock = dict.fromkeys(LEAF_CRUISE_THROTTLE_SIGNALS, 0)
  stock['NO_BUTTON_PRESSED'] = 1
  wrapper = NS(out=cs, cruise_throttle_msg=stock)
  controls = structs.CarControlSP()
  previous = (False, False)
  pending = None
  presses = []
  for frame in range(500):
    if pending is not None and frame >= pending:
      cs.cruiseState.speedCluster = 55/3.6
      pending = None
    tick(c, cs, cc, sp, plan, frames=1)
    controls.intelligentCruiseButtonManagement.sendButton = {
      SendButtonState.none: structs.IntelligentCruiseButtonManagement.SendButtonState.none,
      SendButtonState.increase: structs.IntelligentCruiseButtonManagement.SendButtonState.increase,
      SendButtonState.decrease: structs.IntelligentCruiseButtonManagement.SendButtonState.decrease,
    }[c.cruise_button]
    if frame % 2:
      continue
    messages = sender.update(wrapper, controls, packer, frame, sender.last_button_frame, False)
    data = messages[0][1]
    buttons = (bool(data[3] & 16), bool(data[3] & 8))
    if buttons != previous and any(buttons):
      presses.append(buttons)
      pending = frame + 4  # observed OEM acknowledgement, 40ms after press
    previous = buttons
  assert presses == [(False, True)]
  assert round(cs.cruiseState.speedCluster * 3.6) == 55


def test_other_platform_nonmultiple_target_unchanged():
  c, cs, cc, sp, plan = setup(car=CAR.NISSAN_XTRAIL, target=57.)
  plan.aTarget = 0.
  tick(c, cs, cc, sp, plan)
  assert c.v_target == 57


def test_standstill_does_not_use_moving_button_pacing():
  c, cs, cc, sp, plan = setup(target=60.)
  cs.standstill = True
  sp.vTarget = 50/3.6
  plan.aTarget = 0.
  tick(c, cs, cc, sp, plan)
  assert c.cruise_button == SendButtonState.decrease
  assert c.leaf_pending is None


def test_new_deceleration_cancels_pending_increase():
  c, *_ = setup()
  c.is_ready = True
  c.planner_decelerating = True
  c.v_cruise_cluster = 55
  c.leaf_pending = (55, c.leaf_frame, SendButtonState.increase)
  assert c.pace_leaf_button(SendButtonState.increase) == SendButtonState.none
  assert c.leaf_pending is None


def _establish_decrease_press(c, cs, cc, sp, plan, cs_sp=None):
  """Run until a decrease press is in flight (pending acknowledgement)."""
  for _ in range(60):
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
  assert c.cruise_button == SendButtonState.decrease
  assert c.leaf_pending is not None


def test_t_rise_after_decrease_press_allows_early_next_press():
  c, cs, cc, sp, plan = setup(target=60.)
  sp.vTarget = 45 / 3.6
  cs_sp = custom.CarStateSP(leafAdasRequestT=2500)
  _establish_decrease_press(c, cs, cc, sp, plan, cs_sp)
  # OEM acknowledges the decrease by lowering the setpoint...
  cs.cruiseState.speedCluster = 55 / 3.6
  buttons = []
  for i in range(120):
    # ...T at the acknowledgement frame is the reference, then T rises to confirm braking
    cs_sp.leafAdasRequestT = 2500 if i == 0 else 2540
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
    buttons.append(c.cruise_button)
  # T confirmed the decrease: the next press arrives before the full 1.0 s settle elapses
  assert SendButtonState.decrease in buttons[:80]
  # ...but not before LEAF_T_MIN_SETTLE has passed since the acknowledgement
  assert SendButtonState.decrease not in buttons[:30]


def test_no_sp_keeps_full_settle():
  c, cs, cc, sp, plan = setup(target=60.)
  sp.vTarget = 45 / 3.6
  _establish_decrease_press(c, cs, cc, sp, plan, cs_sp=None)
  cs.cruiseState.speedCluster = 55 / 3.6
  buttons = []
  for _ in range(120):
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=None)
    buttons.append(c.cruise_button)
  # without T feedback the full LEAF_SETTLE_TIME (1.0 s = 100 frames) is observed
  assert SendButtonState.decrease not in buttons[:80]
  assert SendButtonState.decrease in buttons[:115]


def test_t_moving_opposite_direction_does_not_release_early():
  c, cs, cc, sp, plan = setup(target=60.)
  sp.vTarget = 45 / 3.6
  cs_sp = custom.CarStateSP(leafAdasRequestT=2500)
  _establish_decrease_press(c, cs, cc, sp, plan, cs_sp)
  cs.cruiseState.speedCluster = 55 / 3.6
  buttons = []
  for i in range(120):
    # a decrease press expects T to rise; T falling the opposite way must not confirm
    cs_sp.leafAdasRequestT = 2500 if i == 0 else 2460
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
    buttons.append(c.cruise_button)
  assert SendButtonState.decrease not in buttons[:80]


# ---- T-feedback hardening (FIXES.md items 2-4, 7, 8, 10) ----

DEC, INC, NONE = SendButtonState.decrease, SendButtonState.increase, SendButtonState.none


def _first(buttons, button):
  return next((i for i, b in enumerate(buttons) if b == button), None)


def _pending_press(direction, t=2500):
  """Leaf with a press of `direction` in flight; T has been valid and flat at `t` the whole time."""
  if direction == DEC:
    c, cs, cc, sp, plan = setup(target=60.)
    sp.vTarget = 45 / 3.6
  else:
    c, cs, cc, sp, plan = setup(target=55.)
    plan.aTarget = 0.
    sp.vTarget = 65 / 3.6
  cs_sp = custom.CarStateSP(leafAdasRequestT=t)
  for _ in range(300):
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
    if c.leaf_pending is not None:
      break
  assert c.leaf_pending is not None and c.leaf_pending[2] == direction
  return c, cs, cc, sp, plan, cs_sp


def _after_ack(env, t_after, frames=250, moved=None, hook=None):
  """Move the cluster one step in the requested direction (or by `moved`); frame 0 is the ack frame."""
  c, cs, cc, sp, plan, cs_sp = env
  setting, _, requested = c.leaf_pending
  cs.cruiseState.speedCluster = (setting + (moved if moved is not None else (-5 if requested == DEC else 5))) / 3.6
  buttons = []
  for i in range(frames):
    cs_sp.leafAdasRequestT = t_after(i)
    if hook is not None:
      hook(i)
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
    buttons.append(c.cruise_button)
  return buttons


def _full_settle(direction):
  """Frame (after the ack) of the next press with the legacy fixed settle; an increase also waits the RES confirm."""
  return LEAF_SETTLE_FRAMES + (LEAF_INCREASE_CONFIRM_FRAMES if direction == INC else 0)


def _early(direction):
  return LEAF_T_MIN_SETTLE_FRAMES + (LEAF_INCREASE_CONFIRM_FRAMES if direction == INC else 0)


def _expected_move(direction, counts):
  """T moved by `counts` in the direction the press should cause (decrease: T rises; increase: T falls)."""
  return counts if direction == DEC else -counts


@pytest.mark.parametrize('direction', [DEC, INC])
def test_t_response_releases_early_in_both_directions(direction):
  env = _pending_press(direction)
  buttons = _after_ack(env, lambda i: 2500 + (_expected_move(direction, 40) if i > 0 else 0))
  assert _first(buttons, direction) == _early(direction)


@pytest.mark.parametrize('direction', [DEC, INC])
def test_t_response_in_wrong_direction_keeps_full_settle(direction):
  env = _pending_press(direction)
  buttons = _after_ack(env, lambda i: 2500 - (_expected_move(direction, 80) if i > 0 else 0))
  assert _first(buttons, direction) == _full_settle(direction)


@pytest.mark.parametrize('direction', [DEC, INC])
@pytest.mark.parametrize('counts,early', [(LEAF_T_CONFIRM_DELTA - 1, False), (LEAF_T_CONFIRM_DELTA, True)])
def test_t_confirmation_boundary(direction, counts, early):
  env = _pending_press(direction)
  buttons = _after_ack(env, lambda i: 2500 + (_expected_move(direction, counts) if i > 0 else 0))
  assert _first(buttons, direction) == (_early(direction) if early else _full_settle(direction))


def test_t_zero_at_ack_then_valid_keeps_full_settle():
  # S1: the reference must not come from an invalid T (it used to confirm against ref 0)
  env = _pending_press(DEC)
  buttons = _after_ack(env, lambda i: 0 if i == 0 else 2595)
  assert _first(buttons, DEC) == _full_settle(DEC)
  env = _pending_press(DEC)
  buttons = _after_ack(env, lambda i: 0 if i == 0 else 2560)  # real-looking response after an invalid ack frame
  assert _first(buttons, DEC) == _full_settle(DEC)


def test_t_never_valid_before_ack_keeps_full_settle():
  env = _pending_press(DEC, t=0)
  buttons = _after_ack(env, lambda i: 0 if i == 0 else 2595)
  assert _first(buttons, DEC) == _full_settle(DEC)


def test_t_valid_only_briefly_before_ack_keeps_full_settle():
  # the pre-press trend needs a continuously valid window; a short one gives no reference
  c, cs, cc, sp, plan, cs_sp = env = _pending_press(DEC, t=0)
  cs_sp.leafAdasRequestT = 2500
  c.update_leaf_t(cs_sp)
  assert len(c.leaf_t_history) < LEAF_T_TREND_FRAMES
  buttons = _after_ack(env, lambda i: 2500 if i == 0 else 2560)
  assert _first(buttons, DEC) == _full_settle(DEC)


@pytest.mark.parametrize('recover', [False, True])
def test_t_lost_mid_settle_keeps_full_settle(recover):
  env = _pending_press(DEC)
  def t_after(i):
    if i == 0:
      return 2500
    if 10 <= i < 15 or (i >= 15 and not recover):
      return 0
    return 2560
  buttons = _after_ack(env, t_after)
  assert _first(buttons, DEC) == _full_settle(DEC)


def _closed_loop(t_at, frames=400):
  """REVIEW S2 scenario: 80 -> 40 km/h SP target, OEM acknowledges each press 40 ms later. Returns press frames."""
  c, cs, cc, sp, plan = setup(target=80.)
  sp.vTarget = 40 / 3.6
  plan.aTarget = 0.
  cs_sp = custom.CarStateSP()
  presses, acks = [], []
  cluster, pending, prev = 80, None, NONE
  for i in range(frames):
    if pending is not None and i >= pending:
      cluster -= 5
      cs.cruiseState.speedCluster = cluster / 3.6
      acks.append(i)
      pending = None
    if t_at is not None:
      cs_sp.leafAdasRequestT = t_at(i, acks)
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp if t_at is not None else None)
    if c.cruise_button == DEC and prev != DEC:
      presses.append(i)
      pending = i + 4
    prev = c.cruise_button
  return presses


def _step_response(i, acks, counts=60, delay=10):
  """T steps up `counts` once per acknowledged press, `delay` frames after the ack."""
  return counts * sum(i >= a + delay for a in acks)


def test_ambient_t_ramp_does_not_speed_up_pressing():
  # S2: T rising steadily for reasons unrelated to the press (+2 counts/frame) must not confirm presses
  baseline = _closed_loop(None)
  ramp = _closed_loop(lambda i, acks: 2530 + 2 * i)
  assert ramp == baseline
  assert all(b - a >= LEAF_SETTLE_FRAMES for a, b in zip(ramp, ramp[1:], strict=False))


def test_genuine_step_response_still_releases_early():
  baseline = _closed_loop(None)
  step = _closed_loop(lambda i, acks: 2530 + _step_response(i, acks))
  assert len(step) > len(baseline)
  assert all(b - a < LEAF_SETTLE_FRAMES / 2 for a, b in zip(step, step[1:], strict=False))


def test_step_response_on_top_of_ambient_ramp_still_releases_early():
  ramp_and_step = _closed_loop(lambda i, acks: 2530 + 2 * i + _step_response(i, acks))
  assert all(b - a < LEAF_SETTLE_FRAMES / 2 for a, b in zip(ramp_and_step, ramp_and_step[1:], strict=False))


def test_falling_ambient_trend_is_not_credited_to_a_decrease_press():
  # T was falling before the press and merely levels off afterwards: not a braking response
  c, cs, cc, sp, plan = setup(target=60.)
  sp.vTarget = 45 / 3.6
  cs_sp = custom.CarStateSP()
  frame = 0
  while c.leaf_pending is None:
    cs_sp.leafAdasRequestT = 2800 - 3 * frame
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
    frame += 1
  level = cs_sp.leafAdasRequestT
  buttons = _after_ack((c, cs, cc, sp, plan, cs_sp), lambda i: level)
  assert _first(buttons, DEC) == _full_settle(DEC)


def test_ack_timeout_clears_reference():
  c, cs, cc, sp, plan, cs_sp = _pending_press(DEC)
  c.leaf_t_ref = LeafTReference(t0=2500, sign=1, drift=0., ack_frame=c.leaf_frame)  # stale leftover
  buttons, timeout_frame = [], None
  for i in range(250):
    cs_sp.leafAdasRequestT = 2500 if timeout_frame is None else 2600
    c.run(cs, cc, sp, True, plan, plan_valid=True, CS_SP=cs_sp)
    buttons.append(c.cruise_button)
    if timeout_frame is None and c.leaf_pending is None:
      assert c.leaf_t_ref is None
      timeout_frame = i
  # no acknowledgement: full settle from the timeout even though T then moved strongly
  assert _first(buttons[timeout_frame + 1:], DEC) == LEAF_SETTLE_FRAMES - 1


def test_target_direction_change_clears_reference():
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget = 0.
  sp.vTarget = 55 / 3.6
  cs_sp = custom.CarStateSP(leafAdasRequestT=2500)
  tick(c, cs, cc, sp, plan, CS_SP=cs_sp)
  assert c.leaf_pending is not None and c.leaf_pending[2] == DEC
  c.leaf_t_ref = LeafTReference(t0=2400, sign=-1, drift=0., ack_frame=c.leaf_frame)  # stale leftover
  sp.vTarget = 65 / 3.6
  cs_sp.leafAdasRequestT = 2300  # would "confirm" the leftover increase reference
  buttons = tick(c, cs, cc, sp, plan, frames=250, CS_SP=cs_sp)
  assert c.leaf_t_ref is None
  assert _first(buttons, INC) == _full_settle(INC)


def test_driver_button_mid_settle_resets_reference():
  env = _pending_press(DEC)
  c, cs = env[0], env[1]
  def hook(i):  # driver taps SET- 50 ms into the settle
    cs.buttonEvents = [structs.CarState.ButtonEvent(type='decelCruise', pressed=i == 5)] if i in (5, 6) else []
  buttons = _after_ack(env, lambda i: 2500 + (40 if i > 0 else 0), hook=hook)
  assert c.leaf_t_ref is None
  assert _first(buttons, DEC) >= 5 + LEAF_SETTLE_FRAMES


def test_opposite_cluster_change_is_not_an_ack():
  # N1: a decrease press followed by the cluster moving up is an external change
  env = _pending_press(DEC)
  c = env[0]
  refs = []
  buttons = _after_ack(env, lambda i: 2500 + (80 if i > 0 else 0), moved=5,
                       hook=lambda i: refs.append(c.leaf_t_ref is None))
  assert all(refs[1:LEAF_SETTLE_FRAMES])  # no reference was ever taken
  assert _first(buttons, DEC) == _full_settle(DEC)


def test_reference_cleared_when_settle_expires():
  # N7/D2: no early release, the settle expires, and the reference is gone with it
  env = _pending_press(DEC)
  c = env[0]
  refs = []
  _after_ack(env, lambda i: 2500, frames=LEAF_SETTLE_FRAMES + 1, hook=lambda i: refs.append(c.leaf_t_ref is not None))
  assert all(refs[1:LEAF_SETTLE_FRAMES])  # held through the settle (hook runs before each frame)
  assert c.leaf_t_ref is None


def test_early_release_without_further_demand_sends_nothing():
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget = 0.
  sp.vTarget = 55 / 3.6
  cs_sp = custom.CarStateSP(leafAdasRequestT=2500)
  tick(c, cs, cc, sp, plan, CS_SP=cs_sp)
  assert c.leaf_pending is not None
  buttons = _after_ack((c, cs, cc, sp, plan, cs_sp), lambda i: 2500 + (40 if i > 0 else 0), frames=60)
  assert all(b == NONE for b in buttons)
  assert c.leaf_t_ref is None and c.leaf_pending is None


@pytest.mark.parametrize('fault', ['none', 'invalid', 'old', 'future'])
def test_car_state_sp_freshness(fault):
  # N3: selfdrived passes carStateSP to ICBM only when this holds
  sm = type('SM', (dict,), {})(carStateSP=custom.CarStateSP())
  sm.logMonoTime = {'carStateSP': 100_000_000_000}
  sm.all_checks = lambda **kw: fault != 'invalid' and kw == {'service_list': ['carStateSP']}
  if fault == 'old':
    sm.logMonoTime['carStateSP'] -= 100_000_000
  elif fault == 'future':
    sm.logMonoTime['carStateSP'] += 100_000_000
  assert car_state_sp_valid(sm, 100.01) == (fault == 'none')


def test_non_planner_car_ignores_t():
  c, cs, cc, sp, plan = setup(car=CAR.NISSAN_XTRAIL)
  tick(c, cs, cc, sp, plan, CS_SP=custom.CarStateSP(leafAdasRequestT=2500))
  assert c.leaf_t == 0 and not c.leaf_t_history


def test_leaf_adas_request_t_survives_capnp_conversion():
  from openpilot.selfdrive.car.helpers import convert_to_capnp
  assert convert_to_capnp(structs.CarStateSP(leafAdasRequestT=3415)).leafAdasRequestT == 3415


# ---- S3: planner-mode standstill RES suppression (FIXES.md item 10) ----

def _standstill(car):
  c, cs, cc, sp, plan = setup(car=car, speed=0., target=30.)
  sp.vTarget = 60 / 3.6
  plan.aTarget = 0.
  cs.standstill = True
  return c, cs, cc, sp, plan


def test_planner_standstill_sends_no_res():
  c, cs, cc, sp, plan = _standstill(CAR.NISSAN_LEAF_IC)
  assert INC not in tick(c, cs, cc, sp, plan, frames=300)
  # moving again: RES resumes through the normal pacing (confirm, ack, settle)
  cs.standstill = False
  cs.vEgo = 10 / 3.6
  assert INC in tick(c, cs, cc, sp, plan, frames=300)


def test_non_planner_standstill_res_unchanged():
  c, cs, cc, sp, plan = _standstill(CAR.NISSAN_XTRAIL)
  buttons = tick(c, cs, cc, sp, plan, frames=300)
  assert INC in buttons


# ---- TUNING_CONSULT.md Q5 1-3: cap quantization hysteresis, cruise-source cap floor, reverse dwell ----

def _drive(c, cs, cc, sp, plan, frames, step, sp_valid=True):
  """Closed loop with an OEM that acknowledges each press 40 ms later by one 5 km/h step (floor 30).

  `step(i)` sets the vehicle/planner inputs for frame i. Returns the (frame, button) of each press and the
  cluster (km/h) at every frame.
  """
  presses, clusters, pending, prev = [], [], None, NONE
  for i in range(frames):
    if pending is not None and i >= pending[0]:
      kph = round(cs.cruiseState.speedCluster * 3.6) + (-5 if pending[1] == DEC else 5)
      cs.cruiseState.speedCluster = max(30, kph) / 3.6
      pending = None
    step(i)
    c.run(cs, cc, sp, True, plan, plan_valid=True)
    clusters.append(round(cs.cruiseState.speedCluster * 3.6))
    b = c.cruise_button
    if b != NONE and b != prev:
      presses.append((i, b))
      pending = (i + 4, b)
    prev = b
  return presses, clusters


def _reversals(presses):
  return sum(a[1] != b[1] for a, b in zip(presses, presses[1:], strict=False))


def _a_cruise(v_target, v_ego):
  return float(min(max(v_target - v_ego, -0.5), 1.0))


def _episode_b(source, period=4.0):
  """vTarget 60 constant, vEgo 60 ± 1.2 km/h sine (OEM speed-hold wobble), aTarget = a_cruise."""
  c, cs, cc, sp, plan = setup(target=60.)
  plan.longitudinalPlanSource = source
  w = 2 * math.pi / period
  def step(i):
    t = i * 0.01
    cs.vEgo = (60 + 1.2 * math.sin(w * t)) / 3.6
    cs.aEgo = 1.2 / 3.6 * w * math.cos(w * t)
    plan.aTarget = _a_cruise(sp.vTarget, cs.vEgo)
  return _drive(c, cs, cc, sp, plan, 3000, step)


@pytest.mark.parametrize('source', ['cruise', 'e2e'])
def test_episode_b_no_set_res_reversals(source):
  presses, _ = _episode_b(source)
  assert _reversals(presses) == 0


def test_episode_b_hunts_without_hysteresis_and_dwell(monkeypatch):
  # control: the same scenario with Q5-1 and Q5-3 disabled reproduces the SET/RES hunting
  from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management import controller as ctl
  monkeypatch.setattr(ctl, 'PLANNER_CAP_QUANT_HYST', 0.)
  monkeypatch.setattr(ctl, 'LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES', 0)
  monkeypatch.setattr(ctl, 'LEAF_REVERSE_DWELL_SET_AFTER_RES_FRAMES', 0)
  presses, _ = _episode_b('e2e')
  assert _reversals(presses) >= 5


def _episode_a(source, cluster=40., frames=4000):
  """vTarget 40, vEgo 46; the OEM brakes at -0.25 m/s² toward its setting; aTarget = clip(vT - vEgo, -0.5)."""
  c, cs, cc, sp, plan = setup(speed=46., target=cluster)
  sp.vTarget = 40 / 3.6
  plan.longitudinalPlanSource = source
  def step(i):
    setting = cs.cruiseState.speedCluster
    cs.aEgo = -0.25 if cs.vEgo > setting + 0.05 else (0.3 if cs.vEgo < setting - 0.3 else 0.)
    cs.vEgo += cs.aEgo * 0.01
    plan.aTarget = _a_cruise(sp.vTarget, cs.vEgo)
  return _drive(c, cs, cc, sp, plan, frames, step)


@pytest.mark.parametrize('cluster', [40., 50.])
def test_episode_a_cruise_source_stops_at_sp_target(cluster):
  presses, clusters = _episode_a('cruise', cluster)
  assert min(clusters) >= 40 and clusters[-1] >= 40
  assert INC not in [b for _, b in presses]


def test_episode_a_e2e_source_still_lowers_below_sp_target():
  _, clusters = _episode_a('e2e')
  assert min(clusters) < 40


def _cap_entry(a_target, source='e2e', a_ego=0.):
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget, plan.longitudinalPlanSource, cs.aEgo = a_target, source, a_ego
  tick(c, cs, cc, sp, plan, frames=1)
  return c, cs, cc, sp, plan


@pytest.mark.parametrize('a_target,source,a_ego,bypass', [
  (-0.5, 'e2e', 0., False),
  (-0.8, 'e2e', 0., True),    # error <= -0.8
  (-0.5, 'e2e', 0.3, True),   # error = -0.8 from a positive aEgo
  (-0.5, 'lead0', 0., True),
  (-0.5, 'lead1', 0., True),
])
def test_cap_hysteresis_bypass(a_target, source, a_ego, bypass):
  c, *_ = _cap_entry(a_target, source, a_ego)
  assert c.planner_cap * 3.6 < 60  # the cap has started below the boundary
  assert c.planner_cap_level == (55 if bypass else 60)
  assert c.v_target == c.planner_cap_level


def test_cap_hysteresis_steps_down_below_boundary_minus_hq_and_back_up_at_boundary():
  c, cs, cc, sp, plan = _cap_entry(-0.5)
  assert c.planner_cap_level == 60
  c.planner_cap = 57.6 / 3.6
  c.quantize_planner_cap(bypass=False)
  assert c.planner_cap_level == 60
  c.planner_cap = 57.5 / 3.6
  c.quantize_planner_cap(bypass=False)
  assert c.planner_cap_level == 55
  c.planner_cap = 59.9 / 3.6  # recovering, still short of the boundary
  c.quantize_planner_cap(bypass=False)
  assert c.planner_cap_level == 55
  c.planner_cap = 60. / 3.6
  c.quantize_planner_cap(bypass=False)
  assert c.planner_cap_level == 60


def test_cap_hysteresis_delays_first_step_only_until_hq_is_integrated():
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget = -0.5  # error -0.5: cap falls at 0.5 m/s/s, 2.5 km/h takes ~1.4 s
  buttons = tick(c, cs, cc, sp, plan, frames=200)
  first = _first(buttons, DEC)
  assert first is not None and 100 < first < 200


def test_cruise_source_cap_does_not_go_below_sp_target():
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget, plan.longitudinalPlanSource = -0.5, 'cruise'
  assert DEC not in tick(c, cs, cc, sp, plan, frames=1000)
  assert c.planner_cap * 3.6 >= 60 - 1e-3 and c.v_target == 60


def test_cruise_source_does_not_raise_a_cap_lowered_by_e2e():
  c, cs, cc, sp, plan = setup(target=60.)
  plan.aTarget = -0.5
  tick(c, cs, cc, sp, plan, frames=200)
  lowered = c.planner_cap
  assert lowered * 3.6 < 57.5
  plan.longitudinalPlanSource = 'cruise'
  tick(c, cs, cc, sp, plan, frames=50)
  assert c.planner_cap == pytest.approx(lowered)


@pytest.mark.parametrize('vision_state,expected', [('entering', 35), ('turning', 35), ('enabled', 40), ('leaving', 40)])
def test_cruise_source_scc_vision_curve_allows_one_extra_step(vision_state, expected):
  c, cs, cc, sp, plan = setup(target=40.)
  sp.longitudinalPlanSource = 'sccVision'
  sp.smartCruiseControl.vision.state = vision_state
  plan.aTarget, plan.longitudinalPlanSource = -0.5, 'cruise'
  presses, clusters = _drive(c, cs, cc, sp, plan, 1500, lambda i: None)
  assert clusters[-1] == expected  # one step at most, never a ratchet to 30
  assert c.v_target == expected


def _acked(direction, frames=400, change=None, cs_sp=None):
  """Acknowledge a SP-target-driven press of `direction`; then apply `change` and return the following buttons."""
  if direction == DEC:
    c, cs, cc, sp, plan = setup(target=60.)
    sp.vTarget = 55 / 3.6
  else:
    c, cs, cc, sp, plan = setup(target=55.)
    sp.vTarget = 60 / 3.6
  plan.aTarget = 0.
  for _ in range(400):
    c.run(cs, cc, sp, True, plan, plan_valid=True)
    if c.leaf_pending is not None:
      break
  assert c.leaf_pending is not None and c.leaf_pending[2] == direction
  cs.cruiseState.speedCluster = (55 if direction == DEC else 60) / 3.6
  c.run(cs, cc, sp, True, plan, plan_valid=True)  # ack frame
  assert c.leaf_last_ack == (direction, c.leaf_frame)
  if change is not None:
    change(cs, sp, plan)
  return c, tick(c, cs, cc, sp, plan, frames=frames)


def _go(v_target):
  def change(cs, sp, plan):
    sp.vTarget = v_target / 3.6
  return change


def test_res_waits_reverse_dwell_after_set():
  # without the dwell the RES would come at settle + confirm (200 frames); the dwell runs in parallel, not after
  c, buttons = _acked(DEC, change=_go(65.))
  assert _first(buttons, INC) == LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES - 1
  assert all(b == NONE for b in buttons[:LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES - 1])


def test_res_after_set_dwell_has_no_bypass():
  def change(cs, sp, plan):
    sp.vTarget = 65 / 3.6
    plan.longitudinalPlanSource = 'lead0'
  c, buttons = _acked(DEC, change=change)
  assert _first(buttons, INC) == LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES - 1


def test_set_waits_reverse_dwell_after_res():
  c, buttons = _acked(INC, change=_go(50.))
  assert _first(buttons, DEC) == LEAF_REVERSE_DWELL_SET_AFTER_RES_FRAMES - 1


def _bypass(kind):
  def change(cs, sp, plan):
    sp.vTarget = 50 / 3.6
    if kind == 'strong':
      plan.aTarget = -0.8
    elif kind in ('lead0', 'lead1'):
      plan.longitudinalPlanSource = kind
    elif kind in ('entering', 'turning', 'enabled'):
      sp.longitudinalPlanSource = 'sccVision'
      sp.smartCruiseControl.vision.state = kind
    elif kind == 'mild':
      plan.aTarget = -0.79
  return change


@pytest.mark.parametrize('kind,bypassed', [
  ('strong', True), ('lead0', True), ('lead1', True), ('entering', True), ('turning', True),
  ('enabled', False), ('mild', False),
])
def test_set_after_res_dwell_bypass(kind, bypassed):
  c, buttons = _acked(INC, change=_bypass(kind))
  expected = LEAF_SETTLE_FRAMES - 1 if bypassed else LEAF_REVERSE_DWELL_SET_AFTER_RES_FRAMES - 1
  assert _first(buttons, DEC) == expected


@pytest.mark.parametrize('direction,v_target,expected', [
  (DEC, 50., LEAF_SETTLE_FRAMES - 1),
  (INC, 65., LEAF_SETTLE_FRAMES + LEAF_INCREASE_CONFIRM_FRAMES - 1),
])
def test_reverse_dwell_does_not_affect_same_direction(direction, v_target, expected):
  c, buttons = _acked(direction, change=_go(v_target))
  assert _first(buttons, direction) == expected
