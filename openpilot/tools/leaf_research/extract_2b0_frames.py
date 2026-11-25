#!/usr/bin/env python3
import argparse
import bisect
import glob
import json
import os
import sys
from collections import deque

from openpilot.tools.lib.logreader import LogReader

REALDATA = "/data/media/0/realdata"
NS_200MS = 200_000_000
RUN_CAP = 200


def warn(msg):
    sys.stderr.write("warning: %s\n" % msg)
    sys.stderr.flush()


def find_segments(route):
    paths = glob.glob(os.path.join(REALDATA, "*--%s--*" % route, "rlog.zst"))
    def segidx(p):
        d = os.path.basename(os.path.dirname(p))
        try:
            return int(d.rsplit("--", 1)[-1])
        except ValueError:
            return 0
    return sorted(paths, key=segidx)


def seg_name(p):
    return os.path.basename(os.path.dirname(p))


def hex_str(dat):
    return " ".join("%02x" % b for b in dat)


def gear_str(cs):
    try:
        return str(cs.gearShifter)
    except Exception:
        return None


def make_entry(seg, t_s, dat, cs):
    if cs is not None:
        return {
            "segment": seg,
            "t_s": t_s,
            "hex": hex_str(dat),
            "vEgo": float(cs.vEgo),
            "aEgo": float(cs.aEgo),
            "cruise_enabled": bool(cs.cruiseState.enabled),
            "gear": gear_str(cs),
        }
    return {
        "segment": seg,
        "t_s": t_s,
        "hex": hex_str(dat),
        "vEgo": None,
        "aEgo": None,
        "cruise_enabled": None,
        "gear": None,
    }


def nearest_cs(t_f, cs_times, cs_list):
    if not cs_times:
        return None
    j = bisect.bisect_right(cs_times, t_f) - 1
    if j >= 0:
        if t_f - cs_times[j] <= NS_200MS:
            return cs_list[j]
        if j + 1 < len(cs_times) and cs_times[j + 1] - t_f <= NS_200MS:
            return cs_list[j + 1]
        return None
    if cs_times[0] - t_f <= NS_200MS:
        return cs_list[0]
    return None


def pct(stats):
    total = (stats["bit1_engaged"] + stats["bit1_not_engaged"]
             + stats["bit0_engaged"] + stats["bit0_not_engaged"])
    if total == 0:
        return 0.0
    agree = stats["bit1_engaged"] + stats["bit0_not_engaged"]
    return round(100.0 * agree / total, 3)


def cond_idle(cs):
    return (cs is not None
            and gear_str(cs) == "park"
            and abs(float(cs.vEgo)) < 0.05
            and not bool(cs.gasPressed)
            and not bool(cs.cruiseState.enabled))


def cond_light(cs):
    return (cs is not None
            and 0.2 <= float(cs.aEgo) <= 1.2
            and float(cs.vEgo) > 0.5
            and bool(cs.gasPressed)
            and not bool(cs.cruiseState.enabled))


def cond_icc(cs):
    return (cs is not None
            and bool(cs.cruiseState.enabled)
            and float(cs.aEgo) <= -0.4
            and not bool(cs.brakePressed))


