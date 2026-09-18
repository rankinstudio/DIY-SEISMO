# EG-4.5-II low-frequency extension

A preamp that flattens an EG-4.5-II geophone (4.5 Hz) down to **0.31 Hz** and
band-limits it at 40 Hz, feeding a Raspberry Pi through an ADS1220 24-bit ADC.
Two boards on ElectroCookie solderable breadboards:

- **Main board** — damping shunt, two shelf stages (AD797, LT1113), 60 Hz
  Sallen-Key, output buffer. 28 parts, 15 jumpers.
- **Interface board** — 5 V → ±5 V converter, 2.5 V reference, level shifter,
  the ADS1220 module in a socket, header for the Pi. 15 parts, 16 jumpers.

| | |
|---|---|
| Sensitivity | 1310 V/(m/s) at J3-1; 433 V/(m/s) at the ADC |
| Band | 0.31 – 40 Hz, ripple −0.49 / +0.00 dB from 1 to 40 Hz |
| Full scale | ±1.18 mm/s, 0.141 nm/s per count |
| Noise floor (design) | 8.7 nm/s/√Hz at 1 Hz, 0.1 at 40 Hz |
| Supply | 5 V from the Pi; 18.7 mA per rail on the board |
| Output rate | 330 SPS at the ADC, logged at 165 SPS |

