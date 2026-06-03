# geobid KERG — przypisywanie operatów GESUT / BDOT500

Skrypty Python przypisujące operaty do geometrii w bazach Firebird EwMapy
(GESUT, BDOT500) na podstawie pliku punktów eksportowanego z EwMapy.

## Skrypty

- **`py/ewm_elements_update.py`** — główny skrypt. Aktualizuje `OPERAT`
  bezpośrednio w tabelach elementów (`EW_POLYLINE`, `EW_TEXT`). Tryby:
  - `--info` — tylko info o strukturze bazy (katalogi, warstwy), bez zmian
  - bez flagi — **DRY RUN** (analiza i raport, zero zmian w bazie)
  - `--execute` — wykonuje `UPDATE`-y i commit

  Kluczowe opcje: `--katalog ID`, `--typ N [N...]`, `--tolerance M`.

- **`py/ewm_objects_update.py`** — starsza wersja, aktualizująca `EW_OBIEKTY`
  (poziom obiektu, nie elementu). Tryb DRY/RUN steruje stała `DRY_RUN: bool`
  w kodzie (nie CLI).

## Wymagania

- **Python 3.14**
- **`uv`** (manager pakietów) — instalacja: https://docs.astral.sh/uv/
- **Firebird 3.0** lokalnie na porcie `3050`
- **Windows** lub **FreeBSD/Linux**

## Setup po klonowaniu

### Windows

```powershell
# raz na konto użytkownika, aby PowerShell pozwolił uruchamiać skrypty .ps1
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

git clone …
cd 2026-05-07_update_ewm_fdb
.\bootstrap.ps1
```

`bootstrap.ps1`:

- tworzy venv pod `D:\.virtualenvs\venv_py314_ewm_fdb` (poza katalogiem
  projektu — projekt jest na Google Drive, gdzie venva nie da się poprawnie
  synchronizować);
- robi junction `.venv` → ten venv (uv traktuje `.venv` jak swoje
  środowisko, więc dalsze polecenia działają bez zmiennych globalnych);
- instaluje zależności (`uv sync`).

Bazę katalogu venvów można nadpisać zmienną `VIRTUALENVS_HOME`. Nazwa venva
jest hardcoded w pierwszym wierszu skryptu — przy portowaniu do innego
projektu edytuj.

> **Uruchamiaj z otwartej sesji PowerShella** (`cd` → `.\bootstrap.ps1`),
> nie dwuklikiem w Explorerze — dwuklik zamyka okno zaraz po zakończeniu,
> więc nie zobaczysz ewentualnego błędu.

Alternatywa bez modyfikacji ExecutionPolicy (ad-hoc, na jedno uruchomienie):

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

### FreeBSD / Linux

```bash
git clone …
cd 2026-05-07_update_ewm_fdb
./bootstrap.sh
```

Domyślna baza venvów to `~/.virtualenvs/` (zgodnie z konwencją serwera
hostingowego). Nadpisanie: `VIRTUALENVS_HOME=/inna/sciezka ./bootstrap.sh`.

### Uwaga: bootstrap.sh NIE działa w Git Bashu na Windows

W Git Bashu `$HOME` wskazuje na `C:\Users\<user>\`, a `ln -s` w MSYS bez
Developer Mode nie tworzy junction — kopiuje katalog albo rzuca błąd.
**Na Windows używaj wyłącznie `bootstrap.ps1` z PowerShella.**

## Uruchamianie skryptów

```powershell
uv run python py/ewm_elements_update.py --help
```

Flagi `-u` i `-X utf8` są zbędne — skrypty same ustawiają kodowanie UTF-8
i line-buffering (`sys.stdout/stderr.reconfigure(...)`).

Typowe wywołania:

```powershell
# tylko info o strukturze bazy
uv run python py/ewm_elements_update.py --info

# dry run (default), TYP=3, katalog GESUT (ID=2)
uv run python py/ewm_elements_update.py --katalog 2 --typ 3

# wykonanie zmian
uv run python py/ewm_elements_update.py --katalog 2 --typ 3 --execute
```

## Konfiguracja Firebirda

- Serwer: `127.0.0.1` port `3050` (TCP — Ewmapa i DBeaver mogą trzymać bazę
  otwartą równolegle)
- Login: `SYSDBA` / `masterkey`
- Charset: `UTF8`
- Sterownik Pythona: `firebird-driver`

W systemie mogą chodzić równolegle trzy serwery Firebird (2.5, 3.0, 5.0) —
używamy FB 3 na porcie `3050`.

## Format pliku punktów

Plik TSV eksportowany z EwMapy, kodowanie `cp1250`:

```
<ID> \t <X> \t <Y> \t <TYP>#<NUMER_OPERATU>
```

- Separator: tabulator lub spacje
- Kolumna 1 (ID) — ignorowana
- Kolumna 4: `<TYP>` (liczba 1–8) `#` `<NUMER>` (string, może zawierać spacje;
  wszystko po `#` to numer, **bez normalizacji**)
- Wiersze bez `#` w kol. 4 są cicho pomijane (inna sekcja formatu w pliku)

Domyślnie skrypt obrabia wpisy z **`TYP=3`** ("Operaty bazowe"), bo to ~97.63%
zawartości produkcyjnej bazy punktów (jednej, wspólnej dla GESUT/BDOT500/EWID).

## Lokalizacja danych testowych

```
D:\zzz_tmp\2026-05-07_geobid_KERG\
├── Example_1\1_source\
│   ├── gesut.fdb              # baza GESUT
│   └── pkt_z bazy.txt         # plik punktów
├── Example_1\3_automated\
│   └── gesut.fdb              # baza po automatycznej aktualizacji
├── Example_2\1_source\
│   ├── bdot500.fdb            # baza BDOT500
│   └── pkt_z bazy_wersja2.txt # plik punktów (identyczny z Example_1)
└── Example_2\3_automated\
    └── bdot500.fdb            # baza po automatycznej aktualizacji
```

Pliki `pkt_z bazy.txt` w obu przykładach są **identyczne bajt po bajcie**
(MD5 `e3713f93…`) — produkcyjna baza punktów jest wspólna dla wszystkich
typów baz EwMapy (GESUT/BDOT500/EWID/…), klient wyeksportował ją raz pod
różnymi nazwami. Bazy `.fdb` są oczywiście różne.

## Konwencje językowe (commity, identyfikatory, komunikacja)

- Identyfikatory w kodzie (zmienne, funkcje, stałe, klasy, parametry):
  **po angielsku**
- Treść commitów Git: **po angielsku**
- Komunikacja w issue/PR/dokumentacji projektu: po polsku
