import json
import tempfile
import threading
import unittest
from pathlib import Path

from construction_safety.source_settings import SourceSettings


class StreamAliasTests(unittest.TestCase):
    def test_alias_persists_without_changing_routes_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'configs').mkdir()
            config = root / 'configs/dashboard.json'
            original = {'routes': {'src1': {'source': 1, 'channel': 0}}, 'runtime': {'backend': 'neat-device'}}
            config.write_text(json.dumps(original))
            settings = SourceSettings(root, ['src1'], threading.Lock())
            settings.submit({'action': 'alias', 'camera': 'src1', 'alias': ' North site '})
            self.assertEqual(settings.aliases(), {'src1': 'North site'})
            self.assertEqual(json.loads(config.read_text())['routes'], original['routes'])
            self.assertEqual(settings.job, {'status': 'idle'})
            settings.submit({'action': 'alias', 'camera': 'src1', 'alias': ''})
            self.assertEqual(settings.aliases(), {})
            for alias in (None, 'x' * 65, 'invalid\nname'):
                with self.assertRaises(ValueError):
                    settings.save_alias({'camera': 'src1', 'alias': alias})
            settings.lock.acquire()
            try:
                with self.assertRaises(ValueError):
                    settings.save_alias({'camera': 'src1', 'alias': 'Busy'})
            finally:
                settings.lock.release()
            self.assertEqual(settings.aliases(), {})
