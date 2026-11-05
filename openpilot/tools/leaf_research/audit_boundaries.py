"""Audit recorded transition boundaries; never sends data or connects to a vehicle."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.audit_transport import load_frames
from openpilot.tools.leaf_research.classify_events import button_context, decode_2b0, previous, true_runs


def edges(times, flag, rising):
  return np.flatnonzero((flag[1:] != flag[:-1]) & (flag[1:] == rising) & (np.diff(times) <= 0.03)) + 1


def bracket_order(a, b):
  """Compare sampled transition brackets; a shared endpoint is not strict order."""
  if abs(a[1] - a[0]) <= 1e-9 or abs(b[1] - b[0]) <= 1e-9:
    return "same_event_batch_boundary"
  if a[1] < b[0] - 1e-9:
    return "1c3_bracket_before_2b0"
  if b[1] < a[0] - 1e-9:
    return "2b0_bracket_before_1c3"
  return "overlap_or_touch"


def candidates(times, indices, reference, bracket):
  selected = indices[np.abs(times[indices] - reference) <= 0.2]
  return [{"first_changed_offset_ms": float((times[k] - reference) * 1000),
           "bracket_offsets_ms": [float((times[k-1] - reference) * 1000), float((times[k] - reference) * 1000)],
           "order": bracket_order(times[k-1:k+1], bracket)} for k in selected]


def analyze(path, tx_path=None):
  with np.load(path, allow_pickle=False) as z:
    t0 = float(z["t0"])
    r, p = load_frames(z, "b1_2b0"), load_frames(z, "b1_1c3")
    if r is None or p is None or not len(r) or not len(p):
      raise ValueError("b1_2b0 and b1_1c3 are required")
    rt, pt = r[:, 0] - t0, p[:, 0] - t0
    active, special, q = decode_2b0(r[:, 1:])
    pb = p[:, 1:].astype(int)
    raw10 = ((pb[:, 0] & 63) << 4) | (pb[:, 1] >> 4)
    rise, fall = edges(pt, raw10 > 0, True), edges(pt, raw10 > 0, False)
    states = np.asarray(z["states"])
    if states.ndim != 2 or states.shape[1] != 9 or not np.all(np.isfinite(states)) or np.any(np.diff(states[:, 0]) < 0):
      raise ValueError("Expected finite time-ordered states with nine columns")
    rx = load_frames(z, "b0_239")
    tx = load_frames(z, "tx_b2_239")
    if tx_path is not None:
      with np.load(tx_path, allow_pickle=False) as tz:
        tx = load_frames(tz, "tx_b2_239")
    records = []
    for i, j in true_runs(special, rt, 0.03):
      if rt[j-1] - rt[i] < 0.1:
        continue
      for name, k, censored in (
        ("entry", i, i == 0 or rt[i] - rt[i-1] > 0.03),
        ("exit", j, j == len(rt) or rt[j] - rt[j-1] > 0.03),
      ):
        if censored:
          records.append({"kind": name, "special_start_s": float(rt[i]), "censored": True})
          continue
        reference = float(rt[k])
        state, fresh = previous(states[:, 0] - t0, states[:, 1:], np.array([reference]), 0.1)
        state = state[0]
        # Require continuously observed state context, including the previous value at window start.
        context_times = np.r_[reference - 0.2,
                              states[(states[:, 0] - t0 > reference - 0.2) & (states[:, 0] - t0 <= reference + 0.2), 0] - t0,
                              reference + 0.2]
        context_state, context_fresh = previous(states[:, 0] - t0, states[:, 1:], context_times, 0.1)
        uninterrupted = bool(context_fresh.all() and np.all(np.diff(context_times) <= 0.1))
        acc_no_pedal = bool(uninterrupted and np.all(context_state[:, 4] != 0)
                            and np.all(context_state[:, 2:4] == 0))
        record = {
          "kind": name, "special_start_s": float(rt[i]), "time_s": reference, "censored": False,
          "2b0_bracket_offsets_ms": [float((rt[k-1] - reference) * 1000), 0.0],
          "speed_mps": float(state[0]) if fresh[0] else None,
          "moving_acc_no_pedal_context": bool(fresh[0] and state[0] > 3/3.6 and acc_no_pedal),
          "ordinary_active_neighbor": bool(active[k-1] if name == "entry" else active[k]),
          "ordinary_q_neighbor": int(q[k-1] if name == "entry" else q[k]) if (active[k-1] if name == "entry" else active[k]) else None,
          "1c3_edge_candidates": candidates(pt, rise if name == "entry" else fall, reference, rt[k-1:k+1]),
          "rx_buttons": button_context(rx, t0, reference),
          "tx_requests": button_context(tx, t0, reference),
        }
        records.append(record)
    summaries = {}
    for kind in ("entry", "exit"):
      selected = [e for e in records if e["kind"] == kind and not e["censored"]
                  and e["moving_acc_no_pedal_context"] and e["ordinary_active_neighbor"]]
      unique = [e for e in selected if len(e["1c3_edge_candidates"]) == 1]
      offsets = [e["1c3_edge_candidates"][0]["first_changed_offset_ms"] for e in unique]
      summaries[kind] = {
        "moving_context_boundaries": len(selected), "unique_edge_matches": len(unique),
        "multiple_edge_matches": sum(len(e["1c3_edge_candidates"]) > 1 for e in selected),
        "no_edge_matches": sum(not e["1c3_edge_candidates"] for e in selected),
        "first_changed_offset_ms": {"min": min(offsets), "median": float(np.median(offsets)), "max": max(offsets)} if offsets else None,
        "bracket_orders_unique_matches": dict(Counter(e["1c3_edge_candidates"][0]["order"] for e in unique)),
        "ordinary_q_neighbor_range": [min(e["ordinary_q_neighbor"] for e in selected), max(e["ordinary_q_neighbor"] for e in selected)] if selected else None,
      }
      unmarked = [e for e in selected if all(e[source].get("status") == "available"
                  and not any(e[source][button]["frames"] for button in ("res", "set", "cancel"))
                  for source in ("rx_buttons", "tx_requests"))]
      summaries[kind]["no_nearby_rx_tx_button_markers"] = {
        "boundaries": len(unmarked),
        "ordinary_q_neighbor_range": [min(e["ordinary_q_neighbor"] for e in unmarked), max(e["ordinary_q_neighbor"] for e in unmarked)] if unmarked else None,
      }
    return {"input": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "tx_sha256": hashlib.sha256(tx_path.read_bytes()).hexdigest() if tx_path else None,
            "summary": summaries, "boundaries": records}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("inputs", nargs="+", type=Path)
  parser.add_argument("--tx-59", type=Path)
  parser.add_argument("--out", required=True, type=Path)
  args = parser.parse_args()
  result = {
    "schema": 1,
    "rules": {"special_run_min_span_s": 0.1, "edge_match_window_s": [-0.2, 0.2],
              "max_can_gap_s": 0.03, "max_state_age_s": 0.1,
              "moving_speed_min_mps": 3/3.6, "state_context_s": [-0.2, 0.2]},
    "limitations": [
      "Offline RX-NPZ observations; not hardware timestamps or ECU causal order.",
      "All 1C3 edge candidates within window retained; summaries use exactly one match only.",
      "State context is retrospective selection, not a predictive controller.",
      "Buttons annotated, not excluded automatically; sendcan is not proof of acceptance.",
      "Transition brackets use adjacent recorded samples; overlap/touch does not establish strict order.",
      "Censored or inactive-neighbor boundaries are not normal-to-special transitions.",
    ],
    "archives": [analyze(path, args.tx_59 if path.name == "acc_frames.npz" else None) for path in args.inputs],
  }
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
