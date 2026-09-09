import json
import tempfile
import unittest
from pathlib import Path
from paperbot.host import launch_args
from paperbot.state import Store


class HostTests(unittest.TestCase):
    def test_setup_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(launch_args('setup',d))
            self.assertEqual(list(Path(d).iterdir()),[])
    def test_missing_state_never_auto_initializes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(launch_args('observe',d))
            self.assertIsNone(launch_args('paper',d))
            self.assertFalse((Path(d)/'state.sqlite3').exists())
    def test_modes_preserve_explicit_order_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);store.save({'rules':'fixture'});store.close()
            self.assertNotIn('--enable-paper-orders',launch_args('observe',d))
            self.assertIn('--enable-paper-orders',launch_args('paper',d))
    def test_bad_mode_and_corrupt_state_fail(self):
        with self.assertRaises(ValueError):launch_args('live','unused')
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'state.sqlite3').write_text('corrupt')
            with self.assertRaises(ValueError):launch_args('paper',d)


if __name__=='__main__':unittest.main()
