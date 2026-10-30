import tempfile
import unittest
from pathlib import Path

from server import CanvasApp


class JobHistoryCheck(unittest.TestCase):
    def test_completed_history_survives_service_restart_without_requeue(self):
        with tempfile.TemporaryDirectory() as folder:
            app = CanvasApp(root=Path(folder))
            app.jobs = {'done': {'status': 'completed', 'prompt_id': 'existing', 'outputs': []},
                        'busy': {'status': 'running', 'prompt_id': 'still-running'}}
            app.store_terminal_jobs()
            restored = CanvasApp(root=Path(folder))
            self.assertEqual(set(restored.jobs), {'done'})
            self.assertEqual(restored.jobs['done']['prompt_id'], 'existing')


if __name__ == '__main__':
    unittest.main()
