#!/usr/bin/env python3
"""
EG-4.5-II response correction for the Raspberry Pi / ADS1220 seismograph.

Two ways to flatten the 12 dB/octave roll-off below the 4.5 Hz natural
frequency of an EG-4.5-II:

  1. In hardware - the extender board in this folder (two cascaded shelving stages).
  2. In software - this module. Exact, adjustable, no component tolerance,
     and it does not amplify amplifier drift. Use it when the analogue chain
     is FLAT (damping shunt + low-noise gain only).

Do not apply both to the same data.

If you built the board, you want ads1220_capture.py, not this. That module has
the ADC scaling, the mains notch and the DC block, and it deliberately does not
touch Compensator. What is still useful here is sensor_response() and
velocity_noise_floor(): they describe the bare sensor, which is the reference
you compare the board against.

The geophone is a velocity transducer:

                        G s^2
    V_out(s) = ------------------------- * v_ground(s)
                s^2 + 2 z w0 s + w0^2

with w0 = 2*pi*4.5 rad/s and G = 28.8 V/(m/s). Damping z depends on the total
load across the coil. From the datasheet (4.5 Hz, 11.3 g moving mass, 0.600
open-circuit damping, 375 ohm coil):

    z = 0.600 + 1298 / (375 + Rload)

The board fits a 2.87k shunt, which puts z at 1.000.

To flatten the response down to a new corner f1, deconvolve the sensor's own
poles and substitute a new pole pair:

               s^2 + 2 z  w0 s + w0^2
    C(s) = ---------------------------------
               s^2 + 2 z1 w1 s + w1^2

C(s) has unity gain above w0 and rises to (w0/w1)^2 at DC, which is exactly the
sensor's deficit. Discretised below with a pre-warped bilinear transform so the
corner lands on frequency at any sample rate.
"""

import math

F0        = 4.5     # Hz,      natural frequency        (datasheet, +/-10%)
G_SENS    = 28.8    # V/(m/s), open-circuit sensitivity (datasheet, +/-5%)
RCOIL     = 375.0   # ohm,     coil resistance          (datasheet, +/-5%)
ZETA_OPEN = 0.600   # open-circuit damping              (datasheet, +/-5%)
MASS      = 0.0113  # kg,      moving mass              (datasheet)
K_DAMP    = 1298.0  # ohm,     G^2/(2*m*w0)
R_SHUNT   = 2870.0  # ohm,     fitted on the board -> zeta = 1.000

# kept as aliases so older scripts still import cleanly
F0_SM24, G_SM24 = F0, G_SENS


def damping(r_load):
    """Total damping ratio with r_load ohms across the coil. inf = open circuit."""
    if r_load == float("inf"):
        return ZETA_OPEN
    return ZETA_OPEN + K_DAMP / (RCOIL + r_load)


def shunt_for_damping(zeta):
    """Shunt resistance that yields the requested total damping ratio."""
    if zeta <= ZETA_OPEN:
        raise ValueError("zeta must exceed the open-circuit value of 0.25")
    return K_DAMP / (zeta - ZETA_OPEN) - RCOIL


def compensator_biquad(fs, f1=0.31, zeta1=0.707, r_shunt=R_SHUNT, f0=F0):
    """
    Direct-form-I biquad (b0,b1,b2,a1,a2) that extends the corner to f1 Hz.
    Unity gain in the sensor's own flat band, so the output stays in V/(m/s)
    once divided by the analogue chain's flat-band gain.
    """
    z0, w0 = damping(r_shunt), 2 * math.pi * f0
    w1 = 2 * math.pi * f1
    # ONE bilinear substitution s -> k (1-z^-1)/(1+z^-1) for the whole ratio,
    # pre-warped at f0 so the sensor's own corner is cancelled exactly.
    k = w0 / math.tan(w0 / (2 * fs))
    # numerator: s^2 + 2 z0 w0 s + w0^2
    n0 = k * k + 2 * z0 * w0 * k + w0 * w0
    n1 = 2 * (w0 * w0 - k * k)
    n2 = k * k - 2 * z0 * w0 * k + w0 * w0
    # denominator: s^2 + 2 z1 w1 s + w1^2
    d0 = k * k + 2 * zeta1 * w1 * k + w1 * w1
    d1 = 2 * (w1 * w1 - k * k)
    d2 = k * k - 2 * zeta1 * w1 * k + w1 * w1
    return (n0 / d0, n1 / d0, n2 / d0, d1 / d0, d2 / d0)


class Compensator:
    """Streaming biquad. Feed it samples in volts, get volts-equivalent-flat out."""

    def __init__(self, fs, f1=0.31, zeta1=0.707, r_shunt=R_SHUNT):
        self.b0, self.b1, self.b2, self.a1, self.a2 = compensator_biquad(
            fs, f1, zeta1, r_shunt)
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def __call__(self, x):
        y = (self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2
             - self.a1 * self.y1 - self.a2 * self.y2)
        self.x2, self.x1 = self.x1, x
        self.y2, self.y1 = self.y1, y
        return y

    def reset(self):
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0


def sensor_response(f, r_shunt=R_SHUNT, f0=F0):
    """Open-circuit-EMF response in V/(m/s) at frequency f, with load damping."""
    z, w0 = damping(r_shunt), 2 * math.pi * f0
    s = 1j * 2 * math.pi * f
    return G_SENS * s * s / (s * s + 2 * z * w0 * s + w0 * w0)


def velocity_noise_floor(f, r_shunt=R_SHUNT, en=0.0, temp=300.0):
    """
    Sensor-limited ground-velocity noise density, m/s/rtHz.
    en is the front-end amplifier voltage noise in V/rtHz.
    """
    kb = 1.380649e-23
    r_par = RCOIL * r_shunt / (RCOIL + r_shunt)
    e_r = math.sqrt(4 * kb * temp * r_par)          # at the coil terminals
    div = r_shunt / (RCOIL + r_shunt)
    e_tot = math.sqrt(e_r ** 2 + en ** 2)
    return e_tot / (abs(sensor_response(f, r_shunt)) * div)
