#!/usr/bin/env python3
"""Live display for the geocapture stream: band-passed trace over a spectrogram.

    python geoclient.py                 # host from config.JSON (serverIP)
    python geoclient.py 192.168.1.50    # or name it
    python geoclient.py --fresh         # ignore the Pi's replay, start with an empty window

Press c in the plot window to clear it and start from the next block -- after moving
the sensor, say, when the last fifteen minutes are no longer the same ground.

Everything on screen is in nm/s (or um/s when it gets big). The display band defaults
to 0.3-40 Hz, which is the instrument's flat band; set cutLow/cutHigh in config.JSON
for something narrower, e.g. 0.3-2 Hz to watch for teleseisms. An STA/LTA on the
envelope of the filtered trace sits in the title and turns the title red when it
crosses staltaTrigger.
"""
import bisect
import sys
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.animation import FuncAnimation
from scipy import signal
from scipy.ndimage import uniform_filter1d

from loadOpts import load_config
from geostream import connect, read_block

opts = load_config()
FRESH = '--fresh' in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith('--')]
HOST = _args[0] if _args else opts['serverIP']
PORT = opts['serverPort']
PLOT_SEC = float(opts['plotSeconds'])


# ----------------------------------------------------------------- receiver --
class Receiver(threading.Thread):
    """Keeps the last plotSeconds (+ a margin) of blocks. Reconnects forever."""

    def __init__(self):
        super().__init__(daemon=True)
        self.blocks = deque()
        self.lock = threading.Lock()
        self.status = "connecting"
        self.fs = None
        self.received = 0
        self.t_last = None
        self.t_first = None            # wall time of the first sample ever received
        self.t_min = time.time() - 5 if FRESH else 0.0     # blocks older than this are dropped

    def clear(self):
        """Forget everything held; only blocks from now on are kept."""
        with self.lock:
            self.blocks.clear()
            self.t_min = time.time() - 5
        self._log("display cleared")

    def run(self):
        while True:
            s = connect(HOST, PORT, log=self._log)
            self.status = "connected"
            try:
                while True:
                    t0, fs, seg, data = read_block(s)
                    with self.lock:
                        if t0 < self.t_min:
                            continue                    # replayed history we chose not to keep
                        if self.t_first is None:
                            self.t_first = t0
                        # the server replays its history on connect: merge it in time
                        # order and skip blocks we already hold
                        if self.blocks and t0 <= self.blocks[-1][0] + 1e-3:
                            ts = [b[0] for b in self.blocks]
                            k = bisect.bisect_left(ts, t0 - 1e-3)
                            if k < len(ts) and abs(ts[k] - t0) <= 1e-3:
                                continue                    # duplicate
                            self.blocks.insert(k, (t0, fs, seg, data))
                        else:
                            self.blocks.append((t0, fs, seg, data))
                        self.fs = fs
                        self.received += data.size
                        self.t_last = time.time()
                        # drop from the front once we hold more than we plot
                        while (self.blocks[-1][0] - self.blocks[0][0]) > PLOT_SEC + 30:
                            self.blocks.popleft()
            except (OSError, ConnectionError) as e:
                self._log("stream lost: %s" % e)
                self.status = "reconnecting"
                try:
                    s.close()
                except OSError:
                    pass
                time.sleep(2)

    def _log(self, msg):
        print(datetime.now().strftime('%H:%M:%S ') + msg, flush=True)

    def snapshot(self):
        """(t, x, fs) arrays for everything held, or None if too little yet."""
        with self.lock:
            if not self.blocks:
                return None
            blocks = list(self.blocks)
        fs = blocks[-1][1]
        t = np.concatenate([b[0] + np.arange(b[3].size) / b[1] for b in blocks])
        x = np.concatenate([b[3] for b in blocks]).astype(np.float64)
        return t, x, fs


# ------------------------------------------------------------------- display --
def bandpass(x, fs):
    """Zero-phase Butterworth. cutLow of 0 means a plain low-pass -- the stream is
    already DC-blocked at 0.05 Hz on the Pi, so there is nothing below to remove."""
    lo, hi = opts['cutLow'], min(opts['cutHigh'], 0.45 * fs)
    if lo <= 0:
        sos = signal.butter(opts['filterOrder'], hi, btype='low', fs=fs, output='sos')
    else:
        sos = signal.butter(opts['filterOrder'], [lo, hi], btype='band', fs=fs, output='sos')
    return signal.sosfiltfilt(sos, x)


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


def sta_lta(y, fs):
    ns, nl = int(opts['STA'] * fs), int(opts['LTA'] * fs)
    if y.size < nl:
        return None
    env = np.abs(y)
    sta = env[-ns:].mean()
    lta = env[-nl:].mean()
    return sta / lta if lta > 0 else None


def unit_for(peak_nms):
    return (1e-3, "µm/s") if peak_nms >= 5000 else (1.0, "nm/s")


plt.style.use('dark_background')
fig, (ax_tr, ax_sp) = plt.subplots(2, 1, figsize=(opts['figW'], opts['figH']), sharex=True)
fig.canvas.manager.set_window_title("geoclient  %s:%d" % (HOST, PORT))
rx = Receiver()
rx.start()
_state = {"laid_out": False, "last_trigger": 0.0}


def on_key(ev):
    if ev.key == 'c':
        rx.clear()
        ax_tr.clear()
        ax_sp.clear()
        ax_tr.set_title("cleared -- waiting for data", fontsize=opts['titleFontSize'])
        fig.canvas.draw_idle()


fig.canvas.mpl_connect('key_press_event', on_key)


