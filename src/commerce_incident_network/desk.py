"""Single-merchant local operator desk with a human action trail.

This is a pilot workflow, not an identity provider or remote write executor.
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .core import DataError, _datetime, build_report


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, sku TEXT NOT NULL, offer_id TEXT NOT NULL, kind TEXT NOT NULL,
  severity TEXT NOT NULL, state TEXT NOT NULL, owner TEXT, approver TEXT,
  priority_proxy TEXT, detail TEXT NOT NULL, recommendation TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, action TEXT NOT NULL,
  actor TEXT NOT NULL, at TEXT NOT NULL, note TEXT NOT NULL,
  FOREIGN KEY(case_id) REFERENCES cases(id)
);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise DataError("desk database must not be readable or writable by group or others")
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


@contextmanager
def _db(path: Path):
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _event(db: sqlite3.Connection, case_id: str | None, action: str, actor: str, note: str) -> None:
    db.execute("INSERT INTO events(case_id,action,actor,at,note) VALUES(?,?,?,?,?)", (case_id, action, actor, _utc(), note))


def _actor(value: str, label: str) -> str:
    if not value or not value.strip() or len(value) > 100:
        raise DataError(f"{label} must be a nonempty label of at most 100 characters")
    return value.strip()


def sync(db_path: Path, inputs: Path, as_of: str, actor: str) -> dict:
    actor = _actor(actor, "actor")
    report = build_report(inputs, as_of)
    scope = json.dumps(report["scope"], sort_keys=True)
    stamp = report["snapshot_at"]
    active = {incident["id"]: incident for incident in report["incidents"]}
    with _db(db_path) as db:
        existing_scope = _meta(db, "scope")
        if existing_scope and existing_scope != scope:
            raise DataError("desk is bound to a different merchant/channel scope")
        existing_mapping = _meta(db, "mapping_sha256")
        current_mapping = report["source_sha256"]["mapping.csv"]
        if existing_mapping and existing_mapping != current_mapping:
            raise DataError("mapping changed; migrate the desk explicitly before syncing")
        last = _meta(db, "last_snapshot_at")
        if last and _datetime(last, "last snapshot") >= _datetime(stamp, "snapshot_at"):
            raise DataError("desk requires a newer complete snapshot")
        new = ongoing = reopened = resolved = 0
        for identifier, incident in active.items():
            previous = db.execute("SELECT state,detail,owner FROM cases WHERE id=?", (identifier,)).fetchone()
            if previous is None:
                db.execute("""INSERT INTO cases(id,sku,offer_id,kind,severity,state,priority_proxy,detail,recommendation,first_seen,last_seen)
                              VALUES(?,?,?,?,?,'open',?,?,?,?,?)""",
                           (identifier, incident["sku"], incident["offer_id"], incident["kind"], incident["severity"],
                            incident["daily_margin_priority_proxy_usd"], incident["detail"], incident["recommendation"], stamp, stamp))
                _event(db, identifier, "new", actor, "Issue present in complete snapshot")
                new += 1
            elif previous["state"] == "resolved_observed":
                db.execute("""UPDATE cases SET state='open', owner=NULL, approver=NULL, resolved_at=NULL,
                              severity=?, priority_proxy=?, detail=?, recommendation=?, last_seen=? WHERE id=?""",
                           (incident["severity"], incident["daily_margin_priority_proxy_usd"], incident["detail"], incident["recommendation"], stamp, identifier))
                _event(db, identifier, "reopened", actor, "Issue present again in complete snapshot")
                reopened += 1
            else:
                changed = previous["detail"] != incident["detail"]
                reset = changed and previous["state"] in {"approved_manual_fix", "awaiting_verification"}
                next_state = ("assigned" if previous["owner"] else "open") if reset else previous["state"]
                db.execute("""UPDATE cases SET state=?, approver=CASE WHEN ? THEN NULL ELSE approver END,
                              severity=?, priority_proxy=?, detail=?, recommendation=?, last_seen=? WHERE id=?""",
                           (next_state, reset, incident["severity"], incident["daily_margin_priority_proxy_usd"],
                            incident["detail"], incident["recommendation"], stamp, identifier))
                if reset:
                    _event(db, identifier, "approval_invalidated", actor, "Incident details changed in newer snapshot")
                ongoing += 1
        prior = db.execute("SELECT id FROM cases WHERE state!='resolved_observed'").fetchall()
        for row in prior:
            if row["id"] not in active:
                db.execute("UPDATE cases SET state='resolved_observed', resolved_at=? WHERE id=?", (stamp, row["id"]))
                _event(db, row["id"], "resolved_observed", actor, "Absent in newer complete snapshot; cause unverified")
                resolved += 1
        _set_meta(db, "scope", scope)
        _set_meta(db, "mapping_sha256", current_mapping)
        _set_meta(db, "last_snapshot_at", stamp)
        _event(db, None, "snapshot_sync", actor, json.dumps({"snapshot_at": stamp, "source_sha256": report["source_sha256"]}, sort_keys=True))
    return {"new": new, "ongoing": ongoing, "reopened": reopened, "resolved_observed": resolved, "snapshot_at": stamp}


def transition(db_path: Path, case_id: str, action: str, actor: str, note: str = "", owner: str = "") -> dict:
    actor = _actor(actor, "actor")
    if len(note) > 2000:
        raise DataError("note exceeds 2000 characters")
    with _db(db_path) as db:
        row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise DataError("case not found")
        state = row["state"]
        if action == "assign":
            owner = _actor(owner, "owner")
            if state not in {"open", "assigned"}:
                raise DataError("only open or assigned cases can be assigned")
            db.execute("UPDATE cases SET state='assigned', owner=?, approver=NULL WHERE id=?", (owner, case_id))
        elif action == "approve_manual_fix":
            if state != "assigned" or actor == row["owner"] or not note.strip():
                raise DataError("approval needs an assigned case, a different approver, and rationale")
            db.execute("UPDATE cases SET state='approved_manual_fix', approver=? WHERE id=?", (actor, case_id))
        elif action == "record_manual_fix":
            if state != "approved_manual_fix" or actor != row["owner"] or not note.strip():
                raise DataError("only the assigned owner can record an approved manual fix with a note")
            db.execute("UPDATE cases SET state='awaiting_verification' WHERE id=?", (case_id,))
        else:
            raise DataError("unsupported case action")
        _event(db, case_id, action, actor, note.strip())
        result = db.execute("SELECT id,sku,kind,state,owner,approver FROM cases WHERE id=?", (case_id,)).fetchone()
        return dict(result)


def view(db_path: Path) -> dict:
    with _db(db_path) as db:
        if _meta(db, "scope") is None:
            raise DataError("desk has no snapshot yet")
        cases = [dict(row) for row in db.execute("SELECT * FROM cases ORDER BY state='resolved_observed', severity!='high', COALESCE(CAST(priority_proxy AS REAL),-1) DESC, sku, kind")]
        events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 100")]
        return {"scope": json.loads(_meta(db, "scope") or "null"), "last_snapshot_at": _meta(db, "last_snapshot_at"),
                "cases": cases, "recent_events": events}


def render_desk(data: dict) -> str:
    escape = lambda value: html.escape(str(value if value is not None else "—"), quote=True)
    cells = ("state", "severity", "sku", "kind", "owner", "approver", "priority_proxy", "last_seen", "detail")
    rows = "".join("<tr>" + "".join(f"<td>{escape(case.get(key))}</td>" for key in cells) + "</tr>" for case in data["cases"])
    event_rows = "".join("<tr>" + "".join(f"<td>{escape(event.get(key))}</td>" for key in ("at", "action", "actor", "case_id", "note")) + "</tr>" for event in data["recent_events"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Commerce Command</title>
<style>:root{{color-scheme:dark}}body{{font:16px/1.5 system-ui;margin:0;background:#0c1724;color:#eaf1fa}}main{{max-width:1500px;margin:auto;padding:30px}}
h1{{margin-bottom:4px}}p{{color:#b8cede}}.panel{{background:#182c40;border:1px solid #486886;border-radius:12px;padding:18px;margin:18px 0}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #405d78;padding:9px;text-align:left;vertical-align:top}}th{{background:#2a4864;color:#fff}}
strong{{color:#fff}}</style></head><body><main><h1>Commerce Command</h1><p>Local operator desk · snapshot {escape(data['last_snapshot_at'])}</p>
<div class="panel"><strong>Scope</strong><p>{escape(data['scope'])}</p><p>Actions in this desk are human declarations. Platform writes are not performed by this app.</p></div>
<h2>Cases</h2><div class="scroll"><table><thead><tr>{''.join(f'<th>{escape(key.replace("_", " ").title())}</th>' for key in cells)}</tr></thead><tbody>{rows}</tbody></table></div>
<h2>Recent events</h2><div class="scroll"><table><thead><tr><th>At</th><th>Action</th><th>Actor</th><th>Case</th><th>Note</th></tr></thead><tbody>{event_rows}</tbody></table></div>
</main></body></html>"""
