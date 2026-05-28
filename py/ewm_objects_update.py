"""ewm_objects_update.py — przypisywanie operatów do obiektów GESUT.

V2: indeks przestrzenny w pamięci.

Stara wersja robiła N × M zapytań SQL z pełnym skanem EW_POLYLINE/EW_TEXT przy
każdej linii pliku (brak indeksu przestrzennego w Firebirdzie). Dla 595 tys.
linii × ~1 mln wierszy geometrii to 5×10⁹ porównań — niewykonalne.

Tutaj: wczytujemy raz EW_OPERATY / EW_OBIEKTY / EW_OB_ELEMENTY / EW_POLYLINE
endpoints / EW_TEXT.POS do struktur Pythona (dict + grid hash 1 m × 1 m),
potem dla każdej linii pliku robimy lookup w pamięci — O(~9 komórek) zamiast
O(rozmiar tabeli).

Algorytm:
  1. Lookup operatu (typ, numer) → docelowy UID (z EW_OPERATY).
  2. Lookup geometrii przy (X, Y) ± TOLERANCE w indeksie.
  3. Z geometrii znajdź obiekt(y) przez junction-table EW_OB_ELEMENTY:
       TYP=0  → IDE = EW_POLYLINE.ID
       TYP=1  → IDE = EW_TEXT.UID
  4. Klasyfikacja każdego obiektu po OPERAT:
       OPERAT = 0            → would-update / UPDATE
       OPERAT = target_uid   → już prawidłowy
       OPERAT = inny operat  → MISMATCH (zawsze tylko raport)

Tryb pracy steruje stała DRY_RUN. Domyślnie True — żadnych zmian w bazie.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import firebird.driver as fb


# =========================== KONFIGURACJA ============================

DEFAULT_DB_PATH = r"D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\source\gesut.fdb"
DEFAULT_FILE_PATH = r"D:\zzz_tmp\2026-05-07_geobid_KERG\Example_1\source\pkt_z bazy.txt"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3050  # FB 3 w systemie; FB 2 i FB 5 na innych portach
DEFAULT_USER = "SYSDBA"
DEFAULT_PASSWORD = "masterkey"
DEFAULT_CHARSET = "UTF8"

DEFAULT_TOLERANCE = 0.01  # m
DEFAULT_FILE_ENCODING = "cp1250"

DEFAULT_GRID_CELL_SIZE = 1.0  # m — siatka indeksu przestrzennego
                              # >> tolerancja (0.01 m), żeby 3×3 sąsiedztwo
                              # gwarantowało znalezienie wszystkich kandydatów

# True  → tylko raport, ZERO zmian w bazie
# False → wykonaj UPDATE-y w jednej transakcji + commit
DRY_RUN = True

MAX_DETAIL_LINES = 20


# =========================== HELPERY POŁĄCZENIA ============================

def resolve_path(p: str | Path) -> Path:
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


resolve_db_path = resolve_path  # alias dla wstecznej zgodności ze skryptami discover_*


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
        user=user,
        password=password,
        charset=charset,
    )


def test_connection(
    db_path: str | Path = DEFAULT_DB_PATH,
    host: str | None = DEFAULT_HOST,
    port: int | None = DEFAULT_PORT,
) -> None:
    path = resolve_path(db_path)
    print(f"Łączę z bazą: {build_dsn(path, host, port)}")
    with connect(path, host=host, port=port) as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT "
                "rdb$get_context('SYSTEM', 'ENGINE_VERSION'), "
                "rdb$get_context('SYSTEM', 'DB_NAME'), "
                "current_user, current_date "
                "FROM rdb$database"
            )
            engine_version, db_name, db_user, today = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM rdb$relations WHERE rdb$system_flag = 0")
            (user_table_count,) = cur.fetchone()
    print("Połączenie OK.")
    print(f"  Wersja Firebird:    {engine_version}")
    print(f"  Nazwa bazy:         {db_name}")
    print(f"  Użytkownik:         {db_user}")
    print(f"  Data serwera:       {today}")
    print(f"  Liczba tabel user.: {user_table_count}")


# =========================== INDEKS PRZESTRZENNY ============================

class GeometryIndex:
    """Grid hash dla punktów geometrii.

    Każdy punkt zapamiętujemy jako (geom_type, geom_key, x, y), gdzie:
      geom_type = 0 (polilinia) lub 1 (tekst)  ← zgodne z EW_OB_ELEMENTY.TYP
      geom_key  = dla polilinii: EW_POLYLINE.ID  (referowane przez EW_OB_ELEMENTY.IDE)
                  dla tekstu:    EW_TEXT.UID
    """

    __slots__ = ("cell_size", "cells", "size")

    def __init__(self, cell_size: float = DEFAULT_GRID_CELL_SIZE) -> None:
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], list[tuple[int, int, float, float]]] = {}
        self.size = 0

    def add(self, geom_type: int, geom_key: int, x: float, y: float) -> None:
        cell = (int(x // self.cell_size), int(y // self.cell_size))
        bucket = self.cells.get(cell)
        if bucket is None:
            self.cells[cell] = [(geom_type, geom_key, x, y)]
        else:
            bucket.append((geom_type, geom_key, x, y))
        self.size += 1

    def query(self, qx: float, qy: float, tol: float) -> set[tuple[int, int]]:
        """Zwraca {(geom_type, geom_key)} dla punktów w odległości ≤ tol od (qx, qy)."""
        results: set[tuple[int, int]] = set()
        cx = int(qx // self.cell_size)
        cy = int(qy // self.cell_size)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = self.cells.get((cx + dx, cy + dy))
                if not bucket:
                    continue
                for geom_type, key, x, y in bucket:
                    if abs(x - qx) <= tol and abs(y - qy) <= tol:
                        results.add((geom_type, key))
        return results


# =========================== ŁADOWANIE DANYCH ============================

@dataclass(frozen=True)
class ObjectInfo:
    uid: int
    operat: int
    numer: str
    kod: str
    idkatalog: int


def _progress(label: str, n: int, every: int = 100_000) -> None:
    if n % every == 0 and n:
        print(f"    ... {label}: {n:,}")


def load_operaty(con: fb.Connection) -> tuple[dict[tuple[int, str], int], dict[int, str]]:
    """Zwraca (by_key, by_uid_label):
      by_key: (typ, numer) → UID
      by_uid_label: UID → 'typ#numer' (do raportów)
    """
    print("  EW_OPERATY...")
    t0 = time.time()
    by_key: dict[tuple[int, str], int] = {}
    by_uid_label: dict[int, str] = {}
    with con.cursor() as cur:
        cur.execute("SELECT UID, TYP, NUMER FROM EW_OPERATY")
        for uid, typ, numer in cur:
            by_key[(typ, numer)] = uid
            by_uid_label[uid] = f"{typ}#{numer}"
    print(f"    wczytano {len(by_uid_label):,} operatów  ({time.time()-t0:.1f}s)")
    return by_key, by_uid_label


def load_objects(con: fb.Connection) -> dict[int, ObjectInfo]:
    """UID → ObjectInfo dla wszystkich obiektów."""
    print("  EW_OBIEKTY...")
    t0 = time.time()
    result: dict[int, ObjectInfo] = {}
    n = 0
    with con.cursor() as cur:
        cur.execute("SELECT UID, OPERAT, NUMER, KOD, IDKATALOG FROM EW_OBIEKTY")
        for uid, operat, numer, kod, idkat in cur:
            result[uid] = ObjectInfo(uid, operat, numer, kod, idkat)
            n += 1
            _progress("EW_OBIEKTY", n)
    print(f"    wczytano {len(result):,} obiektów  ({time.time()-t0:.1f}s)")
    return result


def load_object_junctions(
    con: fb.Connection,
) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """Zwraca (poly_id → [obj_uids], text_uid → [obj_uids]) z EW_OB_ELEMENTY."""
    print("  EW_OB_ELEMENTY...")
    t0 = time.time()
    poly_to_uidos: dict[int, list[int]] = {}
    text_to_uidos: dict[int, list[int]] = {}
    n = 0
    with con.cursor() as cur:
        cur.execute("SELECT UIDO, IDE, TYP FROM EW_OB_ELEMENTY")
        for uido, ide, typ in cur:
            if ide is None or uido is None:
                continue
            if typ == 0:
                lst = poly_to_uidos.get(ide)
                if lst is None:
                    poly_to_uidos[ide] = [uido]
                else:
                    lst.append(uido)
            elif typ == 1:
                lst = text_to_uidos.get(ide)
                if lst is None:
                    text_to_uidos[ide] = [uido]
                else:
                    lst.append(uido)
            n += 1
            _progress("EW_OB_ELEMENTY", n)
    print(
        f"    wczytano {n:,} powiązań  "
        f"(polilinia: {len(poly_to_uidos):,}, tekst: {len(text_to_uidos):,})  "
        f"({time.time()-t0:.1f}s)"
    )
    return poly_to_uidos, text_to_uidos


def load_geometry_index(
    con: fb.Connection, cell_size: float = DEFAULT_GRID_CELL_SIZE
) -> GeometryIndex:
    """Buduje indeks siatkowy z P0/P1/PN polilinii i POS tekstów."""
    idx = GeometryIndex(cell_size=cell_size)

    print("  EW_POLYLINE...")
    t0 = time.time()
    n = 0
    with con.cursor() as cur:
        # ID, bo to po nim EW_OB_ELEMENTY.IDE referuje dla TYP=0.
        # P0/P1/PN: pierwszy, drugi i ostatni wierzchołek polilinii.
        cur.execute("SELECT ID, P0_X, P0_Y, P1_X, P1_Y, PN_X, PN_Y FROM EW_POLYLINE")
        for poly_id, p0x, p0y, p1x, p1y, pnx, pny in cur:
            if poly_id is None:
                continue
            if p0x is not None and p0y is not None:
                idx.add(0, poly_id, p0x, p0y)
            if p1x is not None and p1y is not None:
                idx.add(0, poly_id, p1x, p1y)
            if pnx is not None and pny is not None:
                idx.add(0, poly_id, pnx, pny)
            n += 1
            _progress("EW_POLYLINE", n)
    print(f"    {n:,} polilinii, {idx.size:,} punktów  ({time.time()-t0:.1f}s)")

    print("  EW_TEXT...")
    t0 = time.time()
    before = idx.size
    n = 0
    with con.cursor() as cur:
        cur.execute("SELECT UID, POS_X, POS_Y FROM EW_TEXT")
        for text_uid, px, py in cur:
            if text_uid is None or px is None or py is None:
                continue
            idx.add(1, text_uid, px, py)
            n += 1
            _progress("EW_TEXT", n)
    print(f"    {n:,} tekstów, {idx.size - before:,} punktów  ({time.time()-t0:.1f}s)")
    print(f"  Indeks: {idx.size:,} punktów w {len(idx.cells):,} komórkach")
    return idx


# =========================== PARSOWANIE PLIKU ============================

@dataclass(frozen=True)
class FileEntry:
    line_no: int
    x: float
    y: float
    operat_typ: int
    operat_numer: str


def parse_text_file(
    path: Path, encoding: str = DEFAULT_FILE_ENCODING
) -> tuple[list[FileEntry], list[tuple[int, str]]]:
    entries: list[FileEntry] = []
    errors: list[tuple[int, str]] = []
    with path.open(encoding=encoding) as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                errors.append((line_no, f"za mało kolumn ({len(parts)})"))
                continue
            try:
                x = float(parts[1])
                y = float(parts[2])
            except ValueError:
                errors.append((line_no, "X/Y nie są liczbą"))
                continue
            col4 = parts[3]
            if "#" not in col4:
                errors.append((line_no, "brak '#' w kolumnie 4"))
                continue
            typ_str, _, numer = col4.partition("#")
            try:
                typ = int(typ_str)
            except ValueError:
                errors.append((line_no, f"typ '{typ_str}' nie jest liczbą"))
                continue
            if not numer:
                errors.append((line_no, "pusty numer operatu po '#'"))
                continue
            entries.append(FileEntry(line_no, x, y, typ, numer))
    return entries, errors


# =========================== AKTUALIZACJA W BAZIE ============================

def execute_updates(
    con: fb.Connection,
    updates: Iterable[tuple[int, int]],  # (obj_uid, target_operat_uid)
) -> int:
    """Wykonuje wszystkie UPDATE-y w jednej transakcji. Zwraca liczbę zmienionych wierszy.
    Safety: WHERE OPERAT = 0 — nie nadpisujemy wartości ustawionych w międzyczasie.
    """
    sql = "UPDATE EW_OBIEKTY SET OPERAT = ? WHERE UID = ? AND OPERAT = 0"
    changed = 0
    n = 0
    t0 = time.time()
    with con.cursor() as cur:
        for obj_uid, target_uid in updates:
            cur.execute(sql, (target_uid, obj_uid))
            changed += cur.rowcount
            n += 1
            if n % 10_000 == 0:
                print(f"    ... wykonano {n:,} UPDATE-ów")
    con.commit()
    print(f"    razem: {n:,} prób, {changed:,} zmienionych wierszy  ({time.time()-t0:.1f}s)")
    return changed


# =========================== GŁÓWNA LOGIKA ============================

@dataclass(frozen=True)
class Mismatch:
    file_line: int
    file_x: float
    file_y: float
    file_operat_label: str
    obj_uid: int
    obj_numer: str
    obj_kod: str
    obj_idkatalog: int
    current_operat_uid: int
    current_operat_label: str


def run_update(
    db_path: str | Path = DEFAULT_DB_PATH,
    file_path: str | Path = DEFAULT_FILE_PATH,
    tolerance: float = DEFAULT_TOLERANCE,
    encoding: str = DEFAULT_FILE_ENCODING,
    cell_size: float = DEFAULT_GRID_CELL_SIZE,
    dry_run: bool = DRY_RUN,
) -> None:
    file_path_resolved = resolve_path(file_path)
    if not file_path_resolved.exists():
        raise FileNotFoundError(f"Plik tekstowy nie istnieje: {file_path_resolved}")

    print("=" * 70)
    print("KONFIGURACJA")
    print("=" * 70)
    print(f"  Baza:         {resolve_path(db_path)}")
    print(f"  Plik:         {file_path_resolved}")
    print(f"  Tolerancja:   {tolerance} m")
    print(f"  Cell size:    {cell_size} m")
    print(f"  Encoding:     {encoding}")
    print(f"  DRY_RUN:      {dry_run}"
          + ("  ⚠ BAZA ZOSTANIE ZMODYFIKOWANA!" if not dry_run else ""))

    # === Parsing pliku ===
    print(f"\n[1/3] Parsuję plik...")
    t_parse = time.time()
    entries, parse_errors = parse_text_file(file_path_resolved, encoding=encoding)
    print(f"  poprawnych wpisów:  {len(entries):,}")
    print(f"  z błędem składni:   {len(parse_errors):,}")
    print(f"  ({time.time()-t_parse:.1f}s)")
    if not entries:
        print("Brak wpisów do przetworzenia, kończę.")
        return

    # === Ładowanie tabel ===
    print(f"\n[2/3] Wczytuję tabele do pamięci...")
    t_load = time.time()
    with connect(db_path) as con:
        operaty_by_key, operaty_label = load_operaty(con)
        objects = load_objects(con)
        poly_to_uidos, text_to_uidos = load_object_junctions(con)
        geom_idx = load_geometry_index(con, cell_size=cell_size)
    print(f"  Łącznie ładowanie: {time.time()-t_load:.1f}s")

    # === Klasyfikacja wpisów ===
    print(f"\n[3/3] Klasyfikuję wpisy pliku...")
    t_proc = time.time()
    no_operat: list[FileEntry] = []
    no_geometry: list[FileEntry] = []
    no_object_link: list[FileEntry] = []
    matched_objects_total = 0
    would_update: list[tuple[FileEntry, ObjectInfo, int]] = []
    already_correct = 0
    mismatches: list[Mismatch] = []

    for i, entry in enumerate(entries, 1):
        if i % 50_000 == 0:
            print(f"  ... przetworzono {i:,}/{len(entries):,}")

        target_uid = operaty_by_key.get((entry.operat_typ, entry.operat_numer))
        if target_uid is None:
            no_operat.append(entry)
            continue

        hits = geom_idx.query(entry.x, entry.y, tolerance)
        if not hits:
            no_geometry.append(entry)
            continue

        obj_uids: set[int] = set()
        for geom_type, geom_key in hits:
            if geom_type == 0:
                obj_uids.update(poly_to_uidos.get(geom_key, ()))
            else:
                obj_uids.update(text_to_uidos.get(geom_key, ()))

        if not obj_uids:
            # geometria znaleziona, ale nie linkuje do żadnego obiektu w EW_OB_ELEMENTY
            no_object_link.append(entry)
            continue

        matched_objects_total += len(obj_uids)
        for obj_uid in obj_uids:
            info = objects.get(obj_uid)
            if info is None:
                continue
            if info.operat == 0:
                would_update.append((entry, info, target_uid))
            elif info.operat == target_uid:
                already_correct += 1
            else:
                current_label = operaty_label.get(info.operat, f"<UID={info.operat}>")
                mismatches.append(Mismatch(
                    file_line=entry.line_no,
                    file_x=entry.x, file_y=entry.y,
                    file_operat_label=f"{entry.operat_typ}#{entry.operat_numer}",
                    obj_uid=info.uid, obj_numer=info.numer,
                    obj_kod=info.kod, obj_idkatalog=info.idkatalog,
                    current_operat_uid=info.operat,
                    current_operat_label=current_label,
                ))
    print(f"  klasyfikacja: {time.time()-t_proc:.1f}s")

    # === Faza UPDATE (jeśli nie dry-run) ===
    if not dry_run and would_update:
        print(f"\n⚠ Wykonuję UPDATE-y ({len(would_update):,} prób)...")
        with connect(db_path) as con:
            execute_updates(con, ((info.uid, target_uid) for _, info, target_uid in would_update))

    # === Raport ===
    print("\n" + "=" * 70)
    print("RAPORT" + (" (DRY RUN — bez zmian w bazie)" if dry_run else ""))
    print("=" * 70)
    print(f"  Wpisów z błędem parsowania:              {len(parse_errors):,}")
    print(f"  Wpisów z nieznanym operatem:             {len(no_operat):,}")
    print(f"  Wpisów bez geometrii w tolerancji:       {len(no_geometry):,}")
    print(f"  Wpisów z geometrią bez linku do obiektu: {len(no_object_link):,}")
    print(f"  Obiekty znalezione łącznie:              {matched_objects_total:,}")
    action = "do aktualizacji (DRY RUN)" if dry_run else "ZAKTUALIZOWANE"
    print(f"    z OPERAT=0 → {action:<27}      {len(would_update):,}")
    print(f"    z OPERAT = target (już prawidłowe):    {already_correct:,}")
    print(f"    z OPERAT = inny operat (MISMATCH):     {len(mismatches):,}")

    _print_truncated(
        "Błędy parsowania",
        parse_errors,
        lambda x: f"linia {x[0]}: {x[1]}",
    )
    _print_truncated(
        "Nieznane operaty (brak w EW_OPERATY)",
        no_operat,
        lambda e: f"linia {e.line_no}: {e.operat_typ}#{e.operat_numer}",
    )
    _print_truncated(
        "Brak geometrii w tolerancji",
        no_geometry,
        lambda e: f"linia {e.line_no}: ({e.x}, {e.y}) operat {e.operat_typ}#{e.operat_numer}",
    )
    _print_truncated(
        "Geometria znaleziona, ale bez linku do obiektu",
        no_object_link,
        lambda e: f"linia {e.line_no}: ({e.x}, {e.y}) operat {e.operat_typ}#{e.operat_numer}",
    )
    _print_truncated(
        "⚠ MISMATCH-e (operat w bazie ≠ operat w pliku)",
        mismatches,
        lambda m: (
            f"linia {m.file_line}: ({m.file_x}, {m.file_y}) "
            f"plik='{m.file_operat_label}'\n"
            f"      obiekt UID={m.obj_uid} NUMER={m.obj_numer!r} "
            f"KOD={m.obj_kod} IDKAT={m.obj_idkatalog}\n"
            f"      ma OPERAT={m.current_operat_uid} ({m.current_operat_label})"
        ),
    )


def _print_truncated(title, lst, formatter):
    if not lst:
        return
    print(f"\n  {title} (pokazuję pierwsze {MAX_DETAIL_LINES} z {len(lst):,}):")
    for item in lst[:MAX_DETAIL_LINES]:
        print(f"    {formatter(item)}")
    if len(lst) > MAX_DETAIL_LINES:
        print(f"    ... i jeszcze {len(lst) - MAX_DETAIL_LINES:,}")


if __name__ == "__main__":
    run_update()
