#!/usr/bin/env python3
import argparse
import glob
import json
import os
import sys

from openpilot.tools.lib.logreader import LogReader

REALDATA = "/data/media/0/realdata"


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


def compute_Vinv(dat):
    return (~(((dat[2] >> 4) << 8) | dat[0])) & 0xFFF


def compute_q(dat):
    return 4 * ((dat[2] >> 4) * 256 + ((~dat[0]) & 0xFF)) + ((dat[7] >> 2) & 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", required=True,
                    help="comma-separated route hashes")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    routes = [r.strip() for r in args.routes.split(",") if r.strip()]

    frames_total = 0
    v_mismatch = 0
    dV_all = []
    dq_all = []
    wrap_examples = []
    wrap_count = 0
    wrap_dV_sum = 0
    wrap_dq_sum = 0
    v_at_2518 = {}
    v_idle_counts = {}

    for route in routes:
        for p in find_segments(route):
            name = seg_name(p)
            seg_frames = []
            try:
                for evt in LogReader(p):
                    if evt.which() != "can":
                        continue
                    for cd in evt.can:
                        if (cd.address == 0x2B0 and cd.src == 1
                                and len(cd.dat) == 8):
                            seg_frames.append((evt.logMonoTime,
                                              bytes(cd.dat)))
            except Exception as e:
                warn("skipping corrupt segment %s: %s" % (name, e))
                continue

            seg_frames.sort(key=lambda x: x[0])

            prev = None
            for (t, dat) in seg_frames:
                frames_total += 1
                V = compute_V(dat)
                Vinv = compute_Vinv(dat)
                if V != Vinv:
                    v_mismatch += 1
                q = compute_q(dat)

                if (dat[0] == 0xa8 and dat[1] == 0x27 and dat[2] == 0xd5
                        and dat[4] == 0x5b and dat[7] == 0x09):
                    v_at_2518[V] = v_at_2518.get(V, 0) + 1

                if dat[0] == 0xdc and dat[1] == 0x53 and dat[2] == 0xa2:
                    v_idle_counts[V] = v_idle_counts.get(V, 0) + 1

                if prev is not None:
                    pdat = prev[1]
                    dV = abs(V - prev[2])
                    dq = abs(q - prev[3])
                    dV_all.append(dV)
                    dq_all.append(dq)

                    inv_prev = (~pdat[0]) & 0xFF
                    inv_cur = (~dat[0]) & 0xFF
                    cross = ((inv_prev <= 0x3F and inv_cur >= 0xC0)
                             or (inv_prev >= 0xC0 and inv_cur <= 0x3F))
                    if cross:
                        wrap_count += 1
                        wrap_dV_sum += dV
                        wrap_dq_sum += dq
                        if len(wrap_examples) < 10:
                            wrap_examples.append({
                                "prev_hex": hex_str(pdat),
                                "hex": hex_str(dat),
                                "dV": dV,
                                "dq": dq,
                            })

                prev = (t, dat, V, q)

    v_idle_neutral = None
    if v_idle_counts:
        v_idle_neutral = max(v_idle_counts.items(), key=lambda kv: kv[1])
        v_idle_neutral = {"V": v_idle_neutral[0], "count": v_idle_neutral[1]}

    v_at_2518_sorted = dict(sorted(v_at_2518.items(),
                                  key=lambda kv: -kv[1]))

    out = {
        "routes": routes,
        "frames_2b0_total": frames_total,
        "v_mismatch": v_mismatch,
        "V_steps": step_stats(dV_all),
        "q_steps": step_stats(dq_all),
        "wrap_steps": {
            "count": wrap_count,
            "mean_abs_dV": round(wrap_dV_sum / wrap_count, 4)
                           if wrap_count else 0.0,
            "mean_abs_dq": round(wrap_dq_sum / wrap_count, 4)
                           if wrap_count else 0.0,
            "examples": wrap_examples,
        },
        "v_at_2518": v_at_2518_sorted,
        "v_idle_neutral": v_idle_neutral,
    }

    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)

    print("routes: %s" % ", ".join(routes))
    print("frames_2b0_total: %d" % frames_total)
    print("v_mismatch: %d" % v_mismatch)
    print("V_steps: %s" % out["V_steps"])
    print("q_steps: %s" % out["q_steps"])
    print("wrap_steps: count=%d mean|dV|=%.3f mean|dq|=%.3f (examples=%d)"
          % (wrap_count,
             out["wrap_steps"]["mean_abs_dV"],
             out["wrap_steps"]["mean_abs_dq"],
             len(wrap_examples)))
    print("v_at_2518 distinct: %d" % len(v_at_2518_sorted))
    print("v_idle_neutral: %s" % v_idle_neutral)


if __name__ == "__main__":
    main()
