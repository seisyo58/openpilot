import unittest
from collections import defaultdict

from opendbc.can import CANPacker
from opendbc.car import structs, DT_CTRL
from opendbc.car.nissan.interface import CarInterface
from opendbc.car.nissan.values import CAR
from opendbc.sunnypilot.car.interfaces import setup_interfaces
from opendbc.sunnypilot.car.nissan.auto_resume import (
  ARM_FRAMES,
  CLOSE_LEAD_M,
  COOLDOWN_FRAMES,
  DEPART_M,
  DEPART_VREL_MIN,
  HUD3_SUCCESS_FRAMES,
  MAX_TAPS_PER_STOP,
  OUTCOME_WINDOW_FRAMES,
  PRESS_RES_SET_TO_CHANGE_SPEED,
  PRESS_RES_TO_RESTART,
  RES_BUTTON,
  RES_HOLD_FRAMES,
  RETRY_WAIT_FRAMES,
  TAP_GAP_FRAMES,
  TRIGGER_FRAMES,
  V_EGO_SUCCESS,
  NissanAutoResume,
)
from opendbc.sunnypilot.car.nissan.values import NissanFlagsSP, NissanSafetyFlagsSP


def _step(resume, n=1, **overrides):
  values = dict(
    enabled=True,
    standstill=True,
    cruise_enabled=True,
    brake_pressed=False,
    gas_pressed=False,
    bottom_msg=PRESS_RES_TO_RESTART,
    lead_present=True,
    lead_drel=4.0,
    lead_vrel=1.0,
    parking_blocked=False,
    cancel=False,
    v_ego=0.0,
  )
  values.update(overrides)
  pressed = None
  for _ in range(n):
    pressed = resume.update(**values)
  return pressed


def _res_pressed(out) -> bool:
  return out == RES_BUTTON


def _run_one_tap_cycle(resume, bottom=PRESS_RES_TO_RESTART, **kw):
  """Complete hold + gap + outcome window with synthetic HUD."""
  kw.setdefault("lead_drel", 7.5)
  _step(resume, RES_HOLD_FRAMES, bottom_msg=bottom, **kw)
  _step(resume, TAP_GAP_FRAMES, bottom_msg=bottom, **kw)
  _step(resume, OUTCOME_WINDOW_FRAMES + 1, bottom_msg=bottom, **kw)
  _step(resume, RETRY_WAIT_FRAMES, bottom_msg=bottom, **kw)