class Detector:
    def __init__(self):
        self.frames_2b0_total = 0
        self.segments_scanned = 0
        self.idle = {"done": False, "buf": [], "sample": None}
        self.light = {"done": False, "buf": [], "sample": None}
        self.icc = {"done": False, "buf": [], "sample": None}
        self.pat_count = 0
        self.pat_examples = []
        self.stats_en = {"bit1_engaged": 0, "bit1_not_engaged": 0,
                         "bit0_engaged": 0, "bit0_not_engaged": 0}
        self.stats_av = {"bit1_engaged": 0, "bit1_not_engaged": 0,
                         "bit0_engaged": 0, "bit0_not_engaged": 0}
        self.deque = deque(maxlen=16)
        self.prev_inv = None
        self.overall_wrap = None
        self.cruise_wrap = None
        self.light_icc = {"done": False, "buf": [], "sample": None}
        self.bit0_engaged_samples = []
        self.bit0_engaged_seg = None
        self.bit0_engaged_taken = False
        self.bit1_notengaged = {"count": 0, "by_gear": {}, "examples": []}
        self.bit_by_speed = {"engaged_moving": {"bit1": 0, "bit0": 0},
                             "engaged_stopped": {"bit1": 0, "bit0": 0}}

    def feed(self, entry, dat, cs):
        self.frames_2b0_total += 1

        if entry["segment"] != self.bit0_engaged_seg:
            self.bit0_engaged_seg = entry["segment"]
            self.bit0_engaged_taken = False

        if (dat[0] == 0xa8 and dat[1] == 0x27 and dat[2] == 0xd5
                and dat[3] == 0xac and dat[4] == 0x5b and dat[5] == 0x00
                and (dat[6] & 0x0F) == 0x0E and dat[7] == 0x09):
            self.pat_count += 1
            if len(self.pat_examples) < 3:
                self.pat_examples.append(entry)

        if cs is not None:
            bit = (dat[4] & 0x40) != 0
            en = bool(cs.cruiseState.enabled)
            av = bool(cs.cruiseState.available)
            if bit and en:
                self.stats_en["bit1_engaged"] += 1
            elif bit and not en:
                self.stats_en["bit1_not_engaged"] += 1
            elif not bit and en:
                self.stats_en["bit0_engaged"] += 1
            else:
                self.stats_en["bit0_not_engaged"] += 1
            if bit and av:
                self.stats_av["bit1_engaged"] += 1
            elif bit and not av:
                self.stats_av["bit1_not_engaged"] += 1
            elif not bit and av:
                self.stats_av["bit0_engaged"] += 1
            else:
                self.stats_av["bit0_not_engaged"] += 1

            if en:
                vEgo = float(cs.vEgo)
                bucket = ("engaged_moving" if vEgo > 0.5
                          else "engaged_stopped")
                if bit:
                    self.bit_by_speed[bucket]["bit1"] += 1
                else:
                    self.bit_by_speed[bucket]["bit0"] += 1

            if bit and not en:
                self.bit1_notengaged["count"] += 1
                g = gear_str(cs)
                self.bit1_notengaged["by_gear"][g] = \
                    self.bit1_notengaged["by_gear"].get(g, 0) + 1
                if len(self.bit1_notengaged["examples"]) < 3:
                    self.bit1_notengaged["examples"].append(entry)

            if en and not bit and not self.bit0_engaged_taken \
                    and len(self.bit0_engaged_samples) < 6:
                self.bit0_engaged_samples.append(entry)
                self.bit0_engaged_taken = True

        self._run(self.idle, cond_idle, entry, cs, True)
        self._run(self.light, cond_light, entry, cs, False)
        self._run(self.icc, cond_icc, entry, cs, False)
        self._run_light_icc(entry, dat, cs)
        self._disc(entry, dat)

    def _run(self, state, cond, entry, cs, idle_mode):
        if state["done"]:
            return
        if cond(cs):
            state["buf"].append(entry)
            if len(state["buf"]) >= RUN_CAP:
                self._capture(state, idle_mode)
        else:
            if len(state["buf"]) >= 10:
                self._capture(state, idle_mode)
            else:
                state["buf"] = []

    def _capture(self, state, idle_mode):
        buf = state["buf"]
        if idle_mode:
            sample = buf[0:5]
        else:
            mid = len(buf) // 2
            start = mid - 2
            if start < 0:
                start = 0
            sample = buf[start: start + 5]
        state["sample"] = sample
        state["done"] = True
        state["buf"] = []

    def _run_light_icc(self, entry, dat, cs):
        state = self.light_icc
        if state["done"]:
            return
        cond = (cs is not None
                and bool(cs.cruiseState.enabled)
                and 0.2 <= float(cs.aEgo) <= 1.0
                and float(cs.vEgo) > 3.0)
        if cond:
            state["buf"].append((entry, dat[0]))
            if len(state["buf"]) >= RUN_CAP:
                if len(set(b for _, b in state["buf"])) >= 2:
                    self._capture_light_icc(state)
                else:
                    state["buf"] = []
        else:
            if (len(state["buf"]) >= 10
                    and len(set(b for _, b in state["buf"])) >= 2):
                self._capture_light_icc(state)
            else:
                state["buf"] = []

    def _capture_light_icc(self, state):
        entries = [e for e, _ in state["buf"]]
        mid = len(entries) // 2
        start = mid - 2
        if start < 0:
            start = 0
        state["sample"] = entries[start: start + 5]
        state["done"] = True
        state["buf"] = []

    def _disc(self, entry, dat):
        inv = (~dat[0]) & 0xFF
        new_overall = False
        new_cruise = False
        if self.prev_inv is not None:
            wrap = ((self.prev_inv >= 0xC0 and inv <= 0x3F)
                    or (self.prev_inv <= 0x3F and inv >= 0xC0))
            if wrap:
                pre = [e for e, _ in list(self.deque)[-5:]] + [entry]
                cruise_now = entry["cruise_enabled"] is True
                if self.overall_wrap is None:
                    self.overall_wrap = {"frames": list(pre), "post": [],
                                         "complete": False}
                    new_overall = True
                if self.cruise_wrap is None and cruise_now:
                    self.cruise_wrap = {"frames": list(pre), "post": [],
                                        "complete": False}
                    new_cruise = True
        for w, new in ((self.overall_wrap, new_overall),
                       (self.cruise_wrap, new_cruise)):
            if w is not None and not new and not w["complete"]:
                w["post"].append(entry)
                if len(w["post"]) >= 5:
                    w["complete"] = True
        self.deque.append((entry, inv))
        self.prev_inv = inv

    def finalize_disc(self):
        chosen = self.cruise_wrap
        if chosen is None:
            chosen = self.overall_wrap
        if chosen is None:
            return {"found": False, "frames": [],
                    "note": "no inv wrap found"}
        frames = chosen["frames"] + chosen["post"]
        return {"found": True, "frames": frames, "note": None}

    def finalize_runs(self):
        for state, idle_mode in ((self.idle, True), (self.light, False),
                                 (self.icc, False)):
            if not state["done"] and len(state["buf"]) >= 10:
                self._capture(state, idle_mode)
        state = self.light_icc
        if not state["done"] and len(state["buf"]) >= 10 \
                and len(set(b for _, b in state["buf"])) >= 2:
            self._capture_light_icc(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    det = Detector()
    carry_cs = None

    for route in args.routes:
        for p in find_segments(route):
            name = seg_name(p)
            cs_times = []
            cs_list = []
            frames = []
            seg_start = None
            try:
                for evt in LogReader(p):
                    if seg_start is None:
                        seg_start = evt.logMonoTime
                    w = evt.which()
                    if w == "carState":
                        cs_times.append(evt.logMonoTime)
                        cs_list.append(evt.carState)
                    elif w == "can":
                        for cd in evt.can:
                            if (cd.address == 0x2B0 and cd.src == 1
                                    and len(cd.dat) == 8):
                                frames.append((evt.logMonoTime, bytes(cd.dat)))
            except Exception as e:
                warn("skipping corrupt segment %s: %s" % (name, e))
                continue

            local_times = []
            local_list = []
            if carry_cs is not None:
                local_times.append(carry_cs[0])
                local_list.append(carry_cs[1])
            local_times.extend(cs_times)
            local_list.extend(cs_list)

            for (t, dat) in frames:
                cs = nearest_cs(t, local_times, local_list)
                t_s = round((t - seg_start) / 1e9, 3)
                entry = make_entry(name, t_s, dat, cs)
                det.feed(entry, dat, cs)

            if cs_list:
                carry_cs = (cs_times[-1], cs_list[-1])
            det.segments_scanned += 1

    det.finalize_runs()
    disc = det.finalize_disc()

    out = {
        "routes": args.routes,
        "segments_scanned": det.segments_scanned,
        "frames_2b0_total": det.frames_2b0_total,
        "idle_ready": det.idle["sample"] or [],
        "light_accel": det.light["sample"] or [],
        "icc_regen": det.icc["sample"] or [],
        "discontinuity": disc,
        "pattern_2518": {"count": det.pat_count, "examples": det.pat_examples},
        "b4_icc_stats": {
            "vs_enabled": {**det.stats_en, "agree_pct": pct(det.stats_en)},
            "vs_available": {**det.stats_av, "agree_pct": pct(det.stats_av)},
        },
        "followup": {
            "light_accel_icc": det.light_icc["sample"] or [],
            "bit0_engaged_samples": det.bit0_engaged_samples,
            "bit1_notengaged": det.bit1_notengaged,
            "bit_by_speed": det.bit_by_speed,
        },
    }

    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)

    print("routes: %s" % ", ".join(args.routes))
    print("segments_scanned: %d" % det.segments_scanned)
    print("frames_2b0_total: %d" % det.frames_2b0_total)
    print("idle_ready: %d" % len(out["idle_ready"]))
    print("light_accel: %d" % len(out["light_accel"]))
    print("icc_regen: %d" % len(out["icc_regen"]))
    print("discontinuity found: %s (%d frames)" % (disc["found"], len(disc["frames"])))
    print("pattern_2518 count: %d (examples: %d)" % (det.pat_count, len(det.pat_examples)))
    print("b4_icc vs_enabled: %s" % out["b4_icc_stats"]["vs_enabled"])
    print("b4_icc vs_available: %s" % out["b4_icc_stats"]["vs_available"])
    print("followup light_accel_icc: %d" % len(out["followup"]["light_accel_icc"]))
    print("followup bit0_engaged_samples: %d" % len(out["followup"]["bit0_engaged_samples"]))
    print("followup bit1_notengaged: %s" % out["followup"]["bit1_notengaged"]["count"])
    print("followup bit_by_speed: %s" % out["followup"]["bit_by_speed"])


if __name__ == "__main__":
    main()
