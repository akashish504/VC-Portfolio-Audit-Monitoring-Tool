"""
End-to-end API tests for org-chart reconciliation.

Seeds real DB records, fires HTTP requests against the running server,
asserts DB state, then cleans up.

Run:
    PYTHONPATH=. .venv/bin/python3 tests/e2e_reconciliation_test.py
"""
from __future__ import annotations

import json
import sys
import traceback
import uuid
from typing import Any

import requests
from sqlalchemy import text, create_engine
from sqlalchemy.orm import Session

BASE = "http://localhost:8001/api/v1"
DB_URL = (
    "postgresql+psycopg2://postgres:tailoredAI@"
    "peakxv-test-db.cxok8ouastuu.eu-north-1.rds.amazonaws.com:5432/"
    "peakxv_pr_tool?sslmode=require"
)
S = "portfolioauditreview"

engine = create_engine(DB_URL, connect_args={"connect_timeout": 10})

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_cleanup_ids: dict[str, list] = {
    "records": [], "batches": [], "files": [], "entities": [],
    "companies": [], "cycles": [],
}

results: list[tuple[str, bool, str]] = []


def ok(name: str, msg: str = ""):
    results.append((name, True, msg))
    print(f"  {PASS}  {name}" + (f"  — {msg}" if msg else ""))


def fail(name: str, msg: str):
    results.append((name, False, msg))
    print(f"  {FAIL}  {name}  — {msg}")


def assert_eq(name: str, got, expected):
    if got == expected:
        ok(name, f"{got!r}")
    else:
        fail(name, f"expected {expected!r}, got {got!r}")


def assert_true(name: str, value, detail: str = ""):
    if value:
        ok(name, detail)
    else:
        fail(name, detail or "expected truthy")


def db_fetch(sql: str, **params) -> list:
    with engine.connect() as c:
        return c.execute(text(sql), params).fetchall()


def db_one(sql: str, **params):
    rows = db_fetch(sql, **params)
    return rows[0] if rows else None


def cleanup():
    with engine.connect() as c:
        t = c.begin()
        try:
            for rid in _cleanup_ids["records"]:
                c.execute(text(f'DELETE FROM "{S}".org_chart_upload_records WHERE id=:id'), {"id": rid})
            for bid in _cleanup_ids["batches"]:
                c.execute(text(f'DELETE FROM "{S}".org_chart_upload_batches WHERE id=:id'), {"id": bid})
            for fid in _cleanup_ids["files"]:
                c.execute(text(f'DELETE FROM "{S}".files WHERE id=:id'), {"id": fid})
            for eid in _cleanup_ids["entities"]:
                # null parent first to avoid FK issues
                c.execute(text(f'UPDATE "{S}".entities SET parent_entity_id=NULL WHERE id=:id'), {"id": eid})
            for eid in _cleanup_ids["entities"]:
                c.execute(text(f'DELETE FROM "{S}".entities WHERE id=:id'), {"id": eid})
            for cid in _cleanup_ids["companies"]:
                c.execute(text(f'DELETE FROM "{S}".portfolio_companies WHERE id=:id'), {"id": cid})
            for rcid in _cleanup_ids["cycles"]:
                c.execute(text(f'DELETE FROM "{S}".review_cycles WHERE id=:id'), {"id": rcid})
            t.commit()
        except Exception as e:
            t.rollback()
            print(f"  [cleanup error] {e}")


# ── seed helpers ───────────────────────────────────────────────────────────────

def seed_cycle(conn, cycle_id: str) -> str:
    conn.execute(text(f"""
        INSERT INTO "{S}".review_cycles(id, name, status, meta, created_at, updated_at)
        VALUES(:id, :name, 'active', '{{}}', now(), now())
        ON CONFLICT(id) DO NOTHING
    """), {"id": cycle_id, "name": f"E2E Test {cycle_id}"})
    _cleanup_ids["cycles"].append(cycle_id)
    return cycle_id


