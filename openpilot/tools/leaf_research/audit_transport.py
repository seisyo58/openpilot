"""Audit archived RX-only NPZ bus visibility; never connects to a vehicle.

Input: b{bus}_{address:03x} arrays of [event time, eight padded payload bytes],
plus scalar t0, as produced by extract_acc.py / extract_route5a.py / extract_route5c.py.
Event timestamps are batch timestamps, not per-frame hardware timestamps.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


ADDRESSES = ("1d6", "180", "1cb", "1c3", "2b0", "169", "30f")


def load_frames(archive, key):
  if key not in archive:
    return None
  frames = np.asarray(archive[key])
  if frames.ndim != 2 or frames.shape[1] != 9:
    raise ValueError(f"{key}: expected [time, eight padded bytes], got {frames.shape}")
  if not np.all(np.isfinite(frames)) or np.any(np.diff(frames[:, 0]) < 0):
    raise ValueError(f"{key}: nonfinite values or decreasing timestamps")
  payload = frames[:, 1:]
  if np.any((payload < 0) | (payload > 255) | (payload != np.floor(payload))):
    raise ValueError(f"{key}: invalid payload bytes")
  return frames


def describe(frames, t0):
  if frames is None:
    return {"status": "key_not_in_archive"}
  result = {"status": "present", "count": len(frames)}
  if len(frames):
    times = frames[:, 0] - t0
    result.update(first_s=float(times[0]), last_s=float(times[-1]),
                  gaps_over_250ms=int(np.sum(np.diff(times) > 0.25)))
    if len(times) > 1:
      result["median_event_interval_ms"] = float(np.median(np.diff(times)) * 1000)
  return result


def audit(path):
  with np.load(path, allow_pickle=False) as archive:
    t0_array = np.asarray(archive["t0"])
    if t0_array.shape != () or not np.isfinite(t0_array):
      raise ValueError("t0 must be a finite scalar")
    t0 = float(t0_array)
    frames = {f"b{bus}_{addr}": load_frames(archive, f"b{bus}_{addr}")
              for addr in ADDRESSES for bus in range(3)}
    result = {
      "input": path.name,
      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
      "visibility": {key: describe(value, t0) for key, value in frames.items()},
    }
    # An observed-data window, not an inferred relay transition or safety-mode change.
    b2 = frames["b2_1d6"]
    if b2 is not None and len(b2):
      cutoff = float(b2[-1, 0]) + 1.0
      result["after_last_bus2_1d6_plus_1s"] = {
        "start_s": cutoff - t0,
        "visibility": {key: describe(None if value is None else value[value[:, 0] >= cutoff], t0)
                       for key, value in frames.items()},
      }
      result["after_last_bus2_1d6_plus_1s"]["first_word_ranges_1d6"] = {}
      for bus in range(3):
        value = frames[f"b{bus}_1d6"]
        if value is not None:
          selected = value[value[:, 0] >= cutoff]
          if len(selected):
            word = selected[:, 1].astype(int) * 256 + selected[:, 2].astype(int)
            result["after_last_bus2_1d6_plus_1s"]["first_word_ranges_1d6"][f"b{bus}"] = {
              "min": int(word.min()), "max": int(word.max()),
            }
    return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("inputs", nargs="+", type=Path)
  parser.add_argument("--out", required=True, type=Path)
  args = parser.parse_args()
  result = {
    "schema": 1,
    "limitations": [
      "RX-only extracted archives; raw rlog and TX receipts were not re-audited.",
      "A missing key does not prove bus absence; extraction may select IDs.",
      "Event timestamps group CAN frames; do not infer sub-ms ordering or propagation delay.",
      "The tail window is defined by last observed bus2 1D6, not a measured relay change.",
      "Visibility does not identify a physical sender, receiver, or request acceptance.",
      "Gaps and missing segments are not interpolated.",
    ],
    "archives": [audit(path) for path in args.inputs],
  }
  # Avoid replacing an existing analysis accidentally.
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
