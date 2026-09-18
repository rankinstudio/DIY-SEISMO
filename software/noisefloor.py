#!/usr/bin/env python3
"""Spectrum of a shorted-input recording against the design noise budget.

    python noisefloor.py 2026-09-14T05:56 2026-09-14T06:09 --ref 2026-09-14T04:05 2026-09-14T04:46 --save floor.png

The window is a stretch logged with a resistor across J1 in place of the geophone,
so what is recorded is the electronics alone: amplifier, board, supply, ADC,
temperature. It is plotted in the same nm/s per sqrt(Hz) as everything else, i.e.
referred to ground velocity through the sensor's response, so it reads directly as
"the floor below which this board cannot see". Against it: the budget from the
board's design values (coil-and-shunt Johnson noise, AD797 voltage and current noise,
R4), the sensor's own Brownian noise, and optionally a --ref window recorded with
the geophone connected.
"""
import argparse
import glob
import math
import os
import sys

import numpy as np
import matplotlib.pyplot as plt
from scipy import signal

from loadOpts import load_config
from geoplot import load_day, merge_log

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'hardware'))
import geophone_response as GR

opts = load_config()
KB = 1.380649e-23


def load_ours(t1, t2):
    from obspy import Stream
    st = Stream()
    day = t1
    while day < t2 + 86400:
        try:
            st += load_day(day.strftime('%Y%m%d'))
        except SystemExit:
            pass
        day += 86400
    st = merge_log(st).trim(t1, t2)
    if not st:
        sys.exit("no data in %s .. %s" % (t1, t2))
    return st[0]


def spectrum(tr, seg_s=60):
    fs = tr.stats.sampling_rate
    nper = int(seg_s * fs)
    f, P = signal.welch(tr.data.astype(float), fs=fs, nperseg=nper, noverlap=nper // 2, detrend='linear')
    return f, np.sqrt(P)


def budget(f, r_src=375.0):
    """Electronics floor in nm/s/rtHz: white input noise through the sensor response.
    AD797 0.9 nV/rtHz voltage, 2 pA/rtHz current into the source, R4 = 301 ohm."""
    r_par = r_src * GR.R_SHUNT / (r_src + GR.R_SHUNT)
    e_n = math.sqrt(4 * KB * 300 * r_par + (0.9e-9) ** 2 + (2e-12 * r_par) ** 2 + 4 * KB * 300 * 301.0)
    div = GR.R_SHUNT / (GR.RCOIL + GR.R_SHUNT)
    return np.array([e_n / (abs(GR.sensor_response(x)) * div) for x in f]) * 1e9


# The design's own floor, from the simulated budget (AD797 1/f included).
# budget() above is the white-noise-only version and reads low below 2 Hz.
DESIGN_F = np.array([0.3, 0.5, 1, 2, 5, 10, 40])
DESIGN_A = np.array([102, 36.6, 8.7, 2.6, 0.8, 0.3, 0.1])


def design_floor(f):
    return 10 ** np.interp(np.log10(f), np.log10(DESIGN_F), np.log10(DESIGN_A))


def brownian(f):
    """Thermal noise of the suspended mass, as ground velocity, nm/s/rtHz."""
    z, w0 = GR.damping(GR.R_SHUNT), 2 * math.pi * GR.F0
    a = math.sqrt(4 * KB * 300 * 2 * z * w0 / GR.MASS)     # m/s^2/rtHz, white
    return a / (2 * math.pi * f) * 1e9


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('start'); ap.add_argument('end')
    ap.add_argument('--ref', nargs=2, metavar=('START', 'END'), help='a window recorded with the geophone on')
    ap.add_argument('--rsrc', type=float, default=330.0, help='resistor across J1 during the test, ohm')
    ap.add_argument('--save')
    args = ap.parse_args()
    from obspy import UTCDateTime

    tr = load_ours(UTCDateTime(args.start), UTCDateTime(args.end))
    f, a = spectrum(tr)
    keep = (f >= 0.1) & (f <= 45)
    f, a = f[keep], a[keep]
    b = design_floor(f)
    print("shorted input (%g ohm), %s -> %s, %.0f s" % (args.rsrc, tr.stats.starttime, tr.stats.endtime,
                                                         tr.stats.endtime - tr.stats.starttime))
    print("   Hz    measured   design   ratio   nm/s/rtHz   (design = simulated floor, with 1/f)")
    for x in (0.3, 0.5, 0.7, 1, 1.5, 2, 3, 5, 10, 20, 30):
        i = np.argmin(abs(f - x))
        print("  %4g   %7.1f   %7.2f   %5.1f" % (x, a[i], b[i], a[i] / b[i]))

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(opts['figW'], opts['figH'] * 0.8))
    ax.loglog(f, a, color='#9CE29C', lw=1.0, label="measured, %g ohm across J1" % args.rsrc)
    ax.loglog(f, b, color='#9CE29C', lw=1.0, ls='--', label="design floor (simulated, with 1/f)")
    ax.loglog(f, budget(f, args.rsrc), color='#9CE29C', lw=0.6, ls=':', alpha=0.6, label="white-noise part only")
    ax.loglog(f, brownian(f), color='white', lw=0.7, ls=':', alpha=0.6, label="sensor Brownian noise (with geophone)")
    if args.ref:
        r1, r2 = UTCDateTime(args.ref[0]), UTCDateTime(args.ref[1])
        fr, ar = spectrum(load_ours(r1, r2))
        k = (fr >= 0.1) & (fr <= 45)
        ax.loglog(fr[k], ar[k], color='#DC9257', lw=0.8, alpha=0.9, label="geophone on, %s-%s" % (r1.strftime('%H:%M'), r2.strftime('%H:%M')))
    ax.set_xlabel("Hz"); ax.set_ylabel("nm/s per √Hz, referred to ground velocity")
    ax.set_title("electronics noise floor with the input shorted", fontsize=opts['titleFontSize'])
    ax.grid(True, which='both', alpha=0.2)
    ax.legend(fontsize=8)
    fig.tight_layout(pad=1)
    if args.save:
        fig.savefig(args.save, dpi=120); print("wrote", args.save)
    else:
        plt.show()


if __name__ == '__main__':
    main()
