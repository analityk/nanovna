#!/usr/bin/env python3
"""
LiteVNA CLI: pomiar S11/S21, eksport Touchstone (S11)/CSV oraz wlasna
1-portowa kalibracja SOL z dowolnym rezystorem LOAD i S21 THRU/ISOLATION.

S11/S21 Enhanced Response: calibrate-er --load 50 --out er.lvcal.json, potem
     measure-s21 --cal er.lvcal.json --out dut_s21.csv.

S21 response (bez SOL): calibrate-s21 --out s21.lvcal.json, potem
     measure-s21 --cal s21.lvcal.json --out dut_s21.csv.
Korekcja S21 jest programowa, nie zapisuje wspolczynnikow do LiteVNA.

Wymagania:
    pip install pyserial

Najwazniejsze tryby:

1) Pobranie DOKLADNIE kalibracji aktywnej w samym LiteVNA (mode 3):
    python litevna_cli.py measure --port COM7 --device-cal \
        --start 400k --stop 2M --points 1024 --avg 2 \
        --format rjx,series-l,logmag --marker 431.281k --out trafo.s1p

2) Wlasna kalibracja SOL w Pythonie, np. LOAD=23.75 ohm:
    python litevna_cli.py calibrate --port COM7 --start 400k --stop 2M \
        --points 1024 --avg 2 --load 23.75 --out trafo_23R75.lvcal.json

   Program poprosi kolejno o OPEN, SHORT i LOAD.

3) Pomiar z wlasna kalibracja:
    python litevna_cli.py measure --port COM7 --cal trafo_23R75.lvcal.json \
        --format rjx,series-l,series-c,logmag --marker 431.281k --out dut.s1p

4) Surowy pomiar bez korekcji:
    python litevna_cli.py measure --port COM7 --raw \
        --start 400k --stop 2M --points 1024 --out raw.s1p

5) Uzycie aktualnego zakresu ustawionego na ekranie LiteVNA:
    python litevna_cli.py measure --port COM7 --device-cal --current \
        --format all --out current.s1p

Uwagi:
- Format wyswietlania LiteVNA (LOGMAG, SERIES L, C itd.) NIE zmienia danych RF.
  LiteVNA mierzy zespolone S11. Ten skrypt zawsze zachowuje S11 w .s1p,
  a wybrane reprezentacje wylicza z tego samego S11 i zapisuje do CSV.
- Przy --device-cal skrypt prosi firmware o "send calibrated data" (mode 3),
  czyli wykorzystuje kalibracje aktywna w SAMYM LiteVNA, a nie kalibracje apki PC.
- Przy --cal skrypt pobiera niekorygowane S11 i stosuje wlasny 3-term SOL.
- Zamknij NanoVNA-App / Saver itp. przed uruchomieniem; port COM moze miec
  tylko jednego wlasciciela.
"""

from __future__ import annotations

import argparse
import cmath
import csv
import datetime as dt
import json
import math
import struct
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Brak pyserial. Zainstaluj: pip install pyserial", file=sys.stderr)
    raise SystemExit(2)

# NanoVNA V2 / LiteVNA binary protocol
CMD_INDICATE = 0x0D
CMD_READ1 = 0x10
CMD_READ2 = 0x11
CMD_READ8 = 0x13
CMD_READFIFO = 0x18
CMD_WRITE1 = 0x20
CMD_WRITE2 = 0x21
CMD_WRITE8 = 0x23

REG_SWEEP_START = 0x00
REG_SWEEP_STEP = 0x10
REG_SWEEP_POINTS = 0x20
REG_VALUES_PER_FREQ = 0x22
REG_DATA_MODE = 0x26
REG_VALUES_FIFO = 0x30
REG_AVERAGE = 0x40
REG_CHANNEL_SELECT = 0x44

REG_DEVICE_VARIANT = 0xF0
REG_PROTOCOL_VERSION = 0xF1
REG_HW_REVISION = 0xF2
REG_FW_MAJOR = 0xF3
REG_FW_MINOR = 0xF4

MODE_USB_UNCAL = 0
MODE_NORMAL = 2
MODE_USB_CAL = 3

CHANNEL_BOTH = 0
CHANNEL_S11 = 1
CHANNEL_S21 = 2
FIFO_RECORD_SIZE = 32
Z0_DEFAULT = 50.0

FORMAT_ALIASES = {
    "rjx": "rjx",
    "impedance": "rjx",
    "z": "rjx",
    "logmag": "logmag",
    "linmag": "linmag",
    "phase": "phase",
    "swr": "swr",
    "return-loss": "return-loss",
    "returnloss": "return-loss",
    "rl": "return-loss",
    "series-r": "series-r",
    "rs": "series-r",
    "series-x": "series-x",
    "xs": "series-x",
    "series-l": "series-l",
    "l": "series-l",
    "series-c": "series-c",
    "c": "series-c",
    "series-q": "series-q",
    "q": "series-q",
    "admittance": "admittance",
    "y": "admittance",
    "parallel-r": "parallel-r",
    "rp": "parallel-r",
    "parallel-x": "parallel-x",
    "xp": "parallel-x",
    "parallel-l": "parallel-l",
    "lp": "parallel-l",
    "parallel-c": "parallel-c",
    "cp": "parallel-c",
}

ALL_FORMATS = [
    "rjx", "logmag", "linmag", "phase", "swr", "return-loss",
    "series-r", "series-x", "series-l", "series-c", "series-q",
    "admittance", "parallel-r", "parallel-x", "parallel-l", "parallel-c",
]


def parse_eng(s: str) -> float:
    t = str(s).strip().replace("Hz", "").replace("hz", "")
    mult = 1.0
    if t and t[-1] in "kK":
        mult, t = 1e3, t[:-1]
    elif t and t[-1] in "mM":
        mult, t = 1e6, t[:-1]
    elif t and t[-1] in "gG":
        mult, t = 1e9, t[:-1]
    return float(t) * mult


