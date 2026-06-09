"""ewm_elements_update.py — przypisywanie operatów do elementów graficznych GESUT/BDOT500.

Cel:
  Aktualizacja kolumny OPERAT w EW_POLYLINE i EW_TEXT dla warstw należących
  do wskazanego katalogu (--katalog ID) w dowolnej bazie fdb Ewmapy
  (gesut.fdb, bdot500.fdb lub innej o tej samej strukturze).

Tryby pracy:
  --info    Tylko informacja o strukturze bazy (katalogi, warstwy, liczba elementów).
            Brak zmian w bazie. Nie wymaga podania pliku TXT.
  (domyślny) DRY RUN — analiza i raport, zero zmian w bazie.
  --execute Zapisuje zmiany w bazie.

Algorytm (DRY RUN / --execute):
  1. Weryfikuje, że wskazany katalog (--katalog ID) istnieje w EW_KATALOGI.
  2. Ładujemy słownik EW_OPERATY, indeks przestrzenny elementów z wybranego katalogu.
  3. Parsujemy plik TXT; przetwarzamy tylko wpisy z TYP in --typ.
  4. Dla każdego wpisu szukamy elementów w pobliżu (X,Y) ± TOLERANCE:
       OPERAT=0, operat znany w DB  → would-update / UPDATE
       OPERAT=0, operat nieznany    → operat zostanie wstawiony + UPDATE
                                      (INSERT tylko jeśli przynajmniej 1 trafienie)
       OPERAT=target               → already correct
       OPERAT=inny                 → MISMATCH, tylko raport
  5. Faza UPDATE (jeśli --execute):
       - INSERT brakujących operatów (przez generator EW_OPERATY_UID_GEN)
       - UPDATE EW_POLYLINE / EW_TEXT z sentinel-guardem (WHERE OPERAT = 0)

Przykłady:
  # Informacja o strukturze gesut.fdb
  python ewm_elements_update.py gesut.fdb --info

  # Dry run GESUT (katalog ID=2, operaty TYP=3)
  python ewm_elements_update.py gesut.fdb punkty.txt --katalog 2 --typ 3

  # Dry run BDOT500 (katalog ID=2, operaty TYP=3)
  python ewm_elements_update.py bdot500.fdb punkty.txt --katalog 2 --typ 3

  # Wykonaj zmiany
  python ewm_elements_update.py bdot500.fdb punkty.txt --katalog 2 --typ 3 --execute
"""

from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import firebird.driver as fb

if sys.platform == "win32" and not fb.driver_config.fb_client_library.value:
    for _d in (r"C:\Program Files\Firebird\Firebird_3_0",
               r"C:\Program Files (x86)\Firebird\Firebird_3_0"):
        _dll = os.path.join(_d, "fbclient.dll")
        if os.path.isfile(_dll):
            fb.driver_config.fb_client_library.value = _dll
            break


# =========================== KONFIGURACJA ============================

DEFAULT_DB_PATH   = r"D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\1_source\gesut.fdb"
DEFAULT_FILE_PATH = r"D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\1_source\pkt_z bazy.txt"

DEFAULT_HOST     = "127.0.0.1"
DEFAULT_PORT     = 3050        # FB 3; FB 2 i FB 5 chodzą na innych portach
DEFAULT_USER     = "SYSDBA"
DEFAULT_PASSWORD = "masterkey"
DEFAULT_CHARSET  = "UTF8"

DEFAULT_TOLERANCE      = 0.01   # m
DEFAULT_FILE_ENCODING  = "cp1250"
DEFAULT_GRID_CELL_SIZE = 1.0    # m; >> tolerancja, więc 3x3 sąsiedztwo wystarcza

DEFAULT_KATALOG_ID  = 2         # EW_KATALOGI.ID dla "GESUT" i "BDOT500" (wg rozp. MRPiT 2021)
OPERAT_TYP_FILTER   = {3}       # TYP operatów z pliku do przetworzenia (3=GESUT, 2=BDOT500)

DRY_RUN = True   # True = tylko raport, zero zmian w bazie
MAX_DETAIL_LINES = 20


# ===================== HELPERY POŁĄCZENIA (reuse) =====================

def resolve_path(p: str | Path) -> Path:
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def build_dsn(path: Path, host: str | None, port: int | None) -> str:
    if not host:
        return str(path)
    if port:
        return f"{host}/{port}:{path}"
    return f"{host}:{path}"


def connect(
    db_path: str | Path = DEFAULT_DB_PATH,
    host: str | None = DEFAULT_HOST,
    port: int | None = DEFAULT_PORT,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    charset: str = DEFAULT_CHARSET,
) -> fb.Connection:
    path = resolve_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Plik bazy nie istnieje: {path}")
    return fb.connect(
        database=build_dsn(path, host, port),
        user=user, password=password, charset=charset,
    )


