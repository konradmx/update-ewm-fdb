# CLAUDE.md — geobid KERG: przypisywanie operatów GESUT / BDOT500

## Współpraca Użytkownik (Konrad) <-> Claude Code

- Konrad uruchamia terminale, środowisko wirtualne, instaluje pakiety i
  testuje skrypty — chyba że wyraźnie poprosi o pomoc przy tych czynnościach.

## Konwencje językowe

- **Komunikacja z użytkownikiem:** po polsku
- **Identyfikatory w kodzie** (zmienne, funkcje, stałe, klasy, parametry): po
  angielsku
- **Treść commitów Git:** zawsze po angielsku

## Środowisko

- **Python:** 3.14
- **Manager pakietów:** `uv` — zależności w `pyproject.toml`, lock w `uv.lock`
- **Venv:** `D:\.virtualenvs\venv_py314_ewm_fdb` — **poza katalogiem projektu**
  (projekt na Google Drive; ciężkie środowisko nie synchronizuje się przez sieć).
  W katalogu projektu siedzi tylko `.venv` jako **junction** (Windows) / dowiązanie
  symboliczne (FreeBSD/Linux) wskazujące na ten katalog.
- **Setup po klonowaniu** — jedno polecenie z katalogu projektu:
  - **Windows: `.\bootstrap.ps1` (PowerShell)** — jedyna obsługiwana ścieżka
    na Windows. `bootstrap.sh` w Git Bashu na Windows **nie zadziała**:
    `$HOME` wskazuje na `C:\Users\<user>\`, więc domyślna baza venvów
    rozjeżdża się z `D:\.virtualenvs\`, a `ln -s` w MSYS bez Developer Mode
    nie tworzy junction (kopiuje katalog albo rzuca błąd). Junction
    `mklink /J` działa tylko z `cmd.exe`/PowerShella.
  - **FreeBSD/Linux: `./bootstrap.sh`**

  Skrypt jest idempotentny: zakłada katalog venva pod docelową ścieżką
  (`uv venv --python 3.14`), tworzy junction (Windows) / symlink (Unix)
  `.venv` w projekcie, instaluje zależności (`uv sync`). Domyślna baza venvów
  to `D:\.virtualenvs\` (Windows) i `~/.virtualenvs/` (FreeBSD); można
  nadpisać zmienną `VIRTUALENVS_HOME`. Nazwa venva jest hardcoded w pierwszym
  wierszu skryptu (`venv_py314_ewm_fdb`) — edytuj przy portowaniu do innego
  projektu.

  **Pierwsze odpalenie na świeżym Windows** wymaga jednorazowego odblokowania
  uruchamiania skryptów PowerShell:
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```
  `RemoteSigned` dopuszcza skrypty lokalne (nasz `bootstrap.ps1`), wymaga
  podpisu dla pobranych z internetu. Alternatywa bez modyfikacji polityki
  (na jedno odpalenie): `powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1`.

  **`bootstrap.ps1` uruchamiaj z otwartej sesji PowerShella** (`cd <projekt>`,
  potem `.\bootstrap.ps1`). Dwuklik w Explorerze odpala skrypt w nowym oknie
  i zamyka je natychmiast — błąd, jeśli wystąpi, miga przez ułamek sekundy.
- **Dodawanie pakietów:** `uv add <pakiet>` (z poziomu projektu — uv idzie po
  `.venv` przez junction, więc nie trzeba ustawiać `UV_PROJECT_ENVIRONMENT`).
- **Uruchamianie skryptów:** `uv run python py/<skrypt.py>` — flagi `-u` i
  `-X utf8` są zbędne; każdy skrypt ustawia kodowanie i line-buffering przez
  `sys.stdout/stderr.reconfigure(encoding='utf-8', line_buffering=True)`
- **Terminal:** Git Bash (MINGW64)

