import tempfile
import unittest
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.classify_events import button_context, classify, control_context, decode_2b0, labels_for, previous, true_runs


class TestEventClassifier(unittest.TestCase):
  def test_previous_never_uses_future_or_stale(self):
    values, valid = previous(np.array([1., 3.]), np.array([10, 30]), np.array([0., 1., 2., 3.]), 0.1)
    self.assertEqual(valid.tolist(), [False, True, False, True])
    self.assertEqual(values[valid].tolist(), [10, 30])

  def test_runs_split_at_missing_segment(self):
    runs = true_runs(np.array([True, True, True, False, True]), np.array([0., .01, 2., 2.01, 2.02]), .03)
    self.assertEqual(runs, [(0, 2), (2, 3), (4, 5)])

  def test_special_signature_ignores_only_counter_nibble(self):
    b = np.array([list(bytes.fromhex(h)) for h in ["a827d5ac5b000e09", "a827d5ac5b00fe09", "a827d5ac5b00ff09"]])
    active, special, _ = decode_2b0(b)
    self.assertTrue(active.all())
    self.assertEqual(special.tolist(), [True, True, False])

  def test_hold_does_not_require_numeric_or_bit_agreement_and_stale_wins(self):
    labels = labels_for(np.array([True, False, True, True]), np.array([True, True, False, False]),
                        np.array([True, True, False, False]), np.ones(4, dtype=bool),
                        np.zeros(4, dtype=bool), np.ones(4, dtype=bool),
                        np.array([False, False, True, False]), np.array([True, False, True, False]))
    self.assertEqual(labels.tolist(), ["stopped_hold_candidate", "unknown_stale",
                                       "coordinated_braking_candidate", "normal_request_candidate"])

  def test_cancel_bit_and_missing_tx_are_distinguished(self):
    frames = np.array([[10, 0, 0, 0, 2, 0, 0, 0, 0]], dtype=float)
    self.assertEqual(button_context(frames, 0, 10)["cancel"]["frames"], 1)
    self.assertEqual(button_context(None, 0, 10)["status"], "not_available")

  def test_extended_history_preserves_earlier_rx_res(self):
    frames = np.array([[9.12, 0, 0, 0, 16, 0, 0, 0, 0]], dtype=float)
    self.assertEqual(button_context(frames, 0, 10)["res"]["frames"], 0)
    self.assertEqual(button_context(frames, 0, 10, before=3.5)["res"]["frames"], 1)

  def test_car_control_cancel_is_distinct_from_resume(self):
    frames = np.array([[9.9, 1, 1, 0, 1, 0, 0, 0, 0], [10., 1, 1, 0, 0, 1, 0, 0, 0]])
    result = control_context(frames, 0, 10)
    self.assertEqual(result["cancel"]["frames"], 1)
    self.assertAlmostEqual(result["cancel"]["first_offset_ms"], -100)
    self.assertEqual(result["resume"]["frames"], 1)
    self.assertEqual(control_context(None, 0, 10), "not_in_archive")

  def test_censored_hold_does_not_create_a_release(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "example.npz"
      times = np.arange(0., 1., .01)
      empty = np.zeros((len(times), 8))
      frames = np.column_stack((times, empty))
      request = frames.copy()
      request[:, 5] = 64
      hold = frames.copy()
      hold[:, 1] = 16
      states = np.column_stack((times, empty))
      np.savez(path, t0=np.array(0.), b1_1c3=frames, b1_2b0=request, b0_1cb=frames, b2_30f=hold, states=states)
      result = classify(path)
      event = result["hold_flag_runs_at_least_500ms"][0]
      self.assertTrue(event["left_censored"])
      self.assertTrue(event["right_censored"])
      self.assertIsNone(event["release"])


if __name__ == "__main__":
  unittest.main()
