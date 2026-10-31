import unittest
from collections import defaultdict

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.nissan.interface import CarInterface
from opendbc.car.nissan.values import CAR
from opendbc.sunnypilot.car.interfaces import setup_interfaces
from opendbc.sunnypilot.car.nissan.auto_resume import (
  ARM_FRAMES,
  CLOSE_LEAD_M,
  COOLDOWN_FRAMES,
  DEPART_M,
  DEPART_VREL_MIN,
  PRESS_RES_TO_RESTART,
  RES_HOLD_FRAMES,
  TRIGGER_FRAMES,
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
  )
  values.update(overrides)
  pressed = False
  for _ in range(n):
    pressed = resume.update(**values)
  return pressed


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
    # 1.0 m above the minimum is not enough; the threshold is strict.
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 2, lead_drel=4.2 + DEPART_M))
    self.assertFalse(self.resume._departed)

  def test_triggers_after_sustained_departure_and_holds_res(self):
    self._arm(4.0)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES - 1, lead_drel=7.5))
    presses = [_step(self.resume, 1, lead_drel=7.5) for _ in range(RES_HOLD_FRAMES)]
    self.assertEqual(presses, [True] * RES_HOLD_FRAMES)
    self.assertFalse(_step(self.resume, 5, lead_drel=7.5))

  def test_short_departure_does_not_trigger(self):
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES - 1, lead_drel=7.5))
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=4.0))
    self.assertFalse(self.resume._departed)

  def test_once_per_standstill(self):
    self._arm()
    self.assertTrue(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5))
    _step(self.resume, RES_HOLD_FRAMES)
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 5, lead_drel=7.0))

  def test_new_standstill_after_cooldown_can_resume_again(self):
    self._arm()
    self.assertTrue(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5))
    _step(self.resume, 1, standstill=False)
    self.assertFalse(self.resume._sent)
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5))
    self.assertTrue(self.resume._departed)
    pressed = False
    for _ in range(COOLDOWN_FRAMES + 2):
      pressed = _step(self.resume, 1, lead_drel=7.5)
      if pressed:
        break
    self.assertTrue(pressed)

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
        self.assertNotIn(True, pressed)

  def test_wrong_bottom_msg_waits_until_press_res(self):
    # The lead is still beyond the departure threshold when the cluster catches up.
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, bottom_msg=0))
    self.assertTrue(self.resume._departed)
    self.assertTrue(_step(self.resume, 1, bottom_msg=PRESS_RES_TO_RESTART, lead_drel=7.5))

  def test_returning_lead_invalidates_pending_departure(self):
    # Departure while a gate is closed, then the lead comes back and stays stopped.
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
        self.assertFalse(_step(self.resume, 500, lead_drel=4.0, **gate))
        # Opening the gate with the lead still stopped must not press RES.
        self.assertFalse(_step(self.resume, 5, lead_drel=4.0))
        self.assertFalse(self.resume._departed)
        # A new pull-away, still in this standstill, is a current event.
        self.assertTrue(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5))

  def test_missing_lead_invalidates_pending_departure(self):
    self._arm(4.0)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, bottom_msg=0))
    self.assertTrue(self.resume._departed)
    self.assertFalse(_step(self.resume, 1, lead_present=False, lead_drel=7.5, bottom_msg=0))
    self.assertFalse(self.resume._departed)
    self.assertFalse(self.resume._armed)
    self.assertFalse(_step(self.resume, 10, lead_present=False, lead_drel=7.5))
    self.assertFalse(_step(self.resume, ARM_FRAMES - 1, lead_drel=4.0))
    self.assertFalse(self.resume._departed)

  def test_armed_near_8m_can_depart_past_the_window(self):
    self._arm(7.2)
    steps = 20
    for i in range(1, steps + 1):
      drel = 7.2 + (10.7 - 7.2) * i / steps
      self.assertFalse(_step(self.resume, 1, lead_drel=drel))
      self.assertTrue(self.resume._armed)
      self.assertFalse(self.resume._departed)
    self.assertGreater(10.7, CLOSE_LEAD_M)
    presses = [_step(self.resume, 1, lead_drel=10.7) for _ in range(TRIGGER_FRAMES)]
    self.assertIn(True, presses)

  def test_confirmation_continues_when_closer_lead_crosses_8m(self):
    self._arm(4.0)
    for drel in (6.5, 7.6, 8.0, 8.6, 9.4):
      self.assertFalse(_step(self.resume, 1, lead_drel=drel))
      self.assertTrue(self.resume._armed)
    self.assertTrue(_step(self.resume, TRIGGER_FRAMES, lead_drel=9.4))

  def test_cancel_aborts_an_in_progress_press(self):
    self._arm()
    self.assertTrue(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5))
    self.assertFalse(_step(self.resume, 1, cancel=True, lead_drel=7.5))
    self.assertFalse(_step(self.resume, RES_HOLD_FRAMES, lead_drel=7.5))

  def test_ppp_clears_a_latched_departure(self):
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, bottom_msg=0))
    self.assertTrue(self.resume._departed)
    _step(self.resume, 1, parking_blocked=True, bottom_msg=PRESS_RES_TO_RESTART)
    self.assertFalse(self.resume._departed)
    self.assertFalse(_step(self.resume, 1))

  def test_brake_holds_off_the_press_until_released(self):
    self._arm()
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=7.5, brake_pressed=True))
    self.assertTrue(self.resume._departed)
    self.assertFalse(_step(self.resume, 3, brake_pressed=True, lead_drel=7.5))
    self.assertTrue(_step(self.resume, 1, lead_drel=7.5))

  def test_distance_without_closing_speed_does_not_trigger(self):
    self._arm(3.5)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 5, lead_drel=3.5 + DEPART_M + 1.0, lead_vrel=0.0))
    self.assertFalse(self.resume._departed)
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES + 2, lead_drel=3.5 + DEPART_M + 1.0, lead_vrel=DEPART_VREL_MIN))
    self.assertFalse(self.resume._departed)

  def test_vrel_drop_clears_a_pending_departure(self):
    self._arm(3.5)
    far = 3.5 + DEPART_M + 1.0
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=far, lead_vrel=1.0, bottom_msg=0))
    self.assertTrue(self.resume._departed)
    self.assertFalse(_step(self.resume, 1, lead_drel=far, lead_vrel=0.0, bottom_msg=0))
    self.assertFalse(self.resume._departed)
    self.assertFalse(_step(self.resume, 3, lead_drel=far, lead_vrel=0.0))

  def test_transition_log_marks_arm_depart_and_res(self):
    events = []
    self.resume = NissanAutoResume(on_transition=events.append)
    self._arm(3.5)
    self.assertEqual(events[-1]["phase"], "armed")
    self.assertEqual(events[-1]["minDRel"], 3.5)
    far = 3.5 + DEPART_M + 1.0
    self.assertFalse(_step(self.resume, TRIGGER_FRAMES, lead_drel=far, bottom_msg=0))
    self.assertEqual(events[-1]["phase"], "departed")
    self.assertTrue(_step(self.resume, 1, lead_drel=far, icc_brake_pt=3, icc_brake_adas=0))
    self.assertEqual(events[-1]["phase"], "res")
    self.assertEqual(events[-1]["iccBrakePt"], 3)
    self.assertTrue(events[-1]["sent"])


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
      self.assertFalse(sp.safetyParam & NissanSafetyFlagsSP.AUTO_RESUME)
      sp = self._apply(car, True)
      self.assertTrue(sp.flags & NissanFlagsSP.AUTO_RESUME)
      self.assertTrue(sp.safetyParam & NissanSafetyFlagsSP.AUTO_RESUME)

    for car in (CAR.NISSAN_XTRAIL, CAR.NISSAN_ALTIMA):
      sp = self._apply(car, True)
      self.assertFalse(sp.flags & NissanFlagsSP.AUTO_RESUME)
      self.assertFalse(sp.safetyParam & NissanSafetyFlagsSP.AUTO_RESUME)


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
    cc = structs.CarControl(latActive=False)
    cc_sp = structs.CarControlSP()
    cc_sp.leadOne.status = True
    cc_sp.leadOne.dRel = 4.0
    cc_sp.leadOne.vRel = 1.0
    return ci, cc, cc_sp

  def _buttons(self, ci, cc, cc_sp, n, drel):
    cc_sp.leadOne.dRel = drel
    # CarController calls actuators.as_builder(), which exists on a reader.
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

  def test_enabled_leaf_sends_one_res_pulse(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    during_arm, _ = self._buttons(ci, cc, cc_sp, ARM_FRAMES, 4.0)
    self.assertNotIn(True, during_arm)
    res, cancel = self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES + RES_HOLD_FRAMES + 4, 7.5)
    self.assertTrue(any(res))
    self.assertNotIn(True, cancel)
    self.assertGreaterEqual(sum(res), 1)
    # 0.12 s at 100 Hz, transmitted every other frame.
    self.assertLessEqual(sum(res), RES_HOLD_FRAMES // 2 + 1)
    res_again, _ = self._buttons(ci, cc, cc_sp, ARM_FRAMES + TRIGGER_FRAMES, 3.0)
    res_again += self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES, 6.5)[0]
    self.assertNotIn(True, res_again)

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
    self.assertLessEqual(sum(res), RES_HOLD_FRAMES // 2 + 1)
    res, _ = self._buttons(ci, cc, cc_sp, 100, 7.5)
    self.assertNotIn(True, res)

  def test_driver_set_is_preserved_over_auto_resume(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    ci.CS.cruise_throttle_msg["SET_BUTTON"] = 1
    res, _ = self._run_departure(ci, cc, cc_sp)
    self.assertNotIn(True, res)
    _, sends = ci.apply(cc.as_reader(), cc_sp, 0)
    # Use a full transmission interval regardless of controller frame parity.
    _, sends2 = ci.apply(cc.as_reader(), cc_sp, 0)
    throttle = [dat for addr, dat, _ in sends + sends2 if addr == 0x239]
    self.assertEqual(len(throttle), 1)
    self.assertTrue(self._bit(throttle[0], "SET_BUTTON"))

  def test_driver_button_interrupts_res_without_restarting_pulse(self):
    ci, cc, cc_sp = self._car(CAR.NISSAN_LEAF, True)
    self._buttons(ci, cc, cc_sp, ARM_FRAMES, 4.0)
    res, _ = self._buttons(ci, cc, cc_sp, TRIGGER_FRAMES + 1, 7.5)
    self.assertIn(True, res)
    ci.CS.cruise_throttle_msg["SET_BUTTON"] = 1
    res, _ = self._buttons(ci, cc, cc_sp, 2, 7.5)
    self.assertNotIn(True, res)
    ci.CS.cruise_throttle_msg["SET_BUTTON"] = 0
    res, _ = self._buttons(ci, cc, cc_sp, RES_HOLD_FRAMES, 7.5)
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
    ci.CC.queued_buttons.append("RES_BUTTON")
    _, sends = ci.apply(cc, cc_sp, 0)
    self.assertEqual(sends, [])
    self.assertFalse(ci.CC.queued_buttons)
    res, _ = self._run_departure(ci, cc, cc_sp)
    self.assertEqual(res, [])


if __name__ == "__main__":
  unittest.main()
