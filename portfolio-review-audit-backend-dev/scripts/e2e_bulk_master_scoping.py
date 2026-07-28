"""
End-to-end test for the Master Scoping bulk-update feature.

Exercises the real HTTP route -> route handler -> service -> Postgres, then
verifies the database actually reflects the changes. Seeds rows under a unique
synthetic review cycle and deletes everything it created in a finally block, so
nothing persists.

Run:
    PYTHONPATH=. .venv/bin/python scripts/e2e_bulk_master_scoping.py
"""
from __future__ import annotations

import asyncio
import uuid

# Bypass JWT auth for the in-process ASGI client (sets a synthetic actor email).
from src.configs.env import settings
settings.DISABLE_AUTH = True  # type: ignore[attr-defined]

import httpx

from src.db.models import PortfolioCompanyMetadata, ReviewCycle
from src.db.session import AsyncSessionLocal
from src.main import app

BULK_URL = "/api/v1/master-scoping/bulk"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

_results: list[tuple[bool, str]] = []


def check(cond: bool, msg: str) -> None:
    _results.append((cond, msg))
    print(f"  [{PASS if cond else FAIL}] {msg}")


async def seed(cycle_id: str) -> list[int]:
    """Insert one review cycle and four metadata rows; return their ids."""
    async with AsyncSessionLocal() as db:
        db.add(ReviewCycle(id=cycle_id, name=f"E2E {cycle_id}", status="active"))
        tag = cycle_id[-8:]
        rows = [
            # r0, r1: plain rows for scoping + stage1 edits
            PortfolioCompanyMetadata(fund=f"E2E-{tag}", deal_id=f"D1-{tag}", strategy="S",
                                     deal_name="Row0", scoping_for_audit=False, review_cycle_id=cycle_id),
            PortfolioCompanyMetadata(fund=f"E2E-{tag}", deal_id=f"D2-{tag}", strategy="S",
                                     deal_name="Row1", scoping_for_audit=False, review_cycle_id=cycle_id),
            # r2: Stage 1 = completed -> Stage 2 edit ALLOWED
            PortfolioCompanyMetadata(fund=f"E2E-{tag}", deal_id=f"D3-{tag}", strategy="S",
                                     deal_name="Row2", deal_level_stage_1="Completed within due date",
                                     review_cycle_id=cycle_id),
            # r3: Stage 1 = overdue -> Stage 2 edit should be SKIPPED (locked)
            PortfolioCompanyMetadata(fund=f"E2E-{tag}", deal_id=f"D4-{tag}", strategy="S",
                                     deal_name="Row3", deal_level_stage_1="Overdue",
                                     review_cycle_id=cycle_id),
        ]
        db.add_all(rows)
        await db.commit()
        for r in rows:
            await db.refresh(r)
        return [r.id for r in rows]


async def fetch(ids: list[int]) -> dict[int, PortfolioCompanyMetadata]:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        recs = (await db.execute(
            select(PortfolioCompanyMetadata).where(PortfolioCompanyMetadata.id.in_(ids))
        )).scalars().all()
        return {r.id: r for r in recs}


async def cleanup(cycle_id: str, ids: list[int]) -> None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(PortfolioCompanyMetadata).where(PortfolioCompanyMetadata.id.in_(ids)))
        await db.execute(delete(ReviewCycle).where(ReviewCycle.id == cycle_id))
        await db.commit()


