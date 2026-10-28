import unittest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.nissan.interface import CarInterface
from opendbc.car.nissan.values import CAR
from opendbc.car.nissan.nissancan import nissan_checksum
from opendbc.sunnypilot.car.nissan.parking import NissanParkingInterlock
from opendbc.sunnypilot.car.nissan.values import NissanSafetyFlagsSP


class TestParking(unittest.TestCase):
  def setUp(self):
    self.monitor = NissanParkingInterlock()
    self.packer = CANPacker("nissan_leaf_2018_generated")
    self.t = 1_000_000_000

  def frame(self, active, bus=2):
    addr, dat, _ = self.packer.make_can_msg("LKAS", bus, {"PPP_ACTIVE_CANDIDATE": active})
    return addr, dat[:7] + bytes([nissan_checksum(dat[:7])]), bus

  def send(self, *frames):
    self.t += 10_000_000
    self.monitor.update([(self.t, list(frames))])

  def main(self, on):
    return self.packer.make_can_msg("CRUISE_THROTTLE", 0, {"CRUISE_AVAILABLE": on})

  def test_active_exit_and_explicit_rearm(self):
    self.assertTrue(self.monitor.blocked)
    self.send(self.frame(False), self.main(True))
    self.assertFalse(self.monitor.blocked)
    self.send(self.frame(True))
    self.assertTrue(self.monitor.blocked)
    self.send(self.frame(False))
    self.assertTrue(self.monitor.blocked)
    self.send(self.frame(False), self.main(True))
    self.assertTrue(self.monitor.blocked)
    self.send(self.frame(False), self.main(False))
    self.assertTrue(self.monitor.blocked)
    self.send(self.frame(False), self.main(True))
    self.assertFalse(self.monitor.blocked)

  def test_main_cycle_during_parking_does_not_rearm(self):
    for main in [True, False, True]:
      self.send(self.frame(True), self.main(main))
      self.assertTrue(self.monitor.blocked)
    self.send(self.frame(False), self.main(True))
    self.assertTrue(self.monitor.blocked)

  def test_cruise_must_be_off_to_rearm(self):
    self.send(self.frame(True))
    cruise = self.packer.make_can_msg("CRUISE_STATE", 2, {"CRUISE_ENABLED": True})
    self.send(self.frame(False), cruise, self.main(False))
    self.send(self.frame(False), self.main(True))
    self.assertTrue(self.monitor.blocked)

  def test_missing_stale_corrupt_and_duplicate(self):
    for failure in ['missing', 'corrupt', 'duplicate', 'short']:
      with self.subTest(failure=failure):
        self.setUp()
        frame = self.frame(False)
        self.send(frame)
        self.assertFalse(self.monitor.blocked)
        if failure == 'missing':
          for _ in range(11):
            self.monitor.update([])
          self.t = self.monitor.now
        elif failure == 'corrupt':
          addr, dat, bus = self.frame(False)
          self.send((addr, dat[:7] + bytes([dat[7] ^ 1]), bus))
        elif failure == 'short':
          self.send((0x169, bytes(7), 2))
        else:
          self.send(frame)
        self.assertTrue(self.monitor.blocked)
        self.send(self.frame(False))
        self.assertTrue(self.monitor.blocked)

  def test_small_counter_gaps_are_tolerated(self):
    self.send(self.frame(False))
    for skipped in [1, 2]:
      for _ in range(skipped):
        self.frame(False)
      self.send(self.frame(False))
      self.assertFalse(self.monitor.blocked)

  def test_wrong_bus_cannot_unlock(self):
    self.send(self.frame(False, bus=0))
    self.assertTrue(self.monitor.blocked)

  def test_scope_and_controller_suppression(self):
    for car in (CAR.NISSAN_LEAF_IC, CAR.NISSAN_LEAF, CAR.NISSAN_XTRAIL, CAR.NISSAN_ALTIMA):
      cp = CarInterface.get_non_essential_params(car)
      sp = CarInterface.get_non_essential_params_sp(cp, car)
      self.assertEqual(bool(sp.safetyParam & NissanSafetyFlagsSP.PPP_INTERLOCK), car == CAR.NISSAN_LEAF_IC)
    cp = CarInterface.get_non_essential_params(CAR.NISSAN_LEAF_IC)
    sp = CarInterface.get_non_essential_params_sp(cp, CAR.NISSAN_LEAF_IC)
    ci = CarInterface(cp, sp)
    cc = structs.CarControl(enabled=True, latActive=True, longActive=True)
    cc.cruiseControl.cancel = True
    cc.actuators.steeringAngleDeg = 100
    cc_sp = structs.CarControlSP()
    cc_sp.mads.enabled = cc_sp.mads.active = True
    for active in [True, False]:
      self.t += 10_000_000
      _, state_sp = ci.update([(self.t, [self.frame(active)])])
      self.assertTrue(state_sp.nissanParkingBlocked)
      actuators, sends = ci.apply(cc, cc_sp, self.t)
      self.assertEqual(sends, [])
      self.assertEqual(actuators.steeringAngleDeg, 0)


if __name__ == '__main__':
  unittest.main()
