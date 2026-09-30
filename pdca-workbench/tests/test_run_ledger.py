# -*- coding: utf-8 -*-
"""app.scheduler.run_ledger tests: claim / finish / stale recovery."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.scheduled_job_run import ScheduledJobRun
from app.scheduler import run_ledger


class RunLedgerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(self.engine)
        self.patch = patch('app.scheduler.run_ledger.get_engine', return_value=self.engine)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.engine.dispose()

    def test_claim_then_finish_blocks_reclaim(self):
        self.assertTrue(run_ledger.claim_run('daily_report', '2026-09-11'))
        self.assertFalse(run_ledger.claim_run('daily_report', '2026-09-11'))
        run_ledger.finish_run('daily_report', '2026-09-11', 'sent')
        self.assertFalse(run_ledger.claim_run('daily_report', '2026-09-11'))
        with Session(self.engine) as s:
            row = s.exec(select(ScheduledJobRun)).first()
        self.assertEqual(row.status, 'sent')

    def test_stale_sending_is_reclaimed(self):
        self.assertTrue(run_ledger.claim_run('daily_report', '2026-09-11'))
        with Session(self.engine) as s:
            row = s.exec(select(ScheduledJobRun)).first()
            row.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            s.add(row)
            s.commit()
        # 超时 sending 复用同一行重新认领
        self.assertTrue(run_ledger.claim_run('daily_report', '2026-09-11'))
        with Session(self.engine) as s:
            rows = s.exec(select(ScheduledJobRun).order_by(ScheduledJobRun.id)).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, 'sending')
        self.assertIsNone(rows[0].finished_at)

    def test_fresh_sending_is_not_reclaimed(self):
        self.assertTrue(run_ledger.claim_run('daily_report', '2026-09-11'))
        self.assertFalse(run_ledger.claim_run('daily_report', '2026-09-11'))


if __name__ == "__main__":
    unittest.main()