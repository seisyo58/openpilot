import unittest

import numpy as np

from openpilot.tools.leaf_research.audit_boundaries import bracket_order, candidates, edges


class TestBoundaryAudit(unittest.TestCase):
  def test_touching_brackets_do_not_prove_strict_order(self):
    self.assertEqual(bracket_order([0, .01], [.01, .02]), "overlap_or_touch")
    self.assertEqual(bracket_order([0, .009], [.01, .02]), "1c3_bracket_before_2b0")
    self.assertEqual(bracket_order([.03, .04], [.01, .02]), "2b0_bracket_before_1c3")
    self.assertEqual(bracket_order([0, 0], [.01, .02]), "same_event_batch_boundary")

  def test_gap_does_not_create_an_observed_edge(self):
    self.assertEqual(edges(np.array([0., 1.]), np.array([False, True]), True).tolist(), [])

  def test_multiple_candidates_are_preserved(self):
    t = np.array([.8, .9, 1., 1.1])
    result = candidates(t, np.array([1, 3]), 1., np.array([.99, 1.]))
    self.assertEqual(len(result), 2)
    self.assertAlmostEqual(result[0]["first_changed_offset_ms"], -100)
    self.assertAlmostEqual(result[1]["first_changed_offset_ms"], 100)


if __name__ == "__main__":
  unittest.main()