def write1(ser, addr: int, value: int):
    ser.write(bytes((CMD_WRITE1, addr & 0xFF, value & 0xFF)))


def write2(ser, addr: int, value: int):
    ser.write(bytes((CMD_WRITE2, addr & 0xFF)) + struct.pack("<H", value))


def write8(ser, addr: int, value: int):
    ser.write(bytes((CMD_WRITE8, addr & 0xFF)) + struct.pack("<Q", value))


def read1(ser, addr: int) -> int:
    ser.write(bytes((CMD_READ1, addr & 0xFF)))
    b = ser.read(1)
    if len(b) != 1:
        raise TimeoutError(f"Brak odpowiedzi przy odczycie 0x{addr:02X}")
    return b[0]


def read2(ser, addr: int) -> int:
    ser.write(bytes((CMD_READ2, addr & 0xFF)))
    b = ser.read(2)
    if len(b) != 2:
        raise TimeoutError(f"Brak odpowiedzi przy odczycie 0x{addr:02X}")
    return struct.unpack("<H", b)[0]


def read8(ser, addr: int) -> int:
    ser.write(bytes((CMD_READ8, addr & 0xFF)))
    b = ser.read(8)
    if len(b) != 8:
        raise TimeoutError(f"Brak odpowiedzi przy odczycie 0x{addr:02X}")
    return struct.unpack("<Q", b)[0]


def resync(ser):
    ser.reset_input_buffer()
    ser.write(b"\x00" * 8)
    time.sleep(0.02)


def identify(ser) -> dict:
    resync(ser)
    write1(ser, REG_DATA_MODE, MODE_NORMAL)
    time.sleep(0.02)
    ser.write(bytes((CMD_INDICATE,)))
    ans = ser.read(1)
    if ans != b"\x32":
        raise RuntimeError(f"To nie wyglada na LiteVNA/NanoVNA-V2 (INDICATE={ans!r})")
    return {
        "variant": read1(ser, REG_DEVICE_VARIANT),
        "protocol": read1(ser, REG_PROTOCOL_VERSION),
        "hw": read1(ser, REG_HW_REVISION),
        "fw_major": read1(ser, REG_FW_MAJOR),
        "fw_minor": read1(ser, REG_FW_MINOR),
    }


def try_open_port(name: str, timeout: float = 20.0):
    ser = serial.Serial(
        port=name,
        baudrate=115200,  # USB CDC; baudrate jest praktycznie umowne
        timeout=timeout,
        write_timeout=3.0,
    )
    time.sleep(0.05)
    return ser


def auto_find_port():
    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("Nie znaleziono zadnych portow szeregowych.")
    errors = []
    for p in ports:
        ser = None
        try:
            ser = try_open_port(p.device, timeout=1.0)
            info = identify(ser)
            if info["variant"] == 0x02 and info["protocol"] == 0x01:
                ser.timeout = 20.0
                return ser, p.device, info
            ser.close()
        except Exception as e:
            errors.append(f"{p.device}: {e}")
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
    raise RuntimeError(
        "Nie znalazlem LiteVNA. Zamknij inne aplikacje korzystajace z COM.\n"
        + "\n".join(errors)
    )


def connect(port: str | None):
    if port:
        ser = try_open_port(port)
        info = identify(ser)
        return ser, port, info
    return auto_find_port()


def current_sweep(ser):
    resync(ser)
    write1(ser, REG_DATA_MODE, MODE_NORMAL)
    time.sleep(0.02)
    start = read8(ser, REG_SWEEP_START)
    step = read8(ser, REG_SWEEP_STEP)
    points = read2(ser, REG_SWEEP_POINTS)
    avg = read1(ser, REG_AVERAGE)
    return start, step, points, avg


def read_exact(ser, n: int) -> bytes:
    out = bytearray()
    deadline = time.monotonic() + max(ser.timeout or 20.0, 1.0)
    while len(out) < n:
        chunk = ser.read(n - len(out))
        if chunk:
            out.extend(chunk)
            continue
        if time.monotonic() > deadline:
            raise TimeoutError(f"FIFO: odebrano {len(out)} z {n} bajtow")
    return bytes(out)


def request_fifo_records(ser, count: int) -> bytes:
    if not (1 <= count <= 255):
        raise ValueError("FIFO count musi byc 1..255")
    ser.write(bytes((CMD_READFIFO, REG_VALUES_FIFO, count)))
    return read_exact(ser, count * FIFO_RECORD_SIZE)


def parse_record(rec: bytes):
    if len(rec) != FIFO_RECORD_SIZE:
        raise ValueError("Nieprawidlowy rekord FIFO")
    fwd_re, fwd_im, rev0_re, rev0_im, rev1_re, rev1_im, idx = struct.unpack(
        "<iiiiiiH", rec[:26]
    )
    return idx, complex(fwd_re, fwd_im), complex(rev0_re, rev0_im), complex(rev1_re, rev1_im)


