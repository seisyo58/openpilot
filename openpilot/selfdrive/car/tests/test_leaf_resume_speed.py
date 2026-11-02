import unittest
from unittest.mock import patch

from opendbc.car import structs
from opendbc.car.nissan.values import CAR
from openpilot.selfdrive.car.cruise import LEAF_RESUME_ROLLBACK_FRAMES, VCruiseHelper

ButtonType = structs.CarState.ButtonEvent.Type


class TestLeafResumeSpeed(unittest.TestCase):
  def make_helper(self, fingerprint):
    with patch('openpilot.sunnypilot.selfdrive.car.cruise_ext.Params') as params:
      params.return_value.get_bool.return_value = False
      params.return_value.get.return_value = 1
      helper = VCruiseHelper(structs.CarParams(carFingerprint=fingerprint, pcmCruise=True),
                             structs.CarParamsSP(pcmCruiseSpeed=False))
    helper.v_cruise_kph = 60
    helper.enabled_prev = True
    return helper

  def step(self, helper, stopped, pressed=None, button=ButtonType.accelCruise, cruise_stopped=False, enabled=True):
    cs = structs.CarState(standstill=stopped, cruiseState={'available': True, 'enabled': enabled,
                                                         'standstill': cruise_stopped, 'speed': 60 / 3.6})
    if pressed is not None:
      cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=pressed)]
    helper.update_v_cruise(cs, enabled=enabled, is_metric=True)

  def test_resume_then_move_before_release(self):
    for fingerprint in (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC):
      with self.subTest(fingerprint=fingerprint):
        helper = self.make_helper(fingerprint)
        self.step(helper, True, True)
        self.step(helper, False, False)
        self.assertEqual(helper.v_cruise_kph, 60)
        # A subsequent press while moving must still increase the set speed.
        self.step(helper, False, True)
        self.step(helper, False, False)
        self.assertEqual(helper.v_cruise_kph, 61)

  def test_resume_held_through_departure(self):
    helper = self.make_helper(CAR.NISSAN_LEAF_IC)
    self.step(helper, True, True)
    for _ in range(110):
      self.step(helper, False)
    self.step(helper, False, False)
    self.assertEqual(helper.v_cruise_kph, 60)

  def test_resume_released_while_stopped(self):
    helper = self.make_helper(CAR.NISSAN_LEAF)
    self.step(helper, True, True)
    self.step(helper, True, False)
    self.assertEqual(helper.v_cruise_kph, 61)
    for _ in range(LEAF_RESUME_ROLLBACK_FRAMES + 1):
      self.step(helper, True)
    self.step(helper, False)
    self.assertEqual(helper.v_cruise_kph, 61)

  def test_departure_within_two_seconds_rolls_back(self):
    for delay in (1, LEAF_RESUME_ROLLBACK_FRAMES):
      with self.subTest(delay=delay):
        helper = self.make_helper(CAR.NISSAN_LEAF_IC)
        self.step(helper, True, True)
        self.step(helper, True, False)
        self.assertEqual(helper.v_cruise_kph, 61)
        for _ in range(delay - 1):
          self.step(helper, True)
        self.step(helper, False)
        self.assertEqual(helper.v_cruise_kph, 60)
        self.assertEqual(helper.v_cruise_cluster_kph, 60)

  def test_only_recent_increments_are_undone(self):
    helper = self.make_helper(CAR.NISSAN_LEAF_IC)
    self.step(helper, True, True)
    self.step(helper, True, False)
    for _ in range(LEAF_RESUME_ROLLBACK_FRAMES + 1):
      self.step(helper, True)
    for _ in range(2):
      self.step(helper, True, True)
      self.step(helper, True, False)
    self.assertEqual(helper.v_cruise_kph, 63)
    self.step(helper, False)
    self.assertEqual(helper.v_cruise_kph, 61)

  def test_custom_long_press_rounding_is_undone(self):
    helper = self.make_helper(CAR.NISSAN_LEAF_IC)
    helper.custom_acc_enabled = True
    helper.long_increment = 10
    helper.v_cruise_kph = 63
    self.step(helper, True, True)
    for _ in range(50):
      self.step(helper, True)
    self.assertEqual(helper.v_cruise_kph, 70)
    self.step(helper, False)
    self.assertEqual(helper.v_cruise_kph, 63)

  def test_other_target_change_is_not_overwritten(self):
    helper = self.make_helper(CAR.NISSAN_LEAF)
    self.step(helper, True, True)
    self.step(helper, True, False)
    helper.v_cruise_kph = 80
    self.step(helper, False)
    self.assertEqual(helper.v_cruise_kph, 80)

  def test_set_cancels_pending_rollback(self):
    helper = self.make_helper(CAR.NISSAN_LEAF)
    self.step(helper, True, True)
    self.step(helper, True, False)
    self.step(helper, True, True, button=ButtonType.decelCruise)
    self.step(helper, True, False, button=ButtonType.decelCruise)
    self.step(helper, False)
    self.assertEqual(helper.v_cruise_kph, 60)

  def test_disengage_clears_pending_rollback(self):
    helper = self.make_helper(CAR.NISSAN_LEAF)
    self.step(helper, True, True)
    self.step(helper, True, False)
    self.step(helper, True, enabled=False)
    self.assertFalse(helper.resume_increments)

  def test_set_still_decreases_while_stopped(self):
    helper = self.make_helper(CAR.NISSAN_LEAF_IC)
    self.step(helper, True, True, button=ButtonType.decelCruise)
    self.step(helper, True, False, button=ButtonType.decelCruise)
    self.assertEqual(helper.v_cruise_kph, 59)

  def test_other_nissan_keeps_existing_behavior(self):
    helper = self.make_helper(CAR.NISSAN_XTRAIL)
    self.step(helper, True, True)
    self.step(helper, True, False)
    self.assertEqual(helper.v_cruise_kph, 61)

  def test_existing_cruise_standstill_guard(self):
    helper = self.make_helper(CAR.NISSAN_XTRAIL)
    self.step(helper, False, True, cruise_stopped=True)
    self.step(helper, False, False)
    self.assertEqual(helper.v_cruise_kph, 60)


if __name__ == '__main__':
  unittest.main()
