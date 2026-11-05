"""Summarize recovered raw-log extractions and compare with legacy NPZ evidence."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from openpilot.tools.leaf_research.audit_boundaries import analyze
from openpilot.tools.leaf_research.classify_events import classify, previous


ROUTES = (("59", "00000059--02589b1b20", "acc_frames.npz"),
          ("5a", "0000005a--34bc682d3e", "route5a_frames.npz"),
          ("5c", "0000005c--6a84234ad4", "route5c_frames.npz"))


def review(extracted, legacy, manifest):
  acquisition = json.loads(manifest.read_text())
  result = {"schema": 1, "downloaded_files": len(acquisition["files"]),
            "downloaded_bytes": sum(f["bytes"] for f in acquisition["files"]),
            "download_failures": acquisition["failures"], "camera_files_requested": acquisition["camera_files_requested"],
            "raw_files": acquisition["files"], "routes": []}
  for short, route, old_name in ROUTES:
    path = extracted / f"{route}.npz"
    meta = json.loads((extracted / f"{route}.json").read_text())
    c = classify(path)
    with np.load(path, allow_pickle=False) as z, np.load(legacy/old_name, allow_pickle=False) as old:
      t0 = float(z["t0"])
      common = [k for k in old.files if k in z and (k in ("states", "t0") or k.startswith(("b0_", "b1_", "b2_")))]
      equal = [k for k in common if np.array_equal(z[k], old[k])]
      settings = {}
      moving_settings = {}
      for key, byte, bit in (("b0_2f4", 1, 0), ("b0_5b3", 1, 7)):
        if key in z:
          selected = z[key][z[key][:, 0] - t0 >= 30]
          values, counts = np.unique((selected[:, byte+1].astype(int) >> bit) & 1, return_counts=True)
          settings[key] = {str(v): int(n) for v, n in zip(values, counts, strict=True)}
          s, valid = previous(z["states"][:, 0], z["states"][:, 1:], z[key][:, 0], 0.1)
          moving = z[key][valid & (s[:, 4] != 0) & (s[:, 0] > 3/3.6)]
          values, counts = np.unique((moving[:, byte+1].astype(int) >> bit) & 1, return_counts=True)
          moving_settings[key] = {str(v): int(n) for v, n in zip(values, counts, strict=True)}
      gearbox = z["b0_421"]
      state, fresh = previous(z["states"][:, 0], z["states"][:, 1:], gearbox[:, 0], 0.1)
      selected = gearbox[fresh & (state[:, 4] != 0) & (state[:, 0] > 3/3.6)]
      gear_values, gear_counts = np.unique((selected[:, 1].astype(int) >> 3) & 7, return_counts=True)
      row = {"short": short, "route": route, "npz_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "segments": len(meta["segments"]), "software": meta["software"], "car": meta["car"],
             "car_state_samples": len(z["states"]), "car_control_samples": len(z["car_control"]),
             "gear_counts_carState": meta["gear_counts"], "gear_raw_moving_acc": {str(v): int(n) for v, n in zip(gear_values, gear_counts, strict=True)},
             "epedal_flags_after_30s": settings,
             "epedal_flags_moving_acc": moving_settings,
             "ev_1d4_1da_counts": {k: v for k, v in meta["can_counts"].items() if k.endswith((":1d4", ":1da"))},
             "legacy_arrays_compared": len(common), "legacy_arrays_exactly_equal": equal,
             "legacy_reference_samples": len(old["b1_1c3"]), "new_reference_samples": len(z["b1_1c3"]),
             "panda_changes": [{"time_s": p["time_mono"] - t0, "states": p["states"]} for p in meta["panda_changes"]],
             "classification": c, "boundary_summary": analyze(path)["summary"]}
      result["routes"].append(row)
  parsed_files = {}
  for path in extracted.glob("*.json"):
    meta = json.loads(path.read_text())
    for segment in meta["segments"]:
      filename = segment.get("filename", "rlog.zst")  # Initial extraction reports used zst only.
      parsed_files[f"{meta['route']}/{segment['segment']}/{filename}"] = (segment["bytes"], segment["sha256"])
  downloaded_files = {f["path"]: (f["bytes"], f["sha256"]) for f in acquisition["files"]}
  if parsed_files != downloaded_files:
    raise ValueError("Downloaded files and parsed-source hashes differ")
  result["raw_segments_parsed"] = len(parsed_files)
  result["raw_download_parse_hashes_match"] = True
  result["limitations"] = [
    "Preserved flag itself was not inspected; file listing, download and raw parsing were verified.",
    "Gear and e-Pedal settings do not establish actuation; moving-ACC filter is retrospective.",
    "FuelGauge and regenBraking default values are not measured SOC or regeneration; recorded parser must support a field.",
    "Recorded source commit is not a measurement of flashed Panda binary identity.",
    "No vehicle communication, log-upload request, or setting changes were performed.",
  ]
  return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--extracted-dir", type=Path, required=True)
  parser.add_argument("--legacy-dir", type=Path, required=True)
  parser.add_argument("--manifest", type=Path, required=True)
  parser.add_argument("--out", type=Path, required=True)
  args = parser.parse_args()
  result = review(args.extracted_dir, args.legacy_dir, args.manifest)
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