def acquire_grid(ser, start_hz: int, step_hz: int, points: int, avg: int, mode: int,
                 channel: int = CHANNEL_S11):
    if channel not in (CHANNEL_BOTH, CHANNEL_S11, CHANNEL_S21):
        raise ValueError("Nieobslugiwany kanal")
    if points < 2 or points > 65535:
        raise ValueError("points musi byc 2..65535")
    if not (1 <= avg <= 80):
        raise ValueError("avg musi byc 1..80")
    if step_hz < 1:
        raise ValueError("step musi byc >= 1 Hz")

    resync(ser)
    write1(ser, REG_DATA_MODE, mode)
    write1(ser, REG_CHANNEL_SELECT, channel)
    write1(ser, REG_AVERAGE, avg)
    write8(ser, REG_SWEEP_START, start_hz)
    write8(ser, REG_SWEEP_STEP, step_hz)
    write2(ser, REG_SWEEP_POINTS, points)
    write2(ser, REG_VALUES_PER_FREQ, 1)

    # Flush FIFO. Sweep dziala ciagle i moze byc w dowolnym miejscu.
    write1(ser, REG_VALUES_FIFO, 0)
    ser.reset_input_buffer()
    time.sleep(0.03)

    by_index = {}
    max_records = points * 4
    records_seen = 0

    while len(by_index) < points and records_seen < max_records:
        n = min(255, max(1, points - len(by_index)))
        raw = request_fifo_records(ser, n)
        for off in range(0, len(raw), FIFO_RECORD_SIZE):
            idx, fwd, rev0, rev1 = parse_record(raw[off:off + FIFO_RECORD_SIZE])
            records_seen += 1
            if idx >= points or abs(fwd) < 1e-30:
                continue
            if channel == CHANNEL_BOTH:
                by_index[idx] = (rev0 / fwd, rev1 / fwd)
            else:
                by_index[idx] = (rev0 if channel == CHANNEL_S11 else rev1) / fwd

    missing = [i for i in range(points) if i not in by_index]
    if missing:
        raise RuntimeError(
            f"Brakuje {len(missing)} punktow FIFO; pierwsze: {missing[:10]}"
        )
    freqs = [start_hz + i * step_hz for i in range(points)]
    return freqs, [by_index[i] for i in range(points)]


def acquire_s11_grid(ser, start_hz, step_hz, points, avg, mode):
    return acquire_grid(ser, start_hz, step_hz, points, avg, mode, CHANNEL_S11)


def acquire_s21_grid(ser, start_hz, step_hz, points, avg, mode):
    return acquire_grid(ser, start_hz, step_hz, points, avg, mode, CHANNEL_S21)


def acquire_s11_s21_grid(ser, start_hz, step_hz, points, avg, mode):
    freqs, pairs = acquire_grid(ser, start_hz, step_hz, points, avg, mode, CHANNEL_BOTH)
    return freqs, [v[0] for v in pairs], [v[1] for v in pairs]


def make_grid(start_hz: float, stop_hz: float, points: int):
    if stop_hz <= start_hz:
        raise ValueError("stop musi byc > start")
    if points < 2:
        raise ValueError("points musi byc >= 2")
    start = int(round(start_hz))
    step = int(round((stop_hz - start_hz) / (points - 1)))
    if step < 1:
        raise ValueError("Krok wyszedl < 1 Hz")
    return start, step, points


def gamma_to_z(g: complex, z0: float = Z0_DEFAULT) -> complex:
    d = 1 - g
    if abs(d) < 1e-20:
        return complex(float("inf"), float("inf"))
    return z0 * (1 + g) / d


def z_to_gamma(z: complex, z0: float = Z0_DEFAULT) -> complex:
    return (z - z0) / (z + z0)


