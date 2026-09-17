from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from trucky_live import active_job


class ActiveJobTests(unittest.TestCase):
    def test_unfinished_job_has_status_without_fabricated_end_or_private_profile(self):
        result = active_job({'id': 1, 'status': 'in_progress', 'started_at': '2026-09-17T08:00:00+08:00',
                             'driver': {'name': 'Test', 'email': 'private@example.com'}, 'trailer_in_game_id': 'test.trailer'})
        self.assertEqual(result['status'], 'in_progress')
        self.assertEqual(result['start_time'], '2026-09-17T00:00:00Z')
        self.assertIsNone(result['stop_time'])
        self.assertNotIn('email', str(result))
        self.assertEqual(result['trailers'][0]['unique_id'], 'test.trailer')

    def test_completed_and_cancelled_jobs_are_not_live(self):
        for status in ['completed', 'canceled', None]:
            self.assertIsNone(active_job({'status': status}))
