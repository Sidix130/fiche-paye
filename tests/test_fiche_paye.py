import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fiche_paye import (
    AttachmentCandidate,
    expected_filename,
    extract_employee_name,
    extract_employer_name,
    extract_pay_period,
    load_candidates_from_path,
    previous_month,
    process_candidates,
    read_pdf_text,
    sanitize_filename_component,
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

    def test_invalid_range_start_after_end(self):
        # Période incohérente où le début est postérieur à la fin -> doit renvoyer None
        self.assertIsNone(extract_pay_period("Période du 31/10/2026 au 01/10/2026"))

    def test_previous_month_across_year_boundary(self):
        self.assertEqual(previous_month(date(2026, 1, 2)), (12, 2025))


class NameAndFilenameTests(unittest.TestCase):
    def test_separate_fields(self):
        text = "Nom : MARTIN\nPrénom : Jeanne"
        self.assertEqual(extract_employee_name(text), "MARTIN-Jeanne")

    def test_explicit_compound_field(self):
        text = "Nom et prénom du salarié : MARTIN Jeanne"
        self.assertEqual(extract_employee_name(text), "MARTIN-Jeanne")

    def test_filename_default_is_iso(self):
        # RÈGLE STABILISÉE : Par défaut, sans option supplémentaire, format AAAA-MM_NOM-Prénom.pdf
        self.assertEqual(expected_filename(2026, 10, "LEGENTIL-Pierre"), "2026-10_LEGENTIL-Pierre.pdf")

    def test_filename_one_year_digit_explicit(self):
        # Rétrocompatibilité : requiert explicitement naming_format="MM-Y"
        self.assertEqual(
            expected_filename(2026, 9, "MARTIN-Jeanne", year_digits=1, naming_format="MM-Y"),
            "09-6_MARTIN-Jeanne.pdf",
        )

    def test_filename_two_year_digits_explicit(self):
        self.assertEqual(
            expected_filename(2026, 9, "MARTIN-Jeanne", year_digits=2, naming_format="MM-Y"),
            "09-26_MARTIN-Jeanne.pdf",
        )

    def test_filename_special_characters_and_accents(self):
        self.assertEqual(
            expected_filename(2026, 10, "D'HONDT-Jean-François"),
            "2026-10_D'HONDT-Jean-François.pdf",
        )

    def test_filename_corporate_non_destructive_sanitization(self):
        # Vérifie que & devient -et- et que les points et chiffres sont conservés
        self.assertEqual(sanitize_filename_component("A&B Services"), "A-et-B-Services")
        self.assertEqual(sanitize_filename_component("B.T.P. 51"), "B.T.P.-51")
        self.assertEqual(sanitize_filename_component("3D Multi-Services"), "3D-Multi-Services")
        # Deux dénominations proches restent distinctes
        self.assertNotEqual(
            sanitize_filename_component("A&B Services"),
            sanitize_filename_component("AB Services"),
        )


class EmployerAndEmployeeSeparationTests(unittest.TestCase):
    """Vérification stricte de l'extraction employeur : personnes physiques, personnes morales,
    adresses rejetées et traitement des ambiguïtés."""

    def test_extract_employer_multiline_comma(self):
        text = "Employeur\nLEGENTIL, Pierre\n13 Rue du Mont Aime\n51130 Bergères-lès-Vertus"
        self.assertEqual(extract_employer_name(text), "LEGENTIL-Pierre")

    def test_extract_employer_single_line_comma(self):
        text = "Employeur : DEBAYE, Lydie\n25 Rue des Lys"
        self.assertEqual(extract_employer_name(text), "DEBAYE-Lydie")

    def test_extract_employer_compound_names(self):
        text = "Employeur\nOUGUERROUDJ, Jean Jacques\n3 Rue des Vignes"
        self.assertEqual(extract_employer_name(text), "OUGUERROUDJ-Jean-Jacques")

    def test_extract_employer_uncomma_valid_name(self):
        # Personne physique sans virgule (NOM en capitales, prénom capitalisé)
        text = "Employeur\nLECLERC Jeanne\n10 Rue Neuve\n51000 Châlons"
        self.assertEqual(extract_employer_name(text), "LECLERC-Jeanne")

    def test_extract_employer_uncomma_address_rejected(self):
        # SÉCURITÉ : La ligne suivant Employeur est une adresse -> REJET STRICT
        text = "Employeur\n13 Rue du Mont Aime\n51130 Bergères-lès-Vertus"
        self.assertIsNone(extract_employer_name(text))

    def test_extract_employer_uncomma_way_name_rejected(self):
        # SÉCURITÉ : La ligne commence par un type de voie -> REJET STRICT
        text = "Employeur\nRue de la République\n51000 Châlons"
        self.assertIsNone(extract_employer_name(text))

    def test_extract_employer_corporate_with_legal_form(self):
        # Personne morale avec forme juridique explicite
        text_sarl = "Employeur\nSARL AIDE ET SERVICES\n12 Rue de la Paix\n51100 Reims"
        self.assertEqual(extract_employer_name(text_sarl), "SARL-AIDE-ET-SERVICES")

        text_sas = "Employeur\nSAS OUIHELP\n10 Boulevard Haussmann\n75009 Paris"
        self.assertEqual(extract_employer_name(text_sas), "SAS-OUIHELP")

        text_earl = "Employeur : EARL DU MOULIN\nChemin rural"
        self.assertEqual(extract_employer_name(text_earl), "EARL-DU-MOULIN")

    def test_extract_employer_corporate_no_legal_form(self):
        # Personne morale SANS forme juridique (ex: association ADMR en majuscules)
        text = "Employeur\nADMR VALLÉE DE LA MARNE\n1 Rue de la Mairie\n51150 Aÿ"
        self.assertEqual(extract_employer_name(text), "ADMR-VALLÉE-DE-LA-MARNE")

    def test_extract_employer_corporate_with_special_characters(self):
        # Dénomination contenant '&', des points ou des chiffres
        text_amp = "Employeur\nA&B Services\n14 Avenue de Paris"
        self.assertEqual(extract_employer_name(text_amp), "A&B-Services")

        text_dots = "Raison sociale : B.T.P. 51\nZone Industrielle"
        self.assertEqual(extract_employer_name(text_dots), "B.T.P.-51")

        text_num = "Employeur\n3D Multi-Services\n2 Bis Rue Basse"
        self.assertEqual(extract_employer_name(text_num), "3D-Multi-Services")

    def test_extract_employer_ambiguous_multiple_different_blocks_rejected(self):
        # AMBIGUÏTÉ : Deux blocs employeurs contradictoires dans le document -> REJET STRICT (None)
        text = (
            "Employeur\nLEGENTIL, Pierre\n13 Rue du Mont Aime\n\n"
            "Employeur\nDEBAYE, Lydie\n25 Rue des Lys"
        )
        self.assertIsNone(extract_employer_name(text))

    def test_extract_employer_ambiguous_co_employer_rejected(self):
        # AMBIGUÏTÉ : Mention d'un co-employeur ou employeur secondaire -> REJET STRICT (None)
        text = (
            "Employeur : DUPONT Jean\n"
            "Co-employeur : MARTIN Pierre\n"
            "Salarié : COQUERY Florence"
        )
        self.assertIsNone(extract_employer_name(text))

    def test_strict_isolation_no_fallback_to_employee(self):
        # RÈGLE MÉTIER FONDAMENTALE : Si le document ne contient QUE le salarié, l'employeur DOIT être None.
        text = "Salarié\nCOQUERY, Florence\n6 Rue Cours Coger\n51130 Blancs-Coteaux"
        self.assertIsNone(extract_employer_name(text))
        self.assertEqual(extract_employee_name(text), "COQUERY-Florence")

    def test_both_present_in_document_not_confused(self):
        text = (
            "Employeur\nLEGENTIL, Pierre\n13 Rue du Mont Aime\n\n"
            "Salarié\nCOQUERY, Florence\n6 Rue Cours Coger"
        )
        self.assertEqual(extract_employer_name(text), "LEGENTIL-Pierre")
        self.assertEqual(extract_employee_name(text), "COQUERY-Florence")


class OrganizerCharacterizationTests(unittest.TestCase):
    """Caractérisation des règles de classement, doublons et collisions."""

    def test_missing_employer_goes_to_review_without_employee_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            cand = AttachmentCandidate("ref1", "2026-10-01", "bulletin.pdf", b"%PDF-mock")
            with patch("fiche_paye.read_pdf_text") as mock_read:
                mock_read.return_value = (
                    "Période du 01/10/2026 au 31/10/2026\n"
                    "Salarié\nCOQUERY, Florence\n"
                )
                saved, skipped, review = process_candidates([cand], out, None, None, dry_run=False)
                self.assertEqual((saved, skipped, review), (0, 0, 1))
                review_files = list(out.glob("2026/A_VERIFIER/*.pdf"))
                self.assertEqual(len(review_files), 1)
                self.assertTrue(review_files[0].name.startswith("A_VERIFIER_"))

    def test_missing_period_goes_to_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            cand = AttachmentCandidate("ref2", "2026-10-01", "bulletin.pdf", b"%PDF-mock")
            with patch("fiche_paye.read_pdf_text") as mock_read:
                mock_read.return_value = "Employeur\nLEGENTIL, Pierre\n"
                saved, skipped, review = process_candidates([cand], out, None, None, dry_run=False)
                self.assertEqual((saved, skipped, review), (0, 0, 1))
                review_files = list(out.glob("NON_CLASSE/A_VERIFIER/*.pdf"))
                self.assertEqual(len(review_files), 1)

    def test_duplicate_strict_identical_sha256_is_skipped(self):
        # RÈGLE DE GESTION : Doublon strict (même empreinte SHA-256) -> ignoré silencieusement
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            data = b"%PDF-mock-same-content"
            cand1 = AttachmentCandidate("ref1", "2026-10-01", "doc1.pdf", data)
            cand2 = AttachmentCandidate("ref2", "2026-10-01", "doc2.pdf", data)
            with patch("fiche_paye.read_pdf_text") as mock_read:
                mock_read.return_value = (
                    "Période du 01/10/2026 au 31/10/2026\n"
                    "Employeur\nLEGENTIL, Pierre\n"
                )
                saved, skipped, review = process_candidates([cand1, cand2], out, None, None, dry_run=False)
                self.assertEqual((saved, skipped, review), (1, 1, 0))
                classified = list(out.glob("2026/*.pdf"))
                self.assertEqual(len(classified), 1)
                self.assertEqual(classified[0].name, "2026-10_LEGENTIL-Pierre.pdf")

    def test_collision_different_content_never_overwrites_routes_to_review(self):
        # RÈGLE DE GESTION : Collision de nom avec SHA-256 différent dans le même lot.
        # Fait : deux documents ont la même destination mais un contenu différent.
        # Décision : le premier est classé, le second est consigné dans A_VERIFIER pour arbitrage humain.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            cand1 = AttachmentCandidate("ref1", "2026-10-01", "doc1.pdf", b"%PDF-version-A")
            cand2 = AttachmentCandidate("ref2", "2026-10-01", "doc2.pdf", b"%PDF-version-B")
            with patch("fiche_paye.read_pdf_text") as mock_read:
                mock_read.return_value = (
                    "Période du 01/10/2026 au 31/10/2026\n"
                    "Employeur\nLEGENTIL, Pierre\n"
                )
                saved, skipped, review = process_candidates([cand1, cand2], out, None, None, dry_run=False)
                self.assertEqual((saved, skipped, review), (1, 0, 1))

                # Le premier fichier reste intact
                classified = list(out.glob("2026/*.pdf"))
                self.assertEqual(len(classified), 1)
                self.assertEqual(classified[0].name, "2026-10_LEGENTIL-Pierre.pdf")
                self.assertEqual(classified[0].read_bytes(), b"%PDF-version-A")

                # Le document concurrent est sécurisé dans A_VERIFIER
                reviews = list(out.glob("2026/A_VERIFIER/*.pdf"))
                self.assertEqual(len(reviews), 1)
                self.assertEqual(reviews[0].read_bytes(), b"%PDF-version-B")

    def test_existing_file_on_disk_conflict_never_overwrites(self):
        # Même règle face à un fichier déjà présent sur le disque : aucun écrasement.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            dest_dir = out / "2026"
            dest_dir.mkdir(parents=True)
            existing_file = dest_dir / "2026-10_LEGENTIL-Pierre.pdf"
            existing_file.write_bytes(b"%PDF-deja-sur-disque")

            new_cand = AttachmentCandidate("ref3", "2026-10-01", "nouveau.pdf", b"%PDF-nouvelle-version")
            with patch("fiche_paye.read_pdf_text") as mock_read:
                mock_read.return_value = (
                    "Période du 01/10/2026 au 31/10/2026\n"
                    "Employeur\nLEGENTIL, Pierre\n"
                )
                saved, skipped, review = process_candidates([new_cand], out, None, None, dry_run=False)
                self.assertEqual((saved, skipped, review), (0, 0, 1))

                # Le fichier existant n'a pas été altéré
                self.assertEqual(existing_file.read_bytes(), b"%PDF-deja-sur-disque")

                # Le nouveau est conservé dans A_VERIFIER
                reviews = list(out.glob("2026/A_VERIFIER/*.pdf"))
                self.assertEqual(len(reviews), 1)
                self.assertEqual(reviews[0].read_bytes(), b"%PDF-nouvelle-version")

    def test_month_filter_skips_out_of_range_documents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            cand = AttachmentCandidate("ref1", "2026-10-01", "bulletin.pdf", b"%PDF-mock")
            with patch("fiche_paye.read_pdf_text") as mock_read:
                mock_read.return_value = (
                    "Période du 01/10/2026 au 31/10/2026\n"
                    "Employeur\nLEGENTIL, Pierre\n"
                )
                saved, skipped, review = process_candidates([cand], out, target_month=9, target_year=2026, dry_run=False)
                self.assertEqual((saved, skipped, review), (0, 1, 0))
                self.assertEqual(len(list(out.glob("**/*.pdf"))), 0)


class AutonomousSyntheticZipTests(unittest.TestCase):
    """Niveau 2 : Tests autonomes du pipeline ZIP avec archive générée en mémoire."""

    def test_in_memory_synthetic_zip_processing(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("bulletin1.pdf", b"%PDF-1")
            zf.writestr("bulletin2.pdf", b"%PDF-2")
            zf.writestr("notes.txt", b"fichier ignore")

        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp_zip:
            tmp_zip.write(zip_buffer.getvalue())
            tmp_zip.flush()
            candidates = load_candidates_from_path(Path(tmp_zip.name))
            self.assertEqual(len(candidates), 2)
            self.assertEqual([c.filename for c in candidates], ["bulletin1.pdf", "bulletin2.pdf"])


class RealZipIntegrationTests(unittest.TestCase):
    """Niveau 3 : Tests d'intégration sur l'archive réelle input/ilovepdf_split.zip.
    Vérifie individuellement chaque résultat d'extraction et de classement physique.
    """
    ZIP_PATH = Path("input/ilovepdf_split.zip")

    def setUp(self):
        if not self.ZIP_PATH.exists():
            self.skipTest(f"Archive réelle absente ({self.ZIP_PATH}) - test d'intégration exclu")

    def test_verify_each_of_the_five_individual_destinations(self):
        candidates = load_candidates_from_path(self.ZIP_PATH)
        self.assertEqual(len(candidates), 5, "L'archive doit contenir exactement 5 bulletins")

        expected_targets = {
            "bulletin-paie-10-2026-1.pdf": ("LEGENTIL-Pierre", "2026-10_LEGENTIL-Pierre.pdf"),
            "bulletin-paie-10-2026-2.pdf": ("DEBAYE-Lydie", "2026-10_DEBAYE-Lydie.pdf"),
            "bulletin-paie-10-2026-3.pdf": ("OUGUERROUDJ-Jean-Jacques", "2026-10_OUGUERROUDJ-Jean-Jacques.pdf"),
            "bulletin-paie-10-2026-4.pdf": ("PERSON-Michele", "2026-10_PERSON-Michele.pdf"),
            "bulletin-paie-10-2026-5.pdf": ("PIAT-Nicole", "2026-10_PIAT-Nicole.pdf"),
        }

        extracted_employers = set()
        for c in candidates:
            text = read_pdf_text(c.data)
            period = extract_pay_period(text)
            self.assertIsNotNone(period, f"Période non détectée pour {c.filename}")
            self.assertEqual(period.start, date(2026, 10, 1))
            self.assertEqual(period.end, date(2026, 10, 31))

            emp = extract_employer_name(text)
            self.assertIn(c.filename, expected_targets)
            exp_emp, exp_file = expected_targets[c.filename]
            self.assertEqual(emp, exp_emp, f"Employeur extrait incorrect pour {c.filename}")

            target_file = expected_filename(period.start.year, period.start.month, emp)
            self.assertEqual(target_file, exp_file, f"Nom de fichier cible incorrect pour {c.filename}")
            extracted_employers.add(emp)

        self.assertEqual(len(extracted_employers), 5, "Les 5 employeurs extraits doivent être distincts")

    def test_real_filesystem_classification_in_isolated_tempdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            candidates = load_candidates_from_path(self.ZIP_PATH)
            saved, skipped, review = process_candidates(
                candidates=candidates,
                output_dir=out_dir,
                target_month=10,
                target_year=2026,
                dry_run=False,
            )
            self.assertEqual(saved, 5)
            self.assertEqual(skipped, 0)
            self.assertEqual(review, 0)

            # Vérification physique sur disque des fichiers créés
            written_files = sorted(f.name for f in (out_dir / "2026").glob("*.pdf"))
            expected_files = sorted([
                "2026-10_LEGENTIL-Pierre.pdf",
                "2026-10_DEBAYE-Lydie.pdf",
                "2026-10_OUGUERROUDJ-Jean-Jacques.pdf",
                "2026-10_PERSON-Michele.pdf",
                "2026-10_PIAT-Nicole.pdf",
            ])
            self.assertEqual(written_files, expected_files)

            # Vérification de l'intégrité : taille > 0
            for filename in expected_files:
                file_path = out_dir / "2026" / filename
                self.assertTrue(file_path.is_file())
                self.assertGreater(file_path.stat().st_size, 0)

            # Vérification qu'aucun dossier A_VERIFIER n'a été créé
            review_dir = out_dir / "2026" / "A_VERIFIER"
            self.assertFalse(review_dir.exists())

    def test_dry_run_simulation_guarantees_zero_filesystem_writes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "should_never_exist"
            candidates = load_candidates_from_path(self.ZIP_PATH)
            saved, skipped, review = process_candidates(
                candidates=candidates,
                output_dir=out_dir,
                target_month=10,
                target_year=2026,
                dry_run=True,
            )
            self.assertEqual(saved, 5)
            self.assertFalse(out_dir.exists(), "Le mode simulation ne doit créer aucun dossier sur le disque")


if __name__ == "__main__":
    unittest.main()
