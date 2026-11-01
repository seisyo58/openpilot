import unittest
from unittest.mock import patch

from opendbc.car import structs
from opendbc.car.nissan.values import CAR
from openpilot.selfdrive.car.cruise import VCruiseHelper

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

  def step(self, helper, stopped, pressed=None, button=ButtonType.accelCruise, cruise_stopped=False):
    cs = structs.CarState(standstill=stopped, cruiseState={'available': True, 'enabled': True,
                                                         'standstill': cruise_stopped, 'speed': 60 / 3.6})
    if pressed is not None:
      cs.buttonEvents = [structs.CarState.ButtonEvent(type=button, pressed=pressed)]
    helper.update_v_cruise(cs, enabled=True, is_metric=True)

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
    self.assertEqual(helper.v_cruise_kph, 60)

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
