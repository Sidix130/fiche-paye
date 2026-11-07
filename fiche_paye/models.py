"""Modèles de données du domaine pour le classement des fiches de paie.

Structures immuables et indépendantes de la technologie d'acquisition.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PayPeriod:
    """Représente une période de paie avec dates de début et de fin.

    Préserve rigoureusement les dates exactes sans perte d'information.
    """
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(
                f"Période incohérente : début ({self.start}) postérieur à la fin ({self.end})"
            )

    @property
    def year(self) -> int:
        return self.start.year

    @property
    def month(self) -> int:
        return self.start.month

    @property
    def is_single_month(self) -> bool:
        """Indique si la période est entièrement contenue dans le même mois calendaire."""
        return (self.start.year, self.start.month) == (self.end.year, self.end.month)


@dataclass(frozen=True)
class DocumentCandidate:
    """Représente un document PDF candidat issu d'une source quelconque (ZIP, dossier, Gmail...).

    Indépendant de la technologie d'acquisition.
    """
    source_type: str
    source_reference: str
    filename: str
    data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.filename or not self.filename.strip():
            raise ValueError("Le nom de fichier d'un DocumentCandidate ne peut pas être vide.")
        if not self.data:
            raise ValueError("Les données binaires d'un DocumentCandidate ne peuvent pas être vides.")

    @property
    def sha256(self) -> str:
        """Calcule l'empreinte SHA-256 du document."""
        return hashlib.sha256(self.data).hexdigest()

    @property
    def message_id(self) -> str:
        """Alias de compatibilité pour le code attendant l'ancien AttachmentCandidate."""
        return self.source_reference

    @property
    def message_date(self) -> str:
        """Alias de compatibilité pour le code attendant l'ancien AttachmentCandidate."""
        return str(self.metadata.get("message_date", "date inconnue"))


@dataclass(frozen=True)
class AttachmentCandidate(DocumentCandidate):
    """Sous-classe de transition assurant la compatibilité avec l'ancienne signature."""

    def __init__(
        self,
        message_id: str,
        message_date: str,
        filename: str,
        data: bytes,
        source_type: str = "legacy",
    ) -> None:
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_reference", message_id)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "metadata", {"message_date": message_date})
        self.__post_init__()
