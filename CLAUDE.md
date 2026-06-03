# CLAUDE.md — geobid KERG

Setup, użytkowanie, format danych i konfiguracja Firebirda — patrz
[README.md](./README.md). Ten plik zawiera instrukcje współpracy i
wiedzę domenową, której nie da się szybko wywnioskować z kodu.

## Współpraca

- Konrad uruchamia terminale, środowisko wirtualne, instaluje pakiety
  i testuje skrypty — chyba że wyraźnie poprosi o pomoc przy tych
  czynnościach.

## Konwencje językowe

- **Komunikacja z użytkownikiem:** po polsku
- **Identyfikatory w kodzie** (zmienne, funkcje, stałe, klasy, parametry):
  po angielsku
- **Treść commitów Git:** zawsze po angielsku

## Środowisko (skrótowo)

- Python 3.14, `uv`, Firebird 3.0 (port 3050, TCP, `SYSDBA`/`masterkey`,
  charset `UTF8`).
- Venv: `D:\.virtualenvs\venv_py314_ewm_fdb`, w projekcie jest tylko
  junction `.venv`. Setup automatyzuje `bootstrap.ps1` / `bootstrap.sh`
  — pełne kroki w `README.md`.
- **Nie ustawiaj `UV_PROJECT_ENVIRONMENT`** — globalna zmienna kolidowała
  z innymi projektami pod `D:\.virtualenvs\`. Junction lokalny dla
  projektu rozwiązuje to bez globalnego stanu.
- Skrypty uruchamia się przez `uv run python py/<skrypt.py>` — bez flag
  `-u` / `-X utf8`. Każdy skrypt na początku robi
  `sys.stdout/stderr.reconfigure(encoding='utf-8', line_buffering=True)`.
- Terminal: Git Bash (MINGW64).

## Architektura skryptów

- **`py/ewm_elements_update.py`** — główny. Aktualizuje `OPERAT`
  w `EW_POLYLINE` i `EW_TEXT` (poziom elementu). Tryby steruje CLI:
  `--info` / DRY RUN (default) / `--execute`. Filtry: `--katalog`,
  `--typ`, `--tolerance`.
- **`py/ewm_objects_update.py`** — starszy. Aktualizuje `EW_OBIEKTY`
  (poziom obiektu); do mapowania obiekt ↔ geometria używa junction
  `EW_OB_ELEMENTY` (`TYP=0` polilinia, `TYP=1` tekst). Tryb pracy
  steruje stała `DRY_RUN: bool` w kodzie.

## Schema bazy (kluczowe tabele)

| Tabela | Opis | Ważne kolumny |
|---|---|---|
| `EW_KATALOGI` | katalogi warstw | `ID`, `NAZWA` |
| `EW_OPERATY` | słownik operatów (~6868) | `UID`, `TYP`, `NUMER` |
| `EW_WARSTWA_LINIOWA` | definicje warstw liniowych | `ID`, `ID_KATALOGU` |
| `EW_WARSTWA_TEXTOWA` | definicje warstw tekstowych | `ID`, `ID_KATALOGU` |
| `EW_POLYLINE` | geometria liniowa (~396k) | `UID`, `ID`, `ID_WARSTWY`, `OPERAT`, `P0_X/Y`, `P1_X/Y`, `PN_X/Y` |
| `EW_TEXT` | etykiety (~295k) | `UID`, `ID_WARSTWY`, `OPERAT`, `POS_X`, `POS_Y` |
| `EW_OBIEKTY` | obiekty warstwy | `UID`, `OPERAT`, `NUMER`, `KOD`, `IDKATALOG` |
| `EW_OB_ELEMENTY` | junction: obiekt ↔ geometria | `UIDO`, `IDE`, `TYP` (0=poly, 1=text) |

**`OPERAT = 0` znaczy "brak operatu"** (NULL nie występuje — potwierdzone
empirycznie). Sentinel-guard w UPDATE:
`WHERE UID = ? AND OPERAT = 0`.

### Słownik typów operatu (`EW_OPERATY.TYP`)

Lista odtworzona z ComboBox `OperatList` w
`C:\Program Files\Geobid\EWMAPA\EwMapa.exe` (zasób formularza Delphi,
ten sam wykaz w 3 miejscach binarki):

| TYP | Opis |
|---|---|
| 1 | Operaty ewidencyjne |
| 2 | Operaty przejściowe |
| 3 | **Operaty bazowe** — dominujący |
| 4 | Szkice polowe |
| 5 | Inne dokumenty |
| 6 | Rejestr zgłoszeń |
| 7 | Materiały zasobu |
| 8 | (poza listą GUI; format `P.1017.ROK.LP` — prawdopodobnie bieżące/robocze KERG) |

Histogram w 594 837 wierszach z `#` (pliki testowe): TYP=1: 1.58%,
TYP=2: 0.16%, **TYP=3: 97.63%**, TYP=8: 0.64%. Dlatego domyślne
`OPERAT_TYP_FILTER = {3}` w `ewm_elements_update.py` jest poprawne
zarówno dla GESUT, jak i BDOT500.

