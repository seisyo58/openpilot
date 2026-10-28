import numpy as np
from opendbc.can import CANPacker
from opendbc.car import Bus, DT_CTRL, structs
from opendbc.car.lateral import apply_std_steer_angle_limits
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.nissan import nissancan
from opendbc.car.nissan.values import CAR, CarControllerParams
from opendbc.sunnypilot.car.nissan.auto_resume import NissanAutoResume
from opendbc.sunnypilot.car.nissan.nissancan_ext import create_cruise_throttle_msg
from opendbc.sunnypilot.car.nissan.values import NissanFlagsSP
from opendbc.car.common.filter_simple import FirstOrderFilter

VisualAlert = structs.CarControl.HUDControl.VisualAlert


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP, CP_SP):
    super().__init__(dbc_names, CP, CP_SP)
    self.car_fingerprint = CP.carFingerprint

    self.angle_filter = FirstOrderFilter(0.0, 0.1, DT_CTRL)

    self.apply_angle_last = 0

    self.packer = CANPacker(dbc_names[Bus.pt])
    leaf = CP.carFingerprint in (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC)
    self._auto_resume = NissanAutoResume() if leaf else None
    self._auto_resume_enabled = bool(leaf and CP_SP.flags & NissanFlagsSP.AUTO_RESUME)

  def update(self, CC, CC_SP, CS, now_nanos):
    parking_blocked = CS.parking is not None and CS.parking.blocked
    resume_res = False
    if self._auto_resume is not None:
      bottom_msg = CS.lkas_hud_info_msg.get("BOTTOM_MSG", 0) if CS.lkas_hud_info_msg else 0
      lead = CC_SP.leadOne
      resume_res = self._auto_resume.update(
        enabled=self._auto_resume_enabled,
        standstill=bool(CS.out.standstill),
        cruise_enabled=bool(CS.out.cruiseState.enabled),
        brake_pressed=bool(CS.out.brakePressed),
        gas_pressed=bool(CS.out.gasPressed),
        bottom_msg=int(bottom_msg or 0),
        lead_present=bool(lead.status),
        lead_drel=float(lead.dRel),
        parking_blocked=parking_blocked,
        cancel=bool(CC.cruiseControl.cancel),
        lead_vrel=float(lead.vRel),
        icc_brake_pt=getattr(CS, "icc_brake_pt", None),
        icc_brake_adas=getattr(CS, "icc_brake_adas", None),
      )
      # Published on the next carStateSP. 0x1cc is not brakePressed.
      CS.auto_resume_obs = self._auto_resume.observation()
      CS.auto_resume_obs["res"] = bool(resume_res)

    # Firmware passes stock PPP frames through while host output is suppressed.
    # Keep suppressing cancellation/HUD/steering until an explicit cruise-main cycle.
    if parking_blocked:
      self.apply_angle_last = CS.out.steeringAngleDeg
      self.angle_filter.x = CS.out.steeringAngleDeg
      self.frame += 1
      return structs.CarControl.Actuators(), []

    actuators = CC.actuators
    hud_control = CC.hudControl
    pcm_cancel_cmd = CC.cruiseControl.cancel

    can_sends = []

    ### STEER ###
    steer_hud_alert = 1 if hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw) else 0

    # At low speeds and at high steering angles, EPS is sensitive to jitter in angle request. Smooth to fix uncomfortable response.
    if CC.latActive:
      self.angle_filter.update_alpha(float(np.interp(CS.out.vEgo, [5, 10, 20], [0.2, 0.1, 0.0])))
      self.angle_filter.update(actuators.steeringAngleDeg)
    else:
      self.angle_filter.x = actuators.steeringAngleDeg

    # windup slower
    self.apply_angle_last = apply_std_steer_angle_limits(self.angle_filter.x, self.apply_angle_last, CS.out.vEgoRaw,
                                                         CS.out.steeringAngleDeg, CC.latActive, CarControllerParams.ANGLE_LIMITS)

    lkas_max_torque = 0
    if CC.latActive:
      # Max torque from driver before EPS will give up and not apply torque
      if not bool(CS.out.steeringPressed):
        lkas_max_torque = CarControllerParams.LKAS_MAX_TORQUE
      else:
        # Scale max torque based on how much torque the driver is applying to the wheel.
        # Start scaling torque at STEER_THRESHOLD down to 0.2. If we don't scale this low, EPS will temp fault from high driver and LKAS torque
        lkas_max_torque = max(
          CarControllerParams.LKAS_MIN_TORQUE,
          CarControllerParams.LKAS_MAX_TORQUE - 0.6 * max(0, abs(CS.out.steeringTorque) - CarControllerParams.STEER_THRESHOLD),
        )

    if self.CP.carFingerprint == CAR.NISSAN_ALTIMA and pcm_cancel_cmd:
      can_sends.append(nissancan.create_acc_cancel_cmd(self.packer, self.car_fingerprint, CS.cruise_throttle_msg))

    if self.CP.carFingerprint != CAR.NISSAN_ALTIMA and self.frame % 2 == 0:
      # Cancel wins over auto-resume. A stock CANCEL already in the copied frame
      # is also left alone by create_cruise_throttle_msg.
      if pcm_cancel_cmd:
        button = "CANCEL_BUTTON"
      elif resume_res:
        button = "RES_BUTTON"
      else:
        button = None
      can_sends.append(create_cruise_throttle_msg(self.packer, self.car_fingerprint, CS.cruise_throttle_msg, self.frame, button))

    can_sends.append(nissancan.create_steering_control(
      self.packer, self.apply_angle_last, self.frame, CC.latActive, lkas_max_torque))

    # Below are the HUD messages. We copy the stock message and modify
    if self.CP.carFingerprint != CAR.NISSAN_ALTIMA:
      if self.frame % 2 == 0:
        can_sends.append(nissancan.create_lkas_hud_msg(self.packer, CS.lkas_hud_msg, CC_SP.mads.enabled, hud_control.leftLaneVisible,
                                                       hud_control.rightLaneVisible, hud_control.leftLaneDepart, hud_control.rightLaneDepart))

      if self.frame % 50 == 0:
        can_sends.append(nissancan.create_lkas_hud_info_msg(
          self.packer, CS.lkas_hud_info_msg, steer_hud_alert
        ))

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.apply_angle_last

    self.frame += 1
    return new_actuators, can_sends