def seed_company(conn, cycle_id: str, name: str) -> int:
    cid = f"e2e-{uuid.uuid4().hex[:10]}"
    row = conn.execute(text(f"""
        INSERT INTO "{S}".portfolio_companies(company_id, name, review_cycle_id, extra_data, created_at, updated_at)
        VALUES(:cid, :name, :rcid, '{{}}', now(), now())
        RETURNING id
    """), {"cid": cid, "name": name, "rcid": cycle_id}).fetchone()
    pc_id = row[0]
    _cleanup_ids["companies"].append(pc_id)
    return pc_id


def seed_entity(conn, pc_id: int, name: str, is_parent: bool = False, parent_id: int | None = None) -> int:
    row = conn.execute(text(f"""
        INSERT INTO "{S}".entities(portfolio_company_id, name, is_parent, parent_entity_id, extra_data, created_at, updated_at)
        VALUES(:pc, :name, :isp, :par, '{{}}', now(), now())
        RETURNING id
    """), {"pc": pc_id, "name": name, "isp": is_parent, "par": parent_id}).fetchone()
    eid = row[0]
    _cleanup_ids["entities"].append(eid)
    return eid


def seed_file(conn, pc_id: int, entity_id: int | None, cycle_id: str, filename: str = "test.pdf") -> int:
    row = conn.execute(text(f"""
        INSERT INTO "{S}".files(portfolio_company_id, entity_id, review_cycle_id,
            filename, status, tags, pending_audit_log, entity_detached_acknowledged, created_at, updated_at)
        VALUES(:pc, :eid, :rcid, :fn, 'uploaded', '[]', '[]', false, now(), now())
        RETURNING id
    """), {"pc": pc_id, "eid": entity_id, "rcid": cycle_id, "fn": filename}).fetchone()
    fid = row[0]
    _cleanup_ids["files"].append(fid)
    return fid


def seed_batch(conn, cycle_id: str) -> int:
    row = conn.execute(text(f"""
        INSERT INTO "{S}".org_chart_upload_batches(review_cycle_id, original_zip_filename, status, file_count, created_at, updated_at)
        VALUES(:rcid, 'test.zip', 'completed', 1, now(), now())
        RETURNING id
    """), {"rcid": cycle_id}).fetchone()
    bid = row[0]
    _cleanup_ids["batches"].append(bid)
    return bid


def seed_record(conn, batch_id: int, cycle_id: str, pc_id: int, file_id: int,
                extracted: list, status: str = "completed",
                recon_status: str | None = None) -> int:
    extracted_json = json.dumps(extracted)
    row = conn.execute(text(f"""
        INSERT INTO "{S}".org_chart_upload_records(
            batch_id, review_cycle_id, file_id, file_name, portfolio_company_id,
            extracted_org_chart, extraction_status, reconciliation_status,
            created_at, updated_at)
        VALUES(:bid, :rcid, :fid, 'chart.pdf', :pc,
               cast(:extracted as json), :status, :recon,
               now(), now())
        RETURNING id
    """), {
        "bid": batch_id, "rcid": cycle_id, "fid": file_id, "pc": pc_id,
        "extracted": extracted_json, "status": status, "recon": recon_status,
    }).fetchone()
    rid = row[0]
    _cleanup_ids["records"].append(rid)
    return rid


EXTRACTED_3 = [
    {"llm_id": 1, "name": "HoldCo", "geolocation": "US", "entity_type": "Holding", "is_parent": True, "children_ids": [2, 3]},
    {"llm_id": 2, "name": "SubA",   "geolocation": "US", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
    {"llm_id": 3, "name": "SubB",   "geolocation": "UK", "entity_type": "Subsidiary", "is_parent": False, "children_ids": []},
]


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1 — GET pending-update: no record → has_pending_update=false
# ══════════════════════════════════════════════════════════════════════════════

def test_no_pending_update():
    print("\n[1] pending-update: no batch record → has_pending_update=false")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "NoPendingCo")
        t.commit()

    r = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    assert_eq("status_code", r.status_code, 200)
    data = r.json()
    assert_eq("has_pending_update", data["has_pending_update"], False)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — GET pending-update: completed record, no existing org chart
#               → has_pending_update=true, requires_reconciliation=false
# ══════════════════════════════════════════════════════════════════════════════

