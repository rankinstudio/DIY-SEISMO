#!/usr/bin/env python3
"""Plot a stretch of the on-disk log: band-passed trace and spectrogram.

    python geoplot.py 2026-09-12                      # the whole UTC day
    python geoplot.py 2026-09-12 14:00 15:30          # a UTC window
    python geoplot.py 2026-09-12 14:00 15:30 --band 10 30
    python geoplot.py ... --save out.png

Reads the miniSEED (or CSV) files geocapture.py wrote under logDir.
"""
import argparse
import glob
import gzip
import os
import sys
from datetime import datetime, timezone

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy import signal
from scipy.ndimage import uniform_filter1d

from loadOpts import load_config

opts = load_config()


def load_day(day):
    """obspy Stream (merged) for a UTC day from whatever format is on disk."""
    from obspy import Stream, Trace, UTCDateTime, read
    folder = os.path.join(opts['logDir'], day[:4], day)
    ms = sorted(glob.glob(os.path.join(folder, '*.mseed')))
    if ms:
        st = Stream()
        for f in ms:
            st += read(f)
        return merge_log(st)
    csvs = sorted(glob.glob(os.path.join(folder, '*.csv.gz')))
    if not csvs:
        sys.exit("nothing under %s" % folder)
    st = Stream()
    for f in csvs:
        with gzip.open(f, 'rt') as fh:
            a = np.loadtxt(fh, delimiter=',', comments='#')
        if a.size == 0:
            continue
        t, v = a[:, 0], a[:, 1].astype(np.float32)
        fs = (t.size - 1) / (t[-1] - t[0])
        tr = Trace(data=v)
        tr.stats.starttime = UTCDateTime(t[0])
        tr.stats.sampling_rate = fs
        tr.stats.network, tr.stats.station, tr.stats.channel = opts['network'], opts['station'], opts['channel']
        st += tr
    return merge_log(st)


def merge_log(st):
    """Merge what geocapture.py wrote into one trace per segment.

    Every trace carries the rate the capture had refined by the time it was
    flushed -- 161.807, 161.787, 161.779 ... -- and obspy will not merge traces
    whose rates differ at all. Within a segment each trace's start time is fitted
    to the wall clock, so putting them all on the median rate moves a sample by at
    most a few ms at the end of a 60 s trace, and the merge rounds the joins to
    whole samples. Only a real gap or overlap (a new segment) survives, and
    method=1 interpolates across it."""
    if not st:
        return st
    fs = float(np.median([tr.stats.sampling_rate for tr in st]))
    for tr in st:
        tr.stats.sampling_rate = fs
    return st.merge(method=1, fill_value='interpolate')


def band(tr, lo, hi):
    """Zero-phase Butterworth; lo <= 0 is a plain low-pass, as in geoclient.py."""
    fs = tr.stats.sampling_rate
    hi = min(hi, 0.45 * fs)
    if lo <= 0:
        sos = signal.butter(4, hi, btype='low', fs=fs, output='sos')
    else:
        sos = signal.butter(4, [lo, hi], btype='band', fs=fs, output='sos')
    return signal.sosfiltfilt(sos, tr.data.astype(np.float64))


def spectrogram_db(x, fs):
    """(f, t, dB) for the display: Hann window of specWindow samples, a column every
    specHopSec, then an optional running average over specSmoothSec of columns.
    A single periodogram cell is a chi-squared estimate with 2 degrees of freedom,
    so without averaging a spectrogram is speckle; averaging adjacent columns trades
    time resolution for a picture where a real event stands out from the noise."""
    nper = opts['specWindow']
    hop = min(nper - 1, max(1, int(round(opts['specHopSec'] * fs))))
    hp = max(opts['specMinHz'], opts.get('specHighpassHz', 0))
    if hp > 0:
        # a 2-pole high-pass before the FFT. Two jobs: with specHighpassHz = 0.5 the
        # low rows fade out instead of the flat-to-0.31 Hz 1/f floor owning the
        # palette; and when specMinHz hides rows,
        # the Hann main lobe is two bins wide, so sub-corner energy would otherwise leak
        # up through the bottom rows shown
        sos = signal.butter(2, hp, btype='high', fs=fs, output='sos')
        x = signal.sosfiltfilt(sos, x)
    # zero-padding does not add resolution, it interpolates between the real bins so a
    # bin is drawn as a smooth bump instead of a hard block
    f, ts, S = signal.spectrogram(x, fs=fs, window='hann', nperseg=nper, nfft=nper * max(1, opts['specPad']),
                                  noverlap=nper - hop, detrend='constant', scaling='spectrum')
    db = 10 * np.log10(S + 1e-12)
    k = int(round(opts['specSmoothSec'] * fs / hop))
    if k > 1 and db.shape[1] > k:
        db = uniform_filter1d(db, k, axis=1, mode='nearest')
    if opts['specWhiten']:
        db = db - np.median(db, axis=1, keepdims=True)
    if opts['specScale'] == 'root':
        # rsudp's look: power**0.1. Convex in dB, so the +-5 dB scatter of a noise cell
        # takes a tenth of the palette instead of half, and only loud cells reach yellow.
        db = 10 ** (db / 100)
    return f, ts, db


