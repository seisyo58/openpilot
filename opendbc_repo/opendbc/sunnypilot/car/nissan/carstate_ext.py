"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import math
from enum import StrEnum

from opendbc.car import Bus, structs
from opendbc.can.parser import CANParser
from opendbc.sunnypilot.car.nissan.values import BUTTONS, NissanSafetyFlagsSP, leaf_stock_long
from opendbc.sunnypilot.car.nissan.parking import NissanParkingInterlock

# 0x2B0 (bus 1) stock ADAS accel/brake request. Advisory, receive-only input for the ICBM T feedback.
LEAF_ADAS_REQUEST_MSG = "PROPILOT_REQUEST"
# Publish T only if the last 0x2B0 frame is at most this old relative to the parser's latest update.
LEAF_ADAS_REQUEST_MAX_AGE_NS = 100_000_000  # 100 ms


def leaf_cam_messages(CP) -> list:  # (name, freq) tuples for CANParser; freq=nan means ignore_alive
  """Cam-parser messages registered up front.

  0x2B0 is registered with freq=nan (ignore_alive) so a missing or stopped 0x2B0 never counts
  toward can_valid/canValid. It must not be registered lazily through `vl[...]`, which would
  register it alive-checked.
  """
  return [(LEAF_ADAS_REQUEST_MSG, math.nan)] if leaf_stock_long(CP) else []


class CarStateExt:
  def __init__(self, CP, CP_SP):
    self.CP = CP
    self.CP_SP = CP_SP
    self.parking = NissanParkingInterlock() if CP_SP.safetyParam & NissanSafetyFlagsSP.PPP_INTERLOCK else None
    self.read_leaf_adas_request = leaf_stock_long(CP)

    self.button_events = []
    self.button_states = {button.event_type: False for button in BUTTONS}

  @staticmethod
  def leaf_adas_request_t(cp_cam: CANParser) -> int:
    """Decoded 12-bit T, or 0 (invalid) if 0x2B0 is not registered, never received, or stale."""
    # dict membership does not register the message (VLDict only registers on __getitem__)
    if LEAF_ADAS_REQUEST_MSG not in cp_cam.vl:
      return 0
    ts = cp_cam.ts_nanos[LEAF_ADAS_REQUEST_MSG]["ADAS_REQUEST_HI"]
    if ts == 0 or cp_cam._last_update_nanos - ts > LEAF_ADAS_REQUEST_MAX_AGE_NS:
      return 0
    msg = cp_cam.vl[LEAF_ADAS_REQUEST_MSG]
    return (int(msg["ADAS_REQUEST_HI"]) << 4) | int(msg["ADAS_REQUEST_LO"])

  def update(self, ret: structs.CarState, ret_sp: structs.CarStateSP, can_parsers: dict[StrEnum, CANParser]):
    cp = can_parsers[Bus.pt]
    if self.parking is not None:
      ret_sp.nissanParkingActive = self.parking.active
      ret_sp.nissanParkingBlocked = self.parking.blocked

    if self.read_leaf_adas_request:
      ret_sp.leafAdasRequestT = self.leaf_adas_request_t(can_parsers[Bus.cam])

    button_events = []
    for button in BUTTONS:
      state = (cp.vl[button.can_addr][button.can_msg] in button.values)
      if self.button_states[button.event_type] != state:
        event = structs.CarState.ButtonEvent.new_message()
        event.type = button.event_type
        event.pressed = state
        button_events.append(event)
      self.button_states[button.event_type] = state
    self.button_events = button_events
