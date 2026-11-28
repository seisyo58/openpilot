"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from collections import namedtuple
from opendbc.car import structs
from opendbc.car.nissan.values import CAR


class NissanSafetyFlagsSP:
  DEFAULT = 0
  LEAF = 1
  PPP_INTERLOCK = 2  # experimental: NISSAN_LEAF_IC only


def leaf_stock_long(CP) -> bool:
  """Leaf on stock (OEM ICC) longitudinal: scope of the planner ICBM and the 0x2B0 T feedback."""
  return CP.carFingerprint in (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC) and not CP.openpilotLongitudinalControl


ButtonType = structs.CarState.ButtonEvent.Type
Button = namedtuple('Button', ['event_type', 'can_addr', 'can_msg', 'values'])


BUTTONS = [
  Button(ButtonType.accelCruise, "CRUISE_THROTTLE", "RES_BUTTON", [1]),
  Button(ButtonType.decelCruise, "CRUISE_THROTTLE", "SET_BUTTON", [1]),
]