# ==================== WERYFIKACJA KATALOGU ============================

def load_katalogi(con: fb.Connection) -> dict[int, str]:
    """Zwraca {ID: NAZWA} dla wszystkich katalogów w EW_KATALOGI."""
    with con.cursor() as cur:
        cur.execute("SELECT ID, NAZWA FROM EW_KATALOGI ORDER BY ID")
        return {row[0]: (row[1] or "").strip() for row in cur}


def validate_katalog(con: fb.Connection, katalog_id: int) -> str:
    """Sprawdza, czy katalog o podanym ID istnieje w EW_KATALOGI.

    Wypisuje wszystkie dostępne katalogi, oznacza wybrany.
    Zwraca NAZWA katalogu lub podnosi ValueError.
    """
    katalogi = load_katalogi(con)
    print("  Dostępne katalogi (EW_KATALOGI):")
    for kid, knazwa in sorted(katalogi.items()):
        marker = "  <-- wybrany" if kid == katalog_id else ""
        print(f"    ID={kid}  NAZWA='{knazwa}'{marker}")
    if katalog_id not in katalogi:
        raise ValueError(
            f"Katalog ID={katalog_id} nie istnieje w EW_KATALOGI tej bazy.\n"
            f"Dostępne ID: {sorted(katalogi.keys())}\n"
            "Użyj --katalog z jednym z powyższych ID."
        )
    return katalogi[katalog_id]


def show_db_info(con: fb.Connection) -> None:
    """Wyświetla informacje o strukturze katalogów i warstw w bazie."""
    katalogi = load_katalogi(con)

    print("\n  Katalogi (EW_KATALOGI):")
    for kid, knazwa in sorted(katalogi.items()):
        print(f"    ID={kid}  NAZWA='{knazwa}'")

    with con.cursor() as cur:
        print("\n  Warstwy liniowe (EW_WARSTWA_LINIOWA) per katalog:")
        cur.execute(
            "SELECT ID_KATALOGU, COUNT(*) FROM EW_WARSTWA_LINIOWA"
            " GROUP BY ID_KATALOGU ORDER BY ID_KATALOGU"
        )
        for kid, cnt in cur:
            print(f"    ID_KATALOGU={kid} ('{katalogi.get(kid, '?')}'): {cnt} warstw")

        print("\n  Warstwy tekstowe (EW_WARSTWA_TEXTOWA) per katalog:")
        cur.execute(
            "SELECT ID_KATALOGU, COUNT(*) FROM EW_WARSTWA_TEXTOWA"
            " GROUP BY ID_KATALOGU ORDER BY ID_KATALOGU"
        )
        for kid, cnt in cur:
            print(f"    ID_KATALOGU={kid} ('{katalogi.get(kid, '?')}'): {cnt} warstw")

        print("\n  EW_POLYLINE — elementy z OPERAT=0 per katalog:")
        cur.execute(
            "SELECT wl.ID_KATALOGU, COUNT(*)"
            " FROM EW_POLYLINE p"
            " JOIN EW_WARSTWA_LINIOWA wl ON wl.ID = p.ID_WARSTWY"
            " WHERE p.OPERAT = 0"
            " GROUP BY wl.ID_KATALOGU ORDER BY wl.ID_KATALOGU"
        )
        for kid, cnt in cur:
            print(f"    ID_KATALOGU={kid} ('{katalogi.get(kid, '?')}'): {cnt:,} elementów z OPERAT=0")

        print("\n  EW_TEXT — elementy z OPERAT=0 per katalog:")
        cur.execute(
            "SELECT wt.ID_KATALOGU, COUNT(*)"
            " FROM EW_TEXT t"
            " JOIN EW_WARSTWA_TEXTOWA wt ON wt.ID = t.ID_WARSTWY"
            " WHERE t.OPERAT = 0"
            " GROUP BY wt.ID_KATALOGU ORDER BY wt.ID_KATALOGU"
        )
        for kid, cnt in cur:
            print(f"    ID_KATALOGU={kid} ('{katalogi.get(kid, '?')}'): {cnt:,} elementów z OPERAT=0")

        print("\n  EW_OPERATY — liczba operatów per TYP:")
        cur.execute("SELECT TYP, COUNT(*) FROM EW_OPERATY GROUP BY TYP ORDER BY TYP")
        for typ, cnt in cur:
            print(f"    TYP={typ}: {cnt:,} operatów")


# ==================== INDEKS PRZESTRZENNY ============================

# Wpis w siatce: (table_flag, uid, operat)
#   table_flag = 0 -> EW_POLYLINE, 1 -> EW_TEXT
GeomKey = tuple[int, int, int]  # (table_flag, uid, operat)


