import unittest

import numpy as np

from openpilot.tools.leaf_research.check_recovered_holdout import exclude_windows, metrics


class TestRecoveredHoldout(unittest.TestCase):
  def test_overlapping_exclusions_and_endpoints(self):
    times = np.array([0., 1., 2., 3., 4.])
    result = exclude_windows(times, np.array([1., 2.]), np.array([2., 3.]))
    self.assertEqual(result.tolist(), [True, False, False, False, True])

  def test_empty_exclusions_keep_points(self):
    self.assertTrue(exclude_windows(np.array([0., 1.]), np.array([]), np.array([])).all())

  def test_empty_holdout_does_not_invent_score(self):
    self.assertEqual(metrics(np.array([]), np.array([])), {'samples': 0})


if __name__ == '__main__':
  unittest.main()