class TestAutoResumeStateMachine(unittest.TestCase):
  def setUp(self):
    self.resume = NissanAutoResume()

  def _arm(self, drel=4.0):
    self.assertFalse(_step(self.resume, ARM_FRAMES - 1, lead_drel=drel))
    self.assertFalse(_step(self.resume, 1, lead_drel=drel))

  def test_arms_after_continuous_close_lead(self):
    self.assertFalse(_step(self.resume, ARM_FRAMES - 1))
    self.assertFalse(self.resume._armed)
    _step(self.resume, 1)
    self.assertTrue(self.resume._armed)
    self.assertEqual(self.resume._min_drel, 4.0)

  def test_lead_outside_window_or_dropout_does_not_arm(self):
    self.assertFalse(_step(self.resume, ARM_FRAMES + 5, lead_drel=CLOSE_LEAD_M))
    self.assertFalse(self.resume._armed)
    self.assertFalse(_step(self.resume, ARM_FRAMES, lead_present=False))
    self.assertFalse(self.resume._armed)

  def test_gap_resets_arm_timer(self):
    _step(self.resume, ARM_FRAMES - 1)
    _step(self.resume, 1, lead_present=False)
    self.assertFalse(self.resume._armed)
    _step(self.resume, ARM_FRAMES - 1)
    self.assertFalse(self.resume._armed)

  def test_tracks_minimum_distance_while_armed(self):
    self._arm(5.0)
    _step(self.resume, 1, lead_drel=4.2)
    _step(self.resume, 1, lead_drel=4.8)
    self.assertEqual(self.resume._min_drel, 4.2)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 2, lead_drel=4.2 + DEPART_M))
    self.assertFalse(self.resume._departed)

  def test_one_meter_departure_boundary(self):
    self._arm(4.0)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 2, lead_drel=4.9))
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 2, lead_drel=5.0))
    self.assertEqual(_step(self.resume, TRIGGER_FRAMES, lead_drel=5.1), RES_BUTTON)

  def test_triggers_on_first_qualifying_update_and_holds_res(self):
    self._arm(4.0)
    presses = [_step(self.resume, 1, lead_drel=7.5) for _ in range(RES_HOLD_FRAMES)]
    self.assertEqual(presses, [RES_BUTTON] * RES_HOLD_FRAMES)

  def test_tap_aborts_with_driver_or_disable_gates(self):
    for gate in ({"brake_pressed": True}, {"gas_pressed": True}, {"cancel": True},
                 {"cruise_enabled": False}, {"parking_blocked": True},
                 {"standstill": False}, {"enabled": False}):
      with self.subTest(gate=gate):
        self.resume = NissanAutoResume(on_transition=lambda _: None)
        self._arm()
        self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
        for _ in range(RES_HOLD_FRAMES - 1):
          self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
        self.assertFalse(_res_pressed(_step(self.resume, 1, lead_drel=7.5, **gate)))

  def test_single_qualifying_update_triggers_without_confirmation(self):
    self._arm()
    self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
    self.assertTrue(self.resume._sent)

  def test_once_per_standstill_after_success(self):
    self._arm()
    self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
    _step(self.resume, TAP_GAP_FRAMES, bottom_msg=PRESS_RES_SET_TO_CHANGE_SPEED)
    _step(self.resume, HUD3_SUCCESS_FRAMES, bottom_msg=PRESS_RES_SET_TO_CHANGE_SPEED)
    self.assertTrue(self.resume._session_done)
    self.assertIsNone(_step(self.resume, 20, lead_drel=7.5))

  def test_new_standstill_can_resume_again(self):
    self._arm()
    self.assertEqual(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5), RES_BUTTON)
    _step(self.resume, 1, standstill=False)
    self.assertFalse(self.resume._sent)
    self._arm()
    self.assertEqual(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5), RES_BUTTON)

  def test_blocked_by_ppp_brake_gas_cruise_message_and_toggle(self):
    cases = [
      {"parking_blocked": True},
      {"brake_pressed": True},
      {"gas_pressed": True},
      {"cruise_enabled": False},
      {"bottom_msg": 0},
      {"bottom_msg": 2},
      {"enabled": False},
      {"standstill": False},
      {"cancel": True},
    ]
    for overrides in cases:
      with self.subTest(overrides=overrides):
        self.setUp()
        self._arm()
        pressed = [_step(self.resume, 1, lead_drel=7.5, **overrides) for _ in range(TRIGGER_FRAMES + RES_HOLD_FRAMES)]
        self.assertNotIn(RES_BUTTON, pressed)

  def test_wrong_bottom_msg_waits_until_press_res(self):
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, bottom_msg=0))
    self.assertTrue(self.resume._departed)
    self.assertEqual(_step(self.resume, 1, bottom_msg=PRESS_RES_TO_RESTART, lead_drel=7.5), RES_BUTTON)

  def test_returning_lead_invalidates_pending_departure(self):
    gates = ({"bottom_msg": 0}, {"brake_pressed": True}, {"cancel": True})
    for gate in gates:
      with self.subTest(gate=gate):
        self.setUp()
        self._arm(4.0)
        self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, **gate))
        self.assertTrue(self.resume._departed)
        self.assertFalse(_step(self.resume, 1, lead_drel=4.0, **gate))
        self.assertFalse(self.resume._departed)
        self.assertTrue(self.resume._armed)

  def test_missing_lead_invalidates_pending_departure(self):
    self._arm(4.0)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, bottom_msg=0))
    self.assertTrue(self.resume._departed)
    self.assertFalse(_step(self.resume, 1, lead_present=False, lead_drel=7.5, bottom_msg=0))
    self.assertFalse(self.resume._departed)

  def test_armed_near_8m_can_depart_past_the_window(self):
    self._arm(7.2)
    for i in range(1, 21):
      drel = 7.2 + (10.7 - 7.2) * i / 20
      _step(self.resume, 1, lead_drel=drel, bottom_msg=0)
    presses = [_step(self.resume, 1, lead_drel=10.7) for _ in range(TRIGGER_FRAMES)]
    self.assertIn(RES_BUTTON, presses)

  def test_cancel_aborts_tapping_session(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm()
    self.assertEqual(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5), RES_BUTTON)
    _step(self.resume, 1, cancel=True, lead_drel=7.5)
    self.assertTrue(self.resume._session_done)
    self.assertIn("tap_giveup", [e["phase"] for e in events])
    self.assertIsNone(_step(self.resume, RES_HOLD_FRAMES, lead_drel=7.5))

  def test_ppp_clears_a_latched_departure(self):
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, bottom_msg=0))
    _step(self.resume, 1, parking_blocked=True, bottom_msg=PRESS_RES_TO_RESTART)
    self.assertFalse(self.resume._departed)

  def test_brake_holds_off_the_press_until_released(self):
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, brake_pressed=True))
    self.assertTrue(self.resume._departed)
    self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)

  def test_distance_without_closing_speed_does_not_trigger(self):
    self._arm(3.5)
    far = 3.5 + DEPART_M + 1.0
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 5, lead_drel=far, lead_vrel=0.0))
    self.assertFalse(self.resume._departed)

  def test_transition_log_marks_arm_depart_and_tap(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm(3.5)
    self.assertEqual(events[-1]["phase"], "armed")
    far = 3.5 + DEPART_M + 1.0
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=far, bottom_msg=0))
    self.assertEqual(events[-1]["phase"], "departed")
    self.assertEqual(_step(self.resume, 1, lead_drel=far), RES_BUTTON)
    self.assertEqual(events[-1]["phase"], "tap")
    self.assertEqual(events[-1]["tapIndex"], 1)

  def test_success_on_stable_hud3(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm()
    self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
    _step(self.resume, TAP_GAP_FRAMES, bottom_msg=PRESS_RES_SET_TO_CHANGE_SPEED)
    _step(self.resume, HUD3_SUCCESS_FRAMES, bottom_msg=PRESS_RES_SET_TO_CHANGE_SPEED)
    self.assertTrue(self.resume._session_done)
    success = [e for e in events if e["phase"] == "tap_success"]
    self.assertEqual(len(success), 1)
    self.assertEqual(success[0]["reason"], "hud3_stable")

  def test_retry_after_hud3_falls_to_zero(self):
    self._arm()
    self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
    _step(self.resume, RES_HOLD_FRAMES + TAP_GAP_FRAMES)
    _step(self.resume, 5, bottom_msg=PRESS_RES_SET_TO_CHANGE_SPEED)
    _step(self.resume, 1, bottom_msg=0)
    _step(self.resume, RETRY_WAIT_FRAMES, bottom_msg=0)
    self.assertEqual(_step(self.resume, 1, bottom_msg=0), RES_BUTTON)
    self.assertEqual(self.resume._tap_count, 2)

  def test_retry_when_display_shows_zero(self):
    self._arm()
    self.assertEqual(_step(self.resume, 1, lead_drel=7.5), RES_BUTTON)
    _step(self.resume, RES_HOLD_FRAMES + TAP_GAP_FRAMES)
    _step(self.resume, OUTCOME_WINDOW_FRAMES + 1)
    _step(self.resume, RETRY_WAIT_FRAMES, bottom_msg=0)
    self.assertEqual(_step(self.resume, 1, bottom_msg=0), RES_BUTTON)
    self.assertEqual(self.resume._tap_count, 2)

  def test_caps_at_five_taps(self):
    self._arm()
    _step(self.resume, 1, lead_drel=7.5)
    for _ in range(MAX_TAPS_PER_STOP - 1):
      _run_one_tap_cycle(self.resume)
      _step(self.resume, 1, lead_drel=7.5, bottom_msg=PRESS_RES_TO_RESTART)
    _run_one_tap_cycle(self.resume)
    self.assertEqual(self.resume._tap_count, MAX_TAPS_PER_STOP)
    self.assertIsNone(_step(self.resume, RETRY_WAIT_FRAMES + 50, lead_drel=7.5))
    self.assertTrue(self.resume._session_done)

  def test_give_up_on_icc_off(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm()
    _step(self.resume, 1, lead_drel=7.5)
    _step(self.resume, 1, cruise_enabled=False)
    self.assertTrue(self.resume._session_done)
    self.assertEqual(events[-1]["phase"], "tap_giveup")
    self.assertEqual(events[-1]["reason"], "icc_off")

  def test_give_up_on_driver_input_during_session(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm()
    _step(self.resume, 1, lead_drel=7.5)
    _step(self.resume, 3, brake_pressed=True)
    self.assertEqual(events[-1]["reason"], "driver")

  def test_success_by_vehicle_motion(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm()
    _step(self.resume, 1, lead_drel=7.5)
    _step(self.resume, 1, v_ego=V_EGO_SUCCESS + 0.1)
    self.assertTrue(self.resume._session_done)
    self.assertEqual(events[-1]["phase"], "tap_success")
    self.assertEqual(events[-1]["reason"], "moving")


class TestAutoResumeParam(unittest.TestCase):
  def _apply(self, car, value):
    cp = CarInterface.get_non_essential_params(car)
    sp = CarInterface.get_non_essential_params_sp(cp, car)
    setup_interfaces(CarInterface, cp, sp, [{"NissanAutoResume": value}])
    return sp

  def test_default_off_and_leaf_only(self):
    for car in (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC):
      sp = self._apply(car, False)
      self.assertFalse(sp.flags & NissanFlagsSP.AUTO_RESUME)
      sp = self._apply(car, True)
      self.assertTrue(sp.flags & NissanFlagsSP.AUTO_RESUME)

    for car in (CAR.NISSAN_XTRAIL, CAR.NISSAN_ALTIMA):
      sp = self._apply(car, True)
      self.assertFalse(sp.flags & NissanFlagsSP.AUTO_RESUME)


class TestAutoResumeActuation(unittest.TestCase):
  def setUp(self):
    self.packer = CANPacker("nissan_leaf_2018_generated")

  def _bit(self, dat, name):
    sig = self.packer.dbc.name_to_msg["CRUISE_THROTTLE"].sigs[name]
    return bool(dat[sig.lsb // 8] & (1 << (sig.lsb % 8)))

  def _car(self, fingerprint, enabled):
    cp = CarInterface.get_non_essential_params(fingerprint)
    sp = CarInterface.get_non_essential_params_sp(cp, fingerprint)
    if enabled:
      sp.flags |= NissanFlagsSP.AUTO_RESUME
    ci = CarInterface(cp, sp)
    ci.CS.lkas_hud_msg = defaultdict(int)
    ci.CS.lkas_hud_info_msg = defaultdict(int, {"BOTTOM_MSG": PRESS_RES_TO_RESTART})
    ci.CS.cruise_throttle_msg = defaultdict(int)
    ci.CS.out.standstill = True
    ci.CS.out.cruiseState.enabled = True
    ci.CS.out.vEgo = 0.0
    cc = structs.CarControl(latActive=False)
    cc_sp = structs.CarControlSP()
    cc_sp.leadOne.status = True
    cc_sp.leadOne.dRel = 4.0
    cc_sp.leadOne.vRel = 1.0
    return ci, cc, cc_sp

  def _buttons(self, ci, cc, cc_sp, n, drel):
    cc_sp.leadOne.dRel = drel
    control = cc.as_reader()
    res, cancel = [], []
    for _ in range(n):
      _, sends = ci.apply(control, cc_sp, 0)
      for addr, dat, _ in sends:
        if addr == 0x239:
          res.append(self._bit(dat, "RES_BUTTON"))
          cancel.append(self._bit(dat, "CANCEL_BUTTON"))
    return res, cancel

  def _run_departure(self, ci, cc, cc_sp):
    self._buttons(ci, cc, cc_sp, ARM_FRAMES, 4.0)
    return self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES + RES_HOLD_FRAMES + 4, 7.5)

  def test_enabled_leaf_sends_res_tap_burst(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    during_arm, _ = self._buttons(ci, cc, cc_sp, ARM_FRAMES, 4.0)
    self.assertNotIn(True, during_arm)
    res, cancel = self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES + RES_HOLD_FRAMES + 4, 7.5)
    self.assertTrue(any(res))
    self.assertNotIn(True, cancel)
    self.assertGreaterEqual(sum(res), RES_HOLD_FRAMES // 2)
    self.assertLessEqual(sum(res), MAX_TAPS_PER_STOP * (RES_HOLD_FRAMES // 2 + 2))

  def test_toggle_off_does_not_send_res(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, False)
    res, _ = self._run_departure(ci, cc, cc_sp)
    self.assertNotIn(True, res)

  def test_cancel_takes_priority(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    cc.cruiseControl.cancel = True
    res, cancel = self._run_departure(ci, cc, cc_sp)
    self.assertNotIn(True, res)
    self.assertIn(True, cancel)

  def test_icbm_cannot_resume_early_at_standstill(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    cc_sp.intelligentCruiseButtonManagement.sendButton = structs.IntelligentCruiseButtonManagement.SendButtonState.increase
    res, _ = self._buttons(ci, cc, cc_sp, ARM_FRAMES, 4.0)
    self.assertNotIn(True, res)
    res, _ = self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES + RES_HOLD_FRAMES + 4, 7.5)
    self.assertGreater(sum(res), 0)

  def test_driver_set_is_preserved_over_auto_resume(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    ci.CS.cruise_throttle_msg["SET_BUTTON"] = 1
    res, _ = self._run_departure(ci, cc, cc_sp)
    self.assertNotIn(True, res)

  def test_driver_button_interrupts_tap_session(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    self._buttons(ci, cc, cc_sp, ARM_FRAMES, 4.0)
    res, _ = self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES + 1, 7.5)
    self.assertIn(True, res)
    ci.CS.cruise_throttle_msg["SET_BUTTON"] = 1
    res, _ = self._buttons(ci, cc, cc_sp, 2, 7.5)
    self.assertNotIn(True, res)

  def test_icbm_still_operates_while_moving_and_with_toggle_off(self):
    for enabled, standstill in ((True, False), (False, True)):
      with self.subTest(enabled=enabled, standstill=standstill):
        ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, enabled)
        ci.CS.out.standstill = standstill
        cc_sp.intelligentCruiseButtonManagement.sendButton = structs.IntelligentCruiseButtonManagement.SendButtonState.increase
        res, _ = self._buttons(ci, cc, cc_sp, 40, 4.0)
        self.assertIn(True, res)

  def test_ppp_sends_nothing(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF_IC, True)
    self.assertTrue(ci.CS.parking.blocked)
    res, _ = self._run_departure(ci, cc, cc_sp)
    self.assertEqual(res, [])


if __name__ == "__main__":
  unittest.main()