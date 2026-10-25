"""Experimental Leaf IC PPP interlock. Never infer parking completion from lost CAN."""
from opendbc.car import DT_CTRL
from opendbc.car.nissan.nissancan import nissan_checksum


class NissanParkingInterlock:
  TIMEOUT_NS = 100_000_000  # LKAS is 100 Hz

  def __init__(self):
    self.now = 0
    self.last_valid = None
    self.counter = None
    self.active = False
    self.blocked = True
    self.rearm_required = False
    self.main_off_seen = False
    self.main_on = False
    self.cruise_on = False

  def fresh(self):
    return self.last_valid is not None and 0 <= self.now - self.last_valid <= self.TIMEOUT_NS

  def fault(self):
    self.blocked = True
    self.rearm_required = True
    self.main_off_seen = False

  def update(self, packets):
    # Advance even if the CAN socket supplies no messages.
    self.now = max((t for t, _ in packets), default=self.now + int(DT_CTRL * 1e9))
    if not self.fresh():
      self.blocked = True
      if self.last_valid is not None:
        self.fault()

    for t, frames in packets:
      for addr, dat, bus in frames:
        if (addr, bus) == (0x169, 2):
          if len(dat) != 8 or nissan_checksum(dat[:7]) != dat[7]:
            self.last_valid = None
            self.fault()
            continue
          counter = dat[6] & 0xf
          # The recorded bus occasionally drops one frame. Accept at most two
          # missed frames, but reject repeats/backwards jumps (CRC alone is not liveness).
          counter_ok = self.counter is None or 1 <= (counter - self.counter) % 16 <= 3
          self.counter = counter
          if not counter_ok:
            self.last_valid = None
            self.fault()
            continue
          self.last_valid = t
          self.active = bool(dat[6] & 0x20)
          if self.active:
            self.fault()
          self.blocked = self.active or self.rearm_required or not self.fresh()
        elif (addr, bus, len(dat)) == (0x30f, 2, 3):
          self.cruise_on = bool(dat[0] & 8)
        elif (addr, bus, len(dat)) == (0x239, 0, 8):
          main_on = bool(dat[2] & 2)
          if self.fresh() and not self.active and not self.cruise_on:
            if not main_on:
              self.main_off_seen = True
            elif not self.main_on and self.main_off_seen:
              self.rearm_required = False
              self.blocked = False
              self.main_off_seen = False
          self.main_on = main_on
