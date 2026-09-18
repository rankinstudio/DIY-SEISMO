#!/usr/bin/env python3
"""
ADS1220 capture for the EG-4.5-II extender board.

Replaces test.py, which was a ported ADS1292 example: wrong opcodes
(RDATA is 0x10 on the ADS1220, not 0x12), no register initialisation at
all -- so the part stayed in single-shot 20 SPS and was never told to
convert -- and spi.max_speed_hz = 100, which takes 240 ms to shift three
bytes.

Front end (see eg45-board.html, "Interfacing to the Raspberry Pi"):

    J3-1 (OUT) --[ 10k ]----+-- AIN0
                            |
    2.5 V ref --[ 4.99k ]---+

    J3-2 (AGND) -[ 10k ]----+-- AIN1
                            |
    2.5 V ref --[ 4.99k ]---+

  signal gain 0.3329, common mode 1.668 V, source impedance 3.33k.
  The bias comes off an LM4040 2.5 V reference, not a supply rail: the Pi's
  5 V is its raw input rail and moves tens of millivolts in our band, which
  the divider's match alone would not reject. See build note 9.
  Both legs reference the SAME +5 V, so rail movement is common mode and
  cancels to resistor tolerance -- use 0.1% parts (67 dB) not 1% (47 dB).

  Plus a 100 nF film cap across AIN0-AIN1 and 10 nF from each to AGND. The
  100 nF against 2 x 3.33k is a 239 Hz pole: -0.12 dB at the 40 Hz band edge,
  -3.9 dB more on the worst alias. Datasheet 9.2.1 wants the differential cap
  at least 10x the common-mode pair so their mismatch cannot convert
  common-mode noise into differential noise.

  Datasheet 9.2.1 also states the general case outright: to bias a
  ground-referenced source into range you either run a bipolar analog supply
  (AVDD +2.5, AVSS -2.5) or bypass the PGA. This design bypasses it.

ADS1220 configuration:

    reg0 0x05  AIN0/AIN1, gain 4, PGA BYPASSED
    reg1 0x84  330 SPS, normal mode, continuous conversion
    reg2 0x00  internal 2.048 V reference, 50/60 Hz FIR off
    reg3 0x00

The PGA is bypassed deliberately. AIN1 sits at a fixed bias, so the whole
differential swing lands on AIN0 and the common mode moves with the signal;
with the PGA in circuit that clips early. Bypassed, gain 4 is done in the
modulator and only the pin limits apply -- 1.16 to 2.18 V at full scale
against a -0.1 to 3.4 V allowance.

The 50/60 Hz FIR is off because it only exists at 20 SPS, and 20 SPS gives a
10 Hz Nyquist -- useless for a 0-40 Hz instrument. Mains is removed digitally
instead; see mains_notch below.

330 SPS, not 175. Both this part and the ADS1219 use a linear-phase FIR that
settles in a single cycle, so the anti-alias shape is sinc1 -- notches at
multiples of the data rate, sidelobes only about 20 dB down. It is NOT a sinc3.
At 175 SPS that leaves the worst in-band alias at only -25 dB, and worse, the
sensor's own spurious resonance at 140 Hz folds to 35 Hz with just 27 dB of
rejection. At 330 SPS the worst alias is -45 dB and 140 Hz sits below the
165 Hz Nyquist, so it never folds at all. In-band noise is unchanged (4.0 uV
against 4.2 at 175), because the wider bandwidth spreads the same noise.

Do NOT also run geophone_response.Compensator on this data. The board already
does the low-frequency extension in hardware.

Wiring. Two module variants matter, because they break out different pins.

  A. Bare 16-pin module (the purple ADS1220 board -- DRDY MISO MOSI SCLK CS
     CLK DVDD DGND down one side, AIN0..AIN3 REFN0 REFP0 AVDD AGND down the
     other). This is the default below: START_PIN and RESET_PIN are None and
     both functions are done with SPI commands instead.

        Pi pin 1  (3.3V)   -> AVDD *and* DVDD
        Pi pin 9  (GND)    -> AGND *and* DGND, *and* J3-2
        Pi pin 13 (GPIO27) -> DRDY
        Pi pin 18 (GPIO24) -> CS
        Pi pin 19 (GPIO10) -> MOSI
        Pi pin 21 (GPIO9)  -> MISO
        Pi pin 23 (GPIO11) -> SCLK
        CLK  -> DGND          (probably already pulled down on board; see below)
        REFN0, REFP0, AIN2, AIN3 -> leave open
        AIN0 -> divider tap off J3-1, AIN1 -> divider tap off J3-2

     Three things to get right on this variant:
       * DVDD from 3.3 V, never 5 V. The module has no level shifter, so at
         5 V its DOUT would drive 5 V into GPIO9 and can damage the Pi.
       * AVDD from 3.3 V too. The level shifter's 1.664 V pedestal is sized
         for AVDD/2.
       * CLK low selects the internal oscillator. The purple module already
         has a 10k pulldown on it (the resistor marked 103), so this is
         probably handled -- meter CLK to DGND unpowered and look for ~10k.
         If it reads open, jumper it. CLK is input-only, so a hard jumper to
         DGND is safe either way.

  B. Module with START and RESET broken out (ProtoCentral-style, which is what
     how_to_install_and_use_this_code.txt was written for). Same as above plus
     Pi pin 15 (GPIO22) -> START and Pi pin 16 (GPIO23) -> RESET. Construct
     with ADS1220(start_pin=15, reset_pin=16) to use them.

Either way the board's AGND must tie to Pi pin 9, or there is no return path
for the signal and the reading is meaningless.

CS is on pin 18, which is not either hardware chip select, so the bus is opened
with no_cs and GPIO24 is driven by hand.
"""

