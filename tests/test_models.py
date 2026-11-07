import hashlib
import unittest
from datetime import date

from fiche_paye.models import AttachmentCandidate, DocumentCandidate, PayPeriod


class PayPeriodModelTests(unittest.TestCase):
    def test_valid_period_preserves_dates(self):
        period = PayPeriod(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(period.start, date(2026, 10, 1))
        self.assertEqual(period.end, date(2026, 10, 31))
        self.assertEqual(period.year, 2026)
        self.assertEqual(period.month, 10)
        self.assertTrue(period.is_single_month)

    def test_period_spanning_multiple_months(self):
        period = PayPeriod(date(2026, 1, 15), date(2026, 2, 14))
        self.assertEqual(period.year, 2026)
        self.assertEqual(period.month, 1)
        self.assertFalse(period.is_single_month)

    def test_invalid_dates_raises_value_error(self):
        # Début postérieur à la fin -> doit lever ValueError
        with self.assertRaises(ValueError):
            PayPeriod(date(2026, 10, 31), date(2026, 10, 1))

    def test_pay_period_is_frozen(self):
        period = PayPeriod(date(2026, 10, 1), date(2026, 10, 31))
        with self.assertRaises(Exception):
            period.start = date(2026, 10, 2)  # type: ignore[misc]


class DocumentCandidateModelTests(unittest.TestCase):
    def test_source_independence_local(self):
        data = b"%PDF-local-bytes"
        cand = DocumentCandidate(
            source_type="local_zip",
            source_reference="input/archive.zip:bulletin1.pdf",
            filename="bulletin1.pdf",
            data=data,
            metadata={"extracted_time": "2026-10-31"},
        )
        self.assertEqual(cand.source_type, "local_zip")
        self.assertEqual(cand.source_reference, "input/archive.zip:bulletin1.pdf")
        self.assertEqual(cand.filename, "bulletin1.pdf")
        self.assertEqual(cand.data, data)
        self.assertEqual(cand.sha256, hashlib.sha256(data).hexdigest())

    def test_source_independence_gmail(self):
        data = b"%PDF-gmail-bytes"
        cand = DocumentCandidate(
            source_type="gmail",
            source_reference="msg_18a3f890",
            filename="paie.pdf",
            data=data,
            metadata={"message_date": "2026-11-05"},
        )
        self.assertEqual(cand.source_type, "gmail")
        self.assertEqual(cand.source_reference, "msg_18a3f890")
        # Rétrocompatibilité avec l'ancien contrat
        self.assertEqual(cand.message_id, "msg_18a3f890")
        self.assertEqual(cand.message_date, "2026-11-05")

    def test_empty_filename_raises_value_error(self):
        with self.assertRaises(ValueError):
            DocumentCandidate(
                source_type="local_file",
                source_reference="path/to/file",
                filename="",
                data=b"%PDF-valid",
            )

    def test_empty_data_raises_value_error(self):
        with self.assertRaises(ValueError):
            DocumentCandidate(
                source_type="local_file",
                source_reference="path/to/file",
                filename="doc.pdf",
                data=b"",
            )

    def test_candidate_is_frozen(self):
        cand = DocumentCandidate("file", "ref", "doc.pdf", b"%PDF-valid")
        with self.assertRaises(Exception):
            cand.filename = "new.pdf"  # type: ignore[misc]

    def test_attachment_candidate_alias_identity(self):
        # Vérifie que AttachmentCandidate est une sous-classe transparente de DocumentCandidate
        self.assertTrue(issubclass(AttachmentCandidate, DocumentCandidate))
        cand = AttachmentCandidate("msg1", "2026-10-01", "file.pdf", b"%PDF-legacy")
        self.assertIsInstance(cand, DocumentCandidate)
        self.assertEqual(cand.message_id, "msg1")
        self.assertEqual(cand.message_date, "2026-10-01")


if __name__ == "__main__":
    unittest.main()
