import unittest
from unittest.mock import patch

from audiotext.admission import AudioAdmission


class AudioAdmissionTest(unittest.TestCase):
    def test_quota_uses_a_rolling_minute(self) -> None:
        admission = AudioAdmission(per_minute=2)
        with patch("audiotext.admission.monotonic") as clock:
            clock.return_value = 0.0
            self.assertEqual(admission.reserve(123), "accepted")
            admission.release()
            clock.return_value = 30.0
            self.assertEqual(admission.reserve(123), "accepted")
            admission.release()
            clock.return_value = 59.9
            self.assertEqual(admission.reserve(123), "limited")
            clock.return_value = 60.0
            self.assertEqual(admission.reserve(123), "accepted")
            admission.release()
            self.assertEqual(admission.reserve(123), "limited")