import math
import time

import spidev
import RPi.GPIO as GPIO

# ---------------------------------------------------------------- opcodes ---
RESET_CMD = 0x06
START_CMD = 0x08
POWERDOWN = 0x02
RDATA     = 0x10
RREG      = 0x20        # 0x20 | (reg << 2) | (n - 1)
WREG      = 0x40        # 0x40 | (reg << 2) | (n - 1)

CONFIG = (0x05, 0x84, 0x00, 0x00)

# ------------------------------------------------------------ calibration ---
FS         = 330.0          # SPS, must match reg1
GAIN       = 4              # must match reg0
VREF       = 2.048          # internal reference
DIV_GAIN   = 4.99e3 / (4.99e3 + 10e3)   # 0.33289, the level shifter
SENS       = 1300.0         # V/(m/s) at J3-1

VOLTS_PER_COUNT = (VREF / GAIN) / 2 ** 23           # 6.1035e-08 V
MPS_PER_COUNT   = VOLTS_PER_COUNT / (SENS * DIV_GAIN)   # 1.4103e-10 m/s
FULL_SCALE_MPS  = (VREF / GAIN) / (SENS * DIV_GAIN)     # 1.18 mm/s (unchanged)

# Log rate. The converter always runs at FS (330 SPS); set DECIMATE to drop
# the logged rate by an integer factor. 2 gives 165 SPS. Leave at 1 for 330.
DECIMATE = 2

# Pi header pin numbers. START and RESET are None because the 16-pin module
# does not break them out; both are issued as SPI commands instead.
DRDY_PIN, CS_PIN = 13, 18