def spec_band(fs):
    """(lo, hi) Hz actually shown on the spectrogram axis."""
    return opts['specMinHz'], min(opts['specMaxHz'], fs / 2)


def spec_range(f, dbs, fs):
    """(vmin, vmax) from specRangePct, taken over the displayed band only -- bins
    above specMaxHz (or the DC bins below specMinHz) must not drag the palette."""
    lo, hi = spec_band(fs)
    keep = (f >= lo) & (f <= hi)
    vals = np.concatenate([d[keep].ravel() for d in dbs])
    p_lo, p_hi = opts['specRangePct']
    return np.percentile(vals, p_lo), np.percentile(vals, p_hi)


def spec_extent(f, ts):
    """imshow extent so each cell is centred on its (time, frequency), in seconds."""
    dt = ts[1] - ts[0] if ts.size > 1 else 1.0
    df = f[1] - f[0]
    return ts[0] - dt / 2, ts[-1] + dt / 2, f[0] - df / 2, f[-1] + df / 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('day', help='UTC date, YYYY-MM-DD')
    ap.add_argument('start', nargs='?', help='HH:MM UTC')
    ap.add_argument('end', nargs='?', help='HH:MM UTC')
    ap.add_argument('--band', nargs=2, type=float, default=[opts['cutLow'], opts['cutHigh']],
                    metavar=('LO', 'HI'))
    ap.add_argument('--save', help='write PNG instead of showing')
    args = ap.parse_args()
    from obspy import UTCDateTime, read

    day = args.day.replace('-', '')
    st = load_day(day)
    t1 = UTCDateTime("%sT%s" % (args.day, args.start or "00:00"))
    t2 = UTCDateTime("%sT%s" % (args.day, args.end or "23:59:59"))
    st.trim(t1, t2)
    if not st or st[0].stats.npts < 10:
        sys.exit("no samples in that window")
    tr = st[0]
    lo, hi = args.band
    y = band(tr, lo, hi)
    tt = tr.times('matplotlib')
    fs = tr.stats.sampling_rate

    n_ax = 2
    plt.style.use('dark_background')
    fig, axes = plt.subplots(n_ax, 1, figsize=(opts['figW'], opts['figH']),
                             sharex=True)
    ax_tr, ax_sp = axes[0], axes[1]
    ax_tr.plot(tt, y, lw=opts['plotLineW'], color='#DC9257')
    ax_tr.set_ylabel("nm/s")
    ax_tr.set_title("%s  %g–%g Hz  %.2f SPS  %s to %s UTC" % (
        tr.id, lo, hi, fs, t1.strftime('%Y-%m-%d %H:%M'), t2.strftime('%H:%M')),
        fontsize=opts['titleFontSize'])
    ax_tr.margins(0, 0.05)

    f, ts, db = spectrogram_db(tr.data.astype(np.float64), fs)
    vmin, vmax = spec_range(f, [db], fs)
    x0, x1, f0, f1 = spec_extent(f, ts)
    ax_sp.imshow(db, cmap='inferno', vmin=vmin, vmax=vmax, aspect='auto', origin='lower',
                 interpolation='antialiased',
                 extent=(tt[0] + x0 / 86400.0, tt[0] + x1 / 86400.0, f0, f1))
    ax_sp.set_ylim(*spec_band(fs))
    ax_sp.set_ylabel("Hz")

    loc = mdates.AutoDateLocator(minticks=6, maxticks=14)
    axes[-1].xaxis.set_major_locator(loc)
    axes[-1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    fig.tight_layout(pad=1)
    if args.save:
        fig.savefig(args.save, dpi=120)
        print("wrote", args.save)
    else:
        plt.show()


if __name__ == '__main__':
    main()