def solve3_complex(M, b):
    """3x3 Gaussian elimination, complex, bez numpy."""
    a = [[complex(M[r][c]) for c in range(3)] + [complex(b[r])] for r in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-18:
            raise ArithmeticError("Osobliwy uklad kalibracji")
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        for c in range(col, 4):
            a[col][c] /= p
        for r in range(3):
            if r == col:
                continue
            q = a[r][col]
            for c in range(col, 4):
                a[r][c] -= q * a[col][c]
    return a[0][3], a[1][3], a[2][3]


def fit_sol_point(m_open: complex, m_short: complex, m_load: complex, gamma_load: complex):
    # Mobius: m = (A + B*g) / (1 - C*g)
    # => A + B*g + m*C*g = m
    gs = [1 + 0j, -1 + 0j, gamma_load]
    ms = [m_open, m_short, m_load]
    M = [[1 + 0j, g, m * g] for g, m in zip(gs, ms)]
    A, B, C = solve3_complex(M, ms)
    return A, B, C


def apply_sol_point(m: complex, A: complex, B: complex, C: complex):
    den = B + m * C
    if abs(den) < 1e-20:
        return complex(float("nan"), float("nan"))
    return (m - A) / den


def save_cal(path: Path, *, start: int, step: int, points: int, avg: int, z0: float,
             load_r: float, coeffs, info: dict, standards_prefix: str | None = None):
    obj = {
        "format": "LiteVNA-Python-SOL-1port-v1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "grid": {"start_hz": start, "step_hz": step, "points": points, "avg": avg},
        "z0_ohm": z0,
        "standards": {
            "open_gamma": [1.0, 0.0],
            "short_gamma": [-1.0, 0.0],
            "load_r_ohm": load_r,
            "load_gamma": [z_to_gamma(complex(load_r, 0), z0).real, 0.0],
        },
        "device": info,
        "coefficients": [
            [A.real, A.imag, B.real, B.imag, C.real, C.imag] for A, B, C in coeffs
        ],
    }
    if standards_prefix:
        obj["standard_files_prefix"] = standards_prefix
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def load_cal(path: Path):
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("format") == ER_CAL_FORMAT:
        obj, _, _ = load_s21_cal(path)
        return obj, decode_sol_coefficients(obj)
    if obj.get("format") != "LiteVNA-Python-SOL-1port-v1":
        raise ValueError("Nieznany format pliku kalibracji")
    coeffs = []
    for row in obj["coefficients"]:
        coeffs.append((complex(row[0], row[1]), complex(row[2], row[3]), complex(row[4], row[5])))
    return obj, coeffs


def write_s1p(path: Path, freqs, s11, comment: str, z0: float = Z0_DEFAULT):
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write(f"! {comment}\n")
        f.write(f"! created {dt.datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"# Hz S RI R {z0:g}\n")
        for hz, g in zip(freqs, s11):
            f.write(f"{hz:.0f} {g.real:.12e} {g.imag:.12e}\n")


def safe_log10(x):
    return math.log10(max(x, 1e-300))


def derived_values(f: float, g: complex, z0: float):
    z = gamma_to_z(g, z0)
    R, X = z.real, z.imag
    mag = abs(g)
    phase = math.degrees(cmath.phase(g))
    w = 2 * math.pi * f if f > 0 else float("nan")

    Ls = X / w if f > 0 else float("nan")
    Cs = (-1.0 / (w * X)) if (f > 0 and X < 0) else float("nan")
    Qs = abs(X) / R if R > 0 else float("nan")

    if abs(z) > 1e-300 and math.isfinite(z.real) and math.isfinite(z.imag):
        y = 1 / z
    else:
        y = complex(float("nan"), float("nan"))
    G, Bb = y.real, y.imag
    Rp = 1.0 / G if abs(G) > 1e-30 else float("inf")
    Xp = -1.0 / Bb if abs(Bb) > 1e-30 else float("inf")
    Lp = 1.0 / (w * (-Bb)) if (f > 0 and Bb < 0) else float("nan")
    Cp = Bb / w if (f > 0 and Bb > 0) else float("nan")

    return {
        "freq_Hz": f,
        "S11_re": g.real,
        "S11_im": g.imag,
        "R_ohm": R,
        "X_ohm": X,
        "logmag_dB": 20 * safe_log10(mag),
        "linmag": mag,
        "phase_deg": phase,
        "SWR": (1 + mag) / (1 - mag) if mag < 1 else float("inf"),
        "return_loss_dB": -20 * safe_log10(mag),
        "series_R_ohm": R,
        "series_X_ohm": X,
        "series_L_H": Ls,
        "series_L_uH": Ls * 1e6,
        "series_C_F": Cs,
        "series_C_pF": Cs * 1e12,
        "series_Q": Qs,
        "G_S": G,
        "B_S": Bb,
        "parallel_R_ohm": Rp,
        "parallel_X_ohm": Xp,
        "parallel_L_H": Lp,
        "parallel_L_uH": Lp * 1e6,
        "parallel_C_F": Cp,
        "parallel_C_pF": Cp * 1e12,
    }


FORMAT_COLUMNS = {
    "rjx": ["R_ohm", "X_ohm"],
    "logmag": ["logmag_dB"],
    "linmag": ["linmag"],
    "phase": ["phase_deg"],
    "swr": ["SWR"],
    "return-loss": ["return_loss_dB"],
    "series-r": ["series_R_ohm"],
    "series-x": ["series_X_ohm"],
    "series-l": ["series_L_H", "series_L_uH"],
    "series-c": ["series_C_F", "series_C_pF"],
    "series-q": ["series_Q"],
    "admittance": ["G_S", "B_S"],
    "parallel-r": ["parallel_R_ohm"],
    "parallel-x": ["parallel_X_ohm"],
    "parallel-l": ["parallel_L_H", "parallel_L_uH"],
    "parallel-c": ["parallel_C_F", "parallel_C_pF"],
}


def parse_formats(text: str):
    requested = []
    for token in text.split(","):
        t = token.strip().lower()
        if not t:
            continue
        if t == "all":
            return ALL_FORMATS[:]
        if t not in FORMAT_ALIASES:
            raise ValueError(f"Nieznany format '{t}'. Dostepne: all, " + ", ".join(sorted(FORMAT_ALIASES)))
        canonical = FORMAT_ALIASES[t]
        if canonical not in requested:
            requested.append(canonical)
    return requested or ["rjx"]


def write_csv(path: Path, freqs, s11, formats, z0):
    cols = ["freq_Hz", "S11_re", "S11_im"]
    for fmt in formats:
        for c in FORMAT_COLUMNS[fmt]:
            if c not in cols:
                cols.append(c)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for freq, g in zip(freqs, s11):
            vals = derived_values(freq, g, z0)
            w.writerow({c: vals[c] for c in cols})


def nearest_index(freqs, target):
    return min(range(len(freqs)), key=lambda i: abs(freqs[i] - target))


def print_marker(freqs, s11, target, formats, z0):
    i = nearest_index(freqs, target)
    vals = derived_values(freqs[i], s11[i], z0)
    print(f"Marker: {freqs[i]:.0f} Hz")
    print(f"S11 = {vals['S11_re']:+.8f} {vals['S11_im']:+.8f}j")
    print(f"Z = {vals['R_ohm']:.6g} {vals['X_ohm']:+.6g}j ohm")
    for fmt in formats:
        if fmt == "rjx":
            continue
        parts = [f"{c}={vals[c]:.8g}" for c in FORMAT_COLUMNS[fmt]]
        print(f"{fmt}: " + ", ".join(parts))


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def cmd_info(args):
    ser = None
    try:
        ser, port_name, info = connect(args.port)
        start, step, points, avg = current_sweep(ser)
        print(f"Port: {port_name}")
        print(f"Device: variant=0x{info['variant']:02X}, protocol={info['protocol']}, HW={info['hw']}, FW={info['fw_major']}.{info['fw_minor']}")
        print(f"Current sweep: start={start} Hz, step={step} Hz, points={points}, stop={start + step*(points-1)} Hz, avg={avg}")
    finally:
        if ser:
            ser.close()


def cmd_calibrate(args):
    ser = None
    try:
        ser, port_name, info = connect(args.port)
        start, step, points = make_grid(args.start, args.stop, args.points)
        z0 = args.z0
        gamma_load = z_to_gamma(complex(args.load, 0), z0)
        print(f"Polaczono: {port_name}; HW={info['hw']} FW={info['fw_major']}.{info['fw_minor']}")
        print(f"SOL: {start} .. {start + step*(points-1)} Hz, {points} pkt, AVG={args.avg}")
        print(f"LOAD = {args.load:.9g} ohm -> Gamma={gamma_load.real:+.9f}{gamma_load.imag:+.9f}j dla Z0={z0:g} ohm")
        print("Kalibracja jest wykonywana w Pythonie z NIEKORYGOWANEGO S11.")

        input("\nPodlacz OPEN dokladnie w reference plane i nacisnij Enter... ")
        f_open, m_open = acquire_s11_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        print("OPEN pobrany.")

        input("Podlacz SHORT w tym samym miejscu i nacisnij Enter... ")
        f_short, m_short = acquire_s11_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        print("SHORT pobrany.")

        input(f"Podlacz LOAD {args.load:g} ohm w tym samym miejscu i nacisnij Enter... ")
        f_load, m_load = acquire_s11_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        print("LOAD pobrany.")

        coeffs = [
            fit_sol_point(m_open[i], m_short[i], m_load[i], gamma_load)
            for i in range(points)
        ]

        out = Path(args.out)
        ensure_parent(out)
        prefix = out.with_suffix("")
        open_path = Path(str(prefix) + "_OPEN_raw.s1p")
        short_path = Path(str(prefix) + "_SHORT_raw.s1p")
        load_path = Path(str(prefix) + "_LOAD_raw.s1p")
        write_s1p(open_path, f_open, m_open, "raw OPEN used for software SOL", z0)
        write_s1p(short_path, f_short, m_short, "raw SHORT used for software SOL", z0)
        write_s1p(load_path, f_load, m_load, f"raw LOAD {args.load:g} ohm used for software SOL", z0)
        save_cal(
            out, start=start, step=step, points=points, avg=args.avg, z0=z0,
            load_r=args.load, coeffs=coeffs, info=info,
            standards_prefix=str(prefix),
        )
        print(f"\nZapisano kalibracje: {out.resolve()}")
        print(f"oraz surowe standardy: {open_path.name}, {short_path.name}, {load_path.name}")
        print("Do niezaleznego sanity-checku uzyj INNEGO rezystora, np. pojedynczego 47.5 ohm.")
    finally:
        if ser:
            try:
                resync(ser)
                write1(ser, REG_DATA_MODE, MODE_NORMAL)
            except Exception:
                pass
            ser.close()


def cmd_measure(args):
    ser = None
    try:
        ser, port_name, info = connect(args.port)
        z0 = args.z0
        cal_obj = None
        coeffs = None

        if args.cal:
            cal_obj, coeffs = load_cal(Path(args.cal))
            g = cal_obj["grid"]
            start, step, points = int(g["start_hz"]), int(g["step_hz"]), int(g["points"])
            avg = args.avg if args.avg is not None else int(g.get("avg", 2))
            z0 = float(cal_obj.get("z0_ohm", z0))
            mode = MODE_USB_UNCAL
            source_desc = f"software SOL: {args.cal}"
            if args.current or args.start is not None or args.stop is not None or args.points is not None:
                print("UWAGA: przy --cal siatka czestotliwosci jest brana z pliku kalibracji; --current/start/stop/points sa ignorowane.")
        else:
            if args.current:
                start, step, points, current_avg = current_sweep(ser)
                avg = args.avg if args.avg is not None else current_avg
            else:
                start_hz = parse_eng("400k") if args.start is None else args.start
                stop_hz = parse_eng("2M") if args.stop is None else args.stop
                points_arg = 1024 if args.points is None else args.points
                start, step, points = make_grid(start_hz, stop_hz, points_arg)
                avg = 2 if args.avg is None else args.avg
            if args.raw:
                mode = MODE_USB_UNCAL
                source_desc = "raw/uncalibrated S11"
            else:
                mode = MODE_USB_CAL
                source_desc = "calibrated by LiteVNA device (mode 3)"

        print(f"Polaczono: {port_name}; {source_desc}")
        print(f"Sweep: {start} .. {start + step*(points-1)} Hz, step={step} Hz, points={points}, AVG={avg}")
        freqs, measured = acquire_s11_grid(ser, start, step, points, avg, mode)

        if coeffs is not None:
            if len(coeffs) != len(measured):
                raise RuntimeError("Liczba punktow kalibracji nie zgadza sie z pomiarem")
            s11 = [apply_sol_point(measured[i], *coeffs[i]) for i in range(points)]
        else:
            s11 = measured

        formats = parse_formats(args.format)
        out = Path(args.out)
        ensure_parent(out)
        write_s1p(out, freqs, s11, source_desc, z0)
        csv_path = out.with_suffix(".csv")
        write_csv(csv_path, freqs, s11, formats, z0)

        print(f"Zapisano S1P: {out.resolve()}")
        print(f"Zapisano CSV ({','.join(formats)}): {csv_path.resolve()}")
        marker = parse_eng("431.281k") if args.marker is None else args.marker
        print_marker(freqs, s11, marker, formats, z0)
    finally:
        if ser:
            try:
                resync(ser)
                write1(ser, REG_DATA_MODE, MODE_NORMAL)
                time.sleep(0.02)
            except Exception:
                pass
            ser.close()


S21_CAL_FORMAT = "LiteVNA-Python-S21-response-v1"
ER_CAL_FORMAT = "LiteVNA-Python-EnhancedResponse-v1"


def validate_s21_grid(start, step, points, avg):
    if not (2 <= points <= 65535 and 1 <= avg <= 80 and step >= 1 and start >= 1):
        raise ValueError("Nieprawidlowa siatka: start/step >= 1, points 2..65535, avg 1..80")
    if start + step * (points - 1) >= 2**64:
        raise ValueError("Czestotliwosc poza zakresem protokolu")


def finite_complex(value):
    return math.isfinite(value.real) and math.isfinite(value.imag)


def s21_tracking(thru, isolation):
    if not thru or len(thru) != len(isolation):
        raise ValueError("Niezgodne dlugosci THRU/ISOLATION")
    tracking = []
    for i, (t, iso) in enumerate(zip(thru, isolation)):
        gain = t - iso
        if not finite_complex(t) or not finite_complex(iso) or abs(gain) < 1e-12:
            raise ValueError(f"Nieprawidlowa kalibracja S21 w punkcie {i}: brak sygnalu THRU")
        tracking.append(gain)
    return tracking


def apply_s21(measured, thru, isolation):
    tracking = s21_tracking(thru, isolation)
    if len(measured) != len(tracking):
        raise ValueError("Liczba punktow kalibracji S21 nie zgadza sie z pomiarem")
    return [(m - iso) / gain for m, iso, gain in zip(measured, isolation, tracking)]


def load_s21_cal(path):
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("format") not in (S21_CAL_FORMAT, ER_CAL_FORMAT):
        raise ValueError("Oczekiwano kalibracji z calibrate-er lub calibrate-s21 (sama SOL S11 nie pasuje)")
    grid = obj["grid"]
    values = [grid[k] for k in ("start_hz", "step_hz", "points", "avg")]
    if any(type(v) is not int for v in values):
        raise ValueError("Siatka kalibracji S21 musi zawierac liczby calkowite")
    validate_s21_grid(*values)
    thru = [complex(*v) for v in obj["thru"]]
    isolation = [complex(*v) for v in obj["isolation"]]
    if len(thru) != grid["points"]:
        raise ValueError("Liczba punktow THRU nie zgadza sie z siatka")
    s21_tracking(thru, isolation)
    if obj["format"] == ER_CAL_FORMAT:
        coeffs = decode_sol_coefficients(obj)
        thru_s11 = [complex(*v) for v in obj["thru_s11"]]
        er_tracking(thru, isolation, thru_s11, coeffs)
        if obj.get("z0_ohm") != 50.0:
            raise ValueError("Enhanced Response wymaga Z0=50 ohm")
    return obj, thru, isolation


def write_s21_csv(path, freqs, values, source):
    # CSV preserves S21 explicitly; S1P would incorrectly label it as S11.
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["freq_Hz", "S21_re", "S21_im", "S21_logmag_dB",
                         "S21_phase_deg", "S21_linmag", "insertion_loss_dB", "source"])
        for freq, value in zip(freqs, values):
            magnitude = abs(value)
            db = 20 * math.log10(magnitude) if magnitude else -math.inf
            writer.writerow([freq, value.real, value.imag, db,
                             math.degrees(cmath.phase(value)), magnitude, -db, source])


def close_s21_session(ser):
    if ser is not None:
        try:
            resync(ser)
            write1(ser, REG_CHANNEL_SELECT, 0)  # restore both channels for normal UI
            write1(ser, REG_DATA_MODE, MODE_NORMAL)
        finally:
            ser.close()


def cmd_calibrate_s21(args):
    start, step, points = make_grid(args.start, args.stop, args.points)
    validate_s21_grid(start, step, points, args.avg)
    ser = None
    try:
        ser, port, info = connect(args.port)
        print(f"Polaczono: {port}; S21 THRU, {start} .. {start + step*(points-1)} Hz")
        print("Korekcja programowa w Pythonie; nie zapisuje kalibracji w LiteVNA.")
        isolation = [0j] * points
        if args.isolation:
            input("ISOLATION: rozlacz tor; zakoncz oba konce kabli obciazeniami 50 ohm. Enter... ")
            freqs, isolation = acquire_s21_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        input("THRU: polacz konce kabli S11/CH0 i S21/CH1 zlaczem THRU zamiast DUT. Enter... ")
        freqs, thru = acquire_s21_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        s21_tracking(thru, isolation)
        out = Path(args.out)
        ensure_parent(out)
        prefix = out.with_suffix("")
        write_s21_csv(Path(str(prefix) + "_THRU_raw.csv"), freqs, thru, "raw THRU")
        if args.isolation:
            write_s21_csv(Path(str(prefix) + "_ISOLATION_raw.csv"), freqs, isolation, "raw ISOLATION")
        obj = {
            "format": S21_CAL_FORMAT,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "grid": {"start_hz": start, "step_hz": step, "points": points, "avg": args.avg},
            "device": info,
            "z0_ohm": 50.0,
            "model": "ideal THRU S21=1; response correction, no mismatch correction",
            "isolation_measured": args.isolation,
            "thru": [[v.real, v.imag] for v in thru],
            "isolation": [[v.real, v.imag] for v in isolation],
        }
        out.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Zapisano kalibracje S21: {out.resolve()}")
    finally:
        close_s21_session(ser)


def cmd_measure_s21(args):
    # Validate files and output before contacting hardware.
    out = Path(args.out)
    if out.suffix.lower() != ".csv":
        raise ValueError("S21 wymaga --out plik.csv; S1P oznacza S11, a S12/S22 nie sa mierzone")
    cal = load_s21_cal(Path(args.cal)) if args.cal else None
    ser = None
    try:
        ser, port, info = connect(args.port)
        if cal:
            obj, thru, isolation = cal
            grid = obj["grid"]
            start, step, points = (grid[k] for k in ("start_hz", "step_hz", "points"))
            avg = grid["avg"] if args.avg is None else args.avg
            if args.current or any(v is not None for v in (args.start, args.stop, args.points)):
                print("UWAGA: --cal narzuca siatke; --current/start/stop/points sa ignorowane.")
            kind = "Enhanced Response" if obj["format"] == ER_CAL_FORMAT else "S21 response"
            source = f"software {kind}: {args.cal}"
            mode = MODE_USB_UNCAL
        else:
            if args.current:
                start, step, points, avg = current_sweep(ser)
            else:
                start, step, points = make_grid(
                    400_000 if args.start is None else args.start,
                    2_000_000 if args.stop is None else args.stop,
                    1024 if args.points is None else args.points)
                avg = 2
            if args.avg is not None:
                avg = args.avg
            mode = MODE_USB_UNCAL if args.raw else MODE_USB_CAL
            source = "raw S21" if args.raw else "LiteVNA device calibration (mode 3)"
        validate_s21_grid(start, step, points, avg)
        print(f"Polaczono: {port}; {source}; {points} pkt, AVG={avg}")
        s11 = None
        if cal and obj["format"] == ER_CAL_FORMAT:
            freqs, raw_s11, raw_s21 = acquire_s11_s21_grid(ser, start, step, points, avg, mode)
            coeffs = decode_sol_coefficients(obj)
            thru_s11 = [complex(*v) for v in obj["thru_s11"]]
            s11, values = apply_er(raw_s11, raw_s21, thru, isolation, thru_s11, coeffs)
        else:
            freqs, values = acquire_s21_grid(ser, start, step, points, avg, mode)
            if cal:
                values = apply_s21(values, thru, isolation)
        ensure_parent(out)
        write_s21_csv(out, freqs, values, source)
        print(f"Zapisano S21 CSV: {out.resolve()}")
        if s11 is not None:
            s11_path = out.with_name(out.stem + "_s11.s1p")
            write_s1p(s11_path, freqs, s11, "SOL S11 from Enhanced Response acquisition", 50.0)
            print(f"Zapisano S11 z tego samego sweepu: {s11_path.resolve()}")
        if args.marker is not None:
            i = nearest_index(freqs, args.marker)
            value = values[i]
            db = 20 * math.log10(abs(value)) if value else -math.inf
            print(f"Marker: {freqs[i]} Hz; S21={value}; {db:.6g} dB; {math.degrees(cmath.phase(value)):.6g} deg")
    finally:
        close_s21_session(ser)


def decode_sol_coefficients(obj):
    rows = obj["coefficients"]
    if len(rows) != obj["grid"]["points"] or any(len(row) != 6 for row in rows):
        raise ValueError("Nieprawidlowa liczba wspolczynnikow SOL")
    coeffs = [(complex(r[0], r[1]), complex(r[2], r[3]), complex(r[4], r[5])) for r in rows]
    for A, B, C in coeffs:
        if not all(finite_complex(v) for v in (A, B, C)) or abs(B + A*C) < 1e-12:
            raise ValueError("Osobliwa lub niefinitywna kalibracja SOL")
    return coeffs


def er_source_factors(raw_s11, coeffs):
    if len(raw_s11) != len(coeffs):
        raise ValueError("Liczba punktow S11 nie zgadza sie z SOL")
    corrected, factors = [], []
    for i, (m, (A, B, C)) in enumerate(zip(raw_s11, coeffs)):
        g = apply_sol_point(m, A, B, C)
        factor = 1 - C*g
        if not finite_complex(g) or not finite_complex(factor) or abs(factor) < 1e-12:
            raise ValueError(f"Osobliwa korekcja Enhanced Response w punkcie {i}")
        corrected.append(g)
        factors.append(factor)
    return corrected, factors


def er_tracking(thru, isolation, thru_s11, coeffs):
    # C is source match in m11=(A+B*g)/(1-C*g).
    # With ideal THRU, SOL-corrected reflection is the receiver load match.
    # Account for its interaction with source match during THRU acquisition.
    _, factors = er_source_factors(thru_s11, coeffs)
    gains = s21_tracking(thru, isolation)
    if len(gains) != len(factors):
        raise ValueError("Niezgodne siatki THRU i SOL")
    tracking = [gain * factor for gain, factor in zip(gains, factors)]
    if any(not finite_complex(v) or abs(v) < 1e-12 for v in tracking):
        raise ValueError("Osobliwy tracking Enhanced Response")
    return tracking


def apply_er(raw_s11, raw_s21, thru, isolation, thru_s11, coeffs):
    tracking = er_tracking(thru, isolation, thru_s11, coeffs)
    s11, factors = er_source_factors(raw_s11, coeffs)
    if len(raw_s21) != len(tracking):
        raise ValueError("Liczba punktow S21 nie zgadza sie z kalibracja")
    s21 = [(m - iso)*factor/gain
           for m, iso, factor, gain in zip(raw_s21, isolation, factors, tracking)]
    if any(not finite_complex(v) for v in s21):
        raise ValueError("Niefinitywny wynik S21")
    return s11, s21


def cmd_calibrate_er(args):
    start, step, points = make_grid(args.start, args.stop, args.points)
    validate_s21_grid(start, step, points, args.avg)
    if not math.isfinite(args.load) or args.load <= 0:
        raise ValueError("LOAD musi byc skonczona, dodatnia rezystancja")
    gamma_load = z_to_gamma(complex(args.load), 50.0)
    ser = None
    try:
        ser, port, info = connect(args.port)
        print(f"Polaczono: {port}; Enhanced Response SOL + THRU; {points} pkt, AVG={args.avg}")
        print("Kalibracja programowa Z0=50 ohm; zapis JSON, bez zapisu do LiteVNA.")
        standards = {}
        for name in ("OPEN", "SHORT", "LOAD"):
            detail = f" {args.load:g} ohm" if name == "LOAD" else ""
            input(f"Podlacz {name}{detail} na koncu kabla S11/CH0. Enter... ")
            freqs, standards[name] = acquire_s11_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        coeffs = [fit_sol_point(o, s, l, gamma_load)
                  for o, s, l in zip(standards["OPEN"], standards["SHORT"], standards["LOAD"])]
        isolation = [0j] * points
        if args.isolation:
            input("ISOLATION: rozlacz tor; zakoncz oba kable osobnymi obciazeniami 50 ohm. Enter... ")
            freqs, isolation = acquire_s21_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        input("THRU: polacz konce kabli S11/CH0 i S21/CH1 przelotka zamiast DUT. Enter... ")
        freqs, thru_s11, thru = acquire_s11_s21_grid(ser, start, step, points, args.avg, MODE_USB_UNCAL)
        obj = {
            "format": ER_CAL_FORMAT,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "grid": {"start_hz": start, "step_hz": step, "points": points, "avg": args.avg},
            "device": info, "z0_ohm": 50.0, "load_r_ohm": args.load,
            "model": "SOL + ideal THRU; source-match corrected S21; residual DUT load-match error",
            "isolation_measured": args.isolation,
            "coefficients": [[A.real, A.imag, B.real, B.imag, C.real, C.imag] for A, B, C in coeffs],
            "thru_s11": [[v.real, v.imag] for v in thru_s11],
            "thru": [[v.real, v.imag] for v in thru],
            "isolation": [[v.real, v.imag] for v in isolation],
        }
        decode_sol_coefficients(obj)
        er_tracking(thru, isolation, thru_s11, coeffs)
        out = Path(args.out)
        ensure_parent(out)
        prefix = str(out.with_suffix(""))
        for name, values in standards.items():
            write_s1p(Path(prefix + f"_{name}_raw.s1p"), freqs, values, "raw SOL standard", 50.0)
        write_s1p(Path(prefix + "_THRU_S11_raw.s1p"), freqs, thru_s11, "raw THRU S11", 50.0)
        write_s21_csv(Path(prefix + "_THRU_raw.csv"), freqs, thru, "raw THRU S21")
        if args.isolation:
            write_s21_csv(Path(prefix + "_ISOLATION_raw.csv"), freqs, isolation, "raw ISOLATION")
        out.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Zapisano wspolna kalibracje S11/S21: {out.resolve()}")
    finally:
        close_s21_session(ser)


def build_parser():
    ap = argparse.ArgumentParser(
        description="LiteVNA CLI: S11 SOL, S21 Enhanced Response/THRU i eksport danych"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="pokaz informacje o LiteVNA i aktualny sweep")
    p.add_argument("--port")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("calibrate", help="wlasna 1-port SOL z dowolnym R LOAD")
    p.add_argument("--port")
    p.add_argument("--start", type=parse_eng, default=parse_eng("400k"))
    p.add_argument("--stop", type=parse_eng, default=parse_eng("2M"))
    p.add_argument("--points", type=int, default=1024)
    p.add_argument("--avg", type=int, default=2)
    p.add_argument("--load", type=float, required=True, help="rzeczywista rezystancja LOAD w ohmach, np. 23.75")
    p.add_argument("--z0", type=float, default=Z0_DEFAULT)
    p.add_argument("--out", default="litevna_1port.lvcal.json")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("measure", help="pobierz S11 i zapisz S1P + CSV")
    p.add_argument("--port")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--device-cal", action="store_true", help="uzyj kalibracji aktywnej w LiteVNA (domyslne)")
    src.add_argument("--raw", action="store_true", help="bez korekcji")
    src.add_argument("--cal", help="plik *.lvcal.json z komendy calibrate")
    p.add_argument("--current", action="store_true", help="uzyj aktualnego zakresu/points ustawionych na ekranie LiteVNA")
    p.add_argument("--start", type=parse_eng)
    p.add_argument("--stop", type=parse_eng)
    p.add_argument("--points", type=int)
    p.add_argument("--avg", type=int)
    p.add_argument("--marker", type=parse_eng)
    p.add_argument("--z0", type=float, default=Z0_DEFAULT)
    p.add_argument("--format", default="rjx", help="np. rjx,logmag,series-l,series-c albo all")
    p.add_argument("--out", default="litevna_measurement.s1p")
    p.set_defaults(func=cmd_measure)
    p = sub.add_parser("calibrate-s21", help="kalibracja transmisji THRU, opcjonalnie ISOLATION")
    p.add_argument("--port")
    p.add_argument("--start", type=parse_eng, default=400_000)
    p.add_argument("--stop", type=parse_eng, default=2_000_000)
    p.add_argument("--points", type=int, default=1024)
    p.add_argument("--avg", type=int, default=2)
    p.add_argument("--isolation", action="store_true", help="zmierz przesluch przed THRU")
    p.add_argument("--out", default="litevna_s21.lvcal.json")
    p.set_defaults(func=cmd_calibrate_s21)

    p = sub.add_parser("measure-s21", help="pomiar transmisji S21 do CSV")
    p.add_argument("--port")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--device-cal", action="store_true", help="kalibracja LiteVNA, domyslnie")
    src.add_argument("--raw", action="store_true")
    src.add_argument("--cal", help="plik z calibrate-er lub calibrate-s21")
    p.add_argument("--current", action="store_true")
    p.add_argument("--start", type=parse_eng)
    p.add_argument("--stop", type=parse_eng)
    p.add_argument("--points", type=int)
    p.add_argument("--avg", type=int)
    p.add_argument("--marker", type=parse_eng)
    p.add_argument("--out", default="litevna_s21.csv")
    p.set_defaults(func=cmd_measure_s21)
    p = sub.add_parser("calibrate-er", help="wspolna kalibracja S11/S21 Enhanced Response: SOL + THRU")
    p.add_argument("--port")
    p.add_argument("--start", type=parse_eng, default=400_000)
    p.add_argument("--stop", type=parse_eng, default=2_000_000)
    p.add_argument("--points", type=int, default=1024)
    p.add_argument("--avg", type=int, default=2)
    p.add_argument("--load", type=float, default=50.0, help="rezystancja LOAD SOL, domyslnie 50 ohm")
    p.add_argument("--isolation", action="store_true")
    p.add_argument("--out", default="litevna_er.lvcal.json")
    p.set_defaults(func=cmd_calibrate_er)
    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nPrzerwano.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"BLAD: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
