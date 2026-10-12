"""Apply the Nissan parking lock before either engagement state machine runs."""
from openpilot.cereal import log, custom
from opendbc.sunnypilot.car.nissan.values import NissanSafetyFlagsSP


def parking_interlock_enabled(CP, CP_SP):
  return CP.brand == "nissan" and bool(CP_SP.safetyParam & NissanSafetyFlagsSP.PPP_INTERLOCK)


def apply_parking_interlock(selfdrive):
  if not parking_interlock_enabled(selfdrive.CP, selfdrive.CP_SP):
    return False

  state = selfdrive.sm['carStateSP']
  healthy = selfdrive.sm.all_checks(['carStateSP'])
  if healthy and not state.nissanParkingBlocked:
    return False

  event = custom.OnroadEventSP.EventName.nissanParkingActive if healthy and state.nissanParkingActive else \
          custom.OnroadEventSP.EventName.nissanParkingRearm
  selfdrive.events_sp.add(event)
  was_engaged = selfdrive.enabled or selfdrive.mads.enabled
  selfdrive.enabled = selfdrive.active = False
  selfdrive.state_machine.state = log.SelfdriveState.OpenpilotState.disabled
  selfdrive.state_machine.soft_disable_timer = 0
  selfdrive.state_machine.current_alert_types = ['permanent'] + (['userDisable'] if was_engaged else [])
  selfdrive.mads.enabled = selfdrive.mads.active = False
  selfdrive.mads.state_machine.state = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState.disabled
  selfdrive.enabled_prev = False
  return True
