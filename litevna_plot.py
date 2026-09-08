#!/usr/bin/env python3
"""
Plotter dla plikow LiteVNA/NanoVNA: .s1p oraz .csv.

Nie zmienia formatu plikow pomiarowych. Czyta standardowy Touchstone S1P
albo CSV generowany przez litevna_cli.py i rysuje wybrana reprezentacje.

Wymagania:
    pip install numpy matplotlib

Przyklady:
    py litevna_plot.py sec_primary23R75.s1p
    py litevna_plot.py sec_primary23R75.s1p --mode rjx
    py litevna_plot.py sec_primary23R75.s1p --mode logmag
    py litevna_plot.py sec_primary23R75.s1p --mode series-l --marker 431.281k
    py litevna_plot.py sec_primary23R75.s1p --mode rjx,series-l,logmag
    py litevna_plot.py sec_primary23R75.csv --mode smith
    py litevna_plot.py sec_primary23R75.s1p --mode all --save-dir plots --no-show

Dostepne tryby:
    rjx, impedance, series-r, series-x, series-l, series-c, series-q,
    logmag, linmag, phase, return-loss, swr,
    admittance, parallel-r, parallel-x, parallel-l, parallel-c,
    smith, all

Uwagi:
- Series L jest pokazywane tylko tam, gdzie X > 0.
- Series C jest pokazywane tylko tam, gdzie X < 0.
- Analogicznie dla parallel L/C na podstawie susceptancji.
- Smith pokazuje trase S11 w plaszczyznie Gamma (bez pelnej siatki Smitha).
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt


Z0_DEFAULT = 50.0

ALL_MODES = [
    "rjx",
    "series-l",
    "series-c",
    "series-q",
    "logmag",
    "phase",
    "swr",
    "admittance",
    "parallel-l",
    "parallel-c",
    "smith",
]


def parse_eng(text: str) -> float:
    t = text.strip().replace("Hz", "").replace("hz", "")
    mult = 1.0
    if t and t[-1] in "kK":
        mult = 1e3
        t = t[:-1]
    elif t and t[-1] in "mM":
        mult = 1e6
        t = t[:-1]
    elif t and t[-1] in "gG":
        mult = 1e9
        t = t[:-1]
    return float(t) * mult


def read_s1p(path: Path):
    unit_scale = 1.0
    data_fmt = "RI"
    z0 = Z0_DEFAULT
    rows = []

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("!"):
                continue

            if line.startswith("#"):
                toks = line[1:].split()
                upper = [x.upper() for x in toks]

                if "HZ" in upper:
                    unit_scale = 1.0
                elif "KHZ" in upper:
                    unit_scale = 1e3
                elif "MHZ" in upper:
                    unit_scale = 1e6
                elif "GHZ" in upper:
                    unit_scale = 1e9

                for candidate in ("RI", "MA", "DB"):
                    if candidate in upper:
                        data_fmt = candidate
                        break

                if "R" in upper:
                    try:
                        i = upper.index("R")
                        z0 = float(toks[i + 1])
                    except Exception:
                        pass
                continue

            # usun komentarz koncowy
            if "!" in line:
                line = line.split("!", 1)[0].strip()
            if not line:
                continue

            p = line.replace(",", " ").split()
            if len(p) < 3:
                continue

            try:
                freq = float(p[0]) * unit_scale
                a = float(p[1])
                b = float(p[2])
            except ValueError:
                continue

            if data_fmt == "RI":
                g = complex(a, b)
            elif data_fmt == "MA":
                g = a * np.exp(1j * np.deg2rad(b))
            elif data_fmt == "DB":
                g = (10.0 ** (a / 20.0)) * np.exp(1j * np.deg2rad(b))
            else:
                raise ValueError(f"Nieobslugiwany format Touchstone: {data_fmt}")

            rows.append((freq, g))

    if not rows:
        raise ValueError(f"Brak danych S11 w {path}")

    freq = np.array([r[0] for r in rows], dtype=float)
    gamma = np.array([r[1] for r in rows], dtype=complex)
    return freq, gamma, float(z0)


def _find_col(names, *candidates):
    lower = {n.lower(): n for n in names}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def read_csv_file(path: Path):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        rdr = csv.DictReader(f)
        if rdr.fieldnames is None:
            raise ValueError("CSV nie ma naglowka")
        names = rdr.fieldnames

        fcol = _find_col(names, "freq_Hz", "frequency_Hz", "frequency", "freq")
        recol = _find_col(names, "S11_re", "s11_real", "re")
        imcol = _find_col(names, "S11_im", "s11_imag", "im")
        rcol = _find_col(names, "R_ohm", "series_R_ohm", "R")
        xcol = _find_col(names, "X_ohm", "series_X_ohm", "X")

        if fcol is None:
            raise ValueError("CSV: nie znaleziono kolumny czestotliwosci")

        freq, gamma = [], []

        for row in rdr:
            try:
                hz = float(row[fcol])
            except Exception:
                continue

            if recol is not None and imcol is not None:
                try:
                    g = complex(float(row[recol]), float(row[imcol]))
                except Exception:
                    continue
            elif rcol is not None and xcol is not None:
                try:
                    z = complex(float(row[rcol]), float(row[xcol]))
                    g = (z - Z0_DEFAULT) / (z + Z0_DEFAULT)
                except Exception:
                    continue
            else:
                raise ValueError(
                    "CSV: potrzebne S11_re/S11_im albo R_ohm/X_ohm"
                )

            freq.append(hz)
            gamma.append(g)

    if not freq:
        raise ValueError(f"Brak danych w {path}")

    return np.asarray(freq, float), np.asarray(gamma, complex), Z0_DEFAULT


def load_measurement(path: Path):
    ext = path.suffix.lower()
    if ext == ".s1p":
        return read_s1p(path)
    if ext == ".csv":
        return read_csv_file(path)
    raise ValueError("Obslugiwane rozszerzenia: .s1p, .csv")


def derived(freq, gamma, z0):
    eps = 1e-18
    den = 1.0 - gamma
    z = z0 * (1.0 + gamma) / den

    r = z.real
    x = z.imag
    w = 2.0 * np.pi * freq

    mag = np.abs(gamma)
    logmag = 20.0 * np.log10(np.maximum(mag, 1e-300))
    linmag = mag
    phase = np.rad2deg(np.angle(gamma))
    retloss = -logmag

    swr = np.full_like(mag, np.inf, dtype=float)
    ok = mag < 1.0
    swr[ok] = (1.0 + mag[ok]) / np.maximum(1.0 - mag[ok], eps)

    series_l = np.full_like(freq, np.nan, dtype=float)
    m = x > 0
    series_l[m] = x[m] / w[m]

    series_c = np.full_like(freq, np.nan, dtype=float)
    m = x < 0
    series_c[m] = -1.0 / (w[m] * x[m])

    series_q = np.abs(x) / np.maximum(np.abs(r), eps)

    y = 1.0 / z
    g = y.real
    b = y.imag

    parallel_r = np.full_like(freq, np.nan, dtype=float)
    m = np.abs(g) > eps
    parallel_r[m] = 1.0 / g[m]

    parallel_x = np.full_like(freq, np.nan, dtype=float)
    m = np.abs(b) > eps
    parallel_x[m] = -1.0 / b[m]

    parallel_l = np.full_like(freq, np.nan, dtype=float)
    # indukcyjny susceptance B < 0: B = -1/(wL)
    m = b < 0
    parallel_l[m] = -1.0 / (w[m] * b[m])

    parallel_c = np.full_like(freq, np.nan, dtype=float)
    # pojemnosciowy susceptance B > 0: B = wC
    m = b > 0
    parallel_c[m] = b[m] / w[m]

    return {
        "z": z,
        "r": r,
        "x": x,
        "mag": mag,
        "logmag": logmag,
        "linmag": linmag,
        "phase": phase,
        "return-loss": retloss,
        "swr": swr,
        "series-l": series_l,
        "series-c": series_c,
        "series-q": series_q,
        "y": y,
        "g": g,
        "b": b,
        "parallel-r": parallel_r,
        "parallel-x": parallel_x,
        "parallel-l": parallel_l,
        "parallel-c": parallel_c,
    }


def choose_xscale(freq, requested):
    if requested != "auto":
        scale = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}[requested]
        return scale, requested

    fmax = float(np.nanmax(freq))
    if fmax >= 1e9:
        return 1e9, "GHz"
    if fmax >= 1e6:
        return 1e6, "MHz"
    if fmax >= 1e3:
        return 1e3, "kHz"
    return 1.0, "Hz"


def nearest(freq, target):
    return int(np.nanargmin(np.abs(freq - target)))


def finite_limits(arr):
    a = np.asarray(arr, float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return float(np.min(a)), float(np.max(a))


def plot_mode(mode, freq, gamma, d, xscale, xunit, marker_hz=None):
    xfreq = freq / xscale
    fig, ax = plt.subplots(figsize=(9.2, 5.2))

    if mode in ("rjx", "impedance"):
        ax.plot(xfreq, d["r"], label="R")
        ax.plot(xfreq, d["x"], label="X")
        ax.set_ylabel("Impedance [ohm]")
        ax.set_title("Series impedance R + jX")
        ax.legend()

    elif mode == "series-r":
        ax.plot(xfreq, d["r"])
        ax.set_ylabel("Series R [ohm]")
        ax.set_title("Series resistance")

    elif mode == "series-x":
        ax.plot(xfreq, d["x"])
        ax.axhline(0, linewidth=1)
        ax.set_ylabel("Series X [ohm]")
        ax.set_title("Series reactance")

    elif mode == "series-l":
        ax.plot(xfreq, d["series-l"] * 1e6)
        ax.set_ylabel("Series L [uH]")
        ax.set_title("Series inductance (only X > 0)")

    elif mode == "series-c":
        ax.plot(xfreq, d["series-c"] * 1e12)
        ax.set_ylabel("Series C [pF]")
        ax.set_title("Series capacitance (only X < 0)")

    elif mode == "series-q":
        ax.plot(xfreq, d["series-q"])
        ax.set_ylabel("Q")
        ax.set_title("Series quality factor |X|/|R|")

    elif mode == "logmag":
        ax.plot(xfreq, d["logmag"])
        ax.set_ylabel("S11 [dB]")
        ax.set_title("Log magnitude S11")

    elif mode == "linmag":
        ax.plot(xfreq, d["linmag"])
        ax.set_ylabel("|S11|")
        ax.set_title("Linear magnitude S11")

    elif mode == "phase":
        ax.plot(xfreq, d["phase"])
        ax.set_ylabel("Phase S11 [deg]")
        ax.set_title("Phase S11")

    elif mode == "return-loss":
        ax.plot(xfreq, d["return-loss"])
        ax.set_ylabel("Return loss [dB]")
        ax.set_title("Return loss")

    elif mode == "swr":
        vals = np.array(d["swr"], copy=True)
        vals[~np.isfinite(vals)] = np.nan
        ax.plot(xfreq, vals)
        ax.set_ylabel("SWR")
        ax.set_title("VSWR")

    elif mode == "admittance":
        ax.plot(xfreq, d["g"], label="G")
        ax.plot(xfreq, d["b"], label="B")
        ax.set_ylabel("Admittance [S]")
        ax.set_title("Admittance G + jB")
        ax.legend()

    elif mode == "parallel-r":
        ax.plot(xfreq, d["parallel-r"])
        ax.set_ylabel("Parallel R [ohm]")
        ax.set_title("Parallel resistance")

    elif mode == "parallel-x":
        ax.plot(xfreq, d["parallel-x"])
        ax.set_ylabel("Parallel X [ohm]")
        ax.set_title("Parallel reactance")

    elif mode == "parallel-l":
        ax.plot(xfreq, d["parallel-l"] * 1e6)
        ax.set_ylabel("Parallel L [uH]")
        ax.set_title("Parallel inductance (B < 0)")

    elif mode == "parallel-c":
        ax.plot(xfreq, d["parallel-c"] * 1e12)
        ax.set_ylabel("Parallel C [pF]")
        ax.set_title("Parallel capacitance (B > 0)")

    elif mode == "smith":
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(6.6, 6.6))
        th = np.linspace(0, 2*np.pi, 720)
        ax.plot(np.cos(th), np.sin(th), linewidth=1)
        ax.axhline(0, linewidth=0.8)
        ax.axvline(0, linewidth=0.8)
        ax.plot(gamma.real, gamma.imag)
        ax.scatter([gamma.real[0]], [gamma.imag[0]], s=20, label="Start")
        ax.scatter([gamma.real[-1]], [gamma.imag[-1]], s=20, label="Stop")
        if marker_hz is not None:
            i = nearest(freq, marker_hz)
            ax.scatter([gamma.real[i]], [gamma.imag[i]], s=35, label=f"Marker {freq[i]/1e3:.3f} kHz")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(-1.05, 1.05)
        ax.set_ylim(-1.05, 1.05)
        ax.set_xlabel("Re{Gamma}")
        ax.set_ylabel("Im{Gamma}")
        ax.set_title("S11 trajectory (Smith plane)")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        return fig

    else:
        plt.close(fig)
        raise ValueError(f"Nieznany tryb: {mode}")

    ax.set_xlabel(f"Frequency [{xunit}]")
    ax.grid(True, alpha=0.3)

    if marker_hz is not None:
        i = nearest(freq, marker_hz)
        ax.axvline(freq[i] / xscale, linestyle="--", linewidth=1)

    fig.tight_layout()
    return fig


def marker_report(freq, gamma, d, marker_hz):
    i = nearest(freq, marker_hz)
    f = freq[i]
    z = d["z"][i]

    print()
    print(f"Marker: {f:.0f} Hz ({f/1e3:.3f} kHz)")
    print(f"S11 = {gamma[i].real:+.9f} {gamma[i].imag:+.9f}j")
    print(f"|S11| = {abs(gamma[i]):.9f}")
    print(f"logmag = {d['logmag'][i]:.4f} dB")
    print(f"phase = {d['phase'][i]:.4f} deg")
    print(f"Z = {z.real:.6f} {z.imag:+.6f}j ohm")

    if z.imag > 0:
        print(f"Series L = {d['series-l'][i]*1e6:.6f} uH")
    elif z.imag < 0:
        print(f"Series C = {d['series-c'][i]*1e12:.6f} pF")
    else:
        print("Series L/C = N/A (X = 0)")

    print(f"Series Q = {d['series-q'][i]:.6f}")


def main():
    ap = argparse.ArgumentParser(
        description="Rysowanie pomiarow LiteVNA z .s1p lub .csv"
    )
    ap.add_argument("file", help="sciezka do .s1p albo .csv")
    ap.add_argument(
        "--mode",
        default="rjx",
        help="np. rjx, logmag, series-l albo lista: rjx,series-l,logmag; 'all' = zestaw",
    )
    ap.add_argument(
        "--marker",
        type=parse_eng,
        default=None,
        help="np. 431.281k; wypisuje wartosci i zaznacza pionowa linie",
    )
    ap.add_argument(
        "--xunit",
        choices=["auto", "Hz", "kHz", "MHz", "GHz"],
        default="auto",
    )
    ap.add_argument(
        "--save-dir",
        default=None,
        help="jesli podane, zapisuje PNG do tego katalogu",
    )
    ap.add_argument(
        "--no-show",
        action="store_true",
        help="nie otwieraj okien matplotlib (przydatne razem z --save-dir)",
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Nie znaleziono pliku: {path}")

    freq, gamma, z0 = load_measurement(path)
    d = derived(freq, gamma, z0)

    modes = [m.strip().lower() for m in args.mode.split(",") if m.strip()]
    if "all" in modes:
        modes = ALL_MODES

    valid = {
        "rjx", "impedance", "series-r", "series-x", "series-l", "series-c",
        "series-q", "logmag", "linmag", "phase", "return-loss", "swr",
        "admittance", "parallel-r", "parallel-x", "parallel-l", "parallel-c",
        "smith",
    }

    bad = [m for m in modes if m not in valid]
    if bad:
        raise SystemExit(
            f"Nieznany tryb: {', '.join(bad)}\nDostepne: {', '.join(sorted(valid))}, all"
        )

    xscale, xunit = choose_xscale(freq, args.xunit)

    print(f"Plik: {path.resolve()}")
    print(f"Punkty: {len(freq)}")
    print(f"Zakres: {freq[0]:.0f} .. {freq[-1]:.0f} Hz")
    print(f"Z0: {z0:g} ohm")

    if args.marker is not None:
        marker_report(freq, gamma, d, args.marker)

    save_dir = None
    if args.save_dir:
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

    figs = []
    for mode in modes:
        fig = plot_mode(
            mode, freq, gamma, d, xscale, xunit,
            marker_hz=args.marker
        )
        figs.append(fig)

        if save_dir is not None:
            outfile = save_dir / f"{path.stem}_{mode}.png"
            fig.savefig(outfile, dpi=180)
            print(f"Zapisano: {outfile.resolve()}")

    if not args.no_show:
        plt.show()
    else:
        for fig in figs:
            plt.close(fig)


if __name__ == "__main__":
    main()
