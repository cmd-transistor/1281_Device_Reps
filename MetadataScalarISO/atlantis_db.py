"""Pull ISO buildsheet rows from the Atlantis (Oracle) database instead of ``ISO_Metadata_Input.csv``.

Atlantis stores each structure parameter as one ``TESTSTRUCTUREPARAMETERS`` row
(``PARAMETERNAME`` / ``PARAMETERVALUE``); this module pivots them into one
buildsheet row per test structure, keyed like the CSV columns:

* ``TestRow``            <- ``TESTROW.TESTROWNAME``
* ``StructureName``      <- ``TESTSTRUCTURE.TESTSTRUCTNAME``
* ``StructureComments``  <- ``TESTSTRUCTURE.TESTSTRUCTCOMMENT``
* every other column     <- ``PARAMETERVALUE`` of the parameter with that name (case-insensitive)

Connection settings come from the environment (never from source):
``ATLANTIS_DSN`` (``host:port/service``), ``ATLANTIS_USER`` and ``ATLANTIS_PASSWORD``.
Needs ``python-oracledb`` (``pip install oracledb``; thin mode, no Oracle client required).

CLI::

    python atlantis_db.py x81zs_ctg01 x81zs_ctg02 --out ISO_Metadata_Atlantis.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

from ScalarCalculation import DEFAULT_INPUT

ENV_DSN, ENV_USER, ENV_PASSWORD = "ATLANTIS_DSN", "ATLANTIS_USER", "ATLANTIS_PASSWORD"
# Oracle limits an IN list to 1000 binds.
MAX_BINDS = 1000
_TESTROW_RE = re.compile(r"^[A-Za-z0-9_.$%-]+$")
_EZCONNECT_RE = re.compile(r"^[\w.-]+(?::\d+)?/[\w.$-]+$")
# Atlantis has no node column; the Cadence database (e.g. per process node) groups libraries.
_NODES_QUERY = ("SELECT DISTINCT tr.CADENCEDATABASE FROM TESTROW tr "
                "WHERE tr.LATESTFLAG = '1' AND tr.CADENCEDATABASE IS NOT NULL ORDER BY 1")
_LIBRARIES_QUERY = ("SELECT DISTINCT tr.CADENCELIBRARY FROM TESTROW tr WHERE tr.LATESTFLAG = '1' "
                    "AND tr.CADENCEDATABASE = :node AND tr.CADENCELIBRARY IS NOT NULL ORDER BY 1")
_TEST_ROWS_QUERY = ("SELECT DISTINCT tr.TESTROWNAME FROM TESTROW tr WHERE tr.LATESTFLAG = '1' "
                    "AND tr.CADENCEDATABASE = :node AND tr.CADENCELIBRARY = :library ORDER BY 1")

_QUERY = """
SELECT tr.TESTROWNAME, ts.TESTSTRUCTNAME, ts.TESTSTRUCTCOMMENT, ts.TESTSTRUCTID,
       p.PARAMETERNAME, p.PARAMETERVALUE
FROM TESTROW tr
JOIN TESTSTRUCTURE ts ON ts.TESTROWID = tr.TESTROWID
JOIN STRUCTURETEMPLATE st ON st.TEMPLATEID = ts.TSTEMPLATEID
LEFT JOIN TESTSTRUCTUREPARAMETERS p ON p.TESTSTRUCTID = ts.TESTSTRUCTID
WHERE tr.LATESTFLAG = '1'
  AND st.TEMPLATENAME NOT LIKE '%GNAC%'
  AND {match}
ORDER BY tr.TESTROWNAME, ts.TSSEQUENCE, ts.TESTSTRUCTID, p.SEQUENCE
"""


def csv_fields(path: Path = DEFAULT_INPUT) -> list[str]:
    """Buildsheet column order from the CSV header (empty if the CSV is not available)."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return [field for field in next(csv.reader(fh), []) if field]
    except OSError:
        return []


