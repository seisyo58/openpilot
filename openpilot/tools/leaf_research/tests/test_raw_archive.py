import bz2
from pathlib import Path
import tempfile
import unittest

import zstandard

from openpilot.tools.leaf_research.extract_saved_logs import read_raw
from openpilot.tools.leaf_research.fetch_saved_logs import destination_for


class TestRawArchive(unittest.TestCase):
  def test_camera_filename_is_rejected(self):
    with self.assertRaises(ValueError):
      destination_for(Path('/tmp/archive'), 'route', 'https://example.test/0/fcamera.hevc?secret=x')

  def test_signed_url_is_not_part_of_destination(self):
    result = destination_for(Path('/tmp/archive'), 'route', 'https://example.test/12/rlog.zst?secret=x')
    self.assertEqual(result, Path('/tmp/archive/route/12/rlog.zst'))

  def test_non_numeric_segment_is_rejected(self):
    with self.assertRaises(ValueError):
      destination_for(Path('/tmp/archive'), 'route', 'https://example.test/not-a-segment/rlog.zst')

  def test_both_raw_log_compression_formats_round_trip(self):
    data = b'raw capnp content\x00' * 10
    with tempfile.TemporaryDirectory() as directory:
      for extension, encoded in [('zst', zstandard.ZstdCompressor().compress(data)), ('bz2', bz2.compress(data))]:
        path = Path(directory)/f'rlog.{extension}'
        path.write_bytes(encoded)
        self.assertEqual(read_raw(path), data)


if __name__ == '__main__':
  unittest.main()
