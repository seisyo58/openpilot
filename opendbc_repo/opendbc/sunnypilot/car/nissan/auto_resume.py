"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from opendbc.car import DT_CTRL

# Nissan cluster: PROPILOT_HUD_INFO_MSG.BOTTOM_MSG values.
PRESS_RES_TO_RESTART = 4
PRESS_RES_SET_TO_CHANGE_SPEED = 3

# Arming distance matches Lead Departure Alert. Retry the 1 m departure gate
# for vehicle testing, retaining the relative-speed gate.
# The 8 m window is only for arming. Once armed, a still-present lead may move
# past 8 m when the departure is detected.
CLOSE_LEAD_M = 8.0
ARM_S = 1.0
DEPART_M = 1.0
DEPART_VREL_MIN = 0.5  # m/s; lead pulling away. Equality does not count.
RES_MAX_HOLD_S = 0.15
SET_HOLD_S = 0.12
SET_RECOVERY_TIMEOUT_S = 1.5
COOLDOWN_S = 3.0

ARM_FRAMES = round(ARM_S / DT_CTRL)  # >= 1.0 s
# Detect departure on the first qualifying control update, without a dwell.
TRIGGER_FRAMES = 1
RES_MAX_HOLD_FRAMES = round(RES_MAX_HOLD_S / DT_CTRL)
SET_HOLD_FRAMES = round(SET_HOLD_S / DT_CTRL)
SET_RECOVERY_TIMEOUT_FRAMES = round(SET_RECOVERY_TIMEOUT_S / DT_CTRL)
COOLDOWN_FRAMES = round(COOLDOWN_S / DT_CTRL)

# Kept for tests that reference total res press budget at 100 Hz.
RES_HOLD_FRAMES = RES_MAX_HOLD_FRAMES

RES_BUTTON = "RES_BUTTON"
SET_BUTTON = "SET_BUTTON"