def connect(dsn: str | None = None, user: str | None = None, password: str | None = None):
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("Atlantis access needs python-oracledb: pip install oracledb") from exc
    dsn, user = dsn or os.environ.get(ENV_DSN), user or os.environ.get(ENV_USER)
    password = password or os.environ.get(ENV_PASSWORD)
    missing = [name for name, value in ((ENV_DSN, dsn), (ENV_USER, user), (ENV_PASSWORD, password)) if not value]
    if missing:
        raise RuntimeError(f"Atlantis connection not configured; set {', '.join(missing)}")
    dsn, user = dsn.strip(), user.strip()
    hint = "Use the Oracle connect string host:port/SERVICE_NAME (port is usually 1521) from the Atlantis DBA."
    if re.search(r"^https?://|/BOE\b|/biprws\b|(^|:)8443\b", dsn, re.IGNORECASE):
        raise RuntimeError(f"'{dsn}' is the SAP BI Launch Pad web server (HTTPS, port 8443), not the Atlantis "
                           f"Oracle database. {hint}")
    if not (_EZCONNECT_RE.match(dsn) or dsn.startswith("(") or os.environ.get("TNS_ADMIN")):
        raise RuntimeError(f"'{dsn}' is not an Oracle connect string. {hint}")
    if "@" in user:
        raise RuntimeError(f"'{user}' is an email address; Atlantis needs an Oracle database account name.")
    try:
        return oracledb.connect(user=user, password=password, dsn=dsn)
    except oracledb.Error as exc:
        raise RuntimeError(f"Atlantis login failed for {user}@{dsn}: {exc}") from exc


def _list(query: str, binds: dict[str, str], connection, connect_args) -> list[str]:
    own = connection is None
    connection = connection or connect(**connect_args)
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, binds)
            return [str(row[0]) for row in cursor if row[0]]
    finally:
        if own:
            connection.close()


def list_nodes(connection=None, **connect_args) -> list[str]:
    return _list(_NODES_QUERY, {}, connection, connect_args)


def list_libraries(node: str, connection=None, **connect_args) -> list[str]:
    return _list(_LIBRARIES_QUERY, {"node": node}, connection, connect_args)


def list_test_rows(node: str, library: str, connection=None, **connect_args) -> list[str]:
    return _list(_TEST_ROWS_QUERY, {"node": node, "library": library}, connection, connect_args)


def fetch_rows(test_rows: list[str] | None = None, fields: list[str] | None = None, connection=None,
               node: str | None = None, library: str | None = None,
               **connect_args) -> tuple[list[str], list[dict[str, str]]]:
    """Buildsheet ``(fields, rows)`` for test-row names (``%`` wildcards allowed) and/or a node + library."""
    names = [name.strip() for name in test_rows or [] if name.strip()]
    if not names and not (node and library):
        raise ValueError("Give Atlantis test-row names or a node and library")
    bad = [name for name in names if not _TESTROW_RE.match(name)]
    if bad:
        raise ValueError(f"Invalid test-row name(s): {', '.join(bad)}")
    if len(names) > MAX_BINDS:
        raise ValueError(f"At most {MAX_BINDS} test-row names per query")
    fields = list(fields if fields is not None else csv_fields())
    by_lower = {field.lower(): field for field in fields}
    exact = [name for name in names if "%" not in name]
    wildcards = [name for name in names if "%" in name]
    binds = {f"t{i}": name for i, name in enumerate(exact)} | {f"w{i}": name for i, name in enumerate(wildcards)}
    conditions = ([f"tr.TESTROWNAME IN ({', '.join(f':t{i}' for i in range(len(exact)))})"] if exact else [])
    conditions += [f"tr.TESTROWNAME LIKE :w{i}" for i in range(len(wildcards))]
    clauses = ["(" + " OR ".join(conditions) + ")"] if conditions else []
    if node and library:
        clauses.append("tr.CADENCEDATABASE = :node AND tr.CADENCELIBRARY = :library")
        binds |= {"node": node, "library": library}
    query = _QUERY.replace("{match}", " AND ".join(clauses))

    own = connection is None
    connection = connection or connect(**connect_args)
    rows: dict[object, dict[str, str]] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, binds)
            for testrow, struct, comment, struct_id, param, value in cursor:
                row = rows.setdefault(struct_id, {"TestRow": testrow or "", "StructureName": struct or "",
                                                  "StructureComments": comment or ""})
                if param:
                    column = by_lower.get(param.strip().lower(), param.strip())
                    row[column] = "" if value is None else str(value)
                    if column not in by_lower.values():
                        by_lower[column.lower()] = column
                        fields.append(column)
    finally:
        if own:
            connection.close()
    for key in ("TestRow", "StructureName", "StructureComments"):
        if key not in fields:
            fields.insert(0, key)
    return fields, [{field: row.get(field, "") for field in fields} for row in rows.values()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("test_rows", nargs="+", help="Atlantis TESTROWNAME values (%% wildcards allowed)")
    parser.add_argument("--out", type=Path, default=Path("ISO_Metadata_Atlantis.csv"))
    args = parser.parse_args()
    fields, rows = fetch_rows(args.test_rows)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} structures to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
