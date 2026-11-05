"""Read recorded carFw inventories without transmitting diagnostics or exporting VINs."""

import argparse
import hashlib
import json
from pathlib import Path

from openpilot.tools.leaf_research.extract_saved_logs import read_raw


def audit(path):
  from openpilot.cereal import log

  records = []
  for event in log.Event.read_multiple_bytes(read_raw(path)):
    if event.which() != "carParams":
      continue
    for fw in event.carParams.carFw:
      version = bytes(fw.fwVersion)
      records.append({
        "software_ecu_label": str(fw.ecu), "address": hex(fw.address), "bus": fw.bus,
        "obdMultiplexing": fw.obdMultiplexing, "request": [bytes(request).hex() for request in fw.request],
        "fw_bytes": len(version), "fw_sha256": hashlib.sha256(version).hexdigest(),
        "camera_prefix": version[:7].decode("ascii", errors="replace") if str(fw.ecu) == "fwdCamera" else None,
      })
    break
  return {"route": path.parent.parent.name, "segment": path.parent.name,
          "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "records": records}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("inputs", nargs="+", type=Path)
  parser.add_argument("--out", type=Path, required=True)
  args = parser.parse_args()
  result = {"schema": 1, "archives": [audit(path) for path in args.inputs],
            "limitations": [
              "carFw is a recorded software inventory, possibly cached; it is not an exhaustive fresh physical ECU census.",
              "ECU labels are software classifications, not manufacturer-confirmed source mappings for ordinary CAN IDs.",
              "bus1 with obdMultiplexing=true is not interchangeable with a native bus1 diagnostic attempt without multiplexing.",
              "Duplicate addresses/versions and multiple buses do not prove multiple physical ECUs.",
              "Firmware bytes are hashed; VIN, credentials and location are not exported.",
            ]}
  with args.out.open("x") as output:
    output.write(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
  main()
