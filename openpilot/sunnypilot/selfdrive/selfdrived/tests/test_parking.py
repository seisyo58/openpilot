import unittest
from types import SimpleNamespace

from openpilot.cereal import log, custom
from openpilot.sunnypilot.selfdrive.selfdrived.parking import apply_parking_interlock


class MockSM(dict):
  healthy = True

  def all_checks(self, services):
    return self.healthy


class TestParkingEngagement(unittest.TestCase):
  def test_disables_all_mads_states_and_blocks_resume(self):
    states = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState.schema.enumerants
    for state in states:
      with self.subTest(state=state):
        sm = MockSM(carStateSP=SimpleNamespace(nissanParkingActive=True, nissanParkingBlocked=True))
        sd = SimpleNamespace(CP=SimpleNamespace(brand='nissan'), CP_SP=SimpleNamespace(safetyParam=3), sm=sm,
                             events_sp=set(), enabled=True, active=True, enabled_prev=True,
                             state_machine=SimpleNamespace(state='enabled', soft_disable_timer=100, current_alert_types=[]),
                             mads=SimpleNamespace(enabled=True, active=True, state_machine=SimpleNamespace(state=state)))
        self.assertTrue(apply_parking_interlock(sd))
        self.assertFalse(sd.enabled or sd.active or sd.mads.enabled or sd.mads.active or sd.enabled_prev)
        self.assertEqual(sd.mads.state_machine.state, custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState.disabled)
        self.assertEqual(sd.state_machine.state, log.SelfdriveState.OpenpilotState.disabled)
        self.assertIn('userDisable', sd.state_machine.current_alert_types)
        sm['carStateSP'].nissanParkingActive = False
        self.assertTrue(apply_parking_interlock(sd))
        self.assertNotIn('userDisable', sd.state_machine.current_alert_types)
        sm['carStateSP'].nissanParkingBlocked = False
        sm.healthy = False
        self.assertTrue(apply_parking_interlock(sd))
        sm.healthy = True
        self.assertFalse(apply_parking_interlock(sd))
        self.assertFalse(sd.enabled or sd.mads.enabled)

  def test_other_cars_unchanged(self):
    for brand, param in [('nissan', 1), ('toyota', 3)]:
      sd = SimpleNamespace(CP=SimpleNamespace(brand=brand), CP_SP=SimpleNamespace(safetyParam=param))
      self.assertFalse(apply_parking_interlock(sd))


if __name__ == '__main__':
  unittest.main()
