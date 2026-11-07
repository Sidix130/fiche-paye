"""Package fiche_paye - Classement automatisé et local des bulletins de paie.

Ce package expose les nouveaux modules du domaine (models, namer)
et assure la continuité fonctionnelle avec les composants en cours de migration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from fiche_paye.models import AttachmentCandidate, DocumentCandidate, PayPeriod
from fiche_paye.namer import expected_filename, sanitize_filename_component

__all__ = [
    "AttachmentCandidate",
    "DocumentCandidate",
    "PayPeriod",
    "expected_filename",
    "sanitize_filename_component",
]

# Chargement du monolithe de transition et synchronisation bidirectionnelle
_root_file = Path(__file__).resolve().parent.parent / "fiche_paye.py"
_spec = importlib.util.spec_from_file_location("_legacy_monolith", _root_file)
if _spec and _spec.loader:
    _legacy = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_legacy)

    # Export des fonctions non encore migrées dans ce package
    for _k, _v in _legacy.__dict__.items():
        if not _k.startswith("__") and _k not in globals():
            globals()[_k] = _v

    # Synchronisation pour que les patchs sur fiche_paye affectent directement l'exécution du monolithe
    def _sync_read_pdf_text(data: bytes) -> str:
        current = globals().get("read_pdf_text")
        if current is not _sync_read_pdf_text:
            return current(data)
        return _legacy.read_pdf_text(data)

    _legacy.read_pdf_text = _sync_read_pdf_text