### Katalogi w testowych bazach

| Baza | ID | NAZWA | Status |
|---|---|---|---|
| `gesut.fdb` | 1 | `GESUT_2015` | archiwalna (przepisy 2015) |
| `gesut.fdb` | 2 | `GESUT` | **aktualna** (przepisy 2021) — aktualizujemy |
| `bdot500.fdb` | 1 | `BDOT500_2015` | archiwalna |
| `bdot500.fdb` | 2 | `BDOT500` | **aktualna** — aktualizujemy |

## Wspólna baza punktów (test data)

Bazy `gesut.fdb` i `bdot500.fdb` są **różne** (różny MD5, różna zawartość),
ale dołączone do nich pliki `Example_1\…\pkt_z bazy.txt` i
`Example_2\…\pkt_z bazy_wersja2.txt` są **identyczne bajt po bajcie**
(MD5 `e3713f93…`).

Wyjaśnienie klienta: produkcyjna baza punktów pomiarowych z operatami
jest wspólna dla wszystkich typów baz EwMapy (GESUT, BDOT500, EWID, …).
Klient wyeksportował całą jej zawartość raz i wrzucił do każdego
przykładu testowego pod inną nazwą. Histogram TYP-ów dotyczy więc obu
baz w tym samym stopniu, a skrypt jest w naturalny sposób testowany
tym samym wejściem przeciwko `gesut.fdb` i `bdot500.fdb`.

## Quirki sterownika `firebird-driver`

1. **Polskie znaki w błędach Firebirda są łamane** — sterownik źle
   dekoduje komunikaty CP1250. Nie do naprawienia z poziomu Pythona.
2. **Float w parametrach SQL rzuca
   `AttributeError: 'float' has no attribute 'to_bytes'`** gdy kolumna
   docelowa to `NUMERIC` (scaled INT64). Workaround: **inline'ować
   floaty w SQL** (`f"... BETWEEN {x_lo!r} AND {x_hi!r}"`). Nasze
   stałe — brak ryzyka injection.

## Algorytm (indeks przestrzenny)

Geometria ładowana raz do `GeometryIndex` (grid hash, komórka 1 m × 1 m).
Zapytanie w sąsiedztwie 3×3 komórek + filtr `|dx| ≤ tol` i `|dy| ≤ tol`.
Domyślna tolerancja: `0.01 m`. Komórka 1 m ≫ tolerancja → 3×3 wystarczy.

## Ścieżki danych testowych (skrót)

Pełna struktura — `README.md`. W skrócie: bazy i pliki punktów leżą
w `D:\zzz_tmp\2026-05-07_geobid_KERG\Example_{1,2}\1_source\`,
wersje "po automacie" w `…\3_automated\`.
