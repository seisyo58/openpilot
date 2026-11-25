#!/usr/bin/env python3
import argparse
import bisect
import glob
import json
import os
import sys

from openpilot.tools.lib.logreader import LogReader

REALDATA = "/data/media/0/realdata"
NS_200MS = 200_000_000


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


def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = q / 100.0 * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac, 3)


def step_stats(steps):
    if not steps:
        return {"count": 0, "mean": 0.0, "p50": 0, "p90": 0,
                "p99": 0, "max": 0}
    s = sorted(steps)
    return {
        "count": len(steps),
        "mean": round(sum(steps) / len(steps), 4),
        "p50": percentile(s, 50),
        "p90": percentile(s, 90),
        "p99": percentile(s, 99),
        "max": max(steps),
    }


def compute_V(dat):
    return ((dat[1] >> 4) << 8) | ((dat[2] & 0xF) << 4) | (dat[1] & 0xF)


def compute_T(dat):
    return ((dat[2] >> 4) << 8) | ((~dat[0]) & 0xFF)


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


def ctx_entry(segment, t_s, prev_dat, dat, dV, dT, cs):
    e = {
        "segment": segment,
        "t_s": t_s,
        "prev_hex": hex_str(prev_dat),
        "hex": hex_str(dat),
        "dV": dV,
        "dT": dT,
    }
    if cs is not None:
        e["vEgo"] = float(cs.vEgo)
        e["aEgo"] = float(cs.aEgo)
        e["cruise_enabled"] = bool(cs.cruiseState.enabled)
    else:
        e["vEgo"] = None
        e["aEgo"] = None
        e["cruise_enabled"] = None
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", required=True,
                    help="comma-separated route hashes")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    routes = [r.strip() for r in args.routes.split(",") if r.strip()]

    frames_total = 0
    dV_all = []
    dT_all = []
    big_v_steps = []
    wrap_context = []
    t_at_2518 = {}
    t_idle_counts = {}

    for route in routes:
        carry_cs = None
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
                                frames.append((evt.logMonoTime,
                                               bytes(cd.dat)))
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

            frames.sort(key=lambda x: x[0])

            prev = None
            for (t, dat) in frames:
                frames_total += 1
                V = compute_V(dat)
                T = compute_T(dat)

                if (dat[0] == 0xa8 and dat[1] == 0x27 and dat[2] == 0xd5
                        and dat[4] == 0x5b and dat[7] == 0x09):
                    t_at_2518[T] = t_at_2518.get(T, 0) + 1

                if dat[0] == 0xdc and dat[1] == 0x53 and dat[2] == 0xa2:
                    t_idle_counts[T] = t_idle_counts.get(T, 0) + 1

                if prev is not None:
                    pt, pdat, pV, pT = prev
                    dV = abs(V - pV)
                    dT = abs(T - pT)
                    dV_all.append(dV)
                    dT_all.append(dT)

                    t_s = round((t - seg_start) / 1e9, 3)
                    cs = nearest_cs(t, local_times, local_list)
                    big_v_steps.append(ctx_entry(name, t_s, pdat, dat,
                                                 dV, dT, cs))

                    prev_hi = pdat[2] >> 4
                    cur_hi = dat[2] >> 4
                    if ((prev_hi == 0x8 and cur_hi == 0x9)
                            or (prev_hi == 0x9 and cur_hi == 0x8)):
                        if len(wrap_context) < 6:
                            wrap_context.append(ctx_entry(name, t_s, pdat,
                                                           dat, dV, dT, cs))

                prev = (t, dat, V, T)

            if cs_list:
                carry_cs = (cs_times[-1], cs_list[-1])

    big_v_steps.sort(key=lambda e: -e["dV"])
    big_v_steps = big_v_steps[:10]

    t_idle_neutral = None
    if t_idle_counts:
        item = max(t_idle_counts.items(), key=lambda kv: kv[1])
        t_idle_neutral = {"T": item[0], "count": item[1]}

    t_at_2518_sorted = dict(sorted(t_at_2518.items(),
                                  key=lambda kv: -kv[1]))

    out = {
        "routes": routes,
        "frames_2b0_total": frames_total,
        "V_steps": step_stats(dV_all),
        "T_steps": step_stats(dT_all),
        "big_v_steps": big_v_steps,
        "wrap_context": wrap_context,
        "t_at_2518": t_at_2518_sorted,
        "t_idle_neutral": t_idle_neutral,
    }

    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)

    print("routes: %s" % ", ".join(routes))
    print("frames_2b0_total: %d" % frames_total)
    print("V_steps: %s" % out["V_steps"])
    print("T_steps: %s" % out["T_steps"])
    print("big_v_steps: %d" % len(big_v_steps))
    print("wrap_context: %d" % len(wrap_context))
    print("t_at_2518 distinct: %d" % len(t_at_2518_sorted))
    print("t_idle_neutral: %s" % t_idle_neutral)


if __name__ == "__main__":
    main()
