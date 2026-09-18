# software — capture, stream and display

Companion to `../hardware`, which holds the board and the ADS1220 driver. This
folder is what runs day to day:

| file | where | what |
|---|---|---|
| `geocapture.py` | Pi | **The only process that touches the ADC.** Reads the ADS1220 through `ads1220_capture.py`, timestamps, logs hourly miniSEED (or gzip CSV), and streams to any number of clients on TCP 1243. |
| `geoclient.py` | PC | Live band-passed trace + spectrogram + STA/LTA from the stream. |
| `geoplot.py` | PC | Plot a window from the on-disk log: band-passed trace and spectrogram. |
| `noisefloor.py` | PC | Spectrum of a shorted-input recording against the design floor; how the board's noise is graded. |
| `geostream.py` | both | The wire format: a `struct` header + `float32` samples in nm/s. |
| `loadOpts.py`, `config.JSON` | both | Settings with defaults. `GEOPH_CONFIG=/path/other.JSON` overrides the file. |
| `geocapture.service` | Pi | systemd unit so capture survives reboots. |

## Software install

### Raspberry Pi

**OS.** Raspberry Pi OS Lite, 64-bit, flashed with Raspberry Pi Imager. In the
Imager's customisation dialog set the hostname (`quake`), a user, **enable SSH**,
Wi-Fi if you use it, and the **correct timezone** — sample timestamps are UTC
from the Pi's clock, and any comparison with another instrument depends on them. No desktop is
needed; everything runs over SSH.

**First boot.**

```sh
sudo raspi-config        # Interface Options -> SPI -> Yes, then reboot
ls /dev/spidev*          # expect /dev/spidev0.0 and 0.1
timedatectl              # "System clock synchronized: yes" before trusting any timestamps
```

**Packages.** numpy, spidev and the GPIO library come from apt; obspy is not in
the Pi OS repos, so it goes in a venv that can still see the apt packages:

```sh
sudo apt update
sudo apt install python3-venv python3-numpy python3-scipy python3-spidev
# GPIO: python3-rpi-lgpio (required on a Pi 5, works on all) or python3-rpi.gpio.
# Keep whichever is already working for ads1220_capture.py -- apt will offer to
# swap one for the other; say no.
mkdir -p ~/geoph
python3 -m venv --system-site-packages ~/geoph/venv
~/geoph/venv/bin/pip install obspy                  # aarch64 wheels, a few minutes, no compiling
~/geoph/venv/bin/python -c "import obspy, scipy, spidev, RPi.GPIO; print('ok', obspy.__version__)"
```

obspy is optional: without it `geocapture.py` logs gzip CSV instead of miniSEED
and `/usr/bin/python3` is enough (change `ExecStart` in the service file).

**Files.** From the PC, in this folder:

```sh
scp geocapture.py geostream.py loadOpts.py config.JSON geocapture.service david@10.0.0.237:~/geoph/
scp ../hardware/ads1220_capture.py david@10.0.0.237:~/geoph/
```

Repeat the first line whenever `geocapture.py` or `config.JSON` changes; the
second whenever the driver does.

**First run, by hand.**

```sh
cd ~/geoph && venv/bin/python geocapture.py
```

Expect the measured rate (about 323.6 SPS raw, 161.8 out), `logging miniSEED to
/home/david/geoph/data`, `serving on port 1243`, then a status line every 10 s:
`seg 0` should stay at 0 and `queued` at 0 or 1. Ctrl-C stops it cleanly.

**As a service**, so it survives reboots and restarts itself:

```sh
sudo cp ~/geoph/geocapture.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now geocapture
journalctl -fu geocapture          # follow the log
sudo systemctl restart geocapture  # after copying a new geocapture.py or config.JSON
```

### PC

Python 3.10+ with:

```sh
pip install numpy scipy matplotlib obspy
```

(an Anaconda install already has the first three; `pip install obspy` adds the
last). Set `serverIP` in `config.JSON` to the Pi's address — `10.0.0.237`, with
a DHCP reservation on the router so it stays put — then:

