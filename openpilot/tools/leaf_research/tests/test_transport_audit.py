import tempfile
import unittest
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.audit_transport import audit


class TestTransportAudit(unittest.TestCase):
  def run_audit(self, **arrays):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "frames.npz"
      np.savez(path, t0=np.array(100.0), **arrays)
      return audit(path)

  @staticmethod
  def frames(times):
    return np.array([[t, 0x2e, 0xe0, 0, 0, 0, 0, 0, 0] for t in times], dtype=float).reshape(-1, 9)

  def test_missing_key_is_not_zero_count(self):
    result = self.run_audit(b0_1d6=self.frames([100, 102]))
    self.assertEqual(result["visibility"]["b2_1d6"], {"status": "key_not_in_archive"})
    self.assertNotIn("after_last_bus2_1d6_plus_1s", result)

  def test_tail_and_empty_recorded_bus(self):
    result = self.run_audit(b0_1d6=self.frames([100, 102, 103]), b2_1d6=self.frames([100]),
                            b1_1d6=self.frames([]))
    tail = result["after_last_bus2_1d6_plus_1s"]
    self.assertEqual(tail["start_s"], 1)
    self.assertEqual(tail["visibility"]["b0_1d6"]["count"], 2)
    self.assertEqual(tail["visibility"]["b2_1d6"]["count"], 0)
    self.assertEqual(result["visibility"]["b1_1d6"]["count"], 0)
    self.assertEqual(tail["first_word_ranges_1d6"]["b0"], {"min": 12000, "max": 12000})

  def test_batched_timestamps_are_allowed_but_decreasing_times_rejected(self):
    self.run_audit(b0_1d6=self.frames([100, 100, 101]))
    with self.assertRaises(ValueError):
      self.run_audit(b0_1d6=self.frames([101, 100]))

  def test_dlc_column_format_is_rejected(self):
    with self.assertRaises(ValueError):
      self.run_audit(b0_1d6=np.zeros((2, 10)))


if __name__ == "__main__":
  unittest.main()