def test_pending_update_no_existing_chart():
    print("\n[2] pending-update: completed record, no entities → requires_reconciliation=false")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "FreshCo")
        fid = seed_file(c, pc_id, None, cycle_id, "chart.pdf")
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3)
        t.commit()

    r = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    assert_eq("status_code", r.status_code, 200)
    d = r.json()
    assert_eq("has_pending_update", d["has_pending_update"], True)
    assert_eq("requires_reconciliation", d["requires_reconciliation"], False)
    assert_eq("record_id", d["record_id"], rid)
    assert_eq("extracted_count", len(d["extracted_org_chart"]), 3)
    assert_eq("existing_entities_empty", len(d["existing_entities"]), 0)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — POST apply: no existing chart → entities created, status=applied
# ══════════════════════════════════════════════════════════════════════════════

def test_apply_direct():
    print("\n[3] apply: direct apply, no existing org chart")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "ApplyCo")
        fid = seed_file(c, pc_id, None, cycle_id, "chart.pdf")
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3)
        t.commit()

    r = requests.post(f"{BASE}/org-chart-records/{rid}/apply", json={"applied_by": "e2e-test"})
    assert_eq("status_code", r.status_code, 200)
    d = r.json()
    assert_eq("applied", d["applied"], True)
    assert_eq("entity_count", d["entity_count"], 3)

    # Verify DB state
    entities = db_fetch(
        f'SELECT id, name, is_parent, parent_entity_id FROM "{S}".entities WHERE portfolio_company_id=:pc ORDER BY id',
        pc=pc_id,
    )
    for eid in [row[0] for row in entities]:
        _cleanup_ids["entities"].append(eid)

    assert_eq("db_entity_count", len(entities), 3)
    roots = [e for e in entities if e[2]]  # is_parent=True
    assert_eq("root_count", len(roots), 1)
    assert_eq("root_name", roots[0][1], "HoldCo")

    # Check hierarchy: both SubA and SubB should have parent = HoldCo
    root_id = roots[0][0]
    children = [e for e in entities if e[3] == root_id]
    assert_eq("child_count_under_root", len(children), 2)

    # record marked applied
    rec = db_one(
        f'SELECT reconciliation_status, applied_at, applied_by, applied_entity_ids FROM "{S}".org_chart_upload_records WHERE id=:id',
        id=rid,
    )
    assert_eq("rec_status", rec[0], "applied")
    assert_true("applied_at_set", rec[1] is not None)
    assert_eq("applied_by", rec[2], "e2e-test")
    assert_eq("applied_entity_ids_count", len(rec[3]), 3)

    # company org_chart_file_id set
    pc = db_one(f'SELECT org_chart_file_id FROM "{S}".portfolio_companies WHERE id=:id', id=pc_id)
    assert_eq("org_chart_file_id", pc[0], fid)

    # file now attached to company
    f_row = db_one(f'SELECT portfolio_company_id FROM "{S}".files WHERE id=:id', id=fid)
    assert_eq("file_company_id", f_row[0], pc_id)

    # pending-update now returns false
    r2 = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    assert_eq("no_pending_after_apply", r2.json()["has_pending_update"], False)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 4 — GET pending-update: existing entities → requires_reconciliation=true
# ══════════════════════════════════════════════════════════════════════════════

def test_pending_update_existing_chart():
    print("\n[4] pending-update: company has existing entities → requires_reconciliation=true")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "ExistingCo")
        eid1 = seed_entity(c, pc_id, "OldRoot", is_parent=True)
        eid2 = seed_entity(c, pc_id, "OldChild", parent_id=eid1)
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3)
        t.commit()

    r = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    d = r.json()
    assert_eq("status_code", r.status_code, 200)
    assert_eq("has_pending_update", d["has_pending_update"], True)
    assert_eq("requires_reconciliation", d["requires_reconciliation"], True)
    assert_eq("existing_entities_count", len(d["existing_entities"]), 2)
    assert_eq("extracted_count", len(d["extracted_org_chart"]), 3)

    # file_count included for entities with files
    f2 = db_fetch(f"SELECT id FROM \"{S}\".files WHERE portfolio_company_id=:pc", pc=pc_id)
    # OldRoot has no file yet, OldChild has no file — counts should both be 0
    for ent in d["existing_entities"]:
        assert_true("file_count_field_present", "file_count" in ent)

    return pc_id, eid1, eid2, fid, bid, rid, cycle_id


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 5 — POST reconcile: match + create + archive + parent links + file move
# ══════════════════════════════════════════════════════════════════════════════

