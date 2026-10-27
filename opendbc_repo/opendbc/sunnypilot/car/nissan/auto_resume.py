"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from opendbc.car import DT_CTRL

# Nissan cluster: PROPILOT_HUD_INFO_MSG.BOTTOM_MSG == PRESS_RES_TO_RESTART.
PRESS_RES_TO_RESTART = 4

# Same thresholds as Lead Departure Alert (e2e_alerts_helper.py). That alert
# only runs while openpilot longitudinal is off, so this copies the detection
# and is allowed to run while stock ICC is engaged.
# The 8 m window is only for arming. Once armed, a still-present lead may move
# past 8 m while the departure is confirmed. The alert drops the arm at 8 m,
# which makes a lead stopped at 7–8 m unable to ever count a 1 m pull-away.
CLOSE_LEAD_M = 8.0
ARM_S = 1.0
DEPART_M = 1.0
TRIGGER_S = 0.3
RES_HOLD_S = 0.12
COOLDOWN_S = 3.0

ARM_FRAMES = round(ARM_S / DT_CTRL)  # >= 1.0 s
# Strictly longer than 0.3 s. 30 frames is exactly 0.3 s and does not fire.
TRIGGER_FRAMES = int(TRIGGER_S / DT_CTRL) + 1
RES_HOLD_FRAMES = round(RES_HOLD_S / DT_CTRL)
COOLDOWN_FRAMES = round(COOLDOWN_S / DT_CTRL)


class NissanAutoResume:
  """Press RES once per standstill when a stopped lead pulls away.

  Actuation requires every gate at once: feature enabled, not in ProPILOT Park,
  stock cruise engaged, standstill, brake and gas released, and the cluster
  showing PRESS_RES_TO_RESTART. A cancel request aborts a press in progress.
  """

  def __init__(self):
    self._arm_frames = 0
    self._armed = False
    self._min_drel = None
    self._trigger_frames = 0
    self._departed = False
    self._hold_frames = 0
    self._sent = False
    self._cooldown = 0

  def update(self, enabled: bool, standstill: bool, cruise_enabled: bool, brake_pressed: bool,
             gas_pressed: bool, bottom_msg: int, lead_present: bool, lead_drel: float,
             parking_blocked: bool, cancel: bool) -> bool:
    if self._cooldown > 0:
      self._cooldown -= 1

    if not enabled or not standstill:
      self._reset_episode()
      return False

    if self._hold_frames > 0:
      if self._abort(cruise_enabled, brake_pressed, gas_pressed, parking_blocked, cancel):
        self._hold_frames = 0
        return False
      self._hold_frames -= 1
      return True

    if parking_blocked:
      # Never arm or fire from a parking maneuver, and drop a departure that
      # was only observed while parking was active.
      self._reset_detection()
      self._departed = False
      self._hold_frames = 0
      return False

    # Keep checking the lead after a departure is seen. A gate (cluster message,
    # brake, cancel) may still be closed, and an old event must not press RES
    # after the lead has come back or disappeared.
    self._update_lead(lead_present, lead_drel)

    if not self._ready(cruise_enabled, brake_pressed, gas_pressed, bottom_msg, cancel):
      return False

    self._sent = True
    self._departed = False
    self._cooldown = COOLDOWN_FRAMES
    self._reset_detection()
    self._hold_frames = RES_HOLD_FRAMES - 1
    return True

  def _ready(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, bottom_msg: int, cancel: bool) -> bool:
    return (self._departed and not self._sent and self._cooldown == 0 and not cancel and
            cruise_enabled and not brake_pressed and not gas_pressed and int(bottom_msg) == PRESS_RES_TO_RESTART)

  def _abort(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, parking_blocked: bool, cancel: bool) -> bool:
    return cancel or parking_blocked or brake_pressed or gas_pressed or not cruise_enabled

  def _update_lead(self, lead_present: bool, lead_drel: float) -> None:
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

    if lead_drel - self._min_drel > DEPART_M:
      self._trigger_frames += 1
      if self._trigger_frames >= TRIGGER_FRAMES:
        self._departed = True
    else:
      # Back within 1 m of the stopped minimum. Drop the pending event.
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
