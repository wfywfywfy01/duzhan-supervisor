from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlmodel import SQLModel, create_engine

from app.models.scheduled_job_run import ScheduledJobRun  # noqa: F401
from app.scheduler.run_ledger import claim_run


class SchedulerRunLedgerTests(unittest.TestCase):
    def test_run_key_can_only_be_claimed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'runs.sqlite'}")
            SQLModel.metadata.create_all(engine)
            try:
                with patch("app.scheduler.run_ledger.get_engine", return_value=engine):
                    self.assertTrue(claim_run("daily_report", "2026-09-08"))
                    self.assertFalse(claim_run("daily_report", "2026-09-08"))
                    self.assertTrue(claim_run("daily_report", "2026-09-09"))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