def test_reconcile_full():
    print("\n[5] reconcile: match OldRoot→HoldCo, archive OldChild, create SubA+SubB, move file")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "ReconcileCo")
        eid_root = seed_entity(c, pc_id, "OldRoot", is_parent=True)
        eid_stale = seed_entity(c, pc_id, "StaleChild", parent_id=eid_root)
        # File attached to stale entity
        fid_attached = seed_file(c, pc_id, eid_stale, cycle_id, "report.pdf")
        fid_batch = seed_file(c, pc_id, None, cycle_id, "chart.pdf")
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid_batch, EXTRACTED_3)
        t.commit()

    payload = {
        "portfolio_company_id": pc_id,
        "entity_mappings": [
            # OldRoot matched to HoldCo (llm_id=1)
            {
                "existing_entity_id": eid_root,
                "extracted_temp_id": 1,
                "action": "match",
                "final_name": "HoldCo",
                "final_geolocation": "US",
                "final_entity_type": "Holding",
                "final_is_parent": True,
            },
            # StaleChild archived
            {
                "existing_entity_id": eid_stale,
                "action": "archive",
            },
            # SubA created from extracted llm_id=2
            {
                "extracted_temp_id": 2,
                "action": "create",
                "final_name": "SubA",
                "final_geolocation": "US",
                "final_entity_type": "Subsidiary",
                "final_is_parent": False,
            },
            # SubB created from extracted llm_id=3
            {
                "extracted_temp_id": 3,
                "action": "create",
                "final_name": "SubB",
                "final_geolocation": "UK",
                "final_entity_type": "Subsidiary",
                "final_is_parent": False,
            },
        ],
        "parent_links": [
            # SubA under HoldCo (existing entity after match)
            {"child_ref": "extracted:2", "parent_ref": f"existing:{eid_root}"},
            # SubB under HoldCo
            {"child_ref": "extracted:3", "parent_ref": f"existing:{eid_root}"},
        ],
        "file_moves": [
            # Move attached file from stale entity to the matched HoldCo entity
            {"file_id": fid_attached, "target_entity_ref": f"existing:{eid_root}", "acknowledge_detached": False},
        ],
        "applied_by": "e2e-reconcile",
    }

    r = requests.post(f"{BASE}/org-chart-records/{rid}/reconcile", json=payload)
    assert_eq("status_code", r.status_code, 200)
    d = r.json()
    assert_eq("reconciled", d["reconciled"], True)

    # DB assertions
    entities = db_fetch(
        f'SELECT id, name, status, is_parent, parent_entity_id FROM "{S}".entities WHERE portfolio_company_id=:pc ORDER BY id',
        pc=pc_id,
    )
    for row in entities:
        if row[0] not in _cleanup_ids["entities"]:
            _cleanup_ids["entities"].append(row[0])

    by_name = {e[1]: e for e in entities}
    assert_true("HoldCo_exists", "HoldCo" in by_name)
    assert_true("SubA_exists", "SubA" in by_name)
    assert_true("SubB_exists", "SubB" in by_name)
    assert_true("StaleChild_exists", "StaleChild" in by_name)

    assert_eq("HoldCo_is_parent", by_name["HoldCo"][3], True)
    assert_eq("StaleChild_archived", by_name["StaleChild"][2], "Archive Entity")
    assert_eq("SubA_parent", by_name["SubA"][4], by_name["HoldCo"][0])
    assert_eq("SubB_parent", by_name["SubB"][4], by_name["HoldCo"][0])

    # File reassigned to HoldCo entity
    f_row = db_one(f'SELECT entity_id FROM "{S}".files WHERE id=:id', id=fid_attached)
    assert_eq("file_entity_reassigned", f_row[0], by_name["HoldCo"][0])

    # company org_chart_file_id = batch file
    pc_row = db_one(f'SELECT org_chart_file_id FROM "{S}".portfolio_companies WHERE id=:id', id=pc_id)
    assert_eq("org_chart_file_id", pc_row[0], fid_batch)

    # record reconciliation_status = applied
    rec = db_one(f'SELECT reconciliation_status, applied_by FROM "{S}".org_chart_upload_records WHERE id=:id', id=rid)
    assert_eq("rec_status", rec[0], "applied")
    assert_eq("rec_applied_by", rec[1], "e2e-reconcile")

    # no pending update after reconcile
    r2 = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    assert_eq("no_pending_after_reconcile", r2.json()["has_pending_update"], False)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 6 — POST reconcile: parent cycle → 422, no DB changes
