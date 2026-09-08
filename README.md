# LiteVNA CLI + Plotter

Dwa małe skrypty do pracy z **LiteVNA bez pośrednictwa aplikacji PC**:

- `litevna_cli.py` — bezpośrednia komunikacja z LiteVNA po USB/COM, pomiar S11, eksport Touchstone/CSV oraz własna 1-portowa kalibracja SOL z dowolnym rezystorem LOAD.
- `litevna_plot.py` — niezależny plotter dla plików `.s1p` i `.csv`, z reprezentacjami podobnymi do tych dostępnych w LiteVNA: `R+jX`, `Series L`, `Series C`, `LOGMAG`, `phase`, `SWR`, Smith itd.

Główny cel projektu to uzyskanie **powtarzalnego i audytowalnego toru pomiarowego**, niezależnego od tego, czy aplikacja PC poprawnie odczytuje lub stosuje kalibrację zapisaną w LiteVNA.

## Status

Skrypty zostały praktycznie sprawdzone z LiteVNA:

```text
HW = 2
FW = 2.2
```

Przykładowy sanity check po programowej kalibracji SOL wykonanej z:

```text
LOAD = 47.5 Ω || 47.5 Ω = 23.75 Ω
```

dla niezależnego rezystora ~47 Ω dał przy `431.280 kHz`:

```text
Z = 47.1888 - j0.0264 Ω
|S11| = 0.02893
LOGMAG = -30.77 dB
```

czyli poprawną część rezystancyjną i bardzo mały błąd fazy/reactance.

---

## Wymagania

Python 3.10+.

Instalacja zależności:

```powershell
pip install pyserial numpy matplotlib
```

`litevna_cli.py` wymaga tylko `pyserial`.

`litevna_plot.py` wymaga `numpy` i `matplotlib`.

Na Windows można uruchamiać skrypty przez:

```powershell
py litevna_cli.py ...
py litevna_plot.py ...
```

Na Linux/macOS zwykle:

```bash
python3 litevna_cli.py ...
python3 litevna_plot.py ...
```

> `pip` służy do instalacji pakietów. Skrypty uruchamiaj przez `py`, `python` lub `python3`.

---

# 1. `litevna_cli.py`

## Co robi

Skrypt komunikuje się bezpośrednio z LiteVNA przez binarny protokół USB/serial i obsługuje trzy źródła danych S11:

1. **Kalibracja aktywna w samym LiteVNA**
   - tryb `--device-cal`
   - firmware wysyła skalibrowane dane.

2. **Surowe dane bez korekcji**
   - tryb `--raw`.

3. **Własna programowa kalibracja SOL**
   - `calibrate`
   - następnie `measure --cal ...`
   - kalibracja jest wykonywana całkowicie w Pythonie.

Skrypt zapisuje:

- standardowy Touchstone `.s1p`,
- dodatkowy `.csv` z wybranymi reprezentacjami danych.

Plik `.s1p` pozostaje źródłem kanonicznym: zawiera zespolone S11. `R+jX`, `L`, `C`, `Q`, `LOGMAG` itd. są tylko różnymi reprezentacjami tego samego pomiaru.

---

## Ważne przed pomiarem

Zamknij programy korzystające z tego samego portu COM, np.:

- NanoVNA-App,
- NanoVNA-Saver,
- terminal szeregowy,
- inne narzędzia komunikujące się z LiteVNA.

Port COM może mieć tylko jednego właściciela.

---

## Informacje o urządzeniu

```powershell
py litevna_cli.py info --port COM3
```

Jeżeli `--port` zostanie pominięty, skrypt spróbuje znaleźć zgodne urządzenie automatycznie.

---

# Kalibracja SOL z dowolnym rezystorem LOAD

Przykład:

```powershell
py litevna_cli.py calibrate --port COM3 --start 400k --stop 2M --points 1024 --avg 2 --load 23.75 --out trafo_23R75.lvcal.json
```

Skrypt poprosi kolejno o podłączenie:

```text
OPEN
SHORT
LOAD
```

W tym przykładzie LOAD jest modelowany jako idealny:

```text
Rload = 23.75 Ω
```

dla systemu:

```text
Z0 = 50 Ω
```

i ma współczynnik odbicia:

```text
Gamma_load = (Rload - Z0) / (Rload + Z0)
```

### Założenia obecnej implementacji SOL

- OPEN jest traktowany jako idealny `Gamma = +1`.
- SHORT jest traktowany jako idealny `Gamma = -1`.
- LOAD jest traktowany jako czysta rezystancja `R`.
- Model kalibracji jest pełnym 3-term one-port SOL dla każdego punktu częstotliwości.

Przy pomiarach na kilku MHz i krótkim fixture takie założenie jest zwykle bardzo dobre. Przy znacznie wyższych częstotliwościach można w przyszłości rozszerzyć model o pasożytnicze `Copen`, `Lshort` i model częstotliwościowy LOAD.

Po kalibracji powstają:

```text
trafo_23R75.lvcal.json
trafo_23R75.lvcal_OPEN_raw.s1p
trafo_23R75.lvcal_SHORT_raw.s1p
trafo_23R75.lvcal_LOAD_raw.s1p
```

Surowe pliki standardów są celowo zachowywane, żeby kalibracja była możliwa do późniejszego sprawdzenia.

---

## Sanity check kalibracji

Po kalibracji warto podłączyć **inny standard**, który nie uczestniczył w SOL.

Przykład: kalibracja LOAD = `23.75 Ω`, a następnie pomiar pojedynczego `47.5 Ω`:

```powershell
py litevna_cli.py measure --port COM3 --cal trafo_23R75.lvcal.json --format rjx,logmag --marker 431.281k --out check_47R5.s1p
```

Dobry wynik powinien być bliski:

```text
Z ≈ 47.5 + j0 Ω
```

Nie należy oceniać jakości kalibracji wyłącznie przez ponowny pomiar tego samego LOAD, który był użyty do kalibracji.

---

# Pomiar z programową kalibracją SOL

```powershell
py litevna_cli.py measure --port COM3 --cal trafo_23R75.lvcal.json --format rjx,series-l,series-c,logmag --marker 431.281k --out dut.s1p
```

Powstaną:

```text
dut.s1p
dut.csv
```

Przy `--cal` siatka częstotliwości jest zawsze pobierana z pliku kalibracji.

Opcje:

```text
--current
--start
--stop
--points
```

są wtedy ignorowane celowo, ponieważ SOL jest ważna dla konkretnej siatki częstotliwości.

---

# Pomiar z kalibracją aktywną w LiteVNA

Jeżeli kalibracja została wykonana bezpośrednio na urządzeniu:

```powershell
py litevna_cli.py measure --port COM3 --device-cal --start 400k --stop 2M --points 1024 --avg 2 --format rjx,series-l,logmag --marker 431.281k --out dut.s1p
```

Jeżeli nie podano `--raw` ani `--cal`, kalibracja urządzenia jest również domyślnym źródłem danych.

---

## Użycie aktualnego sweepu ustawionego na ekranie LiteVNA

```powershell
py litevna_cli.py measure --port COM3 --device-cal --current --format all --out current.s1p
```

Skrypt odczyta z urządzenia:

- start,
- step,
- liczbę punktów,
- aktualne averaging.

To jest wygodne, jeśli sweep został już ustawiony ręcznie na ekranie LiteVNA.

---

# Surowy pomiar bez kalibracji

```powershell
py litevna_cli.py measure --port COM3 --raw --start 400k --stop 2M --points 1024 --avg 2 --out raw.s1p
```

Przydatne głównie do:

- debugowania,
- tworzenia własnych procedur kalibracyjnych,
- porównania danych przed i po korekcji.

---

# Format częstotliwości

Argumenty częstotliwości akceptują zapis inżynierski:

```text
400k
431.281k
2M
1000000
1.5M
```

---

# Reprezentacje danych `--format`

Można podać jeden format:

```powershell
--format rjx
```

kilka:

```powershell
--format rjx,series-l,logmag
```

lub:

```powershell
--format all
```

Obsługiwane formaty:

```text
rjx
logmag
linmag
phase
swr
return-loss
series-r
series-x
series-l
series-c
series-q
admittance
parallel-r
parallel-x
parallel-l
parallel-c
```

Dostępne są również aliasy, np.:

```text
z
l
c
q
rs
xs
rp
lp
cp
rl
```

---

# Marker

Przykład:

```powershell
--marker 431.281k
```

Skrypt wybiera najbliższy punkt sweepu i wypisuje jego wartości w konsoli.

Przykład:

```text
Marker: 431280 Hz
S11 = -0.02892512 -0.00027924j
Z = 47.1888 -0.0264j ohm
```

---

# 2. `litevna_plot.py`

Plotter działa całkowicie offline. Nie komunikuje się z LiteVNA.

Obsługuje:

```text
.s1p
.csv
```

Dla `.s1p` obsługiwane są standardowe reprezentacje Touchstone:

```text
RI
MA
DB
```

oraz jednostki:

```text
Hz
kHz
MHz
GHz
```

---

## Najprostsze użycie

```powershell
py litevna_plot.py dut.s1p
```

Domyślny wykres:

```text
R + jX
```

---

## Wybrany tryb

```powershell
py litevna_plot.py dut.s1p --mode logmag
```

```powershell
py litevna_plot.py dut.s1p --mode series-l
```

```powershell
py litevna_plot.py dut.s1p --mode series-c
```

```powershell
py litevna_plot.py dut.s1p --mode phase
```

```powershell
py litevna_plot.py dut.s1p --mode smith
```

---

## Kilka wykresów naraz

```powershell
py litevna_plot.py dut.s1p --mode rjx,series-l,logmag
```

Każdy tryb otwiera osobny wykres.

---

## Marker na wykresie

```powershell
py litevna_plot.py dut.s1p --mode rjx --marker 431.281k
```

Marker:

- dodaje pionową linię na wykresie,
- wypisuje w konsoli S11,
- `|S11|`,
- LOGMAG,
- fazę,
- `R+jX`,
- odpowiednie Series L/C,
- Q.

