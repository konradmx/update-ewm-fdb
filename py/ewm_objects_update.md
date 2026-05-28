# ewm_objects_update.py — historia i kontekst projektu

> ⚠️ **Status**: skrypt **wstrzymany** po wyjaśnieniu klienta. Cel okazał się inny —
> aktualizować trzeba `OPERAT` w **elementach graficznych** (`EW_POLYLINE`,
> `EW_TEXT`, ...), nie w `EW_OBIEKTY`. Nowa aplikacja: zobacz
> [`ewm_elements_update.md`](./ewm_elements_update.md).
>
> Plik niniejszy zostawiam jako *kontekst projektu* — żeby kolejny czat nie
> musiał odkrywać schematu od zera.

---

## Środowisko pracy

| Co | Wartość |
|---|---|
| Working dir | `D:\zzz_tmp\2026-05-07_geobid_KERG` |
| Baza testowa | `Example_1\source\gesut.fdb` |
| Plik wejściowy | `Example_1\source\pkt_z bazy.txt` |
| Firebird | wersja 3, port **3050** (w systemie chodzą równolegle FB 2, 3 i 5) |
| Łączenie | TCP: `127.0.0.1/3050:<ścieżka>`. **Nie embedded** — Ewmapa i DBeaver mają plik otwarty równolegle |
| User/pass | `SYSDBA` / `masterkey` |
| Charset | `UTF8` (mimo, że Firebird zwraca polskie błędy w cp1250 — sterownik je psuje) |
| Sterownik | `firebird-driver` w venv `venv_py314_ewm_fdb` (Python 3.14) |
| Platforma | Windows 10 |

## Quirki sterownika `firebird-driver`

1. **Polskie znaki w komunikatach błędów Firebirda są łamane** (znaki zastępcze `?`) — sterownik źle dekoduje. Nic z tym nie zrobimy z poziomu Pythona.
2. **`cur.execute(sql, (float, ...))` rzuca `AttributeError: 'float' has no attribute 'to_bytes'`** gdy bindowany float trafia w kolumnę typu `NUMERIC` (scaled INT64). Workaround: **inline'ować wartości floatów w SQL-u** (`f"... BETWEEN {x_lo!r} AND {x_hi!r}"`). To nasze stałe, brak ryzyka injection.

## Format pliku `pkt_z bazy.txt`

```
<ID_punktu> \t <X> \t <Y> \t <TYP>#<NUMER_OPERATU>
```

- Kol. 1 (ID punktu) — **ignorujemy** (może istnieć w bazie lub nie).
- Kol. 2, 3 — współrzędne kartezjańskie PUWG (metry, precyzja 0.01 m).
- Kol. 4 — `<TYP>` (liczba) + `#` + `<NUMER>` (string). **Numer może zawierać spacje** (np. `1/93 1806.14`) — wszystko po `#` do końca linii to numer, **bez normalizacji**.
- Encoding: **cp1250**.

**Statystyki ostatniego runa**: 613,519 linii łącznie, z czego 594,837 poprawnych. **Od linii 37,150** pojawia się sekcja, w której kol. 4 nie ma `#` — do wyjaśnienia z klientem (zmiana formatu w pliku?).

## Konwencje bazy `gesut.fdb`

### Dwa katalogi GESUT
Ten sam obiekt logiczny występuje zwykle jako **dwa wiersze** w `EW_OBIEKTY` (różne `UID`, ten sam `NUMER`):

| IDKATALOG | Katalog (Ewmapa) | KOD przykładowy |
|---:|---|---|
| 1 | `GESUT_2015` | `SUPW01` |
| 2 | `GESUT` | `SUWP` |

### Sentinel "puste" = `0`, **nie NULL**
**KLUCZOWE**: `EW_OBIEKTY.OPERAT = 0` znaczy "brak przypisanego operatu". `NULL` w tej kolumnie *nie występuje* (0 z 64,538 wierszy). Ewmapa pokazuje pusty pasek "Podstawa zmian" dla `OPERAT = 0`.

Powtórzyć weryfikację dla `EW_POLYLINE.OPERAT` i `EW_TEXT.OPERAT` w nowym skrypcie — może jest tak samo, może inaczej.

### Kolumny operat-owe w `EW_OBIEKTY`
| Kolumna | Znaczenie | NULL count (64538 wierszy) |
|---|---|---:|
| `OPERAT` | operat utworzenia (= "Podstawa zmian" w Ewmapie) | 0 |
| `OPERATR` | operat rewizji | 48,050 (większość) |
| `OPERATW` | operat wykreślenia | 64,538 (100%) |
| `DOD_OPERATY` | BLOB z listą dodatkowych operatów (tab "Dodatkowe operaty") | ~100% NULL |

