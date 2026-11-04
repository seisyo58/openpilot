#!/usr/bin/env python3
"""Receive-only Leaf longitudinal experiment recorder. No actuation transport."""
import argparse
from collections import Counter
from contextlib import contextmanager
import json
import math
from pathlib import Path
import queue
import select
import sys
import threading
import time

CAN_KEYS = {0x2B0, 0x1C3, 0x1CB, 0x180, 0x292, 0x2AA, 0x1D6, 0x2F4, 0x5B3, 0x30F, 0x239, 0x4CC}


def number(value):
  return type(value) in (float, int) and math.isfinite(value)


def json_safe(value):
  if isinstance(value, dict):
    return {key: json_safe(item) for key, item in value.items()}
  if isinstance(value, list):
    return [json_safe(item) for item in value]
  return None if isinstance(value, float) and not math.isfinite(value) else value


def fresh(item, limit):
  age = item.get('age_s')
  return number(age) and 0 <= age <= limit


def evaluate(sample):
  """Evaluate an illustrative acceleration request, never construct CAN frames.

  1 m/s² per full axis is an experiment display scale, not a vehicle model.
  Eligibility is only for offline comparison, never permission to transmit.
  """
  result = {'transmit_allowed': False, 'candidate_accel_mps2': None, 'comparison_eligible': False}
  operator = sample.get('operator', {})
  vehicle = sample.get('vehicle', {})
  if not fresh(operator, .2):
    reason = 'operator_stale'
  elif not number(operator.get('axis')) or not -1 <= operator['axis'] <= 1:
    reason = 'invalid_axis'
  elif operator.get('held') is not True:
    reason = 'operator_not_held'
  elif not fresh(vehicle, .1) or not number(vehicle.get('v_mps')) or vehicle['v_mps'] < 0:
    reason = 'vehicle_stale_or_invalid'
  elif type(vehicle.get('brake_pressed')) is not bool or type(vehicle.get('gas_pressed')) is not bool:
    reason = 'pedal_state_invalid'
  elif vehicle['brake_pressed'] or vehicle['gas_pressed']:
    reason = 'driver_override'
  elif vehicle.get('gear') != 'drive':
    reason = 'not_drive'
  elif vehicle['v_mps'] <= .3:
    reason = 'stop_handover_unverified'
  else:
    reason = 'comparison_only_conversion_unverified'
    result['comparison_eligible'] = True
    result['candidate_accel_mps2'] = operator['axis']
  return dict(result, reason=reason)


def decode(data):
  """CAN data is observational; no physical force conversion is assumed."""
  address, raw = data['address'], bytes.fromhex(data['hex'])
  if address == 0x2F4 and len(raw) == 5:
    return {'e_pedal_setting': bool(raw[1] & 1)}
  if address == 0x5B3 and len(raw) == 8:
    return {'e_pedal_setting': bool(raw[1] & 128)}
  if address == 0x1C3 and len(raw) == 8:
    if raw[7] != (1 + 0xC3 + sum(raw[:7])) & 255:
      return {'invalid_checksum': True}
    return {'pressure_raw': ((raw[0] & 63) << 4) | (raw[1] >> 4),
            'mode_candidate': bool(raw[5] & 4), 'positive_flag': bool(raw[5] & 128)}
  if address == 0x1CB and len(raw) == 7:
    return {'first11_raw': (raw[0] << 3) | (raw[1] >> 5),
            'second11_raw': (raw[2] << 3) | (raw[3] >> 5),
            'hold_a_candidate': bool(raw[4] & 1), 'hold_b_candidate': bool(raw[5] & 4)}
  if address == 0x239 and len(raw) == 8:
    return {'res': bool(raw[3] & 16), 'cancel': bool(raw[3] & 2)}
  return {}


class Operator:
  def __init__(self):
    self.axis = 0.
    self.held = False
    self.last_event = None

  def update(self, now, axis, held):
    self.axis, self.held, self.last_event = axis, held, now

  def snapshot(self, now):
    age = None if self.last_event is None else now - self.last_event
    valid = age is not None and 0 <= age <= .2
    return {'axis': self.axis if valid else 0., 'held': self.held if valid else False, 'age_s': age}


def gamepad_events(events):
  # Blocking device reads live in a daemon. The recording loop still expires
  # input if the device is silent, disconnected, or its reader stops.
  try:
    from inputs import get_gamepad
    while True:
      for event in get_gamepad():
        events.put((time.monotonic(), event.code, event.state))
  except Exception as exc:
    events.put((time.monotonic(), 'reader_error', type(exc).__name__))


@contextmanager
def keyboard_terminal(enabled):
  if not enabled:
    yield
    return
  import termios
  import tty
  saved = termios.tcgetattr(sys.stdin)
  try:
    tty.setcbreak(sys.stdin.fileno())
    yield
  finally:
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved)