class GeometryIndex:
    """Grid hash — komórka 1 m × 1 m, zapytanie w 3×3 sąsiedztwie."""

    __slots__ = ("cell_size", "cells", "size")

    def __init__(self, cell_size: float = DEFAULT_GRID_CELL_SIZE) -> None:
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], list[tuple[int, int, int, float, float]]] = {}
        self.size = 0

    def add(self, table_flag: int, uid: int, operat: int, x: float, y: float) -> None:
        cell = (int(x // self.cell_size), int(y // self.cell_size))
        bucket = self.cells.get(cell)
        entry = (table_flag, uid, operat, x, y)
        if bucket is None:
            self.cells[cell] = [entry]
        else:
            bucket.append(entry)
        self.size += 1

    def query(self, qx: float, qy: float, tol: float) -> set[GeomKey]:
        """Zwraca {(table_flag, uid, operat)} dla punktów w odl. <= tol od (qx,qy)."""
        results: set[GeomKey] = set()
        cx = int(qx // self.cell_size)
        cy = int(qy // self.cell_size)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = self.cells.get((cx + dx, cy + dy))
                if not bucket:
                    continue
                for tf, uid, operat, x, y in bucket:
                    if abs(x - qx) <= tol and abs(y - qy) <= tol:
                        results.add((tf, uid, operat))
        return results


# ==================== ŁADOWANIE DANYCH ================================

def _progress(label: str, n: int, every: int = 100_000) -> None:
    if n % every == 0 and n:
        print(f"    ... {label}: {n:,}")


def load_operaty(
    con: fb.Connection,
) -> tuple[dict[tuple[int, str], int], dict[int, str]]:
    """by_key: (typ, numer) -> UID;  by_uid_label: UID -> 'typ#numer'."""
    print("  EW_OPERATY...")
    t0 = time.time()
    by_key: dict[tuple[int, str], int] = {}
    by_uid_label: dict[int, str] = {}
    with con.cursor() as cur:
        cur.execute("SELECT UID, TYP, NUMER FROM EW_OPERATY")
        for uid, typ, numer in cur:
            numer = numer.strip()  # CHAR padding w Firebird może dodawać spacje
            by_key[(typ, numer)] = uid
            by_uid_label[uid] = f"{typ}#{numer}"
    print(f"    wczytano {len(by_uid_label):,} operatow  ({time.time()-t0:.1f}s)")
    return by_key, by_uid_label


def load_layer_ids(con: fb.Connection, katalog_id: int, katalog_nazwa: str) -> tuple[set[int], set[int]]:
    """Zwraca (poly_layer_ids, text_layer_ids) dla podanego katalogu."""
    with con.cursor() as cur:
        cur.execute(
            "SELECT ID FROM EW_WARSTWA_LINIOWA WHERE ID_KATALOGU = ?",
            (katalog_id,),
        )
        poly_ids = {r[0] for r in cur}
        cur.execute(
            "SELECT ID FROM EW_WARSTWA_TEXTOWA WHERE ID_KATALOGU = ?",
            (katalog_id,),
        )
        text_ids = {r[0] for r in cur}
    print(f"    warstwy '{katalog_nazwa}' (ID={katalog_id}): {len(poly_ids)} liniowych, {len(text_ids)} tekstowych")
    return poly_ids, text_ids


def load_geometry_index(
    con: fb.Connection,
    poly_layer_ids: set[int],
    text_layer_ids: set[int],
    katalog_nazwa: str = "",
    cell_size: float = DEFAULT_GRID_CELL_SIZE,
) -> GeometryIndex:
    """Buduje indeks z P0/P1/PN polilinii i POS tekstów — tylko warstwy wybranego katalogu."""
    idx = GeometryIndex(cell_size=cell_size)
    label = f" ({katalog_nazwa})" if katalog_nazwa else ""

    print(f"  EW_POLYLINE{label}...")
    t0 = time.time()
    n = 0
    with con.cursor() as cur:
        cur.execute(
            "SELECT UID, OPERAT, ID_WARSTWY, P0_X, P0_Y, P1_X, P1_Y, PN_X, PN_Y"
            " FROM EW_POLYLINE"
        )
        for uid, operat, id_w, p0x, p0y, p1x, p1y, pnx, pny in cur:
            if id_w not in poly_layer_ids:
                continue
            op = operat or 0
            if p0x is not None and p0y is not None:
                idx.add(0, uid, op, p0x, p0y)
            if p1x is not None and p1y is not None:
                idx.add(0, uid, op, p1x, p1y)
            if pnx is not None and pny is not None:
                idx.add(0, uid, op, pnx, pny)
            n += 1
            _progress("EW_POLYLINE", n)
    print(f"    {n:,} polilinii{label}, {idx.size:,} punktow  ({time.time()-t0:.1f}s)")

    print(f"  EW_TEXT{label}...")
    t0 = time.time()
    before = idx.size
    n = 0
    with con.cursor() as cur:
        cur.execute("SELECT UID, OPERAT, ID_WARSTWY, POS_X, POS_Y FROM EW_TEXT")
        for uid, operat, id_w, px, py in cur:
            if id_w not in text_layer_ids:
                continue
            if px is None or py is None:
                continue
            op = operat or 0
            idx.add(1, uid, op, px, py)
            n += 1
            _progress("EW_TEXT", n)
    print(f"    {n:,} tekstow{label}, {idx.size - before:,} punktow  ({time.time()-t0:.1f}s)")
    print(f"  Indeks: {idx.size:,} punktow w {len(idx.cells):,} komorkach")
    return idx


# ==================== PARSOWANIE PLIKU ================================

@dataclass(frozen=True)
class FileEntry:
    line_no: int
    x: float
    y: float
    operat_typ: int
    operat_numer: str
    orig_line: str   # oryginalna linia pliku (bez \n) — do wyswietlania w formacie TXT


def parse_text_file(
    path: Path,
    encoding: str = DEFAULT_FILE_ENCODING,
    typ_filter: set[int] | None = None,
) -> tuple[list[FileEntry], int, int]:
    """Zwraca (entries, ignored_no_operat, skipped_wrong_typ).

    Wiersze bez kolumny TYP#NUMER (za malo pol, brak '#', nieczytelny TYP,
    pusty NUMER) sa cicho pomijane i liczone w ignored_no_operat — nie sa
    traktowane jako bledy, bo plik moze zawierac sekcje innego formatu.

    Prawdziwy blad (X/Y nie sa liczba mimo obecnej kolumny operatu) takze
    jest liczony w ignored_no_operat, bo nie mozemy przetworzyc wspolrzednych.

    typ_filter: jesli podany, pomija wpisy z innym TYP (bez liczenia jako blad).
    """
    entries: list[FileEntry] = []
    ignored = 0
    skipped = 0
    with path.open(encoding=encoding) as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            if '\t' in line:
                parts = line.split('\t', 3)   # separator tabulatorowy; col4 może mieć spacje
            else:
                parts = line.split(maxsplit=3) # separator spacje; col4 zachowuje spacje wewnętrzne
            if len(parts) < 4:
                ignored += 1
                continue
            col4 = parts[3]
            if "#" not in col4:
                ignored += 1
                continue
            typ_str, _, numer = col4.partition("#")
            numer = numer.strip()  # usuwa przypadkowe spacje na końcu pola
            try:
                typ = int(typ_str)
            except ValueError:
                ignored += 1
                continue
            if not numer:
                ignored += 1
                continue
            try:
                x = float(parts[1])
                y = float(parts[2])
            except ValueError:
                ignored += 1
                continue
            if typ_filter and typ not in typ_filter:
                skipped += 1
                continue
            entries.append(FileEntry(line_no, x, y, typ, numer, line))
    return entries, ignored, skipped


# ==================== AKTUALIZACJA W BAZIE ============================

TABLE_NAMES = {0: "EW_POLYLINE", 1: "EW_TEXT"}


def insert_operat(
    cur: fb.Cursor,
    typ: int,
    numer: str,
    by_key: dict[tuple[int, str], int],
    by_uid_label: dict[int, str],
) -> int:
    """Wstawia nowy operat do EW_OPERATY i zwraca jego UID.
    Jesli operat juz istnieje w DB (np. z poprzedniego --execute lub przez padding CHAR),
    zwraca istniejacy UID zamiast probowac INSERT.
    Aktualizuje in-place slowniki by_key i by_uid_label.
    """
    cur.execute(
        "SELECT UID FROM EW_OPERATY WHERE TYP = ? AND NUMER = ?",
        (typ, numer),
    )
    row = cur.fetchone()
    if row:
        uid = row[0]
        by_key[(typ, numer)] = uid
        by_uid_label[uid] = f"{typ}#{numer}"
        return uid
    cur.execute("SELECT GEN_ID(EW_OPERATY_UID_GEN, 1) FROM RDB$DATABASE")
    new_uid = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO EW_OPERATY"
        " (UID, TYP, NUMER, OPIS, UWAGI, OSOU, OSOW, DTU, DTW, OPERACJA, EGIB)"
        " VALUES (?, ?, ?, '', '', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 0)",
        (new_uid, typ, numer),
    )
    by_key[(typ, numer)] = new_uid
    by_uid_label[new_uid] = f"{typ}#{numer}"
    return new_uid


def execute_updates(
    con: fb.Connection,
    updates_poly: list[tuple[int, int]],   # (uid, target_operat_uid)
    updates_text: list[tuple[int, int]],
    new_operaty: list[tuple[int, str]],    # (typ, numer) do INSERT przed UPDATE
    by_key: dict[tuple[int, str], int],
    by_uid_label: dict[int, str],
) -> tuple[int, int, int]:
    """Wykonuje INSERT-y i UPDATE-y w jednej transakcji.
    Zwraca (inserted_operaty, changed_poly, changed_text).
    """
    inserted = 0
    changed_poly = 0
    changed_text = 0
    t0 = time.time()

    with con.cursor() as cur:
        # Faza A: INSERT brakujacych operatow
        for typ, numer in new_operaty:
            if (typ, numer) not in by_key:
                insert_operat(cur, typ, numer, by_key, by_uid_label)
                inserted += 1

        # Faza B: UPDATE EW_POLYLINE
        sql_poly = "UPDATE EW_POLYLINE SET OPERAT = ? WHERE UID = ? AND OPERAT = 0"
        for uid, target_uid in updates_poly:
            cur.execute(sql_poly, (target_uid, uid))
            changed_poly += cur.rowcount

        # Faza C: UPDATE EW_TEXT
        sql_text = "UPDATE EW_TEXT SET OPERAT = ? WHERE UID = ? AND OPERAT = 0"
        for uid, target_uid in updates_text:
            cur.execute(sql_text, (target_uid, uid))
            changed_text += cur.rowcount

        con.commit()

    elapsed = time.time() - t0
    print(f"    INSERT operatow: {inserted}, UPDATE poly: {changed_poly},"
          f" UPDATE text: {changed_text}  ({elapsed:.1f}s)")
    return inserted, changed_poly, changed_text


# ==================== DATAKLASY WYNIKOW ==============================

@dataclass(frozen=True)
class WouldUpdate:
    file_line: int
    file_x: float
    file_y: float
    operat_typ: int
    operat_numer: str
    target_uid: int          # 0 jesli operat trzeba najpierw wstawic
    table_flag: int
    elem_uid: int
    is_new_operat: bool
    orig_line: str           # oryginalna linia pliku (format TXT)


@dataclass(frozen=True)
class Mismatch:
    file_line: int
    file_x: float
    file_y: float
    file_operat_label: str
    table_flag: int
    elem_uid: int
    current_operat_uid: int
    current_operat_label: str
    orig_line: str           # oryginalna linia pliku (format TXT)


# ==================== GLOWNA LOGIKA ==================================

def run_update(
    db_path: str | Path = DEFAULT_DB_PATH,
    file_path: str | Path | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    encoding: str = DEFAULT_FILE_ENCODING,
    cell_size: float = DEFAULT_GRID_CELL_SIZE,
    dry_run: bool = DRY_RUN,
    operat_typ_filter: set[int] | None = OPERAT_TYP_FILTER,
    katalog_id: int = DEFAULT_KATALOG_ID,
    save_reports: bool = False,
    info_only: bool = False,
) -> None:
    db_path_resolved = resolve_path(db_path)

    print("=" * 70)
    print("KONFIGURACJA")
    print("=" * 70)
    print(f"  Baza:       {db_path_resolved}")
    if not info_only:
        fp = resolve_path(file_path) if file_path else Path(DEFAULT_FILE_PATH)
        print(f"  Plik:       {fp}")
    print(f"  Katalog:    ID={katalog_id}")
    if not info_only:
        print(f"  TYP filter: {sorted(operat_typ_filter) if operat_typ_filter else 'wszystkie'}")
        print(f"  Tolerancja: {tolerance} m")
        print(f"  Cell size:  {cell_size} m")
        print(f"  Encoding:   {encoding}")
        tryb = "INFO" if info_only else ("DRY RUN" if dry_run else "EXECUTE (!!! BAZA ZOSTANIE ZMODYFIKOWANA !!!)")
        print(f"  Tryb:       {tryb}")

    # === Weryfikacja katalogu ===
    print(f"\n[0] Weryfikacja katalogu w bazie...")
    with connect(db_path) as con:
        katalog_nazwa = validate_katalog(con, katalog_id)
        print(f"  OK: katalog '{katalog_nazwa}' (ID={katalog_id}) istnieje.")

        if info_only:
            show_db_info(con)
            return

    file_path_resolved = resolve_path(file_path) if file_path else Path(DEFAULT_FILE_PATH)
    if not file_path_resolved.exists():
        raise FileNotFoundError(f"Plik tekstowy nie istnieje: {file_path_resolved}")

    # === Parsowanie ===
    print(f"\n[1/3] Parsuje plik...")
    t_parse = time.time()
    entries, ignored_no_operat, skipped_typ = parse_text_file(
        file_path_resolved, encoding=encoding, typ_filter=operat_typ_filter,
    )
    print(f"  poprawnych wpisow (po filtrze TYP):  {len(entries):,}")
    print(f"  bez kolumny operatu (pominiete):     {ignored_no_operat:,}")
    print(f"  inny TYP (pominiete):                {skipped_typ:,}")
    print(f"  ({time.time()-t_parse:.1f}s)")
    if not entries:
        print("Brak wpisow do przetworzenia.")
        return

    # === Ladowanie ===
    print(f"\n[2/3] Wczytuję tabele do pamieci...")
    t_load = time.time()
    with connect(db_path) as con:
        operaty_by_key, operaty_label = load_operaty(con)
        poly_layers, text_layers = load_layer_ids(con, katalog_id, katalog_nazwa)
        geom_idx = load_geometry_index(
            con, poly_layers, text_layers,
            katalog_nazwa=katalog_nazwa, cell_size=cell_size,
        )
    print(f"  Lacznie ladowanie: {time.time()-t_load:.1f}s")

    # === Klasyfikacja ===
    print(f"\n[3/3] Klasyfikuje wpisy pliku...")
    t_proc = time.time()

    # Kategorie per-wpis — każdy wpis trafia do dokładnie jednej listy.
    # Priorytet przy mieszanych trafieniach: conflict > already_correct > update_known/new > no_match
    no_match: list[FileEntry] = []              # brak geometrii w tolerancji
    already_correct: list[FileEntry] = []       # operat w txt == operat geometrii w fdb
    conflict: list[FileEntry] = []      # operat w txt != operat geometrii w fdb
    update_known: list[FileEntry] = []  # OPERAT=0, operat z txt ISTNIEJE w fdb
    update_new: list[FileEntry] = []        # OPERAT=0, operat z txt NIE ISTNIEJE w fdb

    mismatches: list[Mismatch] = []  # szczegoly per-trafienie, do podgladu w konsoli
    would_update: list[WouldUpdate] = []
    scheduled_uids: set[tuple[int, int]] = set()

    for i, entry in enumerate(entries, 1):
        if i % 50_000 == 0:
            print(f"  ... przetworzono {i:,}/{len(entries):,}")

        target_uid = operaty_by_key.get((entry.operat_typ, entry.operat_numer))

        hits = geom_idx.query(entry.x, entry.y, tolerance)
        if not hits:
            no_match.append(entry)
            continue

        has_mismatch = False
        has_correct = False
        has_candidate = False

        for tf, uid, current_op in hits:
            if current_op == 0:
                has_candidate = True
                key = (tf, uid)
                if key not in scheduled_uids:
                    scheduled_uids.add(key)
                    would_update.append(WouldUpdate(
                        file_line=entry.line_no,
                        file_x=entry.x, file_y=entry.y,
                        operat_typ=entry.operat_typ,
                        operat_numer=entry.operat_numer,
                        target_uid=target_uid if target_uid is not None else 0,
                        table_flag=tf,
                        elem_uid=uid,
                        is_new_operat=(target_uid is None),
                        orig_line=entry.orig_line,
                    ))
            elif target_uid is not None and current_op == target_uid:
                has_correct = True
            else:
                has_mismatch = True
                cur_label = operaty_label.get(current_op, f"<UID={current_op}>")
                mismatches.append(Mismatch(
                    file_line=entry.line_no,
                    file_x=entry.x, file_y=entry.y,
                    file_operat_label=f"{entry.operat_typ}#{entry.operat_numer}",
                    table_flag=tf, elem_uid=uid,
                    current_operat_uid=current_op,
                    current_operat_label=cur_label,
                    orig_line=entry.orig_line,
                ))

        # Jeden wpis → jedna kategoria (priorytet: conflict > already_correct > update_*)
        if has_mismatch:
            conflict.append(entry)
        elif has_correct:
            already_correct.append(entry)
        elif has_candidate:
            if target_uid is None:
                update_new.append(entry)
            else:
                update_known.append(entry)

    print(f"  klasyfikacja: {time.time()-t_proc:.1f}s")

    # Zbierz nowe operaty do wstawienia
    new_operaty_to_insert: list[tuple[int, str]] = sorted({
        (w.operat_typ, w.operat_numer)
        for w in would_update if w.is_new_operat
    })

    # === Faza UPDATE ===
    if not dry_run:
        print(f"\n!!! Wykonuje zmiany w bazie !!!")
        print(f"  Nowe operaty do INSERT: {len(new_operaty_to_insert):,}")
        print(f"  Elementy do UPDATE:     {len(would_update):,}")
        with connect(db_path) as con:
            # Przed UPDATE musimy znać UID-y nowo wstawionych operatów
            with con.cursor() as cur:
                for typ, numer in new_operaty_to_insert:
                    insert_operat(cur, typ, numer, operaty_by_key, operaty_label)
                con.commit()

            # Teraz budujemy listy (uid, target_uid) z uzupełnionymi UID-ami
            upd_poly = [
                (w.elem_uid, operaty_by_key[(w.operat_typ, w.operat_numer)])
                for w in would_update if w.table_flag == 0
            ]
            upd_text = [
                (w.elem_uid, operaty_by_key[(w.operat_typ, w.operat_numer)])
                for w in would_update if w.table_flag == 1
            ]
            with con.cursor() as cur:
                n_poly = 0
                sql_p = "UPDATE EW_POLYLINE SET OPERAT = ? WHERE UID = ? AND OPERAT = 0"
                for uid, tgt in upd_poly:
                    cur.execute(sql_p, (tgt, uid))
                    n_poly += cur.rowcount
                n_text = 0
                sql_t = "UPDATE EW_TEXT SET OPERAT = ? WHERE UID = ? AND OPERAT = 0"
                for uid, tgt in upd_text:
                    cur.execute(sql_t, (tgt, uid))
                    n_text += cur.rowcount
                con.commit()
            print(f"  -> INSERT operatow: {len(new_operaty_to_insert):,},"
                  f"  UPDATE poly: {n_poly:,}, UPDATE text: {n_text:,}")

    # === RAPORT ===
    n_elem_new = sum(1 for w in would_update if w.is_new_operat)
    n_elem_known = len(would_update) - n_elem_new
    total_cat = (len(no_match) + len(already_correct) + len(conflict)
                 + len(update_known) + len(update_new))

    print("\n" + "=" * 70)
    print("RAPORT" + (" (DRY RUN — zero zmian w bazie)" if dry_run else ""))
    print("=" * 70)
    print(f"  Wpisow bez kolumny operatu (pominiete):  {ignored_no_operat:,}")
    print(f"  Wpisow z innym TYP (pominiete):          {skipped_typ:,}")
    print(f"  ---")
    print(f"  Wpisow po filtrze TYP (razem):           {len(entries):,}")
    print(f"    bez geometrii w tolerancji:            {len(no_match):,}")
    print(f"    operat zgodny z geometria w fdb:       {len(already_correct):,}")
    print(f"    operat rozny od geometrii w fdb:       {len(conflict):,}")
    action = "do aktualizacji (DRY)" if dry_run else "ZAKTUALIZOWANE"
    print(f"    kandydaci ({action}):")
    print(f"      operat istnieje w fdb:               {len(update_known):,}")
    print(f"      operat nowy (brak w fdb):            {len(update_new):,}")
    if total_cat != len(entries):
        print(f"  !!! BLAD KATEGORYZACJI: suma {total_cat:,} != wpisow {len(entries):,} !!!")
    print(f"  ---")
    print(f"  Elementow geometrii OPERAT=0 -> {action}: {len(would_update):,}")
    print(f"    w tym nowe operaty (INSERT):           {n_elem_new:,}")
    print(f"    w tym istniejace operaty:              {n_elem_known:,}")
    print(f"  Unikalnych nowych operatow do INSERT:    {len(new_operaty_to_insert):,}")
    print(f"  Trafien MISMATCH (szczegoly per-hit):    {len(mismatches):,}")

    _print_truncated("Brak geometrii w tolerancji", no_match,
                     lambda e: e.orig_line, no_indent=True)
    _print_truncated("Kandydaci (operat istnieje w fdb)", update_known,
                     lambda e: e.orig_line, no_indent=True)
    _print_truncated("Kandydaci (operat nowy)", update_new,
                     lambda e: e.orig_line, no_indent=True)
    _print_truncated("MISMATCH-e (operat w bazie != operat w pliku)", mismatches,
                     lambda m: m.orig_line, no_indent=True)

    # === Zapis raportów do plików ===
    if save_reports:
        stem = file_path_resolved.stem
        out_dir = file_path_resolved.parent

        def _write_report(suffix: str, items: list[FileEntry]) -> None:
            out_path = out_dir / f"{stem}_{suffix}.txt"
            with out_path.open("w", encoding=encoding, newline="\r\n") as f:
                for entry in items:
                    f.write(entry.orig_line + "\r\n")
            print(f"  -> {out_path.name}  ({len(items):,} wierszy)")

        print(f"\n  Zapisuje raporty do: {out_dir}")
        print(f"  (suma = {total_cat:,}"
              + (" = " if total_cat == len(entries) else " != ")
              + f"wpisow po filtrze: {len(entries):,}"
              + (" [OK])" if total_cat == len(entries) else " [BLAD!])"))
        _write_report("brak_pasującej_geometrii",                no_match)
        _write_report("geometria_ma_juz_zgodny_operat",         already_correct)
        _write_report("geometria_ma_juz_inny_operat",           conflict)
        _write_report("geometria_do_zmiany_operat_juz_w_bazie", update_known)
        _write_report("geometria_do_zmiany_operat_tylko_w_txt", update_new)


def _print_truncated(title, lst, formatter, no_indent: bool = False):
    if not lst:
        return
    print(f"\n  {title} (pierwsze {MAX_DETAIL_LINES} z {len(lst):,}):")
    indent = "" if no_indent else "    "
    for item in lst[:MAX_DETAIL_LINES]:
        print(f"{indent}{formatter(item)}")
    if len(lst) > MAX_DETAIL_LINES:
        print(f"  ... i jeszcze {len(lst) - MAX_DETAIL_LINES:,}")


if __name__ == "__main__":
    import argparse, sys

    p = argparse.ArgumentParser(
        description=(
            "Aktualizacja OPERAT w elementach graficznych GESUT / BDOT500.\n\n"
            "Tryby pracy:\n"
            "  --info            tylko informacja o strukturze bazy (bez pliku TXT)\n"
            "  (domyslny)        DRY RUN — analiza i raport, zero zmian\n"
            "  --execute         zapisuje zmiany w bazie\n\n"
            "Przyklady:\n"
            "  python ewm_elements_update.py gesut.fdb --info\n"
            "  python ewm_elements_update.py gesut.fdb punkty.txt --katalog 2 --typ 3\n"
            "  python ewm_elements_update.py bdot500.fdb punkty.txt --katalog 2 --typ 3\n"
            "  python ewm_elements_update.py bdot500.fdb punkty.txt --katalog 2 --typ 3 --execute"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("db",   metavar="PLIK.fdb",
                   help="Baza danych Firebird (gesut.fdb, bdot500.fdb itp.)")
    p.add_argument("txt",  metavar="PLIK.txt", nargs="?",
                   help="Plik punktow TXT (kolumny: ID  X  Y  TYP#NUMER, separator: tabulator lub spacje)."
                        " Wymagany w trybie DRY RUN i --execute; zbedny przy --info.")
    p.add_argument("--info",      action="store_true",
                   help="Tylko informacja o strukturze bazy (katalogi, warstwy, liczba elementow)."
                        " Nie wymaga pliku TXT.")
    p.add_argument("--execute",   action="store_true",
                   help="Zapisz zmiany w bazie (domyslnie: tylko raport DRY RUN)")
    p.add_argument("--katalog",   default=DEFAULT_KATALOG_ID, type=int,
                   metavar="ID",  help=f"ID katalogu w EW_KATALOGI (domyslnie: {DEFAULT_KATALOG_ID};"
                                       " GESUT=2, BDOT500=2)")
    p.add_argument("--typ",       default=list(OPERAT_TYP_FILTER), nargs="+", type=int,
                   metavar="N",   help=f"TYP(y) operatow do przetworzenia (domyslnie: {sorted(OPERAT_TYP_FILTER)};"
                                       " GESUT=3, BDOT500=2)")
    p.add_argument("--tolerance", default=DEFAULT_TOLERANCE, type=float,
                   metavar="M",   help=f"Tolerancja przestrzenna [m] (domyslnie: {DEFAULT_TOLERANCE})")
    p.add_argument("--encoding",  default=DEFAULT_FILE_ENCODING,
                   metavar="ENC", help=f"Kodowanie pliku txt (domyslnie: {DEFAULT_FILE_ENCODING})")
    p.add_argument("--details",   default=MAX_DETAIL_LINES, type=int,
                   metavar="N",   help=f"Liczba przykladow w raporcie (domyslnie: {MAX_DETAIL_LINES})")
    p.add_argument("--save",      action="store_true",
                   help="Zapisz kategorie raportu do plikow TXT obok pliku zrodlowego")

    if len(sys.argv) == 1:
        p.print_help()
        sys.exit(0)

    args = p.parse_args()

    if not args.info and args.txt is None:
        p.error("Plik TXT jest wymagany w trybie DRY RUN i --execute. Dodaj --info aby pominac plik.")

    MAX_DETAIL_LINES = args.details

    try:
        run_update(
            db_path=args.db,
            file_path=args.txt,
            tolerance=args.tolerance,
            encoding=args.encoding,
            dry_run=not args.execute,
            operat_typ_filter=set(args.typ),
            katalog_id=args.katalog,
            save_reports=args.save,
            info_only=args.info,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nBLAD: {exc}", file=sys.stderr)
        sys.exit(1)
