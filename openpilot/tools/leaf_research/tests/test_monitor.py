import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

MODULE = Path(__file__).resolve().parents[1] / 'monitor.py'
spec = importlib.util.spec_from_file_location('leaf_monitor', MODULE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def sample():
  return {'kind': 'sample', 't_mono_s': 1., 'operator': {'axis': .25, 'held': True, 'age_s': 0.},
          'vehicle': {'v_mps': 5., 'brake_pressed': False, 'gas_pressed': False, 'gear': 'drive', 'age_s': 0.}}


class MonitorContracts(unittest.TestCase):
  def test_virtual_request_never_grants_transmission(self):
    result = monitor.evaluate(sample())
    self.assertEqual(result['candidate_accel_mps2'], .25)
    self.assertTrue(result['comparison_eligible'])
    self.assertFalse(result['transmit_allowed'])

  def test_intervention_stale_stop_and_invalid_input_block(self):
    changes = [('operator', 'held', False), ('operator', 'age_s', .201),
               ('operator', 'axis', float('nan')), ('operator', 'axis', 2.),
               ('vehicle', 'age_s', .101), ('vehicle', 'age_s', -.1),
               ('vehicle', 'v_mps', 0.), ('vehicle', 'gear', 'park'),
               ('vehicle', 'brake_pressed', True), ('vehicle', 'gas_pressed', True),
               ('vehicle', 'brake_pressed', None)]
    for group, key, value in changes:
      with self.subTest(group=group, key=key, value=value):
        row = sample()
        row[group][key] = value
        result = monitor.evaluate(row)
        self.assertFalse(result['comparison_eligible'])
        self.assertFalse(result['transmit_allowed'])
        self.assertIsNone(result['candidate_accel_mps2'])

  def test_missing_input_blocks(self):
    self.assertFalse(monitor.evaluate({})['comparison_eligible'])

  def test_input_expires_even_if_last_axis_was_nonzero(self):
    operator = monitor.Operator()
    operator.update(10., .8, True)
    self.assertEqual(operator.snapshot(10.1)['axis'], .8)
    self.assertEqual(operator.snapshot(10.3)['axis'], 0.)
    self.assertFalse(operator.snapshot(10.3)['held'])
    self.assertFalse(operator.snapshot(9.)['held'])

  def test_observed_setting_and_zero_pressure_mode_are_distinct(self):
    self.assertTrue(monitor.decode({'address': 0x2F4, 'hex': '0001000000'})['e_pedal_setting'])
    self.assertTrue(monitor.decode({'address': 0x5B3, 'hex': '0080000000000000'})['e_pedal_setting'])
    brake = monitor.decode({'address': 0x1C3, 'hex': '000000006404ff2b'})
    self.assertEqual(brake['pressure_raw'], 0)
    self.assertTrue(brake['mode_candidate'])
    self.assertFalse(brake['positive_flag'])
    self.assertTrue(monitor.decode({'address': 0x1C3, 'hex': '000000006404ff00'})['invalid_checksum'])

  def test_replay_is_independent_of_saved_evaluation_and_refuses_overwrite(self):
    with tempfile.TemporaryDirectory() as folder:
      source = Path(folder) / 'input.jsonl'
      out = Path(folder) / 'result.jsonl'
      row = sample()
      row['evaluation'] = {'transmit_allowed': True}
      source.write_text(json.dumps(row) + '\n')
      command = [sys.executable, str(MODULE), 'replay', str(source), '--out', str(out)]
      result = subprocess.run(command, capture_output=True, text=True)
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertFalse(json.loads(out.read_text())['evaluation']['transmit_allowed'])
      previous = out.read_text()
      self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
      self.assertEqual(out.read_text(), previous)

  def test_live_record_loop_with_mocked_subscriber(self):
    import time
    class Subscriber:
      def __init__(self, services, **kwargs):
        self.logMonoTime = dict.fromkeys(services, int(time.monotonic() * 1e9))
        self.valid = dict.fromkeys(services, True)
        self.updated = dict.fromkeys(services, True)
        cs = SimpleNamespace(vEgo=0., aEgo=float('nan'), brakePressed=True,
                             gasPressed=False, gearShifter='park', cruiseState=SimpleNamespace(enabled=False))
        cc = SimpleNamespace(enabled=False, longActive=False, actuators=SimpleNamespace(accel=0.),
                             cruiseControl=SimpleNamespace(cancel=False, resume=False))
        self.data = {'carState': cs, 'carControl': cc,
                     'longitudinalPlan': SimpleNamespace(aTarget=0., shouldStop=False),
                     'can': [SimpleNamespace(address=0x2F4, src=0, dat=bytes.fromhex('0001000000'))],
                     'sendcan': []}

      def __getitem__(self, key):
        return self.data[key]

      def update(self, timeout):
        pass

    with tempfile.TemporaryDirectory() as folder:
      out = Path(folder) / 'live.jsonl'
      args = SimpleNamespace(input='none', duration=.01, out=out, axis_code=None,
                             axis_min=None, axis_max=None, hold_button=None)
      cereal = SimpleNamespace(messaging=SimpleNamespace(SubMaster=Subscriber))
      with patch.dict(sys.modules, {'openpilot.cereal': cereal}):
        monitor.record(args)
      rows = [json.loads(line) for line in out.read_text().splitlines()]
      self.assertTrue(rows[0]['receive_only'])
      self.assertGreater(len(rows), 1)
      self.assertIsNone(rows[1]['vehicle']['a_mps2'])
      self.assertTrue(rows[1]['can_events'][0]['decoded']['e_pedal_setting'])
      self.assertFalse(rows[1]['evaluation']['transmit_allowed'])

  def test_tool_has_no_publisher_or_parameter_writes(self):
    tree = ast.parse(MODULE.read_text())
    for node in ast.walk(tree):
      if isinstance(node, ast.Call):
        name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
        self.assertNotIn(name, ['PubMaster', 'Params', 'put_bool', 'send', 'can_send', 'can_send_many'])


if __name__ == '__main__':
  unittest.main()
