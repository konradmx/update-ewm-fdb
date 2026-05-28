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
  (projekt na Google Drive; ciężkie środowisko nie synchronizuje się przez sieć)
- **Dodawanie pakietów:** `uv add <pakiet>` z aktywnym venv lub z ustawioną
  zmienną `UV_PROJECT_ENVIRONMENT=D:\.virtualenvs\venv_py314_ewm_fdb`
- **Uruchamianie skryptów:** zawsze `python -u -X utf8 <skrypt.py>` — Windows
  CP1250
- **Terminal:** Git Bash (MINGW64)

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

Skrypt `py/ewm_elements_update.py` aktualizuje kolumnę `OPERAT` w tabelach
elementów graficznych (`EW_POLYLINE`, `EW_TEXT`) na podstawie pliku punktów
eksportowanego z EwMapy (format TSV: `ID \t X \t Y \t TYP#NUMER_OPERATU`).

Tryby pracy:
- `--info` — tylko info o strukturze bazy (katalogi, warstwy); brak zmian
- (domyślny) **DRY RUN** — analiza i raport, zero zmian w bazie
- `--execute` — zapisuje zmiany w bazie

Kluczowe opcje CLI: `--katalog ID`, `--typ N [N...]`, `--tolerance M`.

## Schema bazy (kluczowe tabele)

| Tabela | Opis | Ważne kolumny |
|---|---|---|
| `EW_KATALOGI` | katalogi warstw | `ID`, `NAZWA` |
| `EW_OPERATY` | słownik operatów (~6868) | `UID`, `TYP`, `NUMER` |
| `EW_WARSTWA_LINIOWA` | definicje warstw liniowych | `ID`, `ID_KATALOGU` |
| `EW_WARSTWA_TEXTOWA` | definicje warstw tekstowych | `ID`, `ID_KATALOGU` |
| `EW_POLYLINE` | geometria liniowa (~396k) | `UID`, `ID_WARSTWY`, `OPERAT`, `P0_X/Y`, `P1_X/Y`, `PN_X/Y` |
| `EW_TEXT` | etykiety (~295k) | `UID`, `ID_WARSTWY`, `OPERAT`, `POS_X`, `POS_Y` |

`OPERAT = 0` znaczy "brak operatu" (NULL nie występuje — potwierdzone empirycznie).
Sentinel-guard w UPDATE: `WHERE UID = ? AND OPERAT = 0`.

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
