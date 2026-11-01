import math
from types import SimpleNamespace as NS

import pytest

from opendbc.car import structs
from opendbc.car.nissan.values import CAR
from openpilot.cereal import custom, log
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, SendButtonState, planner_data_valid,
)


def setup(car=CAR.NISSAN_LEAF_IC, speed=60., target=80.):
  cp = structs.CarParams(carFingerprint=car)
  controller = IntelligentCruiseButtonManagement(cp, custom.CarParamsSP(pcmCruiseSpeed=False))
  cs = structs.CarState(vEgo=speed/3.6, cruiseState={'enabled': True, 'speedCluster': target/3.6})
  cc = structs.CarControl(enabled=True)
  sp = custom.LongitudinalPlanSP(vTarget=target/3.6)
  plan = log.LongitudinalPlan.new_message(aTarget=-1.)
  return controller, cs, cc, sp, plan


def tick(c, cs, cc, sp, plan, valid=True, metric=True, frames=60):
  buttons = []
  for _ in range(frames):
    c.run(cs, cc, sp, metric, plan, plan_valid=valid)
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
  assert SendButtonState.increase in tick(c, cs, cc, sp, plan, frames=250)
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
  c.leaf_pending = (55, c.leaf_time, SendButtonState.increase)
  assert c.pace_leaf_button(SendButtonState.increase) == SendButtonState.none
  assert c.leaf_pending is None
