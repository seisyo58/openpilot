"""Extract bounded, location-free research streams from locally saved raw rlogs."""

import argparse
import bz2
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import zstandard


ADDRESSES = {0x180, 0x1c3, 0x1cb, 0x1cc, 0x1d4, 0x1d6, 0x1da, 0x239, 0x28f, 0x292,
             0x2aa, 0x2b0, 0x2d4, 0x2f4, 0x30f, 0x421, 0x4cc, 0x5b3, 0x707, 0x727, 0x75d, 0x77d}


def read_raw(path):
  if path.suffix == ".bz2":
    return bz2.decompress(path.read_bytes())
  with path.open("rb") as source, zstandard.ZstdDecompressor().stream_reader(source) as reader:
    return reader.read()


def extract(route_dir, output_dir):
  from openpilot.cereal import log

  files = sorted(route_dir.glob("*/rlog.*"), key=lambda p: int(p.parent.name))
  if not files:
    raise ValueError("No raw logs")
  npz_path = output_dir / f"{route_dir.name}.npz"
  report_path = output_dir / f"{route_dir.name}.json"
  if npz_path.exists() or report_path.exists():
    raise FileExistsError("Refusing to replace an extraction")
  chunks = defaultdict(list)
  counts, kinds, gear_counts = Counter(), Counter(), Counter()
  state_rows, extra_rows, control_rows = [], [], []
  software, car, panda_changes, segments = [], [], [], []
  previous_panda = None
  t0 = None
  for path in files:
    frames = defaultdict(list)
    first, last = None, None
    raw = read_raw(path)
    for event in log.Event.read_multiple_bytes(raw):
      kind, time = event.which(), event.logMonoTime / 1e9
      kinds[kind] += 1
      if kind in ("can", "sendcan"):
        if kind == "can":
          if t0 is None:
            t0 = time
          first = time if first is None else first
          last = time
        for frame in getattr(event, kind):
          counts[f"{kind}:b{frame.src}:{frame.address:03x}"] += 1
          if frame.address in ADDRESSES:
            data = bytes(frame.dat)
            if len(data) > 8:
              raise ValueError("Selected classic CAN payload exceeds eight bytes")
            key = f"{'tx_' if kind == 'sendcan' else ''}b{frame.src}_{frame.address:03x}"
            frames[key].append((time, *data, *([0] * (8-len(data)))))
      elif kind == "carState":
        s = event.carState
        state_rows.append((time, s.vEgo, s.aEgo, s.gasPressed, s.brakePressed, s.cruiseState.enabled,
                           s.cruiseState.available, s.cruiseState.speed, s.standstill))
        gear_counts[str(s.gearShifter)] += 1
        extra_rows.append((time, s.gearShifter.raw, s.fuelGauge, s.brakeHoldActive, s.parkingBrake,
                           s.regenBraking, s.stockAeb, s.stockFcw, s.accFaulted, s.canValid, s.canTimeout))
      elif kind == "carControl":
        c = event.carControl
        control_rows.append((time, c.enabled, c.latActive, c.longActive, c.cruiseControl.cancel,
                             c.cruiseControl.resume, c.cruiseControl.override, c.actuators.accel,
                             c.actuators.longControlState.raw))
      elif kind == "initData":
        d = event.initData
        row = {name: getattr(d, name) for name in ("gitCommit", "gitBranch", "version", "dirty")}
        if row not in software:
          software.append(row)
      elif kind == "carParams":
        c = event.carParams
        row = {name: getattr(c, name) for name in ("carFingerprint", "openpilotLongitudinalControl", "pcmCruise")}
        row["safetyConfigs"] = [{"safetyModel": str(s.safetyModel), "safetyParam": s.safetyParam} for s in c.safetyConfigs]
        if row not in car:
          car.append(row)
      elif kind == "pandaStates":
        snapshot = [{"safetyModel": str(p.safetyModel), "safetyParam": p.safetyParam,
                     "harnessStatus": str(p.harnessStatus), "faults": [str(f) for f in p.faults],
                     "safetyRxChecksInvalid": p.safetyRxChecksInvalid} for p in event.pandaStates]
        if snapshot != previous_panda:
          panda_changes.append({"time_mono": time, "states": snapshot})
          previous_panda = snapshot
    for key, values in frames.items():
      chunks[key].append(np.asarray(values, dtype=float))
    segments.append({"segment": int(path.parent.name), "filename": path.name, "bytes": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "can_first_mono": first, "can_last_mono": last})
    del raw
    print(f"parsed {route_dir.name}/{path.parent.name}", flush=True)
  if t0 is None:
    raise ValueError("No received CAN")
  arrays = {key: np.concatenate(value) for key, value in chunks.items()}
  arrays.update(states=np.asarray(state_rows).reshape(-1, 9), state_extra=np.asarray(extra_rows).reshape(-1, 11),
                car_control=np.asarray(control_rows).reshape(-1, 9), t0=np.array(t0))
  report = {"route": route_dir.name, "t0": t0, "software": software, "car": car,
            "event_counts": dict(kinds), "can_counts": dict(counts), "gear_counts": dict(gear_counts),
            "panda_changes": panda_changes, "segments": segments,
            "state_extra_columns": ["time", "gear_raw", "fuelGauge", "brakeHoldActive", "parkingBrake",
                                    "regenBraking", "stockAeb", "stockFcw", "accFaulted", "canValid", "canTimeout"],
            "car_control_columns": ["time", "enabled", "latActive", "longActive", "cancel", "resume", "override", "accel", "longControlState"],
            "warning": "Default-valued carState fields are not proof of measured vehicle quantities; inspect the recorded parser."}
  output_dir.mkdir(parents=True, exist_ok=True)
  # All paths and signed access URLs stay outside version control.
  with npz_path.open("xb") as output:
    np.savez_compressed(output, **arrays)
  with report_path.open("x") as output:
    output.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
  print(f"extracted {route_dir.name}: {len(state_rows)} states", flush=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--out-dir", required=True, type=Path)
  parser.add_argument("route_dirs", nargs="+", type=Path)
  args = parser.parse_args()
  for route in args.route_dirs:
    extract(route, args.out_dir)


if __name__ == "__main__":
  main()