def envelope(t, y, n):
    """Min and max of every bucket of y, n buckets: a trace many times longer than the
    axis is wide draws as ~2n points and still shows every spike at full height."""
    m = y.size // n
    if m < 2:
        return t, y
    k = n * m
    yy = y[:k].reshape(n, m)
    base = np.arange(n) * m
    idx = np.sort(np.concatenate([base + yy.argmin(axis=1), base + yy.argmax(axis=1),
                                  np.arange(k, y.size)]))
    return t[idx], y[idx]


def runs_of(t, x, fs):
    """Split (t, x) into contiguous runs at any gap wider than 1.5 samples."""
    if t.size < 2:
        return [(t, x)]
    cut = np.flatnonzero(np.diff(t) > 1.5 / fs) + 1
    return [(tt, xx) for tt, xx in zip(np.split(t, cut), np.split(x, cut))]


def animate(_):
    t_frame = time.time()
    _animate()
    took = time.time() - t_frame
    if took > opts['refreshMs'] / 1000.0:
        print("slow frame: %.2f s against a %d ms refresh" % (took, opts['refreshMs']), flush=True)


def _animate():
    snap = rx.snapshot()
    if snap is None:
        ax_tr.set_title("%s  |  %s:%d" % (rx.status, HOST, PORT), fontsize=opts['titleFontSize'])
        return
    t, x, fs = snap
    if x.size < max(int(3 * fs), 3 * opts['specWindow']):
        return

    # A fixed plotSeconds window whose right edge is *now*, not the last sample, so
    # a dropout shows as a blank growing in from the right instead of a frozen plot.
    # (If this PC's clock is behind the Pi's, the last sample wins.)
    t_right = max(t[-1], time.time())
    t_left = t_right - PLOT_SEC
    keep = t >= t_left
    t, x = t[keep], x[keep]
    td = mdates.date2num([datetime.fromtimestamp(v) for v in (t_left, t_right)])
    to_num = lambda tt: td[0] + (tt - t_left) / 86400.0

    # Every contiguous run is filtered and transformed on its own. Running one filter
    # across a gap would ring at the join, and one spectrogram across it would land
    # everything after the gap at the wrong time.
    min_run = max(int(3 * fs), 3 * opts['specWindow'])
    runs = [(tt, xx) for tt, xx in runs_of(t, x, fs) if tt.size >= min_run]
    if not runs:
        return

    ax_tr.clear()
    ax_sp.clear()
    peak = 0.0
    y_last = None
    trim = int(2 * fs)                      # edges of a zero-phase filter are not data
    spec = []
    for tt, xx in runs:
        y = bandpass(xx, fs)
        core = y[trim:-trim] if y.size > 3 * trim else y
        peak = max(peak, float(np.abs(core).max()))
        spec.append((tt, y))
        f, ts, db = spectrogram_db(xx, fs)
        spec[-1] = (tt, y, f, ts, db)
        y_last = y

    scale, unit = unit_for(peak)
    vmin, vmax = spec_range(spec[0][2], [d for *_, d in spec], fs)
    n_px = int(fig.get_size_inches()[0] * fig.dpi)
    for tt, y, f, ts, db in spec:
        te, ye = envelope(tt, y * scale, 2 * n_px)
        ax_tr.plot(to_num(te), ye, lw=opts['plotLineW'], color='#DC9257')
        # imshow, not pcolormesh: a bitmap is a fraction of the cost of a shaded mesh
        # of a few hundred thousand cells, and this runs every refresh
        x0, x1, f0, f1 = spec_extent(f, ts)
        ax_sp.imshow(db, cmap='inferno', vmin=vmin, vmax=vmax, aspect='auto', origin='lower',
                     interpolation='antialiased', extent=(to_num(tt[0] + x0), to_num(tt[0] + x1), f0, f1))

    lim = 1.1 * peak * scale
    ax_tr.set_ylim(-lim, lim)
    ax_tr.set_ylabel("velocity  %s" % unit)
    ax_tr.margins(0, 0)

    ratio = sta_lta(y_last, fs)
    # age of the newest sample against this PC's clock. Steady 2-3 s is the pipeline
    # (1 s blocks + refresh); steady and large is a clock offset between Pi and PC;
    # growing means the stream or this display is not keeping up.
    lag = time.time() - t[-1]
    title = "%s.%s %s   %g–%g Hz   %.2f SPS   %s   lag %.1f s" % (
        opts['network'], opts['station'], opts['channel'],
        opts['cutLow'], opts['cutHigh'], fs, rx.status, lag)
    color = 'white'
    if ratio is not None:
        title += "   STA/LTA %.2f" % ratio
        if ratio >= opts['staltaTrigger']:
            color = '#FF5555'
            if time.time() - _state['last_trigger'] > opts['LTA']:
                _state['last_trigger'] = time.time()
                print("%s  TRIGGER  STA/LTA %.2f  peak %.0f nm/s"
                      % (datetime.fromtimestamp(t[-1]).strftime('%Y-%m-%d %H:%M:%S'),
                         ratio, peak), flush=True)
    ax_tr.set_title(title, fontsize=opts['titleFontSize'], color=color)

    ax_sp.set_ylim(*spec_band(fs))
    ax_sp.set_ylabel("Hz")
    ax_sp.margins(0, 0)
    ax_sp.set_xlim(td[0], td[1])
    loc = mdates.AutoDateLocator(minticks=6, maxticks=14)
    ax_sp.xaxis.set_major_locator(loc)
    ax_sp.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    ax_sp.tick_params(axis='x', labelsize=8)

    if not _state['laid_out']:
        fig.tight_layout(pad=1)
        _state['laid_out'] = True


ani = FuncAnimation(fig, animate, interval=opts['refreshMs'], cache_frame_data=False)
plt.show()
