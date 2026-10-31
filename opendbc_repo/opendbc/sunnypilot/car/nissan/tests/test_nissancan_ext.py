import unittest

from opendbc.can import CANPacker, CANParser
from opendbc.car.nissan.values import CAR
from opendbc.sunnypilot.car.nissan.nissancan_ext import create_cruise_throttle_msg


class TestLeafCruiseThrottlePreservation(unittest.TestCase):
  def test_recorded_frames_preserve_non_button_bits(self):
    # Recorded bus-0 frames, including unsure1=24/40 and UNKNOWN_38=0/1.
    frames = ("00ff632042003ff0", "00ffa32041003ff0", "00ff632002003ff0")
    for fingerprint in (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC):
      for raw_hex in frames:
        for button in (None, "RES_BUTTON", "SET_BUTTON", "CANCEL_BUTTON"):
          for frame in (0, 2, 4, 6, 8):
            with self.subTest(car=fingerprint, raw=raw_hex, button=button, frame=frame):
              raw = bytes.fromhex(raw_hex)
              parser = CANParser("nissan_leaf_2018_generated", [("CRUISE_THROTTLE", 0)], 0)
              parser.update([(1_000_000_000, [(0x239, raw, 0)])])
              addr, dat, bus = create_cruise_throttle_msg(
                CANPacker("nissan_leaf_2018_generated"), fingerprint,
                parser.vl["CRUISE_THROTTLE"], frame, button,
              )
              expected = bytearray(raw)
              expected[4] = (expected[4] & ~3) | ((frame // 2) % 4)
              if button is not None:
                expected[3] = (expected[3] & 0xC0) | {
                  "RES_BUTTON": 0x10, "SET_BUTTON": 0x08, "CANCEL_BUTTON": 0x02,
                }[button]
              self.assertEqual((addr, bus), (0x239, 2))
              self.assertEqual(dat, bytes(expected))

  def test_unknown_bit_is_independent_of_driver_brake_and_cancel(self):
    for unknown in (0, 1):
      for brake in (0, 1):
        with self.subTest(unknown=unknown, brake=brake):
          raw = bytearray.fromhex("00ff630002003ff0")
          raw[3] = 0x02  # Physical CANCEL must not be replaced with automatic RES.
          raw[4] = 2 | (unknown << 6) | (brake << 5)
          parser = CANParser("nissan_leaf_2018_generated", [("CRUISE_THROTTLE", 0)], 0)
          parser.update([(1_000_000_000, [(0x239, bytes(raw), 0)])])
          values = parser.vl["CRUISE_THROTTLE"]
          self.assertEqual(values["UNKNOWN_38"], unknown)
          self.assertEqual(values["USER_BRAKE_PRESSED"], brake)
          _, dat, _ = create_cruise_throttle_msg(
            CANPacker("nissan_leaf_2018_generated"), CAR.NISSAN_LEAF_IC, values, 4, "RES_BUTTON",
          )
          self.assertEqual(dat, bytes(raw))


if __name__ == "__main__":
  unittest.main()
