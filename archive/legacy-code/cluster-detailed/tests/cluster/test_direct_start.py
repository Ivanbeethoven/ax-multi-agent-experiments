import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
START_C1 = ROOT / "infra" / "idcai" / "start_direct_c1.py"


def load_start_module():
    spec = importlib.util.spec_from_file_location("start_direct_c1_test_module", START_C1)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DirectStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_start_module()

    def test_awaiting_confirmation_returns_before_legacy_stages_or_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign_path = Path(temporary) / "campaign-direct.json"
            campaign_path.write_text(json.dumps({"review_state": "awaiting-confirmation"}), encoding="utf-8")
            output = io.StringIO()

            with patch.object(self.module, "Path", return_value=campaign_path), \
                    patch.object(self.module, "stage") as stage, \
                    patch.object(self.module.subprocess, "run") as run, \
                    patch.object(self.module.subprocess, "check_output") as check_output, \
                    patch.object(self.module.time, "sleep") as sleep, \
                    redirect_stdout(output):
                result = self.module.main()

        self.assertIsNone(result)
        self.assertEqual(output.getvalue().strip(), "Experiment plan is awaiting user confirmation.")
        stage.assert_not_called()
        run.assert_not_called()
        check_output.assert_not_called()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
