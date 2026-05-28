# ewm_elements_update.py — wytyczne dla nowego skryptu

> 🆕 **Nowa aplikacja**, zastępująca koncepcyjnie [`ewm_objects_update.py`](./ewm_objects_update.md).

## Cel (zmiana koncepcji)

Aktualizować `OPERAT` w **tabelach elementów graficznych** (`EW_POLYLINE`, `EW_TEXT`, prawdopodobnie też inne klasy do uzgodnienia), nie w `EW_OBIEKTY`.

Wyjaśnienie klienta: chodzi o "operaty elementów graficznych na warstwach", czyli każdy *element graficzny* (polilinia, etykieta) ma swój operat i to on jest celem aktualizacji. Tabela `EW_OBIEKTY` (i jej kolumna `OPERAT`) nie jest tu istotna.

## Co zostaje bez zmian

Wszystko co zostało ustalone w `ewm_objects_update.md` jest **aktualne**:

- środowisko (Firebird 3 na `127.0.0.1:3050`, venv `venv_py314_ewm_fdb`),
- pliki (`Example_1/source/gesut.fdb`, `Example_1/source/pkt_z bazy.txt`),
- format pliku (TSV, kol. 4 = `<typ>#<numer>`, numer może zawierać spacje, bez normalizacji),
- konwencja "puste = 0" w `EW_OBIEKTY.OPERAT` (do potwierdzenia czy obowiązuje też w `EW_POLYLINE.OPERAT` / `EW_TEXT.OPERAT`),
- quirki sterownika (inline floatów w SQL-u, polskie znaki w błędach),
- helpery `connect()`, `resolve_path()`, `build_dsn()`, `parse_text_file()` — z `ewm_objects_update.py` można importować lub przekleić.

## Algorytm

Dla każdej linii pliku `(X, Y, typ, numer)`:

1. **Lookup operatu**: `EW_OPERATY WHERE TYP=? AND NUMER=?` → `target_operat_uid`.
   - Jeśli brak → wpis "nieznany operat", skip.
2. **Znajdź elementy graficzne w pobliżu** `(X, Y) ± TOLERANCE` (domyślnie 0.01 m):
   - `EW_POLYLINE` — wierzchołki `P0`, `P1`, `PN` (do uzgodnienia: czy też pośrednie z `EW_POLYLINE_POINTS`).
   - `EW_TEXT` — `POS_X`, `POS_Y`.
   - Inne tabele — do uzgodnienia (patrz pytania niżej).
3. **Dla każdego znalezionego elementu**, sprawdź jego `OPERAT`:
   - `OPERAT = 0` (lub NULL?) → **kandydat do UPDATE** `OPERAT := target_operat_uid`.
   - `OPERAT = target_operat_uid` → już prawidłowy, skip.
   - `OPERAT = inny operat` → **MISMATCH**, tylko raport, **nigdy nie nadpisuj**.
4. (jeśli `not DRY_RUN`) wykonaj UPDATE-y w jednej transakcji + commit.

**Bezpieczeństwo UPDATE-u**: `UPDATE <tabela> SET OPERAT = ? WHERE UID = ? AND OPERAT = 0` (sentinel-guard tak samo jak w starej wersji).

## Architektura — reuse ze starego skryptu

Wzór z `ewm_objects_update.py` zostaje, z drobnymi zmianami:

```
load_operaty(con)             ← bez zmian
load_geometry_index(con)      ← MODYFIKACJA: w komórce siatki zamiast 
                                 (geom_type, geom_key, x, y)
                                 trzymamy (table_name, uid, operat, x, y),
                                 bo target UPDATE-u to bezpośrednio ten wiersz,
                                 a nie obiekt po junction-table
parse_text_file(path)         ← bez zmian
run_update(...)               ← pętla klasyfikacji + faza UPDATE
                                 bez kroku "z geometrii → obiekt"
```

**Co znika**: ładowanie `EW_OBIEKTY`, ładowanie `EW_OB_ELEMENTY`, lookup po junction-table. To wszystko było potrzebne tylko żeby ze znalezionej geometrii dotrzeć do *obiektu* — teraz target to sama geometria.

## Otwarte pytania do klienta (PRZED implementacją)

1. **Zakres tabel** — czy tylko `EW_POLYLINE` + `EW_TEXT` (GESUT-owe warstwy graficzne), czy też:
   - `EW_DZIALKI` (działki — OPERAT, OPERATR)
   - `EW_KONTURY`, `EW_KONTURY_PUNKTY`
   - `EW_PUNKTY` (osnowa)
   - `EW_UZYTKI`, `EW_UZYTKI_PUNKTY`
   - `EW_EDYCJA`

   Pełna lista kolumn `*OPERAT*` w bazie była w logu `inspect_operat_columns.log`.