**`eg45-board.html`** is the full build document (drawings, schematics, wiring
tables, assembly stages, notes) — [rendered here](https://rankinstudio.github.io/DIY-SEISMO/hardware/eg45-board.html),
or download it and open it in a browser. This file is the short
version: parts, assembly and the test sequence.

## Files

| File | What it is |
|---|---|
| `eg45-board.html` | build document: schematics, board drawings, hole-by-hole placement, jumper tables, assembly stages |
| `ads1220_capture.py` | Pi-side driver: ADS1220 setup, calibrated readout, filters |
| `geophone_response.py` | sensor model and noise helpers (do not run its `Compensator` on this board's data) |
| `digikey-order.csv` | the DigiKey order as placed (with spare resistors) |

## Parts

    Rsh   2.87k        damping shunt, zeta = 1.0
    R3    357k    \
    Rs    15.8k    |   stage A (U1, AD797): gain 51 flat, 1187 at DC
    R4    301      |   shelf zero 4.49 Hz, pole 0.194 Hz
    C1    2.2u    /
    Cc    33p          across R3, stability
    Chp   2.2u    \    0.048 Hz DC block
    Rhp   1.5M    /
    R3b   825k    \    stage B (U2a, LT1113): gain 1 flat, 23.1 at DC
    R4b   37.4k    |   shelf zero 4.45 Hz, pole 0.193 Hz
    C1b   1u      /
    Rf1   17.8k   \
    Rf2   17.8k    |   60 Hz Sallen-Key, Q 0.74, plus buffer (U2b)
    Cf1   220n     |
    Cf2   100n    /
    Cd1-4 100n         rail decoupling at each chip
    Rd1/2 10R     \    rail filter at J2, 72 Hz corner
    Cb1/2 220u    /    (polarised: Cb1 + to V+, Cb2 + to GND)

Interface board: `PS1` NMA0505SC converter, `VR1` LM4040 2.5 V with `Rfd` 2.87k,
divider `Ra`/`Ra'` 4.99k (to the reference) and `Rb`/`Rb'` 10k (to signal and
AGND), `Cdif` 100n across AIN0–AIN1, `Ccm1`/`Ccm2` 10n from each input to AGND.

### Ordering list

Confirm `AD797ANZ` and `LT1113CN8` are in stock in DIP-8 before ordering
anything else. Film caps: buy J (±5%) for C1, C1b, Cf1, Cf2; K is fine for Chp.
TDK series number is the pitch: B32529 = 5 mm, B32520 = 7.5 mm.

| Ref | Value | Qty | Manufacturer part | DigiKey |
|---|---|---|---|---|
| U1 | AD797AN | 1 (+1 spare) | `AD797ANZ` | `505-AD797ANZ-ND` |
| U2 | LT1113CN8 | 1 | `LT1113CN8#PBF` | `LT1113CN8#PBF-ND` |
| — | 8-pin DIP socket | 2 | `1-2199298-2` | `A120347-ND` |
| C1, Chp | 2.2 µF 63 V film, 7.5 mm | 2 | `B32520C0225J000` | `495-1128-ND` |
| C1b | 1 µF 63 V film, 7.5 mm | 1 | `B32520C0105J000` | `495-1121-ND` |
| Cf1 | 220 nF 63 V film, 5 mm | 1 | `B32529C0224J000` | `495-1106-ND` |
| Cf2, Cdif | 100 nF 63 V film, 5 mm | 2 | `B32529C0104J000` | `495-1103-ND` |
| Ccm1, Ccm2 | 10 nF 63 V film, 5 mm | 2 | `B32529C0103J000` | `495-1097-ND` |
| Cd1–Cd4 | 100 nF 50 V X7R, 2.54 mm | 4 | `C320C104K5R5TA` | `399-4264-ND` |
| Cc | 33 pF 100 V C0G | 1 (buy a few) | `C315C330J1G5TA` | `399-9729-ND` |
| Cb1, Cb2 | 220 µF 16 V electrolytic, 3.5 mm | 2 | `EEU-FC1C221` | `P11199-ND` |
| Rsh, Rfd | 2.87 kΩ 1% | 2 | `MFR-25FRF52-2K87` | `13-MFR-25FRF52-2K87CT-ND` |
| R3 | 357 kΩ 1% | 1 | `MFR-25FBF52-357K` | `357KXBK-ND` |
| Rs | 15.8 kΩ 1% | 1 | `MFR-25FBF52-15K8` | `15.8KXBK-ND` |
| R4 | 301 Ω 1% | 1 | `RNF14FTD301R` | `RNF14FTD301RCT-ND` |
| Rhp | 1.5 MΩ 1% | 1 | `RNF14FTD1M50` | `RNF14FTD1M50CT-ND` |
| R3b | 825 kΩ 1% | 1 | `MFR-25FRF52-825K` | `13-MFR-25FRF52-825KCT-ND` |
| R4b | 37.4 kΩ 1% | 1 | `MFR-25FBF52-37K4` | `37.4KXBK-ND` |
| Rf1, Rf2 | 17.8 kΩ 1% | 2 | `MFR-25FBF52-17K8` | `13-MFR-25FBF52-17K8-ND` |
| Rd1, Rd2 | 10 Ω 1% | 2 | `MFR-25FBF52-10R` | `10.0XBK-ND` |
| Ra, Ra' | 4.99 kΩ 0.1% | 2 | `YR1B4K99CC` | `1712-YR1B4K99CCCT-ND` |
| Rb, Rb' | 10 kΩ 0.1% | 2 | `MFP-25BRD52-10K` | `10KADCT-ND` |
| J1, J3, J_IN, J_SIG | 2-position 5.08 mm terminal | 4 | `OSTTC022162` | `ED2609-ND` |
| J2, J_PWR | 3-position 5.08 mm terminal | 2 | `OSTTC032162` | `ED2610-ND` |
| PS1 | 5 V to ±5 V, 1 W | 1 | `NMA0505SC` | `811-1448-5-ND` |
| VR1 | 2.5 V shunt reference | 1 | `LM4040DIZ-2.5/NOPB` | `LM4040DIZ-2.5/NOPB-ND` |
| test | 374 Ω 1% | 1 | `MFR-25FRF52-374R` | `13-MFR-25FRF52-374RCT-ND` |

Also needed, not on the order: two 8-pin female header strips (ADS1220 module
socket), one 8-pin male strip (`J_PI`), two ElectroCookie breadboards, the bare
16-pin ADS1220 module.

## Assembly

Hole names are row letter then column (`E9`). Both boards' hole-by-hole
placement and jumper tables are in `eg45-board.html`; the layout data is in
`ecookie_layout.json` and `iface_layout.json`.

Before soldering anything:

- **Leave the edge solder-jumper pads open** on both boards. They would short a
  supply rail to ground.
- **Ignore the board's printed + / −.** The layout uses the four rail strips as
  V+ / GND / GND / V− top to bottom. The drawings show the silkscreen marks
  small at the far left of each rail only so you can orient the board.
- **Meter CLK to DGND on the ADS1220 module**: expect ~10 kΩ. If open, jumper it.

Main board, top to bottom of the drawing:

1. DIP sockets for U1 (columns 6–9) and U2 (18–21), pin 1 to the left.
   Terminals J1 (C1/C3), J2 (I1/I3/I5), J3 (G28/G30) — wire openings toward the
   nearer board edge.
2. Jumpers, any order; every endpoint is a strip, so any free hole on it works.
3. Resistors and film caps. `Rsh`, `R4`, `Rhp`, `R4b`, `Cf2`, `Rd1`, `Rd2` bridge
   a row to its adjacent rail and stand on end; `Rf2` tents. C1 and Chp sit in
   holes 10 mm apart, so the 7.5 mm parts have their leads bent out ~1.3 mm.
4. Cd1–Cd4 across the rails at columns 4, 11, 15, 23. Cb1 (V+ to GND, + on V+)
   and Cb2 (GND to V−, + on GND) at column 6; bend the leads in 0.5 mm each.
5. Cc solders directly onto R3's leads.
6. Op amps go in last, after the cold tests.

Interface board: PS1 at F6–F11 (check its pin-1 mark — no reverse protection),
VR1 at I22–I24 (pins 1 and 3 are both AGND, it cannot go in backwards), the two
8-pin female strips at rows D and I, columns 13–20 (AIN0 and DRDY at column 20),
`J_PI` male header at J13–J20, terminals J_IN (I1/I3), J_PWR (I26/I28/I30),
J_SIG (A27/A29). The module goes in last, label up, after the powered checks.

Cabling: J_PWR → J2 (+5, GND, −5); J3 → J_SIG (OUT, AGND); geophone on J1 with
its shield to a free ground-rail hole at the board end only; seven Dupont wires
from `J_PI` to the Pi (table under *Software*).

## Testing

All resistances with no power, op-amp sockets empty, module out, boards not yet
connected.

### Main board, cold

| Between | Expect |
|---|---|
| V+ ↔ GND, V− ↔ GND, V+ ↔ V− | open, creeping up as Cb1/Cb2 charge |
| top GND ↔ bottom GND | 0 Ω (PJ) |
| edge solder-jumper pads, each side | open |
| J2-1 ↔ V+, J2-3 ↔ V− | 10 Ω (Rd1, Rd2) |
| J2-2 ↔ GND | 0 Ω |
| U1-7 ↔ V+, U1-4 ↔ V−, U2-8 ↔ V+, U2-4 ↔ V− | 0 Ω |
| J1-1 ↔ U1-3 | 0 Ω |
| J1-1 ↔ GND | 2.87 k (Rsh) |
| J1-2 ↔ GND | 0 Ω |
| U1-2 ↔ GND | 301 Ω (R4) |
| U1-2 ↔ U1-6 | 357 k (R3) |
| U1-6 ↔ U2-3 | open (Chp) |
| U2-3 ↔ GND | 1.5 M (Rhp) |
| U2-2 ↔ GND | 37.4 k (R4b) |
| U2-2 ↔ U2-1 | 825 k (R3b) |
| U2-1 ↔ U2-5 | 35.6 k (Rf1 + Rf2) |
| U2-5 ↔ GND | open (Cf2) |
| U2-6 ↔ U2-7 ↔ J3-1 | 0 Ω |
| J3-2 ↔ GND | 0 Ω |
| U1-1, U1-5, U1-8 | open to everything |

### Interface board, cold

| Between | Expect |
|---|---|
| J_IN-1 ↔ J_IN-2 | not a short |
| J_IN either pin ↔ any rail | open (isolation barrier) |
| +5 ↔ AGND, −5 ↔ AGND, +5 ↔ −5 | not near 0 Ω (+5 ↔ AGND ~3 k one polarity, via Rfd and VR1) |
| top AGND ↔ bottom AGND | 0 Ω (PJ) |
| J_PWR-1 ↔ +5, J_PWR-2 ↔ AGND, J_PWR-3 ↔ −5 | 0 Ω |
| PS1 F11 ↔ +5, F10 ↔ AGND, F9 ↔ −5 | 0 Ω |
| I23 ↔ +5 | 2.87 k (Rfd) |
| I23 ↔ AGND | ~15 k one way, diode the other |
| D20 ↔ I23, D19 ↔ I23 | 4.99 k (Ra, Ra') |
| D20 ↔ J_SIG-1 | 10 k (Rb) |
| D19 ↔ AGND | 10 k (Rb') |
| D13 ↔ AGND, J_SIG-2 ↔ AGND | 0 Ω |
| I13 (DGND) ↔ AGND | open — tied only on the module |
| each J_PI pin ↔ its column I13–I20 | 0 Ω, open to everything else |

### Powered, in order

Any 5 V source into J_IN (column 1 +, column 3 ground). Check polarity first;
PS1 has no reverse protection.

1. **Interface board alone, module out.** +5 on the top outer rail, −5 on the
   bottom outer (±5.3–6.1 V unloaded is normal). 2.5 V at I23. With J_SIG's two
   pins shorted together, D20 and D19 both ≈1.67 V within a few mV of each
   other. **Do not fit the module until this reads right.**
2. **J_PWR → J2**, main board sockets empty. U1-7/U2-8 positive, U1-4/U2-4
   negative (about ±5.8/−6.1 V unloaded).
3. **Op amps in**, pin 1 left. Rails pull down and equalise (±5.47 V measured
   before the Rd/Cb rail filter was added; expect ~0.2 V less now).
4. **J1 open, wait a minute**, then DC at J3-1: a few tens of mV, wandering.
5. **Geophone on J1, tap the floor**: several hundred mV either side of ground,
   ringing for a second or two.
6. **Module in** (label up, AIN0/DRDY at column 20), J3 → J_SIG, Dupont wires
   to the Pi, run `python3 ads1220_capture.py`. Expect ~324 SPS measured
   (the ADS1220's oscillator runs ~2% slow) and desk taps of 0.1–1.5 mm/s.

If you can borrow a calibrated seismometer, put it on the same floor for an
hour and compare the two in 10–30 Hz; the amplitude ratio is your correction.

## Software

The Pi-side install (OS, SPI, packages, venv, the `geocapture` systemd service)
and the PC client are documented in `../software/README.md`. For the driver alone:
Pi OS Lite, SPI enabled in `raspi-config`, `python3-numpy python3-scipy
python3-spidev` and `python3-rpi-lgpio` (or `python3-rpi.gpio`).

Pi header → `J_PI` (bare 16-pin module):

| Pi pin | Signal | Module pin |
|---|---|---|
| 1 | 3.3 V | AVDD and DVDD (never 5 V) |
| 9 | GND | DGND — the instrument's only ground tie to the Pi |
| 13 | GPIO27 | DRDY |
| 18 | GPIO24 | CS |
| 19 | GPIO10 | MOSI |
| 21 | GPIO9 | MISO |
| 23 | GPIO11 | SCLK |

CLK stays low (internal oscillator); REFP0/REFN0 and AIN2/AIN3 open. AIN0 and
AIN1 come from the level shifter on the interface board:

    J3-1  OUT  --[ Rb  10k ]----+-- AIN0
    2.5 V ref  --[ Ra  4.99k ]--+
    J3-2  AGND --[ Rb' 10k ]----+-- AIN1
    2.5 V ref  --[ Ra' 4.99k ]--+
    +5 V --[ Rfd 2.87k ]-- LM4040 2.5 V -- AGND

Divider ratio 0.3329, common mode 1.668 V.

`ads1220_capture.py` programs registers `0x05 0x84 0x00 0x00` — AIN0/AIN1,
gain 4 with the PGA bypassed, 330 SPS continuous, internal 2.048 V reference,
mains FIR off. It measures the actual sample rate at start-up and builds the
60 Hz notch, 0.05 Hz DC block and the decimator (`DECIMATE = 2`, 6th-order
Butterworth at 55 Hz → 165 SPS) from that rate. Output is ground velocity in
m/s. Run it directly for a bench test (prints samples); `../../geoph/geocapture.py`
imports it for continuous logging and streaming.

Do not run `geophone_response.Compensator` on this data — the board already
does the extension in hardware.
