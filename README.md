# LiteVNA CLI + Plotter

Dwa małe skrypty do pracy z **LiteVNA bez pośrednictwa aplikacji PC**:

- `litevna_cli.py` — bezpośrednia komunikacja z LiteVNA po USB/COM, pomiar S11/S21, eksport Touchstone (S11)/CSV, własna kalibracja SOL S11, wspólna Enhanced Response S11/S21 oraz prosta THRU/ISOLATION S21.
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

# Kalibracja i pomiar transmisji S21

S21 opisuje transmisję od portu nadawczego **S11/CH0** do odbiorczego
**S21/CH1**. Podłącz wejście DUT do S11, a wyjście do S21. Dotychczasowe
`calibrate` i `measure` nadal dotyczą S11; nowe komendy to `calibrate-s21`
i `measure-s21`; wspólną kalibrację SOL + THRU wykonuje `calibrate-er`.

## Zalecana wspólna kalibracja S11/S21: Enhanced Response

Ta procedura łączy **SOL + THRU** w jednym pliku. Korekcja transmisji
korzysta z niedopasowania źródła wyznaczonego podczas SOL oraz z S11
mierzonego razem z S21. Wcześniejsza komenda `calibrate-s21` pozostaje
prostą normalizacją THRU, bez tej korekcji.

Dla LOAD 50 Ω i zakresu 400 kHz–2 MHz:

```powershell
py litevna_cli.py calibrate-er --port COM3 --start 400k --stop 2M --points 1024 --avg 8 --load 50 --isolation --out er_50ohm.lvcal.json
```

Zamień COM3 na swój port. Pozostaw docelowe kable podłączone do urządzenia
przez całą kalibrację i pomiar. Na ich końcach wykonaj kolejno:

1. **OPEN:** rozwarcie na końcu kabla S11/CH0, Enter.
2. **SHORT:** zwarcie w tym samym miejscu, Enter.
3. **LOAD:** wzorzec 50 Ω w tym samym miejscu, Enter.
4. **ISOLATION:** oba końce kabli zakończ osobnymi obciążeniami 50 Ω,
   bez połączenia między portami, Enter.
5. **THRU:** usuń obciążenia i połącz końce kabli przelotką, Enter.

Zawsze poczekaj na następny komunikat przed zmianą połączeń. `--isolation`
można pominąć; wtedy przesłuch jest przyjmowany jako zero. Opcja `--load`
opisuje rezystancję wzorca SOL (domyślnie 50 Ω); impedancja odniesienia
Enhanced Response pozostaje 50 Ω. OPEN/SHORT oraz THRU są modelowane jako
idealne. Używaj wzorców odpowiednich dla częstotliwości pomiaru — zwykły
rezystor z długimi wyprowadzeniami nie jest idealnym obciążeniem RF.

Po kalibracji zastąp THRU badanym układem:

```powershell
py litevna_cli.py measure-s21 --port COM3 --cal er_50ohm.lvcal.json --marker 1M --out dut_s21.csv
```

Program rozpoznaje Enhanced Response po formacie JSON i pobiera oba kanały
z tych samych rekordów FIFO. Zapisuje:

- `dut_s21.csv` — skorygowane zespolone S21, amplitudę w dB i fazę;
- `dut_s21_s11.s1p` — S11 skorygowane metodą SOL z tego samego sweepu.

Ten sam plik kalibracji można wykorzystać do samego S11:

```powershell
py litevna_cli.py measure --port COM3 --cal er_50ohm.lvcal.json --format rjx,logmag --out dut_s11.s1p
```

Obie komendy używają siatki z kalibracji; `--avg` może nadpisać jej
uśrednianie. Nie łącz `--cal` z `--device-cal` ani `--raw`.
Kalibracja jest wykonywana w Pythonie i nie zapisuje współczynników
do pamięci analizatora. **Stare pliki SOL i THRU nadal działają w swoich
trybach, ale nie stają się automatycznie Enhanced Response.** Wykonaj
`calibrate-er` od początku: wymagany jest także surowy pomiar S11 wzorca THRU.

Do audytu pozostają pliki:

```text
er_50ohm.lvcal.json
er_50ohm.lvcal_OPEN_raw.s1p
er_50ohm.lvcal_SHORT_raw.s1p
er_50ohm.lvcal_LOAD_raw.s1p
er_50ohm.lvcal_THRU_S11_raw.s1p
er_50ohm.lvcal_THRU_raw.csv
er_50ohm.lvcal_ISOLATION_raw.csv  (tylko z --isolation)
```

### Co jest korygowane

Dla każdego punktu częstotliwości SOL wyznacza współczynniki A, B, C:

```text
m11 = (A + B * Gamma) / (1 - C * Gamma)
Gamma = (m11 - A) / (B + C * m11)
```

C opisuje niedopasowanie źródła. Najpierw wyznaczamy Gamma_thru z surowego
S11 wzorca THRU, a następnie tracking transmisji i wynik DUT:

