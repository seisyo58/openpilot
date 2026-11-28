"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from statistics import median

from openpilot.cereal import custom, log
from opendbc.car.structs import car
from opendbc.car import DT_CTRL, structs, apply_hysteresis
from opendbc.sunnypilot.car.nissan.values import leaf_stock_long
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed
from openpilot.sunnypilot.selfdrive.car.cruise_buttons import CRUISE_BUTTON_TIMER, update_manual_button_timers

LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
State = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState
SendButtonState = custom.IntelligentCruiseButtonManagement.SendButtonState

ALLOWED_SPEED_THRESHOLD = 1.8  # m/s, ~4 MPH
HYST_GAP = 0.0  # currently disabled; TODO-SP: might need to be brand-specific
INACTIVE_TIMER = 0.4
PLANNER_GAIN = 1.0  # acceleration error -> set-speed adjustment rate
PLANNER_MAX_LOWER_RATE = 2.0  # m/s of set speed per second
PLANNER_RECOVERY_RATE = 0.5  # m/s of set speed per second
PLANNER_ACCEL_DEADBAND = 0.15  # m/s²
PLANNER_MAX_PENDING_DROP = 5.0 / 3.6  # one observed Leaf OEM step
PLANNER_CAP_QUANT_HYST = 2.5 / 3.6  # the cap must fall this far below a 5 km/h boundary to step down
PLANNER_STRONG_DECEL = -0.8  # m/s²; strong demand bypasses the cap hysteresis and the SET-after-RES dwell
LEAF_BUTTON_TIMEOUT = 0.3  # s; stop requesting if no acknowledgement
LEAF_SETTLE_TIME = 1.0  # s; observe vehicle response after a button request
LEAF_INCREASE_CONFIRM = 1.0  # s; avoid reversing for a transient higher target
LEAF_REVERSE_DWELL_RES_AFTER_SET = 3.0  # s after an acknowledged SET before a RES
LEAF_REVERSE_DWELL_SET_AFTER_RES = 2.0  # s after an acknowledged RES before a SET (bypassable)
# 0x2B0 T early release. All values are first-guess calibration values, to be tuned from logs.
LEAF_T_MIN_SETTLE = 0.3  # s after the acknowledgement before an early release is possible
LEAF_T_CONFIRM_DELTA = 30  # counts of T movement beyond the pre-acknowledgement trend
LEAF_T_TREND_WINDOW = 0.3  # s of continuously valid T before the acknowledgement; fits the trend
LEAF_T_CONFIRM_HOLD = 0.05  # s the response must persist without interruption
PLANNER_RELEASE_THRESHOLD = -0.1  # hysteresis after entering at -0.3
PLANNER_DECEL_THRESHOLD = -0.3  # m/s²; ignore very small deceleration requests
PLAN_MAX_AGE = 0.25  # s
CAR_STATE_SP_MAX_AGE = 0.05  # s; published with every carState (100 Hz)


def _frames(seconds: float) -> int:
  return round(seconds / DT_CTRL)


# Leaf pacing runs on integer frame counts (one per run() call), not accumulated float time.
LEAF_BUTTON_TIMEOUT_FRAMES = _frames(LEAF_BUTTON_TIMEOUT)
LEAF_SETTLE_FRAMES = _frames(LEAF_SETTLE_TIME)
LEAF_INCREASE_CONFIRM_FRAMES = _frames(LEAF_INCREASE_CONFIRM)
LEAF_T_MIN_SETTLE_FRAMES = _frames(LEAF_T_MIN_SETTLE)
LEAF_T_TREND_FRAMES = _frames(LEAF_T_TREND_WINDOW)
LEAF_T_CONFIRM_HOLD_FRAMES = _frames(LEAF_T_CONFIRM_HOLD)
LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES = _frames(LEAF_REVERSE_DWELL_RES_AFTER_SET)
LEAF_REVERSE_DWELL_SET_AFTER_RES_FRAMES = _frames(LEAF_REVERSE_DWELL_SET_AFTER_RES)

PlanSource = log.LongitudinalPlan.LongitudinalPlanSource
VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.VisionState
LEAD_SOURCES = (PlanSource.lead0, PlanSource.lead1)
VISION_DECEL_STATES = (VisionState.entering, VisionState.turning)


