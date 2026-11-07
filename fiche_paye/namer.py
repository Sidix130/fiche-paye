"""Module de nommage pur et déterministe pour les bulletins de paie.

Fonctions pures sans effet de bord : aucune I/O disque, aucun renommage physique.
"""
from __future__ import annotations

import re


def sanitize_filename_component(value: str) -> str:
    """Nettoie et normalise une composante de nom de fichier.

    Règles :
    - Remplacement de '&' par '-et-' (non destructif pour les raisons sociales)
    - Remplacement des séparateurs de chemin '/' et '\\' par des tirets
    - Suppression des caractères interdits sur les systèmes de fichiers (POSIX / Windows)
    - Normalisation des espaces consécutifs en un seul tiret
    - Réduction des tirets successifs
    """
    if not value or not value.strip():
        return "inconnu"
    value = value.replace("&", "-et-")
    value = value.replace("/", "-").replace("\\", "-")
    value = re.sub(r"[\x00-\x1f<>:\"|?*]", "", value)
    value = re.sub(r"\s+", "-", value.strip())
    value = re.sub(r"-+", "-", value)
    return value.strip(".- ") or "inconnu"


def expected_filename(
    year: int,
    month: int,
    employer_name: str,
    year_digits: int = 4,
    naming_format: str = "YYYY-MM",
) -> str:
    """Construit le nom de fichier cible pour un bulletin.

    Contrat :
    - Par défaut : format ISO 'AAAA-MM_NOM-Prénom.pdf' (naming_format="YYYY-MM")
    - Format rétrocompatible : 'MM-Y_NOM-Prénom.pdf' (naming_format="MM-Y")
    - Fonction pure : déterministe et sans effet de bord.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Mois invalide ({month}) : doit être compris entre 1 et 12")
    if year < 1900 or year > 2100:
        raise ValueError(f"Année invalide ({year}) : doit être comprise entre 1900 et 2100")

    safe_name = sanitize_filename_component(employer_name)

    if naming_format == "YYYY-MM":
        return f"{year:04d}-{month:02d}_{safe_name}.pdf"

    if naming_format == "MM-Y":
        if year_digits not in (1, 2, 4):
            raise ValueError("year_digits doit être 1, 2 ou 4 pour le format MM-Y")
        year_text = str(year)[-year_digits:]
        return f"{month:02d}-{year_text}_{safe_name}.pdf"

    raise ValueError(
        f"Format de nommage non reconnu : {naming_format!r} (valeurs acceptées : 'YYYY-MM', 'MM-Y')"
    )