> **Uwaga historyczna:** wcześniej venv był wskazywany przez user-level zmienną
> `UV_PROJECT_ENVIRONMENT`. Zostało to porzucone, bo zmienna jest globalna —
> kolidowała z innymi projektami pod `D:\.virtualenvs\`. Junction lokalny dla
> projektu rozwiązuje problem bez globalnego stanu.

## Firebird

- W systemie chodzą równolegle **trzy serwery Firebird**: 2.5, 3.0, 5.0
- **Używamy FB 3**, port **3050** (`127.0.0.1/3050:<ścieżka_fdb>`)
- Połączenie zawsze TCP — Ewmapa i DBeaver mogą mieć plik otwarty równolegle
- Login: `SYSDBA` / `masterkey`, charset `UTF8`
- Sterownik: `firebird-driver` (pakiet w `.venv`)

## Ścieżki danych testowych

Dane testowe i bazy FDB są **poza katalogiem projektu** (nie synchronizują się
przez Google Drive): `D:\zzz_tmp\2026-05-07_geobid_KERG\`

```
# GESUT
D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\1_source\gesut.fdb
D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\1_source\pkt_z bazy.txt

# BDOT500
D:\zzz_tmp\2026-05-07_geobid_KERG\Example_2\1_source\bdot500.fdb
D:\zzz_tmp\2026-05-07_geobid_KERG\Example_2\1_source\pkt_z bazy_wersja2.txt
```

Bazy przerobione (do porównania):
```
D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\3_automated\gesut.fdb
D:\zzz_tmp\2026-05-07_geobid_KERG\Example_2\3_automated\bdot500.fdb
```

## Cel projektu

Dwa skrypty przypisują operaty do geometrii na podstawie pliku punktów
eksportowanego z EwMapy (format TSV: `ID \t X \t Y \t TYP#NUMER_OPERATU`).

### `py/ewm_elements_update.py` — **główny skrypt**

Aktualizuje `OPERAT` bezpośrednio w tabelach elementów (`EW_POLYLINE`,
`EW_TEXT`). Tryb pracy sterowany flagami CLI:

- `--info` — tylko info o strukturze bazy (katalogi, warstwy); brak zmian
- (domyślny) **DRY RUN** — analiza i raport, zero zmian w bazie
- `--execute` — zapisuje zmiany w bazie

Kluczowe opcje CLI: `--katalog ID`, `--typ N [N...]`, `--tolerance M`.

### `py/ewm_objects_update.py` — skrypt obiektowy (starsza architektura)

Aktualizuje `OPERAT` w `EW_OBIEKTY` (poziom obiektu, nie elementu).
Do znalezienia obiektu używa junction table `EW_OB_ELEMENTY` (kolumny:
`UIDO`, `IDE`, `TYP`; TYP=0 → polilinia, TYP=1 → tekst). Tryb pracy
steruje stała `DRY_RUN: bool` w kodzie (nie CLI).

## Schema bazy (kluczowe tabele)

| Tabela | Opis | Ważne kolumny |
|---|---|---|
| `EW_KATALOGI` | katalogi warstw | `ID`, `NAZWA` |
| `EW_OPERATY` | słownik operatów (~6868) | `UID`, `TYP`, `NUMER` |
| `EW_WARSTWA_LINIOWA` | definicje warstw liniowych | `ID`, `ID_KATALOGU` |
| `EW_WARSTWA_TEXTOWA` | definicje warstw tekstowych | `ID`, `ID_KATALOGU` |
| `EW_POLYLINE` | geometria liniowa (~396k) | `UID`, `ID`, `ID_WARSTWY`, `OPERAT`, `P0_X/Y`, `P1_X/Y`, `PN_X/Y` |
| `EW_TEXT` | etykiety (~295k) | `UID`, `ID_WARSTWY`, `OPERAT`, `POS_X`, `POS_Y` |
| `EW_OBIEKTY` | obiekty warstwy (ewm_objects) | `UID`, `OPERAT`, `NUMER`, `KOD`, `IDKATALOG` |
| `EW_OB_ELEMENTY` | junction: obiekt ↔ geometria | `UIDO`, `IDE`, `TYP` (0=poly, 1=text) |

`OPERAT = 0` znaczy "brak operatu" (NULL nie występuje — potwierdzone empirycznie).
Sentinel-guard w UPDATE: `WHERE UID = ? AND OPERAT = 0`.

### Słownik typów operatu (kolumna `EW_OPERATY.TYP`)

