"""Evaluate the previously locked q->180 model on newly recovered route5a segments."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.classify_events import decode_2b0, previous


# Locked before the missing segments were downloaded; never refit here.
LAG = 0.16
SLOPE = -0.19905726326136464
INTERCEPT = 2067.124863628184
RECOVERED_SEGMENTS = (5, 7, 8, 11)


def exclude_windows(times, starts, ends):
  """Return points outside all closed intervals, without bridging missing data."""
  increments = np.zeros(len(times) + 1, dtype=int)
  np.add.at(increments, np.searchsorted(times, starts, side="left"), 1)
  np.add.at(increments, np.searchsorted(times, ends, side="right"), -1)
  return np.cumsum(increments)[:-1] == 0


def metrics(actual, predicted):
  if not len(actual):
    return {"samples": 0}
  error = predicted-actual
  return {"samples": len(actual), "rmse_raw": float(np.sqrt(np.mean(error**2))),
          "bias_raw": float(error.mean()), "absolute_error_p95_raw": float(np.quantile(abs(error), .95))}


def evaluate(path, metadata_path):
  meta = json.loads(metadata_path.read_text())
  if meta["route"] != "0000005a--34bc682d3e":
    raise ValueError("This holdout consists only of the four recovered route5a segments")
  with np.load(path, allow_pickle=False) as z:
    state = z["states"][::5]
    times = state[:, 0]
    r, p, d = z["b1_2b0"], z["b1_1c3"], z["b0_180"]
    active, special, q = decode_2b0(r[:, 1:])
    rq, rfresh = previous(r[:, 0], np.column_stack((active & ~special & (r[:, 4] == 0xac), q)), times, .03)
    pb = p[:, 1:].astype(int)
    pressure = ((pb[:, 0] & 63) << 4) | (pb[:, 1] >> 4)
    quiet, pfresh = previous(p[:, 0], (pressure == 0) & ((pb[:, 5] & 4) == 0), times, .03)
    base = ((state[:, 5] != 0) & (state[:, 3] == 0) & (state[:, 4] == 0) & (state[:, 1] > 3)
            & (rq[:, 0] != 0) & quiet & rfresh & pfresh)
    bad = times[~base]
    selected = base & exclude_windows(times, bad-1.2, bad+1.2)
    # Deliberately exclude all received/host-requested button bits, not just inferred host-only pulses.
    for key in ("b0_239", "tx_b2_239"):
      if key not in z:
        raise ValueError("Both received and requested button streams are required")
      frames = z[key]
      pressed = frames[(frames[:, 4].astype(int) & 31) != 0, 0]
      selected &= exclude_windows(times, pressed-1, pressed+5)
    db = d[:, 1:].astype(int)
    raw = ((db[:, 2] << 8) | db[:, 3]) >> 4
    raw = np.where(raw >= 2048, raw-4096, raw)
    future, fresh = previous(d[:, 0], raw, times+LAG, .03)
    current, current_fresh = previous(d[:, 0], raw, times, .03)
    selected &= fresh & current_fresh
    rows = []
    combined = np.zeros(len(times), dtype=bool)
    for segment in meta["segments"]:
      if segment["segment"] not in RECOVERED_SEGMENTS:
        continue
      # Keep both the input context and delayed output inside the previously unseen segment.
      mask = selected & (times >= segment["can_first_mono"]+1.2) & (times+LAG <= segment["can_last_mono"]-1.2)
      combined |= mask
      rows.append({"segment": segment["segment"],
                   "locked_model": metrics(future[mask], SLOPE*rq[mask, 1]+INTERCEPT),
                   "current_180_persistence": metrics(future[mask], current[mask])})
    if {row["segment"] for row in rows} != set(RECOVERED_SEGMENTS):
      raise ValueError("Missing recovered segment metadata")
    return {
      "schema": 1, "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
      "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
      "ev_candidate_rx_counts": {address: sum(n for key, n in meta["can_counts"].items()
                                              if key.startswith("can:") and key.endswith(":"+address))
                                 for address in ("1d4", "1d5", "1da", "1db", "1dc", "55b", "5bc", "5c0")},
      "model": {"lag_s": LAG, "slope": SLOPE, "intercept": INTERCEPT,
                "origin": "existing leaf_drive_mapping_holdout.json; fitted on first half of selected route5c only"},
      "recovered_segments": rows,
      "combined_locked_model": metrics(future[combined], SLOPE*rq[combined, 1]+INTERCEPT),
      "combined_current_180_persistence": metrics(future[combined], current[combined]),
      "q_range": [float(rq[combined, 1].min()), float(rq[combined, 1].max())] if combined.any() else None,
      "selection": ("Every fifth carState; speed>3m/s, ACC on, no pedal, normal active 2B0/0xAC, quiet 1C3; " +
                    "previous-only joins<=30ms; bad contexts +/-1.2s; all button presses -1/+5s; segment guards 1.2s"),
      "limitations": [
        "No coefficient, delay or threshold was refit on recovered samples; selection rules are retrospective.",
        "Adjacent samples are correlated and are not independent trials; report segments separately.",
        "180 is not an independently calibrated motor or energy sensor; a good fit can be a reflected request.",
        "This does not identify a causal command, physical units, stock takeover or ECU acceptance.",
      ],
    }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("input", type=Path)
  parser.add_argument("--metadata", required=True, type=Path)
  parser.add_argument("--out", required=True, type=Path)
  args = parser.parse_args()
  result = evaluate(args.input, args.metadata)
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