```sh
python geoclient.py                 # live view
python geoclient.py 10.0.0.237      # or name the host on the command line
python geoplot.py 2026-09-12 14:00 15:30 --band 10 30
```

`geoplot.py` reads the log files from `logDir`, so either run it on the Pi
(`venv/bin/python geoplot.py ... --save out.png`, then copy the PNG), mount the
Pi's `data/` folder, or `rsync` it over:

```sh
rsync -av david@10.0.0.237:~/geoph/data/ ./data/
```

## How timing works, and why it is not `datetime.now()` per sample

The ADS1220 paces the reads with DRDY and runs from an internal oscillator that is
only good to ~2 % (this board: −1.9 %). Stamping each sample with the wall clock
would put I²C/SPI latency jitter on every sample and still get the rate wrong.
Instead `geocapture.py`:

1. measures the real rate over 3 s at startup and builds the filters from it;
2. anchors a *segment* at a wall-clock instant and numbers samples from there;
3. refines the rate against the NTP-disciplined system clock as the run goes on;
4. starts a new segment only if the two disagree by more than a sample for three
   blocks running — a genuine stall, not scheduling jitter — or at once if they
   disagree by more than half a second, which is a clock step.

Drift is measured *before* the rate is refined, never after: refining first fits
the rate to the wall clock and makes the drift zero by definition, so a stall or
a clock step would be absorbed into a wrong sample rate instead of starting a new
segment. That is what happened when the capture was started at boot before NTP
had stepped the Pi's clock forward: a 4.5-minute jump became a stream stamped
148 SPS, and every block overlapped the last by 13 samples. To close that door
`geocapture.py` also waits (up to 60 s) for `timedatectl` to report the clock
synced before anchoring the first segment; `waiting for NTP sync` in the log is
that wait, and `clock jumped +268.00 s (step or stall) -- new segment 1` is what
a step looks like if one gets through anyway.

Every block on the wire and every trace on disk carries `t0`, `fs` and the
segment number, so `obspy.Stream.merge()` knows what is contiguous.

Disk and network run on a separate thread from the ADC reads. The ADS1220
overwrites its data register every conversion (~3 ms), so the read loop is never
allowed to wait on an SD-card write.

## Disk

miniSEED at 161.8 SPS float32 is about 56 MB per day, 20 GB per year. Two caps
in `config.JSON`, checked at startup and hourly, delete whole UTC-day folders
oldest first: `retainDays` (default 180) and `maxLogGB` (default 15). Today's
folder is never touched; 0 disables a cap. Anything you want to keep longer,
copy off the Pi.

## Outages

Capture never depends on the network: the Pi logs to disk whether or not anyone
is watching. On the client side a dropout shows as a blank growing in from the
right edge (the window's right edge is the wall clock, not the last sample), the
receiver reconnects on its own, and on connect the Pi replays its last
`historySec` of blocks so the hole fills back in. Each contiguous run is filtered
and transformed on its own, so a gap is drawn as a gap rather than as ringing.
Restarting `geoclient.py` gets the same replay, so the window is full at once.

## What changed from the earlier ADS1115 version

- One process logs **and** streams; the old `geoph.py` / `geoserver.py` each
  opened the ADC and could not run together.
- Units are nm/s, calibrated through the driver's 1.41e-10 m/s per count and the
  interface board's 0.333 divider; the old plots were raw counts labelled mV.
- The display band is 0.3–40 Hz, the instrument's flat band, not 0.1–2 Hz — the
  hardware compensator, DC block and 60 Hz notch already did the work the old
  bandpass was doing.
- Framing is a fixed header + `float32`, read with exact-length receives. The old
  `recv(100)` + pickle parser desynchronised whenever a chunk straddled two
  messages (the "msg fail" restarts).
- Server binds `0.0.0.0` and serves many clients; a slow client is dropped, not
  the capture.
- STA/LTA runs on the envelope (|x|), not the signed signal, which averaged to
  about zero and made the old ratio meaningless.
- `geolive.py` / `geospec.py` (which crashed on a missing `cutoffFreq` key) are
  replaced by `geoplot.py`, reading the miniSEED log through obspy.
