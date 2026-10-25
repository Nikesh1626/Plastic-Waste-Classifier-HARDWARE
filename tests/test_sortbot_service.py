import unittest
from unittest.mock import patch

from services.sortbot_service import SortBotService


class FakeSerial:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)

    def flush(self):
        return None

    def close(self):
        self.is_open = False


class SortBotServiceTests(unittest.TestCase):
    def test_class_mapping_and_threshold(self):
        service = SortBotService()
        service.enabled = True
        service.port = "COM5"
        service.confidence_threshold = 50

        self.assertEqual(service.prediction_target("PET", 75)["bin"], 1)
        self.assertEqual(service.prediction_target("LDPE", 75)["bin"], 2)
        self.assertEqual(service.prediction_target("PP", 75)["bin"], 3)
        self.assertEqual(service.prediction_target("OTHER", 75)["bin"], 4)
        self.assertFalse(service.prediction_target("PET", 49.99)["eligible"])
        self.assertFalse(service.prediction_target("UNKNOWN", 99)["eligible"])

    @patch("services.sortbot_service.serial.Serial")
    def test_sort_sends_newline_terminated_bin_command(self, serial_class):
        fake_serial = FakeSerial()
        serial_class.return_value = fake_serial
        service = SortBotService()
        service.enabled = True
        service.port = "COM5"
        service.baud_rate = 9600
        service.confidence_threshold = 50

        operation = service.send_sort("HDPE", 80)

        self.assertEqual(operation["command"], "T2")
        self.assertEqual(operation["status"], "command_sent")
        self.assertEqual(fake_serial.writes, [b"T2\n"])
        self.assertTrue(service.status()["busy"])

    def test_disabled_service_never_sends(self):
        service = SortBotService()
        target = service.prediction_target("PET", 99)
        self.assertFalse(target["eligible"])
        self.assertIn("disabled", target["reason"])


if __name__ == "__main__":
    unittest.main()