class ADS1220:
    def __init__(self, bus=0, device=0, speed=1_000_000,
                 start_pin=None, reset_pin=None):
        self.start_pin = start_pin
        self.reset_pin = reset_pin
        # Seconds to sleep before spinning on DRDY. Zero spins the whole
        # conversion period (~3 ms) and pins a core; anything under the period
        # sleeps through most of it and only spins the last stretch. A daemon
        # that runs for months wants this; a bench test does not care.
        self.idle_sleep = 0.0

        GPIO.setmode(GPIO.BOARD)
        GPIO.setwarnings(False)
        GPIO.setup(CS_PIN, GPIO.OUT, initial=GPIO.HIGH)
        # Pulled up so an unwired DRDY reads high and read_raw() times out
        # with a message, instead of reading low and letting the loop spin
        # through the same conversion at SPI speed.
        GPIO.setup(DRDY_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        if start_pin is not None:
            GPIO.setup(start_pin, GPIO.OUT, initial=GPIO.LOW)
        if reset_pin is not None:
            GPIO.setup(reset_pin, GPIO.OUT, initial=GPIO.HIGH)

        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        # CS is on pin 18, driven by hand. Asking the kernel not to drive its
        # own chip-select is a courtesy, not a need: newer kernels (and the
        # Pi 5's RP1) reject SPI_NO_CS with EINVAL, and when they do the
        # hardware CE0 on pin 24 just toggles into thin air.
        try:
            self.spi.no_cs = True
        except OSError:
            pass
        self.spi.mode = 1              # SCLK idle low, sample on the rising edge
        self.spi.max_speed_hz = speed  # 6 MHz max; 1 MHz is plenty for 3 bytes

    # ------------------------------------------------------------ plumbing --
    def _xfer(self, data):
        GPIO.output(CS_PIN, GPIO.LOW)
        out = self.spi.xfer2(list(data))
        GPIO.output(CS_PIN, GPIO.HIGH)
        return out

    def command(self, opcode):
        self._xfer([opcode])

    def write_config(self, cfg=CONFIG):
        self._xfer([WREG | (0 << 2) | (len(cfg) - 1)] + list(cfg))

    def read_config(self):
        return self._xfer([RREG | (0 << 2) | 3] + [0x00] * 4)[1:]

    # --------------------------------------------------------------- setup --
    def begin(self):
        if self.reset_pin is not None:        # hardware reset, >= 50 us low
            GPIO.output(self.reset_pin, GPIO.LOW)
            time.sleep(0.001)
            GPIO.output(self.reset_pin, GPIO.HIGH)
            time.sleep(0.001)

        self.command(RESET_CMD)               # works with or without the pin
        time.sleep(0.001)                     # 50 us + 32 tmod

        self.write_config()
        got = self.read_config()
        if tuple(got) != tuple(CONFIG):
            raise IOError(
                "ADS1220 config readback %s, expected %s -- check MISO, "
                "SPI mode 1, CS on pin 18, and CLK tied to DGND"
                % (["0x%02X" % b for b in got], ["0x%02X" % b for b in CONFIG])
            )

        if self.start_pin is not None:
            GPIO.output(self.start_pin, GPIO.HIGH)
        # In continuous-conversion mode (CM = 1) the START/SYNC *command* is
        # enough on its own -- the pin is only an alternative way to send it.
        self.command(START_CMD)

    # ---------------------------------------------------------- conversion --
    def read_raw(self, timeout=1.0):
        """One 24-bit two's-complement sample, waiting on DRDY."""
        if self.idle_sleep:
            time.sleep(self.idle_sleep)
        deadline = time.time() + timeout
        while GPIO.input(DRDY_PIN):
            if time.time() > deadline:
                raise IOError("DRDY never fell -- was START/SYNC sent, and is "
                          "pin 13 wired to DRDY?")
        b = self._xfer([RDATA, 0x00, 0x00, 0x00])[1:]
        v = (b[0] << 16) | (b[1] << 8) | b[2]
        return v - (1 << 24) if v & 0x800000 else v

    def read_velocity(self, timeout=1.0):
        """Ground velocity in m/s."""
        return self.read_raw(timeout) * MPS_PER_COUNT

    def measure_rate(self, seconds=3.0):
        """The data rate the chip is actually delivering, in SPS.

        The ADS1220 runs its modulator from an internal oscillator that is
        only good to a couple of percent, and 330 SPS nominal has come out at
        323.6 on the bench. That matters twice: the mains notch is 0.5 Hz
        wide, so built for 60 Hz at a nominal 330 SPS it misses real mains
        by over 1 Hz and does nothing; and a rate error of 2% is 70 s/hour of
        timestamp error against any other clock. So time DRDY over a few
        seconds and build the filters, and the log, from what was measured.
        """
        n = int(seconds * FS)
        self.read_raw()                       # align to a DRDY edge
        t0 = time.perf_counter()
        for _ in range(n):
            self.read_raw()
        fs = n / (time.perf_counter() - t0)
        if abs(fs / FS - 1) > 0.05:
            raise IOError("measured %.1f SPS against %g nominal -- DRDY "
                          "timing or reg1 is wrong" % (fs, FS))
        return fs

    def close(self):
        if self.start_pin is not None:
            GPIO.output(self.start_pin, GPIO.LOW)
        self.command(POWERDOWN)
        self.spi.close()
        GPIO.cleanup()


# --------------------------------------------------------------- filtering ---
class Biquad:
    """Direct form 1, so the coefficients read the way they are written."""

    def __init__(self, b0, b1, b2, a1, a2):
        self.b = (b0, b1, b2)
        self.a = (a1, a2)
        self.reset()

    def reset(self):
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def __call__(self, x):
        b0, b1, b2 = self.b
        a1, a2 = self.a
        y = b0 * x + b1 * self.x1 + b2 * self.x2 - a1 * self.y1 - a2 * self.y2
        self.x2, self.x1 = self.x1, x
        self.y2, self.y1 = self.y1, y
        return y


def mains_notch(f_mains=60.0, fs=FS, r=0.98):
    """
    Notch for mains hum. The board's Sallen-Key only gives 2.6 dB at 60 Hz, so
    the hum arrives essentially intact -- but at 330 SPS it lands at 60 Hz,
    outside the 0-40 Hz band, and a notch costs nothing in the passband.

    Width matters more than depth. With r = 0.995 the -3 dB width is 0.5 Hz,
    and the notch is only as well centred as the sample-rate estimate that
    built it: a 0.1% drift of the ADS1220's oscillator (temperature) moves
    real 60 Hz to 60.06 in the filter's frame, where that notch gives 6 dB.
    On the bench that showed as a 60 Hz line still visible in the spectrogram.
    r = 0.98 makes it 2 Hz wide: better than 20 dB anywhere within 0.1 Hz of
    centre, 14 dB at 0.2 Hz, for 0.01 dB at the 40 Hz band edge.
    """
    w0 = 2 * math.pi * f_mains / fs
    c = math.cos(w0)
    g = (1 - 2 * r * c + r * r) / (2 - 2 * c)      # unity gain at DC
    return Biquad(g, -2 * c * g, g, -2 * r * c, r * r)


def butter_lowpass(fc, fs=FS, order=6):
    """Cascaded biquads for a Butterworth low-pass, bilinear with prewarp.

    Pure Python -- no scipy on the Pi. Order must be even.
    """
    if order % 2:
        raise ValueError("order must be even")
    K = math.tan(math.pi * fc / fs)
    out = []
    for k in range(order // 2):
        q = 1.0 / (2 * math.cos(math.pi * (2 * k + 1) / (2 * order)))
        n = 1 + K / q + K * K
        out.append(Biquad(K * K / n, 2 * K * K / n, K * K / n,
                          2 * (K * K - 1) / n, (1 - K / q + K * K) / n))
    return out


class Decimator:
    """Anti-alias filter plus every-Mth-sample, for logging below 330 SPS.

    Do NOT get a lower rate by setting the ADS1220 to 175 SPS instead. Its
    decimation filter is a sinc1, so at 175 SPS the sensor's own spurious
    resonance at 140 Hz folds to 35 Hz with only 27 dB of rejection -- right
    into the band. At 330 SPS, 140 Hz stays below Nyquist and never folds,
    and the filter here is as steep as you care to make it: 6th order at
    55 Hz costs 0.05 dB at the 40 Hz band edge and puts 125 Hz, which is what
    would fold to 40 Hz, 76 dB down.

    M = 2 gives 165 SPS, the natural step down from 330.
    """

    def __init__(self, m=2, fc=55.0, fs=FS, order=6):
        self.m = m
        self.secs = butter_lowpass(fc, fs, order)
        self.n = 0
        self.fs_out = fs / m

    def __call__(self, x):
        """Feed every sample; returns a value on output samples, else None."""
        for s in self.secs:
            x = s(x)
        self.n += 1
        if self.n < self.m:
            return None
        self.n = 0
        return x

    def reset(self):
        self.n = 0
        for s in self.secs:
            s.reset()


def dc_block(f_hp=0.05, fs=FS):
    """
    Removes the board's standing offset -- about +10 mV at J3-1, plus whatever
    the divider mismatch adds, together roughly 0.7% of full scale. One real
    pole well below the 0.31 Hz corner so it costs nothing in the band.
    """
    k = math.exp(-2 * math.pi * f_hp / fs)
    return Biquad(1.0, -1.0, 0.0, -k, 0.0)


# -------------------------------------------------------------------- main ---
def main():
    adc = ADS1220()
    adc.begin()
    print("ADS1220 running: %g SPS nominal, gain %d, PGA bypassed" % (FS, GAIN))
    print("full scale %.2f mm/s, 1 count = %.3f nm/s"
          % (FULL_SCALE_MPS * 1e3, MPS_PER_COUNT * 1e9))
    fs = adc.measure_rate()
    print("measured %.2f SPS (%+.2f%%); filters built for that rate"
          % (fs, 100 * (fs / FS - 1)))

    hp = dc_block(fs=fs)
    nf = mains_notch(60.0, fs=fs)
    dec = Decimator(DECIMATE, fs=fs) if DECIMATE > 1 else None
    if dec:
        print("logging at %.2f SPS after decimating by %d" % (dec.fs_out, DECIMATE))

    n, peak, t0 = 0, 0.0, time.time()
    try:
        while True:
            v = nf(hp(adc.read_velocity()))
            if dec is not None:
                v = dec(v)
                if v is None:
                    continue
            n += 1
            peak = max(peak, abs(v))
            rate = dec.fs_out if dec is not None else fs
            if n % int(rate) == 0:                    # once a second
                dt = time.time() - t0
                if n / dt > 1.5 * rate:
                    raise IOError("%.0f SPS is faster than the ADS1220 can "
                                  "convert -- DRDY is not being honoured; "
                                  "check J_PI column 20 to Pi pin 13"
                                  % (n / dt))
                print("%7.1f s  %8.1f SPS  peak %9.1f nm/s"
                      % (dt, n / dt, peak * 1e9))
                peak = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        adc.close()


if __name__ == "__main__":
    main()
