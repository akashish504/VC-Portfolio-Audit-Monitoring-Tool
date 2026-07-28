"""
Read-only sanity check for the review-cycle rollover.

Usage in deployment shell (env vars POSTGRES_* must be set):

    PYTHONPATH=. python scripts/verify_rollover_sanity.py
    PYTHONPATH=. python scripts/verify_rollover_sanity.py --source CY23-FY24 --target CY24-FY25
    PYTHONPATH=. python scripts/verify_rollover_sanity.py --json

Exits 0 if all checks pass, 1 otherwise. Performs NO writes.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import Entity, File, PortfolioCompany, ReviewCycle
from src.db.session import get_sync_db
from src.services.review_cycle_rollover import (
    PORTFOLIO_COMPANY_METADATA_FIELDS,
    ROLLOVER_ENTITY_STATUS,
    ist_today,
    resolve_rollover_cycle_ids,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("verify-rollover")


def _list_cycles(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(ReviewCycle).order_by(ReviewCycle.starts_at.nullslast())).scalars().all()
    out = []
    for c in rows:
        n_pc = db.execute(
            select(func.count()).select_from(PortfolioCompany)
            .where(PortfolioCompany.review_cycle_id == c.id)
        ).scalar_one()
        out.append({
            "id": c.id, "name": c.name, "status": c.status,
            "starts_at": str(c.starts_at), "ends_at": str(c.ends_at),
            "companies": n_pc, "meta": c.meta,
        })
    return out


def _company_diffs(db: Session, source_id: str, target_id: str) -> dict[str, Any]:
    src_pcs = db.execute(
        select(PortfolioCompany).where(PortfolioCompany.review_cycle_id == source_id)
    ).scalars().all()
    tgt_pcs = db.execute(
        select(PortfolioCompany).where(PortfolioCompany.review_cycle_id == target_id)
    ).scalars().all()
    tgt_by_cid = {p.company_id: p for p in tgt_pcs}

    missing_in_target: list[str] = []
    field_mismatches: list[dict[str, Any]] = []
    wrong_stage: list[dict[str, Any]] = []
    orphan_in_target: list[str] = []
    duplicate_target: list[dict[str, Any]] = []

    src_cids = {p.company_id for p in src_pcs}
    # duplicate detection
    seen: dict[str, int] = {}
    for p in tgt_pcs:
        seen[p.company_id] = seen.get(p.company_id, 0) + 1
    for cid, n in seen.items():
        if n > 1:
            duplicate_target.append({"company_id": cid, "count": n})

    for src in src_pcs:
        tgt = tgt_by_cid.get(src.company_id)
        if tgt is None:
            missing_in_target.append(src.company_id)
            continue
        # Audit state lives on entities now: every rolled-over entity should start at
        # ROLLOVER_ENTITY_STATUS ("Not applicable").
        tgt_entity_statuses = db.execute(
            select(Entity.status).where(Entity.portfolio_company_id == tgt.id)
        ).scalars().all()
        bad_statuses = [s for s in tgt_entity_statuses if s != ROLLOVER_ENTITY_STATUS]
        if bad_statuses:
            wrong_stage.append({
                "company_id": src.company_id,
                "entity_statuses": bad_statuses,
                "expected": ROLLOVER_ENTITY_STATUS,
            })
        for f in PORTFOLIO_COMPANY_METADATA_FIELDS:
            sv, tv = getattr(src, f), getattr(tgt, f)
            if sv != tv:
                field_mismatches.append({"company_id": src.company_id, "field": f,
                                         "source": sv, "target": tv})

    for p in tgt_pcs:
        if p.company_id not in src_cids:
            orphan_in_target.append(p.company_id)

    return {
        "source_companies": len(src_pcs),
        "target_companies": len(tgt_pcs),
        "missing_in_target": missing_in_target,
        "wrong_review_stage": wrong_stage,
        "field_mismatches": field_mismatches,
        "duplicate_target_company_ids": duplicate_target,
        "orphan_in_target": orphan_in_target,
    }


def _entity_check(db: Session, source_id: str, target_id: str) -> dict[str, Any]:
    src_count = db.execute(
        select(func.count()).select_from(Entity).where(Entity.review_cycle == source_id)
    ).scalar_one()
    tgt_count = db.execute(
        select(func.count()).select_from(Entity).where(Entity.review_cycle == target_id)
    ).scalar_one()

    # Workflow fields on target should be reset.
    workflow_leak = db.execute(
        select(func.count()).select_from(Entity)
        .where(Entity.review_cycle == target_id)
        .where(Entity.status.isnot(None))
    ).scalar_one()

    # Parent/child consistency: every parent_entity_id in target must point to an
    # entity that itself belongs to the target cycle.
    dangling_parents = db.execute(
        select(func.count()).select_from(Entity)
        .where(Entity.review_cycle == target_id)
        .where(Entity.parent_entity_id.isnot(None))
        .where(~Entity.parent_entity_id.in_(
            select(Entity.id).where(Entity.review_cycle == target_id)
        ))
    ).scalar_one()

    return {
        "source_entities": src_count,
        "target_entities": tgt_count,
        "target_entities_with_workflow_status": workflow_leak,
        "target_entities_with_dangling_parent": dangling_parents,
    }


def _org_chart_check(db: Session, source_id: str, target_id: str) -> dict[str, Any]:
    src_pcs = db.execute(
        select(PortfolioCompany)
        .where(PortfolioCompany.review_cycle_id == source_id)
        .where(PortfolioCompany.org_chart_file_id.isnot(None))
    ).scalars().all()

    missing: list[str] = []
    storage_mismatches: list[dict[str, Any]] = []
    shared_file_rows: list[dict[str, Any]] = []  # target should NOT reuse source File row

    for src in src_pcs:
        tgt = db.execute(
            select(PortfolioCompany).where(
                PortfolioCompany.company_id == src.company_id,
                PortfolioCompany.review_cycle_id == target_id,
            )
        ).scalar_one_or_none()
        if tgt is None or tgt.org_chart_file_id is None:
            missing.append(src.company_id)
            continue
        if tgt.org_chart_file_id == src.org_chart_file_id:
            shared_file_rows.append({"company_id": src.company_id,
                                     "file_id": tgt.org_chart_file_id})
            continue
        sf = db.get(File, src.org_chart_file_id)
        tf = db.get(File, tgt.org_chart_file_id)
        if sf is None or tf is None:
            missing.append(src.company_id)
            continue
        if sf.storage_uri != tf.storage_uri:
            storage_mismatches.append({"company_id": src.company_id,
                                       "source_uri": sf.storage_uri,
                                       "target_uri": tf.storage_uri})

    return {
        "source_companies_with_org_chart": len(src_pcs),
        "target_missing_org_chart": missing,
        "storage_uri_mismatches": storage_mismatches,
        "target_reuses_source_file_row": shared_file_rows,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="Override source cycle id (e.g. CY23-FY24)")
    ap.add_argument("--target", help="Override target cycle id (e.g. CY24-FY25)")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    args = ap.parse_args(argv)

    today = ist_today()
    derived_src, derived_tgt = resolve_rollover_cycle_ids(today)
    source_id = args.source or derived_src
    target_id = args.target or derived_tgt

    db = get_sync_db()
    try:
        cycles = _list_cycles(db)
        cycle_ids = {c["id"] for c in cycles}

        report: dict[str, Any] = {
            "ist_today": today.isoformat(),
            "derived_source_cycle_id": derived_src,
            "derived_target_cycle_id": derived_tgt,
            "checked_source_cycle_id": source_id,
            "checked_target_cycle_id": target_id,
            "source_cycle_exists": source_id in cycle_ids,
            "target_cycle_exists": target_id in cycle_ids,
            "all_cycles": cycles,
        }

        problems: list[str] = []
        if not report["source_cycle_exists"]:
            problems.append(f"source cycle {source_id} missing")
        if not report["target_cycle_exists"]:
            problems.append(f"target cycle {target_id} missing")

        if report["source_cycle_exists"] and report["target_cycle_exists"]:
            report["companies"] = _company_diffs(db, source_id, target_id)
            report["entities"] = _entity_check(db, source_id, target_id)
            report["org_chart"] = _org_chart_check(db, source_id, target_id)

            c = report["companies"]
            if c["missing_in_target"]:
                problems.append(f"{len(c['missing_in_target'])} source companies missing in target")
            if c["wrong_review_stage"]:
                problems.append(f"{len(c['wrong_review_stage'])} target companies with review_stage != 'Created'")
            if c["field_mismatches"]:
                problems.append(f"{len(c['field_mismatches'])} metadata field mismatches")
            if c["duplicate_target_company_ids"]:
                problems.append(f"{len(c['duplicate_target_company_ids'])} duplicate company_ids in target cycle")
            e = report["entities"]
            if e["target_entities_with_workflow_status"]:
                problems.append(f"{e['target_entities_with_workflow_status']} target entities still carry workflow status")
            if e["target_entities_with_dangling_parent"]:
                problems.append(f"{e['target_entities_with_dangling_parent']} target entities have dangling parent_entity_id")
            o = report["org_chart"]
            if o["target_missing_org_chart"]:
                problems.append(f"{len(o['target_missing_org_chart'])} target companies missing org chart")
            if o["storage_uri_mismatches"]:
                problems.append(f"{len(o['storage_uri_mismatches'])} org chart storage_uri mismatches")
            if o["target_reuses_source_file_row"]:
                problems.append(f"{len(o['target_reuses_source_file_row'])} target rows reuse source File row")

        report["problems"] = problems
        report["ok"] = not problems

        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            log.info("ist_today=%s", report["ist_today"])
            log.info("derived: source=%s target=%s", derived_src, derived_tgt)
            log.info("checking: source=%s (exists=%s)  target=%s (exists=%s)",
                     source_id, report["source_cycle_exists"],
                     target_id, report["target_cycle_exists"])
            log.info("review cycles in DB:")
            for c in cycles:
                log.info("  %s | companies=%d | status=%s | starts=%s",
                         c["id"], c["companies"], c["status"], c["starts_at"])
            if "companies" in report:
                log.info("companies: %s", report["companies"])
                log.info("entities:  %s", report["entities"])
                log.info("org_chart: %s", report["org_chart"])
            if problems:
                log.error("SANITY FAILED:")
                for p in problems:
                    log.error("  - %s", p)
            else:
                log.info("SANITY OK ✓")

        return 0 if report["ok"] else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