def _floor5(kph: float) -> int:
  return 5 * math.floor((kph + 1e-4) / 5)


SEND_BUTTONS = {
  State.increasing: SendButtonState.increase,
  State.decreasing: SendButtonState.decrease,
}


def planner_data_valid(sm, now: float) -> bool:
  services = ('longitudinalPlan', 'longitudinalPlanSP')
  stamps = [sm.logMonoTime[s] * 1e-9 for s in services]
  stamps.append(sm['longitudinalPlan'].modelMonoTime * 1e-9)
  return sm.all_checks(service_list=list(services)) and all(-1e-6 <= now - stamp <= PLAN_MAX_AGE for stamp in stamps)


def car_state_sp_valid(sm, now: float) -> bool:
  stamp = sm.logMonoTime['carStateSP'] * 1e-9
  return sm.all_checks(service_list=['carStateSP']) and -1e-6 <= now - stamp <= CAR_STATE_SP_MAX_AGE


def _trend(values) -> tuple[float, float]:
  """Theil-Sen line through equally spaced samples: (slope per sample, fitted value at the last sample).

  Median-based, so a step (e.g. the plateau of the previous press's response) occupying a minority of
  the window is not mistaken for a ramp, while a steady ramp is recovered exactly.
  """
  values = list(values)
  n = len(values)
  slope = median((values[j] - values[i]) / (j - i) for i in range(n) for j in range(i + 1, n))
  intercept = median(v - slope * i for i, v in enumerate(values))
  return slope, intercept + slope * (n - 1)


@dataclass
class LeafTReference:
  """T state captured when the OEM acknowledged a press, in press-direction terms.

  sign is +1 for a decrease press (T expected to rise) and -1 for an increase press (T expected
  to fall). drift is the pre-acknowledgement trend in the expected direction (counts/frame,
  never negative), so movement that was already under way before the press is not credited to it.
  """
  t0: float
  sign: int
  drift: float
  ack_frame: int
  hits: int = 0

  def response(self, t: int, frame: int) -> float:
    return self.sign * (t - self.t0) - self.drift * (frame - self.ack_frame)


