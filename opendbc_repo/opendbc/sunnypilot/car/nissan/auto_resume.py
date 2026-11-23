"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from opendbc.car import DT_CTRL

PRESS_RES_TO_RESTART = 4
PRESS_RES_SET_TO_CHANGE_SPEED = 3
RETRY_BOTTOM_MSGS = (PRESS_RES_TO_RESTART, PRESS_RES_SET_TO_CHANGE_SPEED, 0)

CLOSE_LEAD_M = 8.0
ARM_S = 1.0
DEPART_M = 1.0
DEPART_VREL_MIN = 0.5
RES_TAP_HOLD_S = 0.10
RES_TAP_GAP_S = 0.12
RETRY_WAIT_S = 0.30
TAP_SUCCESS_HUD3_S = 0.80
TAP_OUTCOME_WINDOW_S = 1.0
SESSION_MAX_S = 8.0
V_EGO_SUCCESS = 0.3
MAX_TAPS_PER_STOP = 5

ARM_FRAMES = round(ARM_S / DT_CTRL)
TRIGGER_FRAMES = 1
RES_HOLD_FRAMES = round(RES_TAP_HOLD_S / DT_CTRL)
TAP_GAP_FRAMES = round(RES_TAP_GAP_S / DT_CTRL)
RETRY_WAIT_FRAMES = round(RETRY_WAIT_S / DT_CTRL)
HUD3_SUCCESS_FRAMES = round(TAP_SUCCESS_HUD3_S / DT_CTRL)
OUTCOME_WINDOW_FRAMES = round(TAP_OUTCOME_WINDOW_S / DT_CTRL)
SESSION_MAX_FRAMES = round(SESSION_MAX_S / DT_CTRL)

RES_BUTTON = "RES_BUTTON"

# SET recovery remains disabled: ICBM does not forward auto buttons when cruise is off.
COOLDOWN_FRAMES = 0