## Schema kluczowych tabel

### `EW_OPERATY` — słownik operatów (~6,868 wierszy)
```
UID    INTEGER NOT NULL  (PK)
TYP    SMALLINT NOT NULL
NUMER  VARCHAR(50) NOT NULL  -- np. "P.1017.2020.184", "1/93 1806.14"
```
Lookup: `SELECT UID FROM EW_OPERATY WHERE TYP=? AND NUMER=?`

### `EW_OBIEKTY` — obiekty logiczne GESUT (~64,538 wierszy)
```
UID, ID, IDKATALOG, KOD, NUMER (22-zn. base64-like ID), IIP (UUID)
OPERAT INTEGER, OPERATR INTEGER, OPERATW INTEGER, DOD_OPERATY BLOB
STATUS SMALLINT  -- 0 = aktualny
+~150 tabel rozszerzeń EW_OB_DD_<numer> per kod obiektu (pomijamy)
```

### `EW_OB_ELEMENTY` — junction obiekt ↔ element graficzny (~179,387 wierszy)
```
UIDO  INTEGER NOT NULL   -- → EW_OBIEKTY.UID
IDE   BIGINT  NOT NULL   -- dyskryminowane przez TYP:
                         --   TYP=0 → EW_POLYLINE.ID  (uwaga: ID, NIE UID!)
                         --   TYP=1 → EW_TEXT.UID
N     INTEGER NOT NULL   -- kolejność elementu w obiekcie
TYP   SMALLINT           -- 0 = linia, 1 = tekst
ATRYBUT SMALLINT
```
Asymetria `IDE→ID` vs `IDE→UID` jest zaskakująca, ale potwierdzona empirycznie.

### `EW_POLYLINE` — geometria liniowa (~395,586 wierszy)
```
UID BIGINT NOT NULL          -- surrogate PK pojedynczego SEGMENTU polilinii
ID  BIGINT NOT NULL          -- ID polilinii (wiele segmentów ma to samo ID)
IDP, IDK BIGINT              -- linked-list segmentów polilinii
ID_WARSTWY INTEGER NOT NULL  -- FK do EW_WARSTWA_LINIOWA
OPERAT INTEGER               -- ⭐ target nowego skryptu
OPERAT_DELETE INTEGER        -- operat wykreślenia segmentu
XMIN, YMIN, XMAX, YMAX DOUBLE  -- bbox
POINTCOUNT INTEGER
P0_X/Y/Z, P1_X/Y/Z, PN_X/Y/Z DOUBLE  -- pierwszy, drugi, ostatni wierzchołek
                                       -- pośrednie są w EW_POLYLINE_POINTS
```

### `EW_TEXT` — etykiety / teksty (~294,853 wierszy)
```
UID BIGINT NOT NULL          -- = ID dla pojedynczych tekstów
ID  BIGINT NOT NULL
ID_WARSTWY INTEGER NOT NULL  -- FK do EW_WARSTWA_TEXTOWA
OPERAT INTEGER               -- ⭐ target nowego skryptu
OPERAT_DELETE INTEGER
TEXT VARCHAR(128)            -- treść lub makro np. '${u.ETYKIETA}'
POS_X, POS_Y, POS_Z DOUBLE   -- pozycja kotwicy etykiety
H, KAT, FONT, JUSTYFIKACJA, ODN_X, ODN_Y, B_SIZE_X, B_SIZE_Y
```

### Inne tabele z `OPERAT*` (potencjalny zakres do uzgodnienia)
- `EW_DZIALKI` (OPERAT, OPERATR) — działki
- `EW_KONTURY`, `EW_KONTURY_PUNKTY`
- `EW_PUNKTY` — osnowa
- `EW_UZYTKI`, `EW_UZYTKI_PUNKTY`
- `EW_EDYCJA` (OPERAT)

## Architektura skryptu (do reuse w nowym)

**Faza 1 — load** (~16 s na tej bazie): pełny scan każdej tabeli raz, wynik trzymany w pamięci jako dict / grid hash.

**Faza 2 — klasyfikacja** (~3 s dla 595k wpisów): czyste lookupy w pamięci.

**Faza 3 — UPDATE** (tylko gdy `DRY_RUN = False`): wszystko w jednej transakcji, commit na końcu. Safety: `UPDATE ... WHERE UID=? AND OPERAT=0` chroni przed nadpisaniem zmian wprowadzonych w międzyczasie.