def record(args):
  # Import only in live mode; replay and tests need no openpilot runtime.
  from openpilot.cereal import messaging

  services = ['carState', 'carControl', 'longitudinalPlan', 'can', 'sendcan']
  sm = messaging.SubMaster(services, ignore_alive=services, ignore_avg_freq=services)
  events = queue.Queue()
  if args.input == 'gamepad':
    threading.Thread(target=gamepad_events, args=(events,), daemon=True).start()
  operator = Operator()
  axis, held = 0., False
  latest_can = {}
  started = time.monotonic()
  count = 0
  # Exclusive creation prevents accidental replacement of an earlier trial.
  with args.out.open('x', encoding='utf-8', buffering=1) as output, keyboard_terminal(args.input == 'keyboard'):
    output.write(json.dumps({'kind': 'metadata', 'schema': 1, 'receive_only': True,
                             'operator_source': args.input, 'axis_scale_mps2': 1.,
                             'axis_min': args.axis_min, 'axis_max': args.axis_max,
                             'axis_code': args.axis_code, 'hold_button': args.hold_button,
                             'note': 'No publisher, Params writes, CAN transport, or vehicle acceleration model.'}) + '\n')
    while time.monotonic() - started < args.duration:
      sm.update(0)
      now = time.monotonic()
      if args.input == 'keyboard' and select.select([sys.stdin], [], [], 0)[0]:
        key = sys.stdin.read(1).lower()
        if key == 'q':
          break
        if key in ('w', 's', ' '):
          operator.update(now, {'w': .25, 's': -.25, ' ': 0.}[key], key != ' ')
      while not events.empty():
        stamp, code, value = events.get_nowait()
        if code == 'reader_error':
          axis, held = 0., False
          operator.update(stamp, axis, held)
          print(f'Input reader stopped: {value}', file=sys.stderr)
        elif code == args.axis_code:
          axis = max(-1., min(1., 2 * (value - args.axis_min) / (args.axis_max - args.axis_min) - 1))
          operator.update(stamp, axis, held)
        elif code == args.hold_button:
          held = value == 1
          operator.update(stamp, axis, held)
      wire_events = []
      for service in ('can', 'sendcan'):
        if sm.updated[service]:
          stamp = sm.logMonoTime[service] / 1e9
          for frame in sm[service]:
            if frame.address in CAN_KEYS:
              row = {'source': service, 'bus': frame.src, 'address': frame.address,
                     'hex': bytes(frame.dat).hex(), 'mono_s': stamp, 'valid': bool(sm.valid[service])}
              row['decoded'] = decode(row)
              wire_events.append(row)
              latest_can[f'{service}:{frame.src}:{frame.address:x}'] = row
      vehicle = {}
      if sm.logMonoTime['carState'] and sm.valid['carState']:
        cs = sm['carState']
        vehicle = {'v_mps': cs.vEgo, 'a_mps2': cs.aEgo, 'brake_pressed': cs.brakePressed,
                   'gas_pressed': cs.gasPressed, 'gear': str(cs.gearShifter),
                   'cruise_enabled': cs.cruiseState.enabled,
                   'age_s': now - sm.logMonoTime['carState'] / 1e9}
      sample = {'kind': 'sample', 't_mono_s': now, 'operator': operator.snapshot(now), 'vehicle': vehicle,
                'can_events': wire_events,
                'latest_can': {k: dict(v, age_s=now - v['mono_s']) for k, v in latest_can.items()}}
      if sm.logMonoTime['carControl']:
        cc = sm['carControl']
        sample['openpilot_control'] = {'enabled': cc.enabled, 'long_active': cc.longActive,
                                   'accel_mps2': cc.actuators.accel, 'cancel': cc.cruiseControl.cancel,
                                   'resume': cc.cruiseControl.resume,
                                   'valid': bool(sm.valid['carControl']),
                                   'age_s': now - sm.logMonoTime['carControl'] / 1e9}
      if sm.logMonoTime['longitudinalPlan']:
        p = sm['longitudinalPlan']
        sample['plan'] = {'a_target': p.aTarget, 'should_stop': p.shouldStop,
                          'valid': bool(sm.valid['longitudinalPlan']),
                          'age_s': now - sm.logMonoTime['longitudinalPlan'] / 1e9}
      sample['evaluation'] = evaluate(sample)
      output.write(json.dumps(json_safe(sample), allow_nan=False) + '\n')
      count += 1
      time.sleep(.05)
  print(f'Saved {count} receive-only samples to {args.out}')


def replay(args):
  reasons = Counter()
  count = 0
  with args.input.open(encoding='utf-8') as source, args.out.open('x', encoding='utf-8') as output:
    for line in source:
      sample = json.loads(line)
      if sample.get('kind') != 'sample':
        continue
      result = evaluate(sample)
      output.write(json.dumps({'t_mono_s': sample['t_mono_s'], 'evaluation': result}, allow_nan=False) + '\n')
      reasons[result['reason']] += 1
      count += 1
  print(json.dumps({'samples': count, 'reasons': dict(reasons), 'transmit_allowed': False}))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  live = commands.add_parser('record', help='Subscribe and record; never enables joystick debug mode')
  live.add_argument('--out', type=Path, required=True)
  live.add_argument('--duration', type=float, default=60.)
  live.add_argument('--input', choices=('none', 'keyboard', 'gamepad'), default='none')
  live.add_argument('--axis-code')
  live.add_argument('--axis-min', type=float)
  live.add_argument('--axis-max', type=float)
  live.add_argument('--hold-button')
  offline = commands.add_parser('replay', help='Recompute comparison eligibility from a recording')
  offline.add_argument('input', type=Path)
  offline.add_argument('--out', type=Path, required=True)
  args = parser.parse_args()
  if args.command == 'record':
    if not number(args.duration) or args.duration <= 0:
      parser.error('duration must be positive and finite')
    if args.input == 'keyboard' and not sys.stdin.isatty():
      parser.error('keyboard input requires a terminal')
    if args.input == 'gamepad' and (not args.axis_code or not args.hold_button or
        not number(args.axis_min) or not number(args.axis_max) or args.axis_min >= args.axis_max):
      parser.error('gamepad requires axis-code, axis-min < axis-max, and hold-button')
    record(args)
  else:
    replay(args)


if __name__ == '__main__':
  main()