Lista odtworzona z ComboBox `OperatList` w `C:\Program Files\Geobid\EWMAPA\EwMapa.exe`
(zasób formularza Delphi, ten sam wykaz powtarza się w 3 miejscach binarki):

| TYP | Opis |
|---|---|
| 1 | Operaty ewidencyjne |
| 2 | Operaty przejściowe |
| 3 | **Operaty bazowe** — dominujący |
| 4 | Szkice polowe |
| 5 | Inne dokumenty |
| 6 | Rejestr zgłoszeń |
| 7 | Materiały zasobu |
| 8 | (poza listą GUI; w danych testowych występuje, format `P.1017.ROK.LP` — prawdopodobnie bieżące/robocze KERG) |

**Dominujący typ:** w pliku punktów TYP=3 stanowi **97.63%** wpisów
(580 720 / 594 837); TYP=1: 1.58%, TYP=2: 0.16%, TYP=8: 0.64%. Dlatego domyślna
wartość `OPERAT_TYP_FILTER = {3}` w `ewm_elements_update.py` jest poprawna
zarówno dla GESUT jak i BDOT500.

> **Uwaga o danych testowych:** bazy `gesut.fdb` i `bdot500.fdb` są oczywiście
> **różne** (różny MD5, różna zawartość), ale dołączone do nich pliki punktów
> `Example_1\…\pkt_z bazy.txt` i `Example_2\…\pkt_z bazy_wersja2.txt` są
> **identyczne bajt po bajcie** (ten sam MD5 `e3713f93…`).
>
> Wyjaśnienie klienta: produkcyjna baza punktów pomiarowych z operatami jest
> **wspólna dla wszystkich typów baz** (GESUT, BDOT500, EWID, …). Klient
> wyeksportował całą jej zawartość raz, a do każdego przykładu testowego
> wrzucił kopię pod inną nazwą. Histogram TYP-ów dotyczy więc obu baz
> w tym samym stopniu, a skrypt jest w naturalny sposób testowany tym samym
> wejściem przeciwko `gesut.fdb` i `bdot500.fdb`.

### Katalogi w testowych bazach

| Baza | ID | NAZWA | Status |
|---|---|---|---|
| `gesut.fdb` | 1 | `GESUT_2015` | archiwalna (przepisy 2015) |
| `gesut.fdb` | 2 | `GESUT` | **aktualna** (przepisy 2021) — aktualizujemy |
| `bdot500.fdb` | 1 | `BDOT500_2015` | archiwalna |
| `bdot500.fdb` | 2 | `BDOT500` | **aktualna** — aktualizujemy |

## Format pliku punktów

```
<ID> \t <X> \t <Y> \t <TYP>#<NUMER_OPERATU>
```

- Separator: tabulator lub spacje
- Kolumna 1 (ID) — ignorowana
- Kolumna 4: `<TYP>` (liczba całkowita) `#` `<NUMER>` (string, może zawierać
  spacje) — wszystko po `#` to numer, **bez normalizacji**
- Encoding: `cp1250`
- Wiersze bez `#` w kol. 4 są cicho pomijane (inna sekcja formatu w pliku)

## Quirki sterownika `firebird-driver`

1. **Polskie znaki w błędach Firebirda są łamane** — sterownik źle dekoduje
   komunikaty CP1250. Nie do naprawienia z poziomu Pythona.
2. **Float w parametrach SQL rzuca `AttributeError: 'float' has no attribute 'to_bytes'`**
   gdy kolumna docelowa to `NUMERIC` (scaled INT64). Workaround: **inline'ować
   floaty w SQL** (`f"... BETWEEN {x_lo!r} AND {x_hi!r}"`). Nasze stałe —
   brak ryzyka injection.

## Algorytm (indeks przestrzenny)

Geometria ładowana raz do `GeometryIndex` (grid hash, komórka 1 m × 1 m).
Zapytanie w sąsiedztwie 3×3 komórek + filtr `|dx| ≤ tol` i `|dy| ≤ tol`.
Domyślna tolerancja: `0.01 m`. Komórka 1 m >> tolerancja → 3×3 wystarczy.