---

## Wszystkie główne wykresy

```powershell
py litevna_plot.py dut.s1p --mode all
```

---

## Zapis PNG

```powershell
py litevna_plot.py dut.s1p --mode rjx,series-l,logmag --save-dir plots
```

Pliki będą miały nazwy typu:

```text
plots/dut_rjx.png
plots/dut_series-l.png
plots/dut_logmag.png
```

Bez otwierania okien GUI:

```powershell
py litevna_plot.py dut.s1p --mode all --save-dir plots --no-show
```

---

# Tryby wykresów

```text
rjx
impedance
series-r
series-x
series-l
series-c
series-q
logmag
linmag
phase
return-loss
swr
admittance
parallel-r
parallel-x
parallel-l
parallel-c
smith
all
```

`smith` pokazuje trasę S11 w płaszczyźnie Gamma. Jest to prosty wykres trajektorii, nie pełna klasyczna siatka Smitha.

---

# Series L i Series C

Dla impedancji:

```text
Z = R + jX
```

skrypt interpretuje:

```text
X > 0  -> charakter indukcyjny -> Series L
X < 0  -> charakter pojemnościowy -> Series C
```

i używa:

```text
L = X / (2*pi*f)
C = -1 / (2*pi*f*X)
```

Wykres `series-l` pokazuje wartości tylko tam, gdzie `X > 0`.

Wykres `series-c` pokazuje wartości tylko tam, gdzie `X < 0`.

### Uwaga przy X bliskim zeru

Gdy:

```text
X ≈ 0
```

przeliczenie na L lub C jest źle uwarunkowane numerycznie. Dla prawie idealnego rezystora może więc pojawić się bardzo małe L albo bardzo duże C.

W takiej sytuacji należy patrzeć przede wszystkim na:

```text
R + jX
```

a nie na równoważne L/C.

---

# Przykładowy workflow

## 1. Kalibracja

```powershell
py litevna_cli.py calibrate --port COM3 --start 400k --stop 2M --points 1024 --avg 2 --load 23.75 --out trafo_23R75.lvcal.json
```

## 2. Niezależny check

```powershell
py litevna_cli.py measure --port COM3 --cal trafo_23R75.lvcal.json --format rjx,logmag --marker 431.281k --out check_47R5.s1p
```

## 3. Pomiar DUT

```powershell
py litevna_cli.py measure --port COM3 --cal trafo_23R75.lvcal.json --format rjx,series-l,series-c,logmag --marker 431.281k --out dut.s1p
```

## 4. Oglądanie wyniku

```powershell
py litevna_plot.py dut.s1p --mode rjx,series-l,logmag --marker 431.281k
```

## 5. Eksport wykresów

```powershell
py litevna_plot.py dut.s1p --mode all --marker 431.281k --save-dir plots --no-show
```

---

# Struktura repozytorium

Minimalnie:

```text
.
├── litevna_cli.py
├── litevna_plot.py
└── README.md
```

Pliki pomiarowe i kalibracyjne najlepiej trzymać osobno, np.:

```text
measurements/
calibration/
plots/
```

Przykład:

```text
.
├── litevna_cli.py
├── litevna_plot.py
├── README.md
├── calibration/
│   └── trafo_23R75.lvcal.json
├── measurements/
│   ├── primary_open.s1p
│   ├── primary_23R75.s1p
│   └── primary_47R5.s1p
└── plots/
```

---

# Ograniczenia

Aktualna wersja:

- obsługuje **S11 / 1-port**,
- nie implementuje pełnej kalibracji 2-portowej,
- nie wykonuje pomiaru S21,
- własna SOL zakłada idealny OPEN/SHORT i czysto rezystancyjny LOAD,
- plotter Smitha pokazuje trajektorię Gamma bez pełnej siatki impedancyjnej,
- dokładność końcowa nadal zależy od jakości fixture, standardów kalibracyjnych i powtarzalności mechanicznego podłączenia.

---

# Dlaczego własna kalibracja?

W praktyce część aplikacji PC potrafi:

- nie użyć kalibracji aktywnej w LiteVNA,
- zastosować własną kalibrację niezależnie od urządzenia,
- wymuszać standard LOAD = 50 Ω,
- eksportować dane inne niż te widoczne na ekranie analizatora.

Ten projekt rozdziela te warstwy.

Można albo:

```text
LiteVNA calibration -> calibrated S11 over USB
```

albo:

```text
raw LiteVNA S11 -> Python SOL -> corrected S11
```

W obu przypadkach wynik końcowy jest zapisywany jako zwykły `.s1p`, który można później analizować dowolnym narzędziem.

---

# Bezpieczeństwo danych pomiarowych

Pliki `.s1p` są traktowane jako podstawowy zapis pomiaru.

CSV i wykresy można zawsze wygenerować ponownie z `.s1p`, dlatego nie ma potrzeby zmieniać formatu Touchstone ani przechowywać wyników wyłącznie w formacie konkretnej aplikacji GUI.