### Indeks przestrzenny (grid hash 1 m × 1 m)
```python
class GeometryIndex:
    cells: dict[(int, int), list[tuple[int, int, float, float]]]
    # klucz: (floor(x), floor(y))
    # wartość: (geom_type, geom_key, x, y)
    
    def query(qx, qy, tol) -> set[(geom_type, geom_key)]:
        # 3x3 sąsiedztwo komórek + filtr |dx|≤tol i |dy|≤tol
```
Komórka 1 m >> tolerancja 0.01 m, więc 3x3 sąsiedztwo gwarantuje znalezienie wszystkich kandydatów.

### Wynik *poprzedniego* DRY-RUN-a (na bazie `source/gesut.fdb`)
| | |
|---|---:|
| Wpisów w pliku | 594,837 |
| Z błędem parsowania | 18,682 |
| Z nieznanym operatem | 105,714 |
| Bez geometrii w tol. 0.01 m | 352,019 |
| Geometria znaleziona, bez linku do `EW_OBIEKTY` | 104,474 |
| **Obiekty znalezione** | **62,898** |
| ↳ `OPERAT=0` → do update | 695 |
| ↳ already OK (= target) | 54,506 |
| ↳ MISMATCH | 7,697 |

Mała liczba "do update" (695 z 595k) była jednym z sygnałów, że stary cel był niewłaściwy. W nowym skrypcie target to `EW_POLYLINE`/`EW_TEXT` *bezpośrednio* — nie schodzimy do `EW_OBIEKTY`.

## Skrypty pomocnicze w `py/` (do reuse)

| Skrypt | Co robi | Status |
|---|---|---|
| `ewm_objects_update.py` | główna logika + connect/helpers | ✅ działa |
| `discover_object.py` | cross-search 3 znanych wartości we wszystkich kolumnach bazy | przydatne |
| `inspect_links.py` | schema + FK + zawartość 4 znanych tabel dla testowego obiektu | przydatne |
| `find_object_geom_link.py` | cross-search UID-ów geometrii → znalezienie tabeli-łącznika | znalazł EW_OB_ELEMENTY |
| `inspect_ob_elementy.py` | schema + zawartość `EW_OB_ELEMENTY` | dało strukturę junctiona |
| `inspect_operat_columns.py` | dump pól `EW_OBIEKTY` dla obiektu + NULL-counts per katalog + lista kolumn `OPERAT*` w całej bazie | dało odkrycie sentinela 0 |

Wszystkie korzystają z `connect()` zaimportowanego z `ewm_objects_update.py`.

## Test object (do reuse)

- **NUMER**: `gPhbxbZHskGLcjhp35efmQ`
- **EW_OBIEKTY**: 2 wiersze:
  - `UID=3312`, `IDKATALOG=1`, `KOD='SUPW01'`, `OPERAT=5038`
  - `UID=34104`, `IDKATALOG=2`, `KOD='SUWP'`, `OPERAT=5038`
- **EW_OPERATY UID=5038**: `TYP=3`, `NUMER='P.1017.2020.184'`
- **Punkt geometrii**: `(5676568.82, 6540976.82)` → 4 polilinie (UID 225501,225502,535852,535853; ID 225047,225048,514663,514664) + 4 teksty (UID 225458,240969,507709,507710)
- **EW_OB_ELEMENTY** dla UIDO=3312 i 34104: po 24 elementy każdy (34 TYP=0 + 14 TYP=1 razem)

Drugi przykład (z screena Ewmapy): **NUMER `9PaXa8xbt0qkMZts1RDVZA`**, UID=23937 (kat. 1) i UID=34454 (kat. 2), `OPERAT=0` w obu, czyli w starej koncepcji byłby kandydatem do update.

## Pamięci zapisane w `~/.claude/projects/.../memory/`

- `firebird_instances.md` — trzy serwery FB (2, 3, 5) na maszynie; FB 3 = port 3050.

## Otwarte kwestie (do uzgodnienia z klientem przy okazji)

1. **Linia 37,150+ w pliku** — co tam jest? Inna sekcja formatu, brak `#` w kol. 4.
2. **76% wpisów spoza zakresu** w starym skrypcie (~352k bez geometrii + 104k bez linku). Czy plik dotyczy też klas spoza GESUT-u (działki, kontury, użytki, osnowa)?
3. **MISMATCH-e** — co robić: tylko raport, czy nadpisywać? Pewnie tylko raport.
4. **Filtr `STATUS`** — czy aktualizować historyczne / wycofane obiekty?
