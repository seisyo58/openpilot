"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from enum import StrEnum

from opendbc.car import Bus, structs
from opendbc.can.parser import CANParser
from opendbc.car.nissan.values import CAR
from opendbc.sunnypilot.car.nissan.values import BUTTONS, NissanSafetyFlagsSP
from opendbc.sunnypilot.car.nissan.parking import NissanParkingInterlock


class CarStateExt:
  def __init__(self, CP, CP_SP):
    self.CP = CP
    self.CP_SP = CP_SP
    self.parking = NissanParkingInterlock() if CP_SP.safetyParam & NissanSafetyFlagsSP.PPP_INTERLOCK else None

    self.button_events = []
    self.button_states = {button.event_type: False for button in BUTTONS}

  def update(self, ret: structs.CarState, ret_sp: structs.CarStateSP, can_parsers: dict[StrEnum, CANParser]):
    cp = can_parsers[Bus.pt]
    if self.parking is not None:
      ret_sp.nissanParkingActive = self.parking.active
      ret_sp.nissanParkingBlocked = self.parking.blocked

    if self.CP.carFingerprint in (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC):
      cp_cam = can_parsers[Bus.cam]
      msg = cp_cam.vl.get("PROPILOT_REQUEST")
      if msg:
        hi = msg.get("ADAS_REQUEST_HI", 0)
        lo = msg.get("ADAS_REQUEST_LO", 0)
        ret_sp.leafAdasRequestT = (hi << 4) | lo

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
