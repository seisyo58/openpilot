"""0x2B0 (PROPILOT_REQUEST) T decode, freshness and canValid isolation through the real CarInterface path."""
import pytest

from opendbc.can import CANPacker
from opendbc.can.dbc import DBC
from opendbc.car import Bus
from opendbc.car.nissan.interface import CarInterface
from opendbc.car.nissan.values import CAR
from opendbc.sunnypilot.car.nissan.carstate_ext import LEAF_ADAS_REQUEST_MAX_AGE_NS

ADDR = 0x2B0
DT_NS = 10_000_000  # 100 Hz
LEAF_CARS = (CAR.NISSAN_LEAF, CAR.NISSAN_LEAF_IC)
OTHER_NISSANS = tuple(c for c in CAR if c not in LEAF_CARS)


def t_frame(b1, b2, bus=1):
  return ADDR, bytes([0, b1, b2, 0, 0, 0, 0, 0]), bus


class Harness:
  """CarInterface fed valid frames for every message its parsers registered (except 0x2B0)."""
  def __init__(self, car):
    CP = CarInterface.get_non_essential_params(car)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, car)
    self.CI = CarInterface(CP, CP_SP)
    self.packer = CANPacker(self.CI.can_parsers[Bus.pt].dbc_name)
    self.t = 0
    self.step()  # first carstate update lazily registers every message it reads

  def background(self):
    frames = [self.packer.make_can_msg(addr, p.bus, {}) for p in self.CI.can_parsers.values()
              for addr in p.addresses if addr != ADDR]
    # unrelated bus-1 traffic keeps flowing whatever 0x2B0 does
    frames.append(self.packer.make_can_msg("STEER_TORQUE_SENSOR", 1, {}))
    return frames

  def step(self, *extra):
    self.t += DT_NS
    return self.CI.update([(self.t, self.background() + list(extra))])


def test_generated_dbc_defines_0x2b0_without_clash():
  dbc = DBC("nissan_leaf_2018_generated")
  assert dbc.addr_to_msg[ADDR].name == "PROPILOT_REQUEST"
  assert dbc.name_to_msg["PROPILOT_REQUEST"].address == ADDR
  assert len(dbc.addr_to_msg) == len(dbc.name_to_msg)  # no address shared by two messages
  assert set(dbc.addr_to_msg[ADDR].sigs) == {"ADAS_REQUEST_HI", "ADAS_REQUEST_LO"}


@pytest.mark.parametrize("car", LEAF_CARS)
def test_0x2b0_is_registered_ignore_alive(car):
  h = Harness(car)
  cam = h.CI.can_parsers[Bus.cam]
  assert cam.message_states[ADDR].ignore_alive


@pytest.mark.parametrize("b1,b2,expected", [
  (0xA7, 0x9E, 2535),        # (0x9E << 4) | 0x7
  (0x07, 0x9E, 2535),        # high nibble of byte 1 is not part of T
  (0x0F, 0xD5, 0xD5F),       # 3423, near the friction-brake marker
  (0x00, 0x01, 16),
  (0xFF, 0xFF, 0xFFF),
])
def test_decode(b1, b2, expected):
  h = Harness(CAR.NISSAN_LEAF_IC)
  for _ in range(5):
    cs, cs_sp = h.step(t_frame(b1, b2))
  assert cs_sp.leafAdasRequestT == expected
  assert cs.canValid


@pytest.mark.parametrize("car", LEAF_CARS)
def test_missing_0x2b0_keeps_can_valid_and_t_zero(car):
  h = Harness(car)
  for _ in range(600):  # 6 s, far beyond any alive timeout
    cs, cs_sp = h.step()
    assert cs.canValid and not cs.canTimeout
    assert cs_sp.leafAdasRequestT == 0


@pytest.mark.parametrize("car", LEAF_CARS)
def test_0x2b0_stopping_mid_stream_zeroes_t_and_keeps_can_valid(car):
  h = Harness(car)
  for _ in range(300):
    cs, cs_sp = h.step(t_frame(0xA7, 0x9E))
    assert cs.canValid
  assert cs_sp.leafAdasRequestT == 2535

  max_stale_frames = LEAF_ADAS_REQUEST_MAX_AGE_NS // DT_NS
  for i in range(1, 600):
    cs, cs_sp = h.step()
    assert cs.canValid and not cs.canTimeout
    if i <= max_stale_frames:
      assert cs_sp.leafAdasRequestT == 2535  # short gaps are tolerated
    else:
      assert cs_sp.leafAdasRequestT == 0  # stale: never publish the frozen last value

  # and it recovers when 0x2B0 returns
  cs, cs_sp = h.step(t_frame(0x00, 0xA0))
  assert cs.canValid and cs_sp.leafAdasRequestT == 0xA00


@pytest.mark.parametrize("car", OTHER_NISSANS)
def test_non_leaf_does_not_read_or_publish(car):
  h = Harness(car)
  for _ in range(50):
    cs, cs_sp = h.step(t_frame(0xA7, 0x9E, bus=0), t_frame(0xA7, 0x9E, bus=1))
  assert all(ADDR not in p.addresses for p in h.CI.can_parsers.values())
  assert cs_sp.leafAdasRequestT == 0
  assert cs.canValid