2. **Sentinel "puste"** — czy `EW_POLYLINE.OPERAT = 0` znaczy "brak operatu" (jak w `EW_OBIEKTY`), czy może NULL? Szybki test:
   ```sql
   SELECT
     SUM(CASE WHEN OPERAT = 0 THEN 1 ELSE 0 END) AS zera,
     SUM(CASE WHEN OPERAT IS NULL THEN 1 ELSE 0 END) AS nulle
   FROM EW_POLYLINE;
   ```
   To samo dla `EW_TEXT` i pozostałych klas.

3. **`OPERAT_DELETE`** — `EW_POLYLINE` i `EW_TEXT` mają kolumnę `OPERAT_DELETE` (operat wykreślenia segmentu). Czy elementy z `OPERAT_DELETE IS NOT NULL` powinniśmy:
   - pomijać (wykreślone, "już nie istnieją"),
   - czy aktualizować na równi z aktualnymi?

4. **Wszystkie wierzchołki polilinii czy tylko endpointy?** Stary skrypt indeksował tylko `P0`/`P1`/`PN` z `EW_POLYLINE`. Pośrednie wierzchołki polilinii są w `EW_POLYLINE_POINTS` — czy je dołączyć?
   - Jeśli punkty w pliku mogą trafiać w środkowe wierzchołki polilinii — TAK, trzeba dołączyć.
   - Jeśli tylko w endpointy / etykiety — wystarczy obecne podejście.

5. **MISMATCH-e — polityka raportu** — zwykle: tylko log do `mismatch_report.csv` (lub `.log`), nie nadpisywanie. Potwierdzić.

6. **Linia ~37,150+ w pliku** — sekcja gdzie kol. 4 nie ma `#`. Co to za rekordy? Może to inna sekcja formatu lub plik to konkatenacja dwóch eksportów.

7. **76% wpisów spoza zakresu** w starym skrypcie. W nowym skrypcie (target = geometria, nie obiekt) wiele z tych 104k "geometria znaleziona bez linku" zniknie z kategorii "nie trafione", bo zniknie wymóg linku do `EW_OBIEKTY`. Ale 352k "bez geometrii w tol." pozostaje. Może warto puścić DRY-RUN z `TOLERANCE = 0.05` / `0.1` / `1.0` żeby zobaczyć ile się "domyka".

## Konfiguracja domyślna (do skopiowania do skryptu)

```python
DEFAULT_DB_PATH = r"D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\source\gesut.fdb"
DEFAULT_FILE_PATH = r"D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\source\pkt_z bazy.txt"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3050  # FB 3 (FB 2 i FB 5 na innych portach)
DEFAULT_USER = "SYSDBA"
DEFAULT_PASSWORD = "masterkey"
DEFAULT_CHARSET = "UTF8"
DEFAULT_TOLERANCE = 0.01            # m
DEFAULT_FILE_ENCODING = "cp1250"
DEFAULT_GRID_CELL_SIZE = 1.0        # m

DRY_RUN = True  # bezpiecznie — żadnych UPDATE-ów dopóki nie zweryfikujemy raportu
```

## Sugerowana sekwencja prac

1. **Sanity check sentinela** — zapytanie z pyt. 2 dla wszystkich kandydujących tabel. Wynik decyduje o filtrze WHERE w UPDATE.
2. **Zerknąć w plik (linie 37,145-37,160)** — co to za sekcja bez `#`.
3. **Uzgodnić zakres tabel** z klientem (pyt. 1).
4. **Skopiować `ewm_objects_update.py` → `ewm_elements_update.py`** i przerobić:
   - usunąć `load_objects()`, `load_object_junctions()`,
   - zmienić `GeometryIndex` żeby pamiętał `(table_name, uid, operat)` zamiast `(geom_type, geom_key)`,
   - pętla klasyfikacji już bez kroku "geometria → obiekt".
5. **DRY_RUN** — sprawdzić liczbowo (ile "do update", ile MISMATCH, ile "bez geometrii"). Powinno wyglądać znacznie sensowniej niż 695 z starego runa.
6. **Decyzja klienta**, real UPDATE.

## Raport — minimum

Tak jak w starym skrypcie, do logu (z truncate do `MAX_DETAIL_LINES=20`):

- Wpisów poprawnych / z błędem parsowania.
- Wpisów z nieznanym operatem (próbka).
- Wpisów bez geometrii w tolerancji (próbka).
- Elementów znalezionych łącznie, per `(tabela)`.
  - z `OPERAT=0` → do/zaktualizowane,
  - z `OPERAT=target` → już prawidłowe,
  - z `OPERAT=inny` → MISMATCH (z czym matchował, na co plik chciał zmienić).

## Notatka o alternatywnych nazwach

Wybrano `ewm_elements_update.py` (paraleluje do `ewm_objects_update.py` i pasuje do terminologii klienta "elementy graficzne"). Rozważane:

- `ewm_graphics_update.py` — też OK, jaśniej oddziela od `EW_OB_ELEMENTY`.
- `ewm_geom_operat_update.py` — najbardziej opisowe, ale długie.

Można zmienić, jeśli klient woli inną.