# ══════════════════════════════════════════════════════════════════════════════

def test_reconcile_cycle_rejected():
    print("\n[6] reconcile: cycle in parent_links → 422, no DB changes")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "CycleCo")
        eid_a = seed_entity(c, pc_id, "EntityA", is_parent=True)
        eid_b = seed_entity(c, pc_id, "EntityB")
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid,
                         [{"llm_id": 1, "name": "X", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}])
        t.commit()

    # A→parent=B and B→parent=A = cycle
    payload = {
        "portfolio_company_id": pc_id,
        "entity_mappings": [
            {"existing_entity_id": eid_a, "action": "keep"},
            {"existing_entity_id": eid_b, "action": "keep"},
        ],
        "parent_links": [
            {"child_ref": f"existing:{eid_a}", "parent_ref": f"existing:{eid_b}"},
            {"child_ref": f"existing:{eid_b}", "parent_ref": f"existing:{eid_a}"},
        ],
        "file_moves": [],
    }

    r = requests.post(f"{BASE}/org-chart-records/{rid}/reconcile", json=payload)
    assert_eq("status_code_422", r.status_code, 422)

    # record must still be un-applied
    rec = db_one(f'SELECT reconciliation_status FROM "{S}".org_chart_upload_records WHERE id=:id', id=rid)
    assert_true("rec_not_applied", rec[0] not in ("applied",))

    # entities unchanged
    ea = db_one(f'SELECT parent_entity_id, status FROM "{S}".entities WHERE id=:id', id=eid_a)
    eb = db_one(f'SELECT parent_entity_id FROM "{S}".entities WHERE id=:id', id=eid_b)
    assert_true("entity_a_unchanged", ea[0] is None)
    assert_true("entity_b_unchanged", eb[0] is None)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 7 — POST reconcile: cross-company file → 422, no DB changes
# ══════════════════════════════════════════════════════════════════════════════

def test_reconcile_cross_company_file_rejected():
    print("\n[7] reconcile: file from another company in file_moves → 422")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc1_id = seed_company(c, cycle_id, "Company1")
        pc2_id = seed_company(c, cycle_id, "Company2")
        # File belongs to company 2
        fid_other = seed_file(c, pc2_id, None, cycle_id, "other.pdf")
        fid_batch = seed_file(c, pc1_id, None, cycle_id, "chart.pdf")
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc1_id, fid_batch,
                         [{"llm_id": 1, "name": "X", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}])
        t.commit()

    payload = {
        "portfolio_company_id": pc1_id,
        "entity_mappings": [{"extracted_temp_id": 1, "action": "create", "final_name": "X", "final_is_parent": True}],
        "parent_links": [],
        "file_moves": [{"file_id": fid_other, "target_entity_ref": None, "acknowledge_detached": True}],
    }

    r = requests.post(f"{BASE}/org-chart-records/{rid}/reconcile", json=payload)
    assert_eq("status_code_422", r.status_code, 422)

    # No new entities created for pc1
    ents = db_fetch(f'SELECT id FROM "{S}".entities WHERE portfolio_company_id=:pc', pc=pc1_id)
    assert_eq("no_entities_created", len(ents), 0)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 8 — POST reconcile: wrong company for record → 422
# ══════════════════════════════════════════════════════════════════════════════

