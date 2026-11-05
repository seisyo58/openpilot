import unittest

import numpy as np

from openpilot.tools.leaf_research.audit_raw_signals import counter_steps, integrity_1c3, integrity_2b0


class TestRawSignals(unittest.TestCase):
  def test_1c3_checksum_corruption_is_detected(self):
    b = np.array([list(bytes.fromhex('000000006404ff2b'))])
    self.assertEqual(integrity_1c3(b)['checksum_mismatches'], 0)
    b[0, 7] ^= 1
    self.assertEqual(integrity_1c3(b)['checksum_mismatches'], 1)

  def test_2b0_redundancy_corruption_is_detected(self):
    b = np.array([list(bytes.fromhex('a827d5ac5b00ee09'))])
    self.assertEqual(integrity_2b0(b), {'inverse_mismatches': 0, 'fraction_mismatches': 0})
    b[0, 0] ^= 1
    self.assertEqual(integrity_2b0(b)['inverse_mismatches'], 1)

  def test_counter_wrap_is_distinct_from_skipped_frames(self):
    result = counter_steps(np.array([0., .01, .02, 1.]), np.array([3, 0, 2, 0]), 4)
    self.assertEqual(result['delta_histogram'], {'1': 1, '2': 1})
    self.assertEqual(result['longer_gaps_excluded'], 1)


if __name__ == '__main__':
  unittest.main()
