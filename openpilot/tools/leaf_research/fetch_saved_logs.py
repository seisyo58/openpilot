"""Download only rlogs from explicitly named routes using existing comma auth.

Does not request uploads, change preservation, share routes, or fetch camera media.
Signed URLs and auth tokens are never written to the manifest or progress output.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

import requests


def destination_for(root, route, url):
  parts = urlparse(url).path.split("/")
  segment, filename = parts[-2:]
  if not segment.isdigit() or filename not in ("rlog.zst", "rlog.bz2"):
    raise ValueError("Unexpected raw-log filename or segment")
  return root / route / segment / filename


def fetch_one(root, route, url):
  destination = destination_for(root, route, url)
  destination.parent.mkdir(parents=True, exist_ok=True)
  if not destination.exists():
    temporary = None
    try:
      with requests.get(url, stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
          temporary = Path(output.name)
          for chunk in response.iter_content(chunk_size=1024*1024):
            output.write(chunk)
        os.replace(temporary, destination)
    finally:
      if temporary is not None and temporary.exists():
        temporary.unlink()
  digest = hashlib.sha256()
  with destination.open("rb") as source:
    for chunk in iter(lambda: source.read(1024*1024), b""):
      digest.update(chunk)
  return {"path": str(destination.relative_to(root)), "bytes": destination.stat().st_size,
          "sha256": digest.hexdigest()}


def main():
  from openpilot.tools.lib.api import CommaApi
  from openpilot.tools.lib.auth_config import get_token

  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--device", required=True)
  parser.add_argument("--out-dir", required=True, type=Path)
  parser.add_argument("routes", nargs="+")
  args = parser.parse_args()
  if not args.device.isalnum() or any(not route or any(c not in "0123456789abcdef-" for c in route) for route in args.routes):
    raise ValueError("Unexpected device or route identifier")
  token = get_token()
  if not token:
    raise RuntimeError("Existing comma authentication is required")
  tasks = []
  for route in args.routes:
    try:
      listing = CommaApi(token).get(f"v1/route/{args.device}|{route}/files", timeout=30)
      tasks.extend((route, url) for url in listing.get("logs", []))
    except Exception as error:
      raise RuntimeError(f"Route listing failed: {route}: {type(error).__name__}") from None
  args.out_dir.mkdir(parents=True, exist_ok=True)
  manifest = {"routes": args.routes, "files": [], "failures": [], "camera_files_requested": 0}
  with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {pool.submit(fetch_one, args.out_dir, route, url): route for route, url in tasks}
    for future in as_completed(futures):
      route = futures[future]
      try:
        result = future.result()
        manifest["files"].append(result)
        print(f"saved {result['path']} ({result['bytes']} bytes)", flush=True)
      except Exception as error:
        manifest["failures"].append({"route": route, "error_type": type(error).__name__})
        print(f"download failed: {route}: {type(error).__name__}", flush=True)
  manifest["files"].sort(key=lambda row: row["path"])
  manifest_path = args.out_dir / "manifest.json"
  with tempfile.NamedTemporaryFile(mode="w", dir=args.out_dir, delete=False) as output:
    json.dump(manifest, output, indent=2)
    output.write("\n")
  os.replace(output.name, manifest_path)
  print(f"completed: {len(manifest['files'])} files; {len(manifest['failures'])} failures", flush=True)
  if manifest["failures"]:
    raise SystemExit(1)


if __name__ == "__main__":
  main()