class IntelligentCruiseButtonManagement:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP):
    self.CP = CP
    self.CP_SP = CP_SP

    self.v_target = 0
    self.v_cruise_cluster = 0
    self.v_cruise_min = 0
    self.cruise_button = SendButtonState.none
    self.state = State.inactive
    self.pre_active_timer = 0

    self.is_ready = False
    self.is_ready_prev = False
    self.v_target_ms_last = 0.0
    self.is_metric = False

    self.cruise_button_timers = CRUISE_BUTTON_TIMER.copy()
    self.planner_cap = None
    self.planner_cap_level: int | None = None  # km/h; hysteretic 5 km/h quantization of planner_cap
    self.planner_decelerating = False
    self.leaf_reverse_bypass = False  # strong/lead/curve deceleration: SET may follow RES without the dwell
    self.leaf_last_ack = None  # (direction, frame) of the last acknowledged press
    self.leaf_frame = 0
    self.leaf_pending = None
    self.leaf_settle_until = 0
    self.leaf_increase_since = None
    self.leaf_t = 0
    self.leaf_t_history: deque[int] = deque(maxlen=LEAF_T_TREND_FRAMES)
    self.leaf_t_ref: LeafTReference | None = None
    self.use_planner = leaf_stock_long(CP)

  @property
  def v_cruise_equal(self) -> bool:
    return self.v_target == self.v_cruise_cluster

  def update_calculations(self, CS: car.CarState, LP_SP: custom.LongitudinalPlanSP,
                          plan: log.LongitudinalPlan | None = None) -> None:
    speed_conv = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH
    ms_conv = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS

    target = LP_SP.vTarget
    self.leaf_reverse_bypass = False
    if self.use_planner and plan is not None:
      minimum = get_minimum_set_speed(self.is_metric) * ms_conv
      cluster = CS.cruiseState.speedCluster
      step = PLANNER_MAX_PENDING_DROP if self.is_metric else 2.0 / 3.6
      error = plan.aTarget - CS.aEgo
      lead = plan.longitudinalPlanSource in LEAD_SOURCES
      vision_decel = (LP_SP.longitudinalPlanSource == LongitudinalPlanSource.sccVision and
                      LP_SP.smartCruiseControl.vision.state in VISION_DECEL_STATES)
      self.leaf_reverse_bypass = plan.aTarget <= PLANNER_STRONG_DECEL or lead or vision_decel
      if plan.aTarget <= PLANNER_DECEL_THRESHOLD:
        self.planner_decelerating = True
      elif plan.aTarget >= PLANNER_RELEASE_THRESHOLD:
        self.planner_decelerating = False
      if self.planner_decelerating:
        if self.planner_cap is None:
          self.planner_cap = max(minimum, min(target, cluster))
        if error < -PLANNER_ACCEL_DEADBAND:
          lowered = self.planner_cap - min(-error * PLANNER_GAIN, PLANNER_MAX_LOWER_RATE) * DT_CTRL
          if plan.longitudinalPlanSource == PlanSource.cruise:
            # a_cruise only means "approach vTarget", which the SP target already requests: lowering the cap
            # below it would count that demand twice. A curve being entered may still take one extra step.
            floor = _floor5(target * speed_conv) * ms_conv if self.is_metric else target
            if vision_decel:
              floor -= step
            lowered = max(lowered, min(self.planner_cap, floor))  # never lower below it; never raise to it
          self.planner_cap = lowered
          # Wait for the OEM setpoint to respond before asking for more.
          self.planner_cap = max(self.planner_cap, cluster - step)
        else:
          # Already braking enough: cancel any remaining downward target.
          self.planner_cap = max(self.planner_cap, cluster)
          if error > PLANNER_ACCEL_DEADBAND:
            self.planner_cap += min(error * PLANNER_GAIN, PLANNER_RECOVERY_RATE) * DT_CTRL
      elif self.planner_cap is not None:
        self.planner_cap += PLANNER_RECOVERY_RATE * DT_CTRL
        if self.planner_cap >= target:
          self.planner_cap = None
      if self.planner_cap is not None:
        self.planner_cap = max(minimum, self.planner_cap)
        target = min(target, self.planner_cap)
      if self.planner_cap is not None and self.is_metric:
        self.quantize_planner_cap(bypass=error <= PLANNER_STRONG_DECEL or lead)
      else:
        self.planner_cap_level = None
    self.v_target_ms_last = apply_hysteresis(target, self.v_target_ms_last, HYST_GAP * ms_conv)

    self.v_target = round(self.v_target_ms_last * speed_conv)
    self.v_cruise_min = get_minimum_set_speed(self.is_metric)
    if self.use_planner and self.is_metric:
      # SP target: plain 5 km/h floor. Planner cap: hysteretic level (quantize_planner_cap).
      level = _floor5(LP_SP.vTarget * speed_conv)
      if self.planner_cap_level is not None:
        level = min(level, self.planner_cap_level)
      self.v_target = max(self.v_cruise_min, level)
    self.v_cruise_cluster = round(CS.cruiseState.speedCluster * speed_conv)

  def quantize_planner_cap(self, bypass: bool) -> None:
    """5 km/h level for planner_cap: stepping down needs the cap PLANNER_CAP_QUANT_HYST below the boundary
    (unless bypassed); stepping back up only needs it back at the boundary."""
    cap = self.planner_cap * CV.MS_TO_KPH
    up = _floor5(cap)
    # highest level L with cap <= (L + 5) - Hq, i.e. inclusive at "boundary - Hq" (within 1e-4 km/h)
    down = up if bypass else 5 * math.ceil((cap + PLANNER_CAP_QUANT_HYST * CV.MS_TO_KPH - 1e-4) / 5) - 5
    level = self.planner_cap_level
    if level is None:
      # New cap (initialized and already lowered once this frame): enter as if from the boundary above it.
      self.planner_cap_level = down
    elif up > level:
      self.planner_cap_level = up
    elif down < level:
      self.planner_cap_level = down

  def update_state_machine(self) -> custom.IntelligentCruiseButtonManagement.SendButtonState:
    self.pre_active_timer = max(0, self.pre_active_timer - 1)

    # HOLDING, ACCELERATING, DECELERATING, PRE_ACTIVE
    if self.state != State.inactive:
      if not self.is_ready:
        self.state = State.inactive

      else:
        # PRE_ACTIVE
        if self.state == State.preActive:
          if self.pre_active_timer <= 0:
            if self.v_cruise_equal:
              self.state = State.holding

            elif self.v_target > self.v_cruise_cluster:
              self.state = State.increasing

            elif self.v_target < self.v_cruise_cluster and self.v_cruise_cluster > self.v_cruise_min:
              self.state = State.decreasing

        # HOLDING
        elif self.state == State.holding:
          if not self.v_cruise_equal:
            self.state = State.preActive

        # ACCELERATING
        elif self.state == State.increasing:
          if self.v_target <= self.v_cruise_cluster:
            self.state = State.holding

        # DECELERATING
        elif self.state == State.decreasing:
          if self.v_target >= self.v_cruise_cluster or self.v_cruise_cluster <= self.v_cruise_min:
            self.state = State.holding

    # INACTIVE
    elif self.state == State.inactive:
      if self.is_ready and not self.is_ready_prev:
        self.pre_active_timer = int(INACTIVE_TIMER / DT_CTRL)
        self.state = State.preActive

    send_button = SEND_BUTTONS.get(self.state, SendButtonState.none)

    return send_button

  def update_leaf_t(self, CS_SP) -> None:
    """Latch the 0x2B0 T for this frame; 0 (CS_SP missing/stale, or T <= 0) means invalid."""
    t = int(CS_SP.leafAdasRequestT) if CS_SP is not None else 0
    self.leaf_t = max(t, 0)
    if self.leaf_t > 0:
      self.leaf_t_history.append(self.leaf_t)
    else:
      self.leaf_t_history.clear()  # the trend window must be continuously valid

  def leaf_t_reference(self, requested, now: int) -> LeafTReference | None:
    """Reference for an acknowledged press, or None (full settle) unless T was valid for the whole trend window."""
    history = self.leaf_t_history
    if len(history) < LEAF_T_TREND_FRAMES:
      return None
    sign = 1 if requested == SendButtonState.decrease else -1
    slope, fitted = _trend(history)  # pre-acknowledgement trend line, evaluated at this frame
    # Both choices can only make confirmation harder: the directionally higher of measured and fitted T,
    # and only a trend already moving in the expected direction is subtracted (an opposite trend is not credited).
    t0 = sign * max(sign * self.leaf_t, sign * fitted)
    return LeafTReference(t0=t0, sign=sign, drift=max(0.0, sign * slope), ack_frame=now)

  def leaf_t_confirmed(self, now: int) -> bool:
    """True once T has responded to the acknowledged press beyond its pre-press trend."""
    ref = self.leaf_t_ref
    if ref is None:
      return False
    if self.leaf_t <= 0:
      self.leaf_t_ref = None  # T lost mid-settle: this settle can no longer release early
      return False
    ref.hits = ref.hits + 1 if ref.response(self.leaf_t, now) >= LEAF_T_CONFIRM_DELTA else 0
    return now - ref.ack_frame >= LEAF_T_MIN_SETTLE_FRAMES and ref.hits >= LEAF_T_CONFIRM_HOLD_FRAMES

  def pace_leaf_button(self, button):
    """Wait for OEM acknowledgement and settling; never chase intermediate speeds."""
    now = self.leaf_frame
    if self.planner_decelerating and button == SendButtonState.increase:
      button = SendButtonState.none
      self.leaf_increase_since = None
    if not self.is_ready:
      self.leaf_pending = self.leaf_increase_since = None
      self.leaf_settle_until = now + LEAF_SETTLE_FRAMES
      self.leaf_t_ref = None
      return SendButtonState.none
    if self.leaf_pending is not None:
      setting, started, requested = self.leaf_pending
      moved = self.v_cruise_cluster - setting
      if moved != 0 or now - started >= LEAF_BUTTON_TIMEOUT_FRAMES:
        self.leaf_pending = None
        self.leaf_settle_until = now + LEAF_SETTLE_FRAMES
        self.leaf_increase_since = None
        # Only a move in the requested direction acknowledges the press; anything else gets the full settle.
        acked = moved < 0 if requested == SendButtonState.decrease else moved > 0
        self.leaf_t_ref = self.leaf_t_reference(requested, now) if acked else None
        if acked:
          self.leaf_last_ack = (requested, now)
        return SendButtonState.none
      # A changed target must not finish an obsolete/opposite press.
      if button == SendButtonState.none or button != requested:
        self.leaf_pending = None
        self.leaf_settle_until = now + LEAF_SETTLE_FRAMES
        self.leaf_t_ref = None
        return SendButtonState.none
      return button
    if now < self.leaf_settle_until:
      if not self.leaf_t_confirmed(now):
        return SendButtonState.none
      self.leaf_settle_until = now  # T confirmed the acknowledged press: release early
    self.leaf_t_ref = None  # settle over, whether released early or expired
    if button == SendButtonState.increase:
      if self.leaf_increase_since is None:
        self.leaf_increase_since = now
      if now - self.leaf_increase_since < LEAF_INCREASE_CONFIRM_FRAMES:
        return SendButtonState.none
    else:
      self.leaf_increase_since = None
    # Checked last, so the dwell runs from the acknowledgement in parallel with settle and RES confirm.
    if self.leaf_reverse_dwell(button, now):
      return SendButtonState.none
    if button != SendButtonState.none:
      self.leaf_pending = (self.v_cruise_cluster, now, button)
    return button

  def leaf_reverse_dwell(self, button, now: int) -> bool:
    """True while a press opposite to the last acknowledged one must wait; same-direction presses never do."""
    if self.leaf_last_ack is None or button == SendButtonState.none:
      return False
    last, frame = self.leaf_last_ack
    if button == last:
      return False
    if button == SendButtonState.increase:
      return now - frame < LEAF_REVERSE_DWELL_RES_AFTER_SET_FRAMES
    return now - frame < LEAF_REVERSE_DWELL_SET_AFTER_RES_FRAMES and not self.leaf_reverse_bypass

  def update_readiness(self, CS: car.CarState, CC: car.CarControl) -> None:
    update_manual_button_timers(CS, self.cruise_button_timers)

    ready = CC.enabled and not CC.cruiseControl.override and not CC.cruiseControl.cancel and not CC.cruiseControl.resume
    button_pressed = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)

    self.is_ready = ready and not button_pressed

  def run(self, CS: car.CarState, CC: car.CarControl, LP_SP: custom.LongitudinalPlanSP, is_metric: bool,
          plan: log.LongitudinalPlan | None = None, *, plan_valid: bool = False, CS_SP=None) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      return

    self.is_metric = is_metric
    self.leaf_frame += 1
    if self.use_planner:
      self.update_leaf_t(CS_SP)

    self.update_readiness(CS, CC)
    if self.use_planner:
      # A missing/stale plan must not restore the higher cruise target or send RES.
      self.is_ready &= (plan_valid and plan is not None and math.isfinite(plan.aTarget) and
                        math.isfinite(LP_SP.vTarget) and math.isfinite(CS.vEgo) and math.isfinite(CS.aEgo) and
                        math.isfinite(CS.cruiseState.speedCluster) and
                        not CS.brakePressed and not CS.gasPressed and CS.cruiseState.enabled)
    if self.use_planner and not self.is_ready:
      self.planner_cap = self.planner_cap_level = None
      self.planner_decelerating = False
    if not self.use_planner or self.is_ready:
      self.update_calculations(CS, LP_SP, plan)

    self.cruise_button = self.update_state_machine()
    if self.use_planner and self.is_metric and not CS.standstill:
      self.cruise_button = self.pace_leaf_button(self.cruise_button)
    elif CS.standstill:
      self.leaf_pending = self.leaf_increase_since = None
      self.leaf_t_ref = None
    # Planner mode only: no ICBM-initiated RES at standstill (on the Leaf, RES while held launches).
    if self.use_planner and CS.standstill and self.cruise_button == SendButtonState.increase:
      self.cruise_button = SendButtonState.none

    self.is_ready_prev = self.is_ready