```text
Et = (m21_thru - isolation) * (1 - C * Gamma_thru)
S21_ER = (m21_dut - isolation) * (1 - C * Gamma_dut) / Et
```

Dzięki pomiarowi S11 THRU wyznaczanie Et uwzględnia interakcję źródła
z niedopasowanym odbiornikiem. Dla idealnego THRU Gamma_thru odpowiada
niedopasowaniu odbiornika widzianemu w płaszczyźnie kalibracji.

**Enhanced Response nadal jest korekcją częściową jednego kierunku.**
Usuwa wpływ niedopasowania źródła na transmisję, ale nie usuwa całego
wpływu obciążenia DUT przez odbiornik. Przy niedopasowanym wyjściu DUT
pozostaje błąd zależny od S22 i dopasowania odbiornika; również S11 układu
transmisyjnego może zawierać wpływ obciążenia wyjścia. Program nie mierzy
S12/S22 i nie deklaruje pełnej korekcji 12-składnikowej. Rozróżnienie
Enhanced Response i pełnej korekcji przy pomiarze w dwóch orientacjach
opisuje [scikit-rf: TwoPortOnePath / EnhancedResponse](https://github.com/scikit-rf/scikit-rf/blob/master/skrf/calibration/calibration.py).

Po kalibracji sprawdź THRU (S21 blisko 0 dB i 0°), a następnie niezależny
znany tłumik. Po zmianie kabli lub zakresu wykonaj nową kalibrację.
Testy syntetyczne obejmują zespolony model błędów VNA, niedopasowanie
źródła i odbiornika oraz przewidywany błąd resztkowy. Nie zastępują
weryfikacji na fizycznym LiteVNA — ta pozostaje do wykonania.

## Prosta kalibracja S21 przez USB — THRU bez SOL

Podłącz LiteVNA, włącz go i zamknij inne aplikacje używające COM.
Sprawdź połączenie:

```powershell
py litevna_cli.py info --port COM3
```

Zostaw oba kable pomiarowe podłączone do analizatora. Kalibruj na ich
końcach, w miejscach późniejszego podłączenia DUT:

```powershell
py litevna_cli.py calibrate-s21 --port COM3 --start 400k --stop 2M --points 1024 --avg 2 --out s21.lvcal.json
```

Na komunikat **THRU** połącz końce kabli odpowiednią przelotką, bez DUT,
i naciśnij Enter. Skrypt wykona rzeczywisty sweep LiteVNA w trybie bez
kalibracji użytkownika i zapisze wzorzec. Zakłada idealny THRU: S21 = 1,
bez strat i opóźnienia. Straty i opóźnienie przelotki zostaną znormalizowane;
przy wyższych częstotliwościach nieidealny THRU ogranicza dokładność.

Opcjonalny pomiar przesłuchu przed THRU:

```powershell
py litevna_cli.py calibrate-s21 --port COM3 --start 400k --stop 2M --points 1024 --avg 8 --isolation --out s21_iso.lvcal.json
```

Na **ISOLATION** rozłącz tor i zakończ każdy kabel osobnym obciążeniem
50 Ω. Porty nie mogą być połączone. Po Enter poczekaj na zakończenie sweepu,
a następnie podłącz THRU zgodnie z następnym komunikatem. Nie używaj tutaj
rezystora 23.75 Ω z przykładu SOL. Pomiar izolacji blisko poziomu szumu może
pogorszyć wynik; używaj go, gdy potrzebujesz korekcji stabilnego przesłuchu.

Powstaną:

```text
s21_iso.lvcal.json
s21_iso.lvcal_THRU_raw.csv
s21_iso.lvcal_ISOLATION_raw.csv
```

Bez `--isolation` nie powstaje plik ISOLATION, a przesłuch jest przyjmowany
jako zero. Dla każdego punktu stosowana jest zespolona korekcja:

```text
S21_corrected = (S21_measured - S21_isolation) / (S21_thru - S21_isolation)
```

To kalibracja odpowiedzi transmisyjnej: koryguje amplitudę, fazę i opcjonalnie
przesłuch. Nie jest pełną kalibracją dwuportową ani Enhanced Response;
nie usuwa błędów niedopasowania źródła i obciążenia, nie mierzy S12/S22.
Współczynniki są zapisane na komputerze, **nie w pamięci LiteVNA**.
Własna SOL S11 pozostaje osobną kalibracją.

## Pomiar i sprawdzenie wyniku

Zastąp THRU badanym układem bez zmieniania kabli:

```powershell
py litevna_cli.py measure-s21 --port COM3 --cal s21.lvcal.json --marker 1M --out dut_s21.csv
```

Jeśli wykonałeś wariant z izolacją, użyj `--cal s21_iso.lvcal.json`.
Siatka częstotliwości i domyślne AVG pochodzą z JSON. `--avg` może nadpisać
uśrednianie; `--start`, `--stop`, `--points` i `--current` są przy `--cal`
ignorowane z komunikatem. Skrypt odrzuci plik kalibracji SOL S11,
niekompletne współczynniki i zerowy sygnał odniesienia THRU.

Kontrola po kalibracji:

1. Ponowny pomiar THRU powinien być bliski 0 dB i 0° — to kontrola spójności.
2. Niezależnie zmierz znany tłumik, np. 10 dB: oczekuj S21 około −10 dB
   w zakresie jego specyfikacji. To lepszy sprawdzian niż sam THRU.
3. Po wymianie kabli, zmianie płaszczyzn pomiarowych lub zakresu wykonaj
   nową kalibrację. Zachowaj surowe CSV standardów do porównania.

CSV zawiera `freq_Hz`, `S21_re`, `S21_im`, `S21_logmag_dB`,
`S21_phase_deg`, `S21_linmag`, `insertion_loss_dB` i `source`.
Dodatnie insertion loss oznacza tłumienie; S21 w dB ma wtedy znak ujemny.
Zero transmisji daje −inf dB, a faza w tym punkcie nie ma znaczenia fizycznego.
CSV z zespolonym S21 jest tutaj zapisem źródłowym. Nie eksportujemy S21
jako `.s1p` ani nie wypełniamy niezmierzonych S12/S22 zerami w `.s2p`.
Obecny `litevna_plot.py` obsługuje wyłącznie S11; CSV S21 można analizować
np. w arkuszu kalkulacyjnym (częstotliwość względem dB/fazy).

Surowy pomiar kontrolny:

```powershell
py litevna_cli.py measure-s21 --port COM3 --raw --start 400k --stop 2M --points 1024 --out raw_s21.csv
```

## Kalibracja zapisana bezpośrednio w LiteVNA

To alternatywa dla JSON. Procedura z instrukcji LiteVNA:

1. Ustaw zakres sweepu i podłącz docelowe kable.
2. Wybierz **CAL → RESET → CALIBRATE**.
3. Na końcu kabla S11 wykonaj kolejno **OPEN**, **SHORT**, **LOAD**
   (standard 50 Ω), czekając na zakończenie każdego kroku.
4. Pozostaw LOAD na torze nadawczym i wykonaj **ISOLN**, bez połączenia
   pomiędzy portami.
5. Połącz końce obu kabli przelotką i wykonaj **THRU**.
6. Wybierz **DONE** i zapisz do wybranego slotu kalibracji.
7. Wybierz ślad S21 przez **DISPLAY → CHANNEL** i format **LOGMAG**.

Nazwy menu mogą różnić się między wersjami firmware. Źródło:
[LiteVNA User Guide, Calibration, s. 10–11](https://storage.eleshop.eu/files/LiteVNA_User-Guide.pdf).

Podłącz DUT i pobierz dane z aktywną korekcją urządzenia:

```powershell
py litevna_cli.py measure-s21 --port COM3 --device-cal --current --marker 1M --out device_s21.csv
```

`--device-cal` (również tryb domyślny) żąda danych skorygowanych przez firmware;
nie uruchamia sekwencji OPEN/SHORT/LOAD/ISOLN/THRU ani nie potwierdza, że
aktywna kalibracja jest poprawna. Sprawdź ją na ekranie urządzenia.
Nie łącz `--device-cal` z `--cal` lub `--raw`.

## Protokół i zakres weryfikacji

S21 korzysta z wyboru CH1: rejestr `0x44`, wartość `2`. Dane pochodzą
z zespolonego ilorazu odbiornika transmisyjnego i sygnału odniesienia
w 32-bajtowym rekordzie FIFO. Po zakończeniu lub przerwaniu nowych komend
skrypt przywraca oba kanały i tryb normalny. Opis rozszerzeń firmware:
[DiSlord — protokół V2/LiteVNA](https://github.com/NanoVNA-Saver/nanovna-saver/issues/768#issuecomment-2622817345).

Obsługę S21 i Enhanced Response sprawdzono testami z symulowanym portem szeregowym i modelem błędów VNA;
nie wykonano jeszcze testu na fizycznym LiteVNA. Historyczne wyniki SOL
z sekcji Status dotyczą S11. Testy uruchomisz bez sprzętu:

```powershell
py -m unittest discover -s tests -v
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

- obsługuje **S11 / 1-port** i **S21 / transmisję w przód**,
- nie implementuje pełnej kalibracji 2-portowej,
- dla S21 oferuje Enhanced Response z korekcją niedopasowania źródła oraz prostą THRU/ISOLATION,
- Enhanced Response nie usuwa wszystkich błędów niedopasowania odbiornika i nie mierzy S12/S22,
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

Pliki `.s1p` są traktowane jako podstawowy zapis pomiaru S11. Dla S21 podstawowym zapisem jest CSV z kolumnami `S21_re` i `S21_im`.

CSV i wykresy można zawsze wygenerować ponownie z `.s1p`, dlatego nie ma potrzeby zmieniać formatu Touchstone ani przechowywać wyników wyłącznie w formacie konkretnej aplikacji GUI.