class NissanAutoResume:
  """Press RES once per standstill when a stopped lead pulls away.

  Actuation requires every gate at once: feature enabled, not in ProPILOT Park,
  stock cruise engaged, standstill, brake and gas released, and the cluster
  showing PRESS_RES_TO_RESTART. A cancel request aborts a press in progress.
  Failed resumes may use one SET recovery and one follow-up RES per stop.
  """

  def __init__(self, on_transition=None):
    self._arm_frames = 0
    self._armed = False
    self._min_drel = None
    self._trigger_frames = 0
    self._departed = False
    self._hold_frames = 0
    self._set_hold_frames = 0
    self._sent = False
    self._res_failed = False
    self._set_recovery_sent = False
    self._retry_res_sent = False
    self._recovery_timeout = 0
    self._recovery_abandoned = False
    self._is_retry_press = False
    self._cooldown = 0
    self._signature = None
    self._on_transition = on_transition

  def observation(self) -> dict:
    pressing_res = self._hold_frames > 0
    pressing_set = self._set_hold_frames > 0
    return {
      "armed": self._armed,
      "departed": self._departed,
      "sent": self._sent,
      "res": pressing_res,
      "set": pressing_set,
      "resFailed": self._res_failed,
      "minDRel": -1.0 if self._min_drel is None else float(self._min_drel),
    }

  def update(self, enabled: bool, standstill: bool, cruise_enabled: bool, brake_pressed: bool,
             gas_pressed: bool, bottom_msg: int, lead_present: bool, lead_drel: float,
             parking_blocked: bool, cancel: bool, lead_vrel: float = 0.0,
             icc_brake_pt: int | None = None, icc_brake_adas: int | None = None) -> str | None:
    bottom_msg = int(bottom_msg)

    if self._cooldown > 0:
      self._cooldown -= 1

    if not enabled or not standstill:
      self._reset_episode()
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None)
      return None

    if self._set_hold_frames > 0:
      if self._abort(cruise_enabled, brake_pressed, gas_pressed, parking_blocked, cancel,
                    allow_cruise_off=True):
        self._set_hold_frames = 0
        if cancel or brake_pressed or gas_pressed:
          self._recovery_abandoned = True
        self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None)
        return None
      self._set_hold_frames -= 1
      if self._set_hold_frames < 0:
        self._set_hold_frames = 0
      still_holding = self._set_hold_frames > 0
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas,
                 SET_BUTTON if still_holding else None,
                 phase_override=None if still_holding else "set_recovery")
      if not still_holding:
        self._set_recovery_sent = True
        self._recovery_timeout = SET_RECOVERY_TIMEOUT_FRAMES
        return None
      return SET_BUTTON

    if self._hold_frames > 0:
      button = self._tick_res_hold(bottom_msg, cruise_enabled, brake_pressed, gas_pressed,
                                   parking_blocked, cancel, lead_drel, lead_vrel, icc_brake_pt, icc_brake_adas)
      return button

    if parking_blocked:
      self._reset_detection()
      self._departed = False
      self._abort_presses()
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None)
      return None

    self._update_lead(lead_present, lead_drel, lead_vrel)
    self._note_resume_failure(bottom_msg)

    if self._try_set_recovery(cruise_enabled, brake_pressed, gas_pressed, parking_blocked, cancel,
                              lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas):
      return SET_BUTTON

    if self._ready_retry(cruise_enabled, brake_pressed, gas_pressed, bottom_msg, cancel):
      self._start_res_hold(True, lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, RES_BUTTON,
                 phase_override="retry_res")
      return RES_BUTTON

    self._tick_recovery_timeout()
    if not self._ready(cruise_enabled, brake_pressed, gas_pressed, bottom_msg, cancel):
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None)
      return None

    self._start_res_hold(False, lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
    self._cooldown = COOLDOWN_FRAMES
    self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, RES_BUTTON)
    return RES_BUTTON

  def _ready(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, bottom_msg: int, cancel: bool) -> bool:
    return (self._departed and not self._sent and self._cooldown == 0 and not cancel and
            cruise_enabled and not brake_pressed and not gas_pressed and bottom_msg == PRESS_RES_TO_RESTART)

  def _ready_retry(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, bottom_msg: int,
                   cancel: bool) -> bool:
    return (self._res_failed and self._set_recovery_sent and not self._retry_res_sent and
            not self._recovery_abandoned and self._departed and not cancel and cruise_enabled and
            not brake_pressed and not gas_pressed and bottom_msg == PRESS_RES_TO_RESTART)

  def _abort(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool, parking_blocked: bool,
             cancel: bool, allow_cruise_off: bool = False) -> bool:
    if cancel or parking_blocked or brake_pressed or gas_pressed:
      return True
    if not allow_cruise_off and not cruise_enabled:
      return True
    return False

  def _tick_res_hold(self, bottom_msg: int, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool,
                     parking_blocked: bool, cancel: bool, lead_drel: float, lead_vrel: float,
                     icc_brake_pt: int | None, icc_brake_adas: int | None) -> str | None:
    if bottom_msg == PRESS_RES_SET_TO_CHANGE_SPEED:
      self._hold_frames = 0
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None,
                 phase_override="res_released_on_3")
      return None

    if bottom_msg == 0:
      self._hold_frames = 0
      if not self._is_retry_press:
        self._res_failed = True
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None,
                 phase_override="res_aborted_on_0")
      return None

    if self._abort(cruise_enabled, brake_pressed, gas_pressed, parking_blocked, cancel):
      self._hold_frames = 0
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None)
      return None

    self._hold_frames -= 1
    if self._hold_frames < 0:
      self._hold_frames = 0
      self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, None)
      return None

    self._emit(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, RES_BUTTON)
    return RES_BUTTON

  def _start_res_hold(self, is_retry: bool, lead_drel: float, lead_vrel: float, bottom_msg: int,
                      icc_brake_pt: int | None, icc_brake_adas: int | None) -> None:
    self._is_retry_press = is_retry
    self._hold_frames = RES_MAX_HOLD_FRAMES - 1
    if is_retry:
      self._retry_res_sent = True
    else:
      self._sent = True

  def _note_resume_failure(self, bottom_msg: int) -> None:
    if (self._sent and not self._is_retry_press and not self._res_failed and self._hold_frames == 0 and
        self._set_hold_frames == 0 and bottom_msg == 0):
      self._res_failed = True

  def _try_set_recovery(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool,
                        parking_blocked: bool, cancel: bool, lead_drel: float, lead_vrel: float,
                        bottom_msg: int, icc_brake_pt: int | None, icc_brake_adas: int | None) -> bool:
    if (not self._res_failed or self._set_recovery_sent or self._recovery_abandoned or
        parking_blocked or brake_pressed or gas_pressed or cancel):
      if cancel and self._res_failed:
        self._recovery_abandoned = True
      return False
    if cruise_enabled:
      return False
    self._set_hold_frames = SET_HOLD_FRAMES - 1
    return True

  def _tick_recovery_timeout(self) -> None:
    if self._recovery_timeout <= 0 or self._retry_res_sent:
      return
    self._recovery_timeout -= 1
    if self._recovery_timeout == 0:
      self._recovery_abandoned = True

  def _abort_presses(self) -> None:
    self._hold_frames = 0
    self._set_hold_frames = 0

  def _emit(self, lead_drel: float, lead_vrel: float, bottom_msg: int,
            icc_brake_pt: int | None, icc_brake_adas: int | None, button: str | None,
            phase_override: str | None = None) -> None:
    pressing_res = button == RES_BUTTON or self._hold_frames > 0
    pressing_set = button == SET_BUTTON or self._set_hold_frames > 0
    signature = (self._armed, self._departed, self._sent, pressing_res, pressing_set, self._res_failed,
                 phase_override)
    if signature == self._signature:
      return
    self._signature = signature
    if phase_override:
      phase = phase_override
    elif pressing_res:
      phase = "res"
    elif pressing_set:
      phase = "set_recovery"
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
      "res": pressing_res,
      "set": pressing_set,
      "resFailed": self._res_failed,
      "minDRel": None if self._min_drel is None else float(self._min_drel),
      "dRel": float(lead_drel),
      "vRel": float(lead_vrel),
      "bottomMsg": int(bottom_msg),
      "departThresholdM": DEPART_M,
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
    self._abort_presses()
    self._sent = False
    self._res_failed = False
    self._set_recovery_sent = False
    self._retry_res_sent = False
    self._recovery_timeout = 0
    self._recovery_abandoned = False
    self._is_retry_press = False