def test_reconcile_wrong_company():
    print("\n[8] reconcile: record belongs to company A, request says company B → 422")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc1_id = seed_company(c, cycle_id, "PCOne")
        pc2_id = seed_company(c, cycle_id, "PCTwo")
        fid = seed_file(c, pc1_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc1_id, fid,
                         [{"llm_id": 1, "name": "X", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}])
        t.commit()

    payload = {
        "portfolio_company_id": pc2_id,  # wrong company
        "entity_mappings": [],
        "parent_links": [],
        "file_moves": [],
    }
    r = requests.post(f"{BASE}/org-chart-records/{rid}/reconcile", json=payload)
    assert_eq("status_code_422", r.status_code, 422)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 9 — POST apply: already applied record → 422
# ══════════════════════════════════════════════════════════════════════════════

def test_apply_already_applied():
    print("\n[9] apply: already-applied record → 422")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "AlreadyAppliedCo")
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3,
                         recon_status="applied")
        t.commit()

    r = requests.post(f"{BASE}/org-chart-records/{rid}/apply", json={})
    assert_eq("status_code_422", r.status_code, 422)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 10 — POST dismiss → reconciliation_status=dismissed, no more dialog
# ══════════════════════════════════════════════════════════════════════════════

def test_dismiss():
    print("\n[10] dismiss: marks dismissed, pending-update returns false")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "DismissCo")
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3)
        t.commit()

    # confirm pending before dismiss
    r = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    assert_eq("pending_before_dismiss", r.json()["has_pending_update"], True)

    r2 = requests.post(f"{BASE}/org-chart-records/{rid}/dismiss", json={"applied_by": "e2e-dismiss"})
    assert_eq("status_code", r2.status_code, 200)
    assert_eq("dismissed", r2.json()["dismissed"], True)

    rec = db_one(f'SELECT reconciliation_status FROM "{S}".org_chart_upload_records WHERE id=:id', id=rid)
    assert_eq("rec_status_dismissed", rec[0], "dismissed")

    r3 = requests.get(f"{BASE}/portfolio-companies/{pc_id}/org-chart/pending-update")
    assert_eq("no_pending_after_dismiss", r3.json()["has_pending_update"], False)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 11 — In-review tracker: pending reconciliation badge flag
# ══════════════════════════════════════════════════════════════════════════════

def test_tracker_pending_flag():
    print("\n[11] in-review tracker: has_pending_org_chart_reconciliation=true for pending company")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "TrackerBadgeCo")
        # Set review_stage='In Review' so it appears in tracker
        c.execute(text(f'UPDATE "{S}".portfolio_companies SET review_stage=\'In Review\' WHERE id=:id'), {"id": pc_id})
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3)
        t.commit()

    r = requests.get(f"{BASE}/in-review-tracker", params={"review_cycle_id": cycle_id})
    assert_eq("status_code", r.status_code, 200)
    items = r.json().get("items", [])
    match = [x for x in items if x["portfolio_company_id"] == pc_id]
    assert_true("company_in_tracker", len(match) > 0, f"pc_id={pc_id}")
    if match:
        assert_eq("pending_flag_true", match[0].get("has_pending_org_chart_reconciliation"), True)

    # After dismiss, flag should be false
    requests.post(f"{BASE}/org-chart-records/{rid}/dismiss", json={})
    r2 = requests.get(f"{BASE}/in-review-tracker", params={"review_cycle_id": cycle_id})
    items2 = r2.json().get("items", [])
    match2 = [x for x in items2 if x["portfolio_company_id"] == pc_id]
    if match2:
        assert_eq("pending_flag_false_after_dismiss", match2[0].get("has_pending_org_chart_reconciliation"), False)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 12 — apply: extraction not completed → 422
# ══════════════════════════════════════════════════════════════════════════════

def test_apply_not_completed():
    print("\n[12] apply: extraction_status != completed → 422")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "NotCompletedCo")
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid, EXTRACTED_3, status="processing")
        t.commit()

    r = requests.post(f"{BASE}/org-chart-records/{rid}/apply", json={})
    assert_eq("status_code_422", r.status_code, 422)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 13 — reconcile: detach file (target_entity_ref=null, acknowledge=true)
# ══════════════════════════════════════════════════════════════════════════════

