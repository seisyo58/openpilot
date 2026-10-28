import unittest

from opendbc.can import CANPacker
from opendbc.car.structs import CarParams
from opendbc.car.nissan.nissancan import nissan_checksum
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.common import make_msg
from opendbc.sunnypilot.car.nissan.values import NissanSafetyFlagsSP


class TestNissanParkingSafety(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.safety.set_current_safety_param_sp(NissanSafetyFlagsSP.LEAF | NissanSafetyFlagsSP.PPP_INTERLOCK)
    self.safety.set_safety_hooks(CarParams.SafetyModel.nissan, 0)
    self.safety.init_tests()
    self.safety.set_mads_params(True, False, False)
    self.packer = CANPacker("nissan_leaf_2018_generated")
    self.t = 0

  def msg(self, name, bus, values):
    addr, dat, _ = self.packer.make_can_msg(name, bus, values)
    if addr == 0x169:
      dat = dat[:7] + bytes([nissan_checksum(dat[:7])])
    return libsafety_py.make_CANPacket(addr, bus, dat)

  def parking(self, active, bus=2):
    return self.msg('LKAS', bus, {'PPP_ACTIVE_CANDIDATE': active})

  def rx(self, msg):
    self.t += 10000
    self.safety.set_timer(self.t)
    return self.safety.safety_rx_hook(msg)

  def main(self, on):
    self.rx(self.msg('CRUISE_THROTTLE', 0, {'CRUISE_AVAILABLE': on}))

  def assert_blocked(self):
    self.assertFalse(self.safety.get_controls_allowed())
    self.assertFalse(self.safety.get_controls_allowed_lateral())
    for addr, bus in [(0x169, 0), (0x2b1, 0), (0x4cc, 0), (0x239, 2)]:
      self.assertFalse(self.safety.safety_tx_hook(make_msg(bus, addr)))

  def test_startup_and_rearm(self):
    self.main(True)
    self.assert_blocked()
    self.rx(self.parking(False))
    self.safety.set_controls_allowed(True)
    self.rx(self.parking(True))
    self.assert_blocked()
    self.rx(self.parking(False))
    self.main(True)
    self.assert_blocked()
    self.main(False)
    self.assert_blocked()
    self.main(True)
    # MADS may now enable on this physical main rising edge; stock ACC is still off.
    self.assertTrue(self.safety.get_controls_allowed_lateral())
    self.assertTrue(self.safety.safety_tx_hook(make_msg(2, 0x239)))

  def test_cruise_and_main_cannot_enable_during_parking(self):
    self.rx(self.parking(True))
    for _ in range(3):
      self.rx(self.msg('CRUISE_STATE', 2, {'CRUISE_ENABLED': False}))
      self.rx(self.msg('CRUISE_STATE', 2, {'CRUISE_ENABLED': True}))
      self.main(False)
      self.main(True)
      self.assert_blocked()
      self.rx(self.parking(True))

  def test_forwarding_order_and_falling_edge(self):
    # Production FDCAN forwards BEFORE safety_rx_hook; exercise that exact order.
    first = self.parking(True)
    self.assertEqual(self.safety.safety_fwd_hook_packet(first), -1)
    self.rx(first)
    next_frame = self.parking(True)
    self.assertEqual(self.safety.safety_fwd_hook_packet(next_frame), 0)
    self.rx(next_frame)
    self.assert_blocked()
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x239), 2)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x2b1), 0)
    inactive = self.parking(False)
    self.assertEqual(self.safety.safety_fwd_hook_packet(inactive), -1)
    self.rx(inactive)
    self.assertEqual(self.safety.safety_fwd_hook(2, 0x169), -1)
    self.assert_blocked()

  def test_invalid_steering_never_forwarded(self):
    self.rx(self.parking(True))
    for kind in ['checksum', 'length', 'counter']:
      with self.subTest(kind=kind):
        msg = self.parking(True)
        if kind == 'checksum':
          msg[0].data[7] ^= 1
        elif kind == 'length':
          msg[0].data_len_code = 7
        else:
          # Advance beyond the two-missed-frame tolerance.
          for _ in range(3):
            msg = self.parking(True)
        self.assertEqual(self.safety.safety_fwd_hook_packet(msg), -1)

  def test_timeout_and_bad_checksum_require_rearm(self):
    for timeout in [True, False]:
      with self.subTest(timeout=timeout):
        self.setUp()
        self.rx(self.parking(False))
        if timeout:
          self.t += 100001
          self.safety.set_timer(self.t)
          self.assertFalse(self.safety.safety_tx_hook(make_msg(2, 0x239)))
        else:
          msg = self.parking(False)
          msg[0].data[7] ^= 1
          self.rx(msg)
        self.rx(self.parking(False))
        self.assert_blocked()

  def test_small_counter_gaps_and_timeout_with_timer_wrap(self):
    self.rx(self.parking(True))
    for missed in [1, 2]:
      for _ in range(missed):
        self.parking(True)
      msg = self.parking(True)
      self.assertEqual(self.safety.safety_fwd_hook_packet(msg), 0)
      self.rx(msg)
    self.safety.set_timer(0xfffffff0)
    self.safety.safety_rx_hook(self.parking(True))
    self.safety.set_timer(100000)
    self.assertEqual(self.safety.safety_fwd_hook_packet(self.parking(True)), -1)
    self.assert_blocked()

  def test_mads_options_and_duplicate_counter(self):
    for enabled, disengage, pause in [(False, False, False), (True, True, False), (True, False, True)]:
      with self.subTest(mads=(enabled, disengage, pause)):
        self.setUp()
        self.safety.set_mads_params(enabled, disengage, pause)
        self.safety.set_controls_allowed(True)
        self.safety.set_controls_allowed_lateral(True)
        msg = self.parking(True)
        self.rx(msg)
        self.assert_blocked()
        self.assertEqual(self.safety.safety_fwd_hook_packet(msg), -1)
        self.rx(msg)
        self.assertEqual(self.safety.safety_fwd_hook(2, 0x169), -1)
        self.rx(self.parking(False))
        self.assert_blocked()

  def test_wrong_bus_and_relay_fault(self):
    self.rx(self.parking(False, bus=0))
    self.main(True)
    self.assert_blocked()
    self.rx(self.parking(True))
    self.safety.set_relay_malfunction(True)
    self.assertEqual(self.safety.safety_fwd_hook_packet(self.parking(True)), -1)
    self.assertEqual(self.safety.safety_fwd_hook(0, 0x239), -1)


if __name__ == '__main__':
  unittest.main()