async def main() -> int:
    cycle_id = f"e2e-{uuid.uuid4().hex[:12]}"
    ids: list[int] = []
    transport = httpx.ASGITransport(app=app)
    try:
        ids = await seed(cycle_id)
        print(f"Seeded cycle={cycle_id} ids={ids}")
        r0, r1, r2, r3 = ids

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            # 1) Bulk set scoping_for_audit = Yes on r0, r1 -------------------
            print("\n[1] Bulk scoping_for_audit -> Yes (rows 0,1)")
            resp = await client.post(BULK_URL, json={"ids": [r0, r1], "field": "scoping_for_audit", "value": "Yes"})
            check(resp.status_code == 200, f"HTTP 200 (got {resp.status_code}: {resp.text[:200]})")
            body = resp.json() if resp.status_code == 200 else {}
            check(body.get("updated") == 2, f"updated == 2 (got {body.get('updated')})")
            db = await fetch(ids)
            check(db[r0].scoping_for_audit is True, "row0.scoping_for_audit persisted True")
            check(db[r1].scoping_for_audit is True, "row1.scoping_for_audit persisted True")

            # 2) Bulk set deal_level_stage_1 on r0, r1 -----------------------
            print("\n[2] Bulk deal_level_stage_1 -> 'Expected delay' (rows 0,1)")
            resp = await client.post(BULK_URL, json={"ids": [r0, r1], "field": "deal_level_stage_1", "value": "Expected delay"})
            check(resp.status_code == 200, f"HTTP 200 (got {resp.status_code})")
            body = resp.json() if resp.status_code == 200 else {}
            check(body.get("updated") == 2, f"updated == 2 (got {body.get('updated')})")
            db = await fetch(ids)
            check(db[r0].deal_level_stage_1 == "Expected delay", "row0.deal_level_stage_1 persisted")

            # 3) Bulk Stage 2 across r2 (allowed) + r3 (locked) -------------
            print("\n[3] Bulk deal_level_stage_2 -> 'Closed' (row2 allowed, row3 locked)")
            resp = await client.post(BULK_URL, json={"ids": [r2, r3], "field": "deal_level_stage_2", "value": "Closed"})
            check(resp.status_code == 200, f"HTTP 200 (got {resp.status_code})")
            body = resp.json() if resp.status_code == 200 else {}
            check(body.get("updated") == 1, f"updated == 1 (got {body.get('updated')})")
            check(body.get("skipped") == 1, f"skipped == 1 (locked row) (got {body.get('skipped')})")
            db = await fetch(ids)
            check(db[r2].deal_level_stage_2 == "Closed", "row2 (Stage1 completed) updated to Closed")
            check(db[r3].deal_level_stage_2 is None, "row3 (Stage1 overdue) left untouched")

            # 4) Validation: invalid field rejected -------------------------
            print("\n[4] Invalid field rejected")
            resp = await client.post(BULK_URL, json={"ids": [r0], "field": "auditor", "value": "X"})
            check(resp.status_code == 422, f"HTTP 422 for non-bulk field (got {resp.status_code})")

            # 5) Validation: invalid enum value rejected --------------------
            print("\n[5] Invalid enum value rejected")
            resp = await client.post(BULK_URL, json={"ids": [r0], "field": "deal_level_stage_1", "value": "Not a real stage"})
            check(resp.status_code == 422, f"HTTP 422 for bad enum value (got {resp.status_code})")

            # 6) Validation: empty ids rejected -----------------------------
            print("\n[6] Empty ids rejected")
            resp = await client.post(BULK_URL, json={"ids": [], "field": "scoping_for_audit", "value": "Yes"})
            check(resp.status_code == 422, f"HTTP 422 for empty ids (got {resp.status_code})")

            # 7) Clearing a value (empty string) ----------------------------
            print("\n[7] Bulk clear deal_level_stage_1 -> '' (row0)")
            resp = await client.post(BULK_URL, json={"ids": [r0], "field": "deal_level_stage_1", "value": ""})
            check(resp.status_code == 200, f"HTTP 200 (got {resp.status_code})")
            db = await fetch(ids)
            check(db[r0].deal_level_stage_1 is None, "row0.deal_level_stage_1 cleared to None")

            # 8) No-op when value already set (idempotency) -----------------
            print("\n[8] Re-apply scoping=Yes on row0 (already Yes) -> skipped")
            resp = await client.post(BULK_URL, json={"ids": [r0], "field": "scoping_for_audit", "value": "Yes"})
            body = resp.json() if resp.status_code == 200 else {}
            check(body.get("updated") == 0 and body.get("skipped") == 1,
                  f"no-op counted as skipped (updated={body.get('updated')}, skipped={body.get('skipped')})")
    finally:
        await transport.aclose()
        if ids:
            await cleanup(cycle_id, ids)
            print(f"\nCleaned up cycle={cycle_id}")

    passed = sum(1 for ok, _ in _results if ok)
    total = len(_results)
    print(f"\n==== {passed}/{total} checks passed ====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