def test_reconcile_detach_file():
    print("\n[13] reconcile: file_move with null target → file detached, entity_detached_acknowledged=true")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "DetachCo")
        eid_old = seed_entity(c, pc_id, "OldEntity", is_parent=True)
        fid_attached = seed_file(c, pc_id, eid_old, cycle_id, "attached.pdf")
        fid_batch = seed_file(c, pc_id, None, cycle_id, "chart.pdf")
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid_batch,
                         [{"llm_id": 1, "name": "NewCo", "geolocation": None, "entity_type": "Holding", "is_parent": True, "children_ids": []}])
        t.commit()

    payload = {
        "portfolio_company_id": pc_id,
        "entity_mappings": [
            {"existing_entity_id": eid_old, "extracted_temp_id": 1, "action": "match",
             "final_name": "NewCo", "final_is_parent": True},
        ],
        "parent_links": [],
        "file_moves": [
            # Explicitly detach
            {"file_id": fid_attached, "target_entity_ref": None, "acknowledge_detached": True},
        ],
    }
    r = requests.post(f"{BASE}/org-chart-records/{rid}/reconcile", json=payload)
    assert_eq("status_code", r.status_code, 200)

    f_row = db_one(f'SELECT entity_id, entity_detached_acknowledged FROM "{S}".files WHERE id=:id', id=fid_attached)
    assert_eq("file_detached", f_row[0], None)
    assert_eq("ack_true", f_row[1], True)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO 14 — reconcile: record with reconciliation_status='pending' (explicit)
#               should be treated same as NULL
# ══════════════════════════════════════════════════════════════════════════════

def test_reconcile_explicit_pending_status():
    print("\n[14] reconcile: record with reconciliation_status='pending' → allowed")
    with engine.connect() as c:
        t = c.begin()
        cycle_id = f"E2E-{uuid.uuid4().hex[:8]}"
        seed_cycle(c, cycle_id)
        pc_id = seed_company(c, cycle_id, "ExplicitPendingCo")
        fid = seed_file(c, pc_id, None, cycle_id)
        bid = seed_batch(c, cycle_id)
        rid = seed_record(c, bid, cycle_id, pc_id, fid,
                         [{"llm_id": 1, "name": "Root", "geolocation": None, "entity_type": None, "is_parent": True, "children_ids": []}],
                         recon_status="pending")
        t.commit()

    payload = {
        "portfolio_company_id": pc_id,
        "entity_mappings": [{"extracted_temp_id": 1, "action": "create", "final_name": "Root", "final_is_parent": True}],
        "parent_links": [],
        "file_moves": [],
    }
    r = requests.post(f"{BASE}/org-chart-records/{rid}/reconcile", json=payload)
    assert_eq("status_code", r.status_code, 200)

    ents = db_fetch(f'SELECT id FROM "{S}".entities WHERE portfolio_company_id=:pc', pc=pc_id)
    for e in ents:
        _cleanup_ids["entities"].append(e[0])
    assert_eq("entity_created", len(ents), 1)


# ══════════════════════════════════════════════════════════════════════════════
# run all
# ══════════════════════════════════════════════════════════════════════════════

def run_all():
    tests = [
        test_no_pending_update,
        test_pending_update_no_existing_chart,
        test_apply_direct,
        test_pending_update_existing_chart,
        test_reconcile_full,
        test_reconcile_cycle_rejected,
        test_reconcile_cross_company_file_rejected,
        test_reconcile_wrong_company,
        test_apply_already_applied,
        test_dismiss,
        test_tracker_pending_flag,
        test_apply_not_completed,
        test_reconcile_detach_file,
        test_reconcile_explicit_pending_status,
    ]

    for fn in tests:
        try:
            fn()
        except Exception:
            fail(fn.__name__, traceback.format_exc())

    print("\n" + "═" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print(f"  {passed}/{total} assertions passed  |  {failed} failed")

    if failed:
        print("\nFailed assertions:")
        for name, ok_, msg in results:
            if not ok_:
                print(f"  ✗  {name}: {msg}")

    print("\nCleaning up test data...")
    cleanup()
    print("  Done.")

    return failed


if __name__ == "__main__":
    failed = run_all()
    sys.exit(1 if failed else 0)