class NissanAutoResume:
  """Repeated RES taps at standstill when a lead pulls away (ICC on)."""

  def __init__(self, on_transition=None):
    self._arm_frames = 0
    self._armed = False
    self._min_drel = None
    self._trigger_frames = 0
    self._departed = False
    self._sent = False
    self._session_active = False
    self._session_done = False
    self._tap_count = 0
    self._session_frames = 0
    self._state = "idle"
    self._hold_remain = 0
    self._gap_remain = 0
    self._wait_retry_remain = 0
    self._outcome_remain = 0
    self._hud3_run = 0
    self._saw_hud3_since_tap = False
    self._on_transition = on_transition
    self._signature = None

  def observation(self) -> dict:
    return {
      "armed": self._armed,
      "departed": self._departed,
      "sent": self._sent,
      "res": self._state == "hold",
      "tapCount": self._tap_count,
      "sessionDone": self._session_done,
      "minDRel": -1.0 if self._min_drel is None else float(self._min_drel),
    }

  def update(self, enabled: bool, standstill: bool, cruise_enabled: bool, brake_pressed: bool,
             gas_pressed: bool, bottom_msg: int, lead_present: bool, lead_drel: float,
             parking_blocked: bool, cancel: bool, lead_vrel: float = 0.0, v_ego: float = 0.0,
             icc_brake_pt: int | None = None, icc_brake_adas: int | None = None) -> str | None:
    bottom_msg = int(bottom_msg)

    if not enabled:
      self._reset_episode()
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._session_active and not standstill:
      self._finish_giveup("moved_off_standstill", lead_drel, lead_vrel, bottom_msg,
                          icc_brake_pt, icc_brake_adas)
      self._reset_episode()
      return None

    if not standstill:
      self._reset_episode()
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if parking_blocked:
      self._reset_detection()
      self._departed = False
      if self._session_active:
        self._finish_giveup("driver", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      self._reset_episode()
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._session_active and self._driver_abort(cancel, brake_pressed, gas_pressed):
      self._finish_giveup("driver", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._session_active and not cruise_enabled:
      self._finish_giveup("icc_off", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    self._update_lead(lead_present, lead_drel, lead_vrel)

    if self._session_active and not self._session_done:
      self._session_frames += 1
      if self._session_frames >= SESSION_MAX_FRAMES:
        self._finish_giveup("timeout", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None
      reason = self._check_success(bottom_msg, v_ego, cruise_enabled, gas_pressed)
      if reason is not None:
        self._finish_success(reason, lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None

    if self._session_done:
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._state == "hold":
      self._tick_monitor(bottom_msg)
      if self._driver_abort(cancel, brake_pressed, gas_pressed):
        self._finish_giveup("driver", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None
      self._hold_remain -= 1
      if self._hold_remain < 0:
        self._state = "gap"
        self._gap_remain = TAP_GAP_FRAMES
        self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None
      self._emit_tap(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, pressing=True)
      return RES_BUTTON

    if self._state == "gap":
      self._gap_remain -= 1
      self._tick_monitor(bottom_msg)
      if self._gap_remain < 0:
        self._state = "monitor"
        self._outcome_remain = OUTCOME_WINDOW_FRAMES
        self._saw_hud3_since_tap = False
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._state == "monitor":
      self._tick_monitor(bottom_msg)
      self._outcome_remain -= 1
      if self._hud3_drop_to_zero(bottom_msg):
        self._begin_wait_retry(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None
      if self._outcome_remain < 0:
        self._begin_wait_retry(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._state == "wait_retry":
      self._tick_monitor(bottom_msg)
      self._wait_retry_remain -= 1
      if self._wait_retry_remain < 0:
        if self._can_start_tap(retry=True, bottom_msg=bottom_msg, cruise_enabled=cruise_enabled,
                              brake_pressed=brake_pressed, gas_pressed=gas_pressed, cancel=cancel):
          return self._begin_tap(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        self._finish_giveup("max_taps", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
        return None
      self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
      return None

    if self._ready_first(cruise_enabled, brake_pressed, gas_pressed, bottom_msg, cancel):
      return self._begin_tap(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)

    self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)
    return None

  def _ready_first(self, cruise_enabled: bool, brake_pressed: bool, gas_pressed: bool,
                   bottom_msg: int, cancel: bool) -> bool:
    return (self._departed and not self._session_active and not cancel and cruise_enabled and
            not brake_pressed and not gas_pressed and bottom_msg == PRESS_RES_TO_RESTART)

  def _can_start_tap(self, retry: bool, bottom_msg: int, cruise_enabled: bool, brake_pressed: bool,
                     gas_pressed: bool, cancel: bool) -> bool:
    if cancel or not cruise_enabled or brake_pressed or gas_pressed:
      return False
    if self._tap_count >= MAX_TAPS_PER_STOP:
      return False
    if self._session_frames >= SESSION_MAX_FRAMES:
      return False
    if retry:
      return bottom_msg in RETRY_BOTTOM_MSGS
    return bottom_msg == PRESS_RES_TO_RESTART

  def _begin_tap(self, lead_drel: float, lead_vrel: float, bottom_msg: int,
                 icc_brake_pt: int | None, icc_brake_adas: int | None) -> str:
    if not self._session_active:
      self._session_active = True
      self._session_frames = 0
    self._tap_count += 1
    self._sent = True
    self._state = "hold"
    self._hold_remain = RES_HOLD_FRAMES - 1
    self._hud3_run = 0
    self._saw_hud3_since_tap = False
    self._emit_tap(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas, pressing=True)
    return RES_BUTTON

  def _begin_wait_retry(self, lead_drel: float, lead_vrel: float, bottom_msg: int,
                        icc_brake_pt: int | None, icc_brake_adas: int | None) -> None:
    self._state = "wait_retry"
    self._wait_retry_remain = RETRY_WAIT_FRAMES - 1
    self._hud3_run = 0
    self._emit_idle(lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)

  def _tick_monitor(self, bottom_msg: int) -> None:
    if bottom_msg == PRESS_RES_SET_TO_CHANGE_SPEED:
      self._saw_hud3_since_tap = True
      self._hud3_run += 1
    else:
      self._hud3_run = 0

  def _hud3_drop_to_zero(self, bottom_msg: int) -> bool:
    return self._saw_hud3_since_tap and bottom_msg == 0

  def _check_success(self, bottom_msg: int, v_ego: float, cruise_enabled: bool, gas_pressed: bool) -> str | None:
    if self._hud3_run >= HUD3_SUCCESS_FRAMES:
      return "hud3_stable"
    if v_ego > V_EGO_SUCCESS and cruise_enabled and not gas_pressed:
      return "moving"
    return None

  def _driver_abort(self, cancel: bool, brake_pressed: bool, gas_pressed: bool) -> bool:
    return cancel or brake_pressed or gas_pressed

  def _finish_success(self, reason: str, lead_drel: float, lead_vrel: float, bottom_msg: int,
                      icc_brake_pt: int | None, icc_brake_adas: int | None) -> None:
    self._session_done = True
    self._state = "idle"
    self._hold_remain = 0
    self._emit_phase("tap_success", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas,
                     reason=reason)

  def _finish_giveup(self, reason: str, lead_drel: float, lead_vrel: float, bottom_msg: int,
                     icc_brake_pt: int | None, icc_brake_adas: int | None) -> None:
    self._session_done = True
    self._state = "idle"
    self._hold_remain = 0
    self._emit_phase("tap_giveup", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas,
                     reason=reason)

  def _emit_idle(self, lead_drel: float, lead_vrel: float, bottom_msg: int,
                 icc_brake_pt: int | None, icc_brake_adas: int | None) -> None:
    if self._departed:
      phase = "departed"
    elif self._armed:
      phase = "armed"
    elif self._sent:
      phase = "sent"
    else:
      phase = "idle"
    self._emit_phase(phase, lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas)

  def _emit_tap(self, lead_drel: float, lead_vrel: float, bottom_msg: int,
                icc_brake_pt: int | None, icc_brake_adas: int | None, pressing: bool) -> None:
    self._emit_phase("tap", lead_drel, lead_vrel, bottom_msg, icc_brake_pt, icc_brake_adas,
                     res=pressing, tap_index=self._tap_count,
                     ms_since_first=max(0.0, self._session_frames * DT_CTRL * 1000.0))

  def _emit_phase(self, phase: str, lead_drel: float, lead_vrel: float, bottom_msg: int,
                  icc_brake_pt: int | None, icc_brake_adas: int | None, **extra) -> None:
    pressing = extra.get("res", phase == "tap")
    signature = (phase, self._armed, self._departed, self._sent, self._tap_count, pressing,
                 bottom_msg, extra.get("reason"), extra.get("tap_index"))
    if signature == self._signature:
      return
    self._signature = signature
    payload = {
      "phase": phase,
      "armed": self._armed,
      "departed": self._departed,
      "sent": self._sent,
      "res": bool(pressing),
      "tapIndex": self._tap_count,
      "tapCount": self._tap_count,
      "minDRel": None if self._min_drel is None else float(self._min_drel),
      "dRel": float(lead_drel),
      "vRel": float(lead_vrel),
      "bottomMsg": int(bottom_msg),
      "departThresholdM": DEPART_M,
      "iccBrakePt": icc_brake_pt,
      "iccBrakeAdas": icc_brake_adas,
      "msSinceFirstTap": max(0.0, self._session_frames * DT_CTRL * 1000.0),
    }
    if reason := extra.get("reason"):
      payload["reason"] = reason
    payload.update({k: v for k, v in extra.items() if k not in ("res",)})
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
    if self._session_active:
      return
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
    self._sent = False
    self._session_active = False
    self._session_done = False
    self._tap_count = 0
    self._session_frames = 0
    self._state = "idle"
    self._hold_remain = 0
    self._gap_remain = 0
    self._wait_retry_remain = 0
    self._outcome_remain = 0
    self._hud3_run = 0
    self._saw_hud3_since_tap = False