import unittest
from datetime import date

from fiche_paye import (
    expected_filename,
    extract_employee_name,
    extract_pay_period,
    previous_month,
)


class PayPeriodTests(unittest.TestCase):
    def test_numeric_range(self):
        period = extract_pay_period("Bulletin de paie pour la période du 01/02/2026 au 28/02/2026")
        self.assertIsNotNone(period)
        self.assertEqual(period.start, date(2026, 2, 1))
        self.assertEqual(period.end, date(2026, 2, 28))

    def test_french_month_names(self):
        period = extract_pay_period("Période : du 1 septembre 2026 au 30 septembre 2026")
        self.assertIsNotNone(period)
        self.assertEqual(period.start, date(2026, 9, 1))
        self.assertEqual(period.end, date(2026, 9, 30))

    def test_no_period_no_guess(self):
        self.assertIsNone(extract_pay_period("Courriel envoyé le 06/03/2026"))

    def test_previous_month_across_year_boundary(self):
        self.assertEqual(previous_month(date(2026, 1, 2)), (12, 2025))


class NameAndFilenameTests(unittest.TestCase):
    def test_separate_fields(self):
        text = "Nom : MARTIN\nPrénom : Jeanne"
        self.assertEqual(extract_employee_name(text), "MARTIN-Jeanne")

    def test_explicit_compound_field(self):
        text = "Nom et prénom du salarié : MARTIN Jeanne"
        self.assertEqual(extract_employee_name(text), "MARTIN-Jeanne")

    def test_filename_one_year_digit(self):
        self.assertEqual(expected_filename(2026, 9, "MARTIN-Jeanne"), "09-6_MARTIN-Jeanne.pdf")

    def test_filename_two_year_digits(self):
        self.assertEqual(expected_filename(2026, 9, "MARTIN-Jeanne", 2), "09-26_MARTIN-Jeanne.pdf")


if __name__ == "__main__":
    unittest.main()
