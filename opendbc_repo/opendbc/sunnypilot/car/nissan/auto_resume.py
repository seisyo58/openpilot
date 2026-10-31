"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from opendbc.car import DT_CTRL

# Nissan cluster: PROPILOT_HUD_INFO_MSG.BOTTOM_MSG == PRESS_RES_TO_RESTART.
PRESS_RES_TO_RESTART = 4

# Arming distance matches Lead Departure Alert. Retry the 1 m departure gate
# for vehicle testing, retaining the relative-speed and confirmation-time gates.
# The 8 m window is only for arming. Once armed, a still-present lead may move
# past 8 m while the departure is confirmed.
CLOSE_LEAD_M = 8.0
ARM_S = 1.0
DEPART_M = 1.0
DEPART_VREL_MIN = 0.5  # m/s; lead pulling away. Equality does not count.
TRIGGER_S = 0.5
RES_HOLD_S = 0.12
COOLDOWN_S = 3.0

ARM_FRAMES = round(ARM_S / DT_CTRL)  # >= 1.0 s
# Strictly longer than TRIGGER_S. 50 frames is exactly 0.5 s and does not fire.
TRIGGER_FRAMES = int(TRIGGER_S / DT_CTRL) + 1
RES_HOLD_FRAMES = round(RES_HOLD_S / DT_CTRL)
COOLDOWN_FRAMES = round(COOLDOWN_S / DT_CTRL)


class NissanAutoResume:
  """Press RES once per standstill when a stopped lead pulls away.

  Actuation requires every gate at once: feature enabled, not in ProPILOT Park,
  stock cruise engaged, standstill, brake and gas released, and the cluster
  showing PRESS_RES_TO_RESTART. A cancel request aborts a press in progress.
  """

  def __init__(self, on_transition=None):
    self._arm_frames = 0
    self._armed = False
    self._min_drel = None
    self._trigger_frames = 0
    self._departed = False
    self._hold_frames = 0
    self._sent = False
    self._cooldown = 0
    self._signature = None
    self._on_transition = on_transition

  def observation(self) -> dict:
    pressing = self._hold_frames > 0
    return {
      "armed": self._armed,
      "departed": self._departed,
      "sent": self._sent,
      "res": pressing,
      "minDRel": -1.0 if self._min_drel is None else float(self._min_drel),
    }

  def update(self, enabled: bool, standstill: bool, cruise_enabled: bool, brake_pressed: bool,
             gas_pressed: bool, bottom_msg: int, lead_present: bool, lead_drel: float,
             parking_blocked: bool, cancel: bool, lead_vrel: float = 0.0,
             icc_brake_pt: int | None = None, icc_brake_adas: int | None = None) -> bool:
    if self._cooldown > 0:
      self._cooldown -= 1

    if not enabled or not standstill:
      self._reset_episode()
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, False)
      return False

    if self._hold_frames > 0:
      if self._abort(cruise_enabled, brake_pressed, gas_pressed, parking_blocked, cancel):
        self._hold_frames = 0
        self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, False)
        return False
      self._hold_frames -= 1
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, True)
      return True

    if parking_blocked:
      # Never arm or fire from a parking maneuver, and drop a departure that
      # was only observed while parking was active.
      self._reset_detection()
      self._departed = False
      self._hold_frames = 0
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, False)
      return False

    # Keep checking the lead after a departure is seen. A gate (cluster message,
    # brake, cancel) may still be closed, and an old event must not press RES
    # after the lead has come back, stopped moving away, or disappeared.
    self._update_lead(lead_present, lead_drel, lead_vrel)

    if not self._ready(cruise_enabled, brake_pressed, gas_pressed, bottom_msg, cancel):
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, False)
      return False

    self._sent = True
    self._departed = False
    self._cooldown = COOLDOWN_FRAMES
    self._reset_detection()
    self._hold_frames = RES_HOLD_FRAMES - 1
    self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, True)
    return True

  def _ready(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, bottom_msg: int, cancel: bool) -> bool:
    return (self._departed and not self._sent and self._cooldown == 0 and not cancel and
            cruise_enabled and not brake_pressed and not gas_pressed and int(bottom_msg) == PRESS_RES_TO_RESTART)

  def _abort(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, parking_blocked: bool, cancel: bool) -> bool:
    return cancel or parking_blocked or brake_pressed or gas_pressed or not cruise_enabled

  def _emit(self, lead_drel: float, lead_vrel: float, bottom_msg: int,
            icc_brake_pt: int | None, icc_brake_adas: int | None, res: bool) -> None:
    pressing = res or self._hold_frames > 0
    signature = (self._armed, self._departed, self._sent, pressing)
    if signature == self._signature:
      return
    self._signature = signature
    if pressing:
      phase = "res"
    elif self._departed:
      phase = "departed"
    elif self._armed:
      phase = "armed"
    elif self._sent:
      phase = "sent"
    else:
      phase = "idle"
    payload = {
      "phase": phase,
      "armed": self._armed,
      "departed": self._departed,
      "sent": self._sent,
      "res": pressing,
      "minDRel": None if self._min_drel is None else float(self._min_drel),
      "dRel": float(lead_drel),
      "vRel": float(lead_vrel),
      "bottomMsg": int(bottom_msg),
      "departThresholdM": DEPART_M,
      # 0x1cc byte 0. ICC hold brake, not CarState.brakePressed.
      "iccBrakePt": icc_brake_pt,
      "iccBrakeAdas": icc_brake_adas,
    }
    if self._on_transition is not None:
      self._on_transition(payload)
      return
    try:
      from openpilot.common.swaglog import cloudlog
      cloudlog.event("nissanAutoResume", **payload)
    except Exception:
      pass

  def _pulling_away(self, lead_drel: float, lead_vrel: float) -> bool:
    if self._min_drel is None:
      return False
    return lead_drel - self._min_drel > DEPART_M and lead_vrel > DEPART_VREL_MIN

  def _update_lead(self, lead_present: bool, lead_drel: float, lead_vrel: float) -> None:
    present = bool(lead_present)
    if not self._armed:
      if present and lead_drel < CLOSE_LEAD_M:
        self._arm_frames += 1
        if self._arm_frames >= ARM_FRAMES:
          self._armed = True
          self._min_drel = lead_drel
          self._trigger_frames = 0
      else:
        self._arm_frames = 0
      return

    # Armed. Distance may pass 8 m. A lead that is gone is not a departure.
    if not present or self._min_drel is None:
      self._reset_detection()
      self._departed = False
      return

    if lead_drel < self._min_drel:
      self._min_drel = lead_drel

    if self._pulling_away(lead_drel, lead_vrel):
      self._trigger_frames += 1
      if self._trigger_frames >= TRIGGER_FRAMES:
        self._departed = True
    else:
      # Back inside the distance gate, or the lead is no longer moving away.
      self._trigger_frames = 0
      self._departed = False

  def _reset_detection(self) -> None:
    self._arm_frames = 0
    self._armed = False
    self._min_drel = None
    self._trigger_frames = 0

  def _reset_episode(self) -> None:
    self._reset_detection()
    self._departed = False
    self._hold_frames = 0
    self._sent = False
