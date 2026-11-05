"""Classify observations in archived RX NPZ files; no vehicle connection or control.

These labels describe combinations of received fields, not commands or ECU states.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.audit_transport import load_frames


def previous(times, values, query, max_age):
  """Causal join: never use the first future sample at a file or gap boundary."""
  if not len(times):
    return np.zeros((len(query), *values.shape[1:]), dtype=values.dtype), np.zeros(len(query), dtype=bool)
  index = np.searchsorted(times, query, side="right") - 1
  safe = np.maximum(index, 0)
  age = query - times[safe]
  return values[safe], (index >= 0) & (age >= 0) & (age <= max_age)


def decode_2b0(payload):
  b = payload.astype(int)
  active = (b[:, 4] & 64) != 0
  # Exact observed signature, ignoring the high counter nibble in byte6.
  special = np.all(b[:, :6] == [0xa8, 0x27, 0xd5, 0xac, 0x5b, 0], axis=1)
  special &= ((b[:, 6] & 15) == 14) & (b[:, 7] == 9)
  q = 4 * ((b[:, 2] >> 4) * 256 + ((~b[:, 0]) & 255)) + ((b[:, 7] >> 2) & 3)
  return active, special, q


def labels_for(valid, stopped, hold, acc, pedal, active, special, positive):
  labels = np.full(len(valid), "other_observed_combination", dtype="U32")
  eligible = valid & acc & ~pedal & ~hold
  labels[eligible & active & ~special & ~positive] = "normal_request_candidate"
  labels[eligible & ~stopped & active & special & positive] = "coordinated_braking_candidate"
  labels[valid & hold & stopped] = "stopped_hold_candidate"
  labels[~valid] = "unknown_stale"
  return labels


def true_runs(mask, times, max_gap):
  """Return [start, end) indices, splitting even a true mask across missing data."""
  split = np.r_[True, np.diff(times) > max_gap]
  starts = np.flatnonzero(mask & (split | ~np.r_[False, mask[:-1]]))
  ends = np.flatnonzero(mask & (np.r_[split[1:], True] | ~np.r_[mask[1:], False])) + 1
  return list(zip(starts.tolist(), ends.tolist(), strict=True))


def nearby_edge(times, flag, rising, reference, max_gap=0.03):
  edges = np.flatnonzero((flag[1:] != flag[:-1]) & (flag[1:] == rising)
                        & (np.diff(times) <= max_gap)) + 1
  offsets = times[edges] - reference
  offsets = offsets[np.abs(offsets) <= 0.5]
  return float(offsets[np.argmin(np.abs(offsets))] * 1000) if len(offsets) else None


def button_context(frames, t0, reference, before=0.5, after=0.7):
  if frames is None:
    return {"status": "not_available"}
  times = frames[:, 0] - t0
  selection = (times >= reference - before) & (times <= reference + after)
  b3 = frames[:, 4].astype(int)
  result = {"status": "available", "frames_in_window": int(selection.sum())}
  for name, bit in (("res", 4), ("set", 3), ("cancel", 1)):
    selected = times[selection & ((b3 & (1 << bit)) != 0)] - reference
    result[name] = {"frames": len(selected), "first_offset_ms": float(selected[0] * 1000) if len(selected) else None}
  return result


def control_context(frames, t0, reference):
  if frames is None:
    return "not_in_archive"
  times = frames[:, 0] - t0
  selection = (times >= reference - 0.5) & (times <= reference + 0.7)
  result = {"status": "available", "frames_in_window": int(selection.sum())}
  for name, column in (("cancel", 4), ("resume", 5)):
    selected = times[selection & (frames[:, column] != 0)] - reference
    result[name] = {"frames": len(selected), "first_offset_ms": float(selected[0] * 1000) if len(selected) else None}
  return result


def classify(path, tx_path=None):
  with np.load(path, allow_pickle=False) as z:
    t0 = float(z["t0"])
    p = load_frames(z, "b1_1c3")
    request = load_frames(z, "b1_2b0")
    hold_frames = load_frames(z, "b2_30f")
    cb = load_frames(z, "b0_1cb")
    if any(value is None or not len(value) for value in (p, request, hold_frames, cb)):
      raise ValueError("Required RX streams: b1_1c3, b1_2b0, b2_30f, b0_1cb")
    states = np.asarray(z["states"])
    if states.ndim != 2 or states.shape[1] != 9 or not len(states):
      raise ValueError("states must contain [time,v,a,gas,brake,acc,available,set_speed,standstill]")
    if not np.all(np.isfinite(states)) or np.any(np.diff(states[:, 0]) < 0):
      raise ValueError("Invalid state values or timestamps")
    t = p[:, 0] - t0
    pb = p[:, 1:].astype(int)
    raw10 = ((pb[:, 0] & 63) << 4) | (pb[:, 1] >> 4)
    mode = (pb[:, 5] & 4) != 0
    active, special, q = decode_2b0(request[:, 1:])
    rt = request[:, 0] - t0
    rq, rq_valid = previous(rt, np.column_stack((active, special, q)), t, 0.03)
    ht = hold_frames[:, 0] - t0
    hf = (hold_frames[:, 1].astype(int) & 16) != 0
    hold, hold_valid = previous(ht, hf, t, 0.15)
    st = states[:, 0] - t0
    state, state_valid = previous(st, states[:, 1:], t, 0.1)
    ct = cb[:, 0] - t0
    cb_bits = np.column_stack(((cb[:, 5].astype(int) & 1) != 0, (cb[:, 6].astype(int) & 4) != 0))
    cb_value, cb_valid = previous(ct, cb_bits, t, 0.03)
    valid = rq_valid & hold_valid & state_valid & cb_valid
    stopped = state[:, 0] <= 0.1
    pedal = (state[:, 2] != 0) | (state[:, 3] != 0)
    labels = labels_for(valid, stopped, hold, state[:, 4] != 0, pedal,
                        rq[:, 0] != 0, rq[:, 1] != 0, raw10 > 0)
    # Only observed intervals <=30ms count toward duration; no final-sample extrapolation.
    dt = np.r_[np.diff(t), 0.0]
    dt[dt > 0.03] = 0
    summary = {}
    for label in sorted(set(labels)):
      mask = labels == label
      runs = true_runs(mask, t, 0.03)
      summary[label] = {
        "samples": int(mask.sum()), "observed_interval_s": float(dt[mask].sum()),
        "runs": len(runs), "runs_at_least_100ms": int(sum(t[j-1] - t[i] >= 0.1 for i, j in runs)),
      }
    transitions = Counter(f"{a} -> {b}" for a, b, delta in zip(labels[:-1], labels[1:], np.diff(t), strict=True)
                          if a != b and delta <= 0.03)
    braking_runs = []
    for i, j in true_runs(labels == "coordinated_braking_candidate", t, 0.03):
      if t[j-1] - t[i] >= 0.1:
        braking_runs.append({
          "start_s": float(t[i]), "last_s": float(t[j-1]), "samples": j-i,
          "speed_start_mps": float(state[i, 0]), "1c3_raw10_peak": int(raw10[i:j].max()),
          "previous_label": str(labels[i-1]) if i > 0 and t[i] - t[i-1] <= 0.03 else None,
          "next_label": str(labels[j]) if j < len(t) and t[j] - t[j-1] <= 0.03 else None,
        })
    rx_buttons = load_frames(z, "b0_239")
    tx_buttons = load_frames(z, "tx_b2_239")
    controls = np.asarray(z["car_control"]) if "car_control" in z else None
    if controls is not None and (controls.ndim != 2 or controls.shape[1] != 9 or not np.all(np.isfinite(controls))
                                 or np.any(np.diff(controls[:, 0]) < 0)):
      raise ValueError("Invalid car_control: expected finite ordered nine-column stream")
    if tx_path is not None:
      with np.load(tx_path, allow_pickle=False) as tx:
        tx_buttons = load_frames(tx, "tx_b2_239")
    hold_events = []
    for i, j in true_runs(hf, ht, 0.25):
      if ht[j-1] - ht[i] < 0.5:
        continue
      start, last = float(ht[i]), float(ht[j-1])
      left_censored = bool(i == 0 or ht[i] - ht[i-1] > 0.25)
      right_censored = bool(j == len(ht) or ht[j] - ht[j-1] > 0.25)
      stop = None if right_censored else float(ht[j])
      window = valid & (t >= start) & (t <= last)
      def signatures_for(mask):
        return dict(Counter(
          f"active={int(a)},special={int(sp)},positive={int(pos)},bit2={int(m)},cb={int(c0)}{int(c1)}"
          for a, sp, pos, m, (c0, c1) in zip(rq[mask, 0], rq[mask, 1], raw10[mask] > 0,
                                             mode[mask], cb_value[mask], strict=True)
        ).most_common())

      core = window & (t >= start + 1) & (t <= last - 1)
      event = {"start_s": start, "last_on_s": last, "off_s": stop,
               "left_censored": left_censored, "right_censored": right_censored,
               "valid_samples": int(window.sum()), "stopped_samples": int((window & stopped).sum()),
               "signatures": signatures_for(window),
               "core_trim_1s": {"valid_samples": int(core.sum()), "signatures": signatures_for(core)},
               "release": None}
      if stop is not None:
        rx_context = button_context(rx_buttons, t0, stop)
        tx_context = button_context(tx_buttons, t0, stop)
        event["release"] = {
          "window_s": [-0.5, 0.7], "rx_buttons": rx_context, "tx_requests": tx_context,
          "extended_window_s": [-3.5, 0.7],
          "extended_rx_buttons": button_context(rx_buttons, t0, stop, before=3.5),
          "extended_tx_requests": button_context(tx_buttons, t0, stop, before=3.5),
          "carControl": control_context(controls, t0, stop),
          "nearby_edges_ms": {
            "2b0_active_rise": nearby_edge(rt, active, True, stop),
            "1c3_bit2_fall": nearby_edge(t, mode, False, stop),
            "1cb_byte4_bit0_fall": nearby_edge(ct, cb_bits[:, 0], False, stop),
            "1cb_byte5_bit2_fall": nearby_edge(ct, cb_bits[:, 1], False, stop),
            "brakePressed_rise": nearby_edge(st, states[:, 4] != 0, True, stop, max_gap=0.1),
            "gasPressed_rise": nearby_edge(st, states[:, 3] != 0, True, stop, max_gap=0.1),
            "acc_enabled_fall": nearby_edge(st, states[:, 5] != 0, False, stop, max_gap=0.1),
          },
        }
      hold_events.append(event)
    # Preserve full combinations rather than interpreting every unmatched sample as a mode.
    mixed = valid & (labels == "other_observed_combination")
    result = {
      "input": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
      "reference_samples": len(t), "labels": summary,
      "adjacent_label_transitions": dict(transitions.most_common()),
      "braking_candidate_runs_at_least_100ms": braking_runs,
      "other_context_counts_overlapping": {
        "pedal": int((mixed & pedal).sum()), "acc_off": int((mixed & (state[:, 4] == 0)).sum()),
        "moving_hold_flag": int((mixed & hold & ~stopped).sum()),
        "normal_active_with_positive_1c3": int((mixed & (rq[:, 0] != 0) & (rq[:, 1] == 0) & (raw10 > 0)).sum()),
      },
      "hold_flag_runs_at_least_500ms": hold_events,
      "tx_source": {"input": tx_path.name, "sha256": hashlib.sha256(tx_path.read_bytes()).hexdigest()}
                   if tx_path is not None else {"input": path.name, "stream_present": tx_buttons is not None},
    }
    return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("inputs", nargs="+", type=Path)
  parser.add_argument("--tx-59", type=Path, help="Separate route59_tx.npz for acc_frames.npz only")
  parser.add_argument("--out", required=True, type=Path)
  args = parser.parse_args()
  result = {
    "schema": 1,
    "rules": {
      "reference": "Every b1_1c3 RX frame; previous sample only",
      "freshness_s": {"2b0": 0.03, "1cb": 0.03, "30f": 0.15, "carState": 0.1},
      "stopped_speed_mps": 0.1,
      "labels": {
        "normal_request_candidate": "ACC on, no pedal, hold flag off, active ordinary 2B0, 1C3 raw10=0",
        "coordinated_braking_candidate": "ACC on, no pedal, moving, hold flag off, active special 2B0, 1C3 raw10>0",
        "stopped_hold_candidate": "Speed<=0.1m/s and 30F bit4 on; other fields are not required to agree",
        "other_observed_combination": "Fresh inputs outside those definitions; not a new ECU mode",
        "unknown_stale": "At least one required prior stream missing or too old",
      },
      "release_window_s": [-0.5, 0.7],
      "release_extended_window_s": [-3.5, 0.7],
    },
    "limitations": [
      "Observational labels only, not executable control states or acceptance conditions.",
      "CAN Event timestamps are batches; edge offsets are not hardware latency or causal order.",
      "TX means sendcan request, not on-wire transmission or ECU acceptance; matching RX may be passed through.",
      "Legacy archives omit carControl; inspect per-release availability. Absence of TX cancel does not establish pure OEM behavior.",
      "Hold flag runs are candidates; stopped_samples and contradictory signatures must be inspected.",
      "Other-context counts overlap; intervals across >30ms gaps are excluded, not interpolated.",
      "Nearest edges within +/-500ms may be unrelated; no fixed waiting-time rule is inferred.",
    ],
    "archives": [classify(path, args.tx_59 if path.name == "acc_frames.npz" else None) for path in args.inputs],
  }
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
