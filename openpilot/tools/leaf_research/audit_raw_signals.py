"""Check candidate integrity and forwarding observations in raw-derived NPZ files."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.audit_transport import load_frames


def integrity_1c3(payload):
  b = payload.astype(int)
  raw10 = ((b[:, 0] & 63) << 4) | (b[:, 1] >> 4)
  return {"checksum_mismatches": int(np.sum(((1 + 0xc3 + b[:, :7].sum(axis=1)) & 255) != b[:, 7])),
          "bit7_positive_mismatches": int(np.sum(((b[:, 5] & 128) != 0) != (raw10 > 0))),
          "raw10_range": [int(raw10.min()), int(raw10.max())]}


def integrity_2b0(payload):
  b = payload.astype(int)
  raw12 = (b[:, 1] << 4) | (b[:, 2] & 15)
  inverse = ((b[:, 2] >> 4) << 8) | ((b[:, 0] & 15) << 4) | (b[:, 0] >> 4)
  fraction = (b[:, 7] >> 2) & 3
  return {"inverse_mismatches": int(np.sum(inverse != ((~raw12) & 4095))),
          "fraction_mismatches": int(np.sum(b[:, 7] != ((fraction << 2) | ((~fraction) & 3))))}


def counter_steps(times, counter, modulus):
  gaps = np.diff(times)
  selected = gaps <= 0.03
  delta = (np.diff(counter) % modulus)[selected]
  values, counts = np.unique(delta, return_counts=True)
  return {"adjacent_steps_with_gap_at_most_30ms": int(selected.sum()),
          "longer_gaps_excluded": int((~selected).sum()),
          "delta_histogram": {str(v): int(n) for v, n in zip(values, counts, strict=True)},
          "nonunit_step_examples": [{"time_s": float(times[k+1]), "interval_ms": float(gaps[k]*1000),
                                     "delta": int((counter[k+1]-counter[k]) % modulus)}
                                    for k in np.flatnonzero(selected & ((np.diff(counter) % modulus) != 1))[:20]]}


def audit(path):
  with np.load(path, allow_pickle=False) as z:
    t0 = float(z["t0"])
    p, r = load_frames(z, "b1_1c3"), load_frames(z, "b1_2b0")
    if p is None or r is None or not len(p) or not len(r):
      raise ValueError("1C3/2B0 RX streams required")
    result = {"input": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "1c3": integrity_1c3(p[:, 1:]), "2b0": integrity_2b0(r[:, 1:])}
    result["1c3"]["frames"] = len(p)
    result["2b0"]["frames"] = len(r)
    result["1c3"]["counter"] = counter_steps(p[:, 0]-t0, p[:, 6].astype(int) & 3, 4)
    result["2b0"]["counter"] = counter_steps(r[:, 0]-t0, r[:, 7].astype(int) >> 4, 16)
    visibility = {}
    for address in ("1d6", "180", "1cb", "1c3", "2b0"):
      channels = {}
      for bus in (0, 1, 2, 128, 129, 130, 192, 193, 194):
        key = f"b{bus}_{address}"
        if key in z:
          frames = load_frames(z, key)
          channels[key] = {"all_frames": len(frames), "after_30s": int(np.sum(frames[:, 0] - t0 >= 30))}
      channels["host_sendcan_frames"] = sum(len(z[k]) for k in z.files if k.startswith("tx_") and k.endswith(f"_{address}"))
      visibility[address] = channels
    result["visibility"] = visibility
    return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("inputs", nargs="+", type=Path)
  parser.add_argument("--out", required=True, type=Path)
  args = parser.parse_args()
  result = {"schema": 1, "archives": [audit(path) for path in args.inputs],
            "limitations": [
              "Candidate redundancy/checksum conformity is not a calibrated physical meaning or acceptance test.",
              "Counter deltas may reflect missing logs, reception loss or batching; do not equate them to ECU faults.",
              "src128/129/130 are returned records, not ordinary RX or receiver acceptance.",
              "The inspected fdcan driver emits returned when handing a TX frame to hardware, not confirmed application acceptance.",
              "Absent extracted keys are not synthesized as observed zero counts; selected-ID extraction must be checked.",
            ]}
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
