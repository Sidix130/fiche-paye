#!/usr/bin/env python3
"""Fetch last month's payslip PDFs from Gmail and classify them locally.

Gmail access is read-only. PDFs are never sent to an external service.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import itertools
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

GMAIL_DEPENDENCY_ERROR: str | None = None
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as exc:
    GMAIL_DEPENDENCY_ERROR = str(exc)
    Request = Credentials = InstalledAppFlow = build = HttpError = None  # type: ignore[misc]

DEPENDENCY_ERROR = GMAIL_DEPENDENCY_ERROR

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore[misc]

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_SENDER = "admin@ouihelp.fr"
MAX_PDF_BYTES = 30 * 1024 * 1024

MONTHS_FR = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
MONTH_PATTERN = (
    r"janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre"
)
DATE_PATTERN = rf"(?:\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{4}}|\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+\d{{4}})"
PERIOD_PATTERN = re.compile(
    rf"\bp[eé]riode\b.{{0,180}}?(?P<start>{DATE_PATTERN})\s*"
    rf"(?:au|à|jusqu['’ ]?au|[-–—])\s*(?P<end>{DATE_PATTERN})",
    re.IGNORECASE | re.DOTALL,
)

from fiche_paye.models import AttachmentCandidate, DocumentCandidate, PayPeriod


def config_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "fiche-paye"


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "Fiches-de-paie"


def previous_month(reference: date | None = None) -> tuple[int, int]:
    reference = reference or date.today()
    if reference.month == 1:
        return 12, reference.year - 1
    return reference.month - 1, reference.year


def normalize_for_matching(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip().lower()


def parse_date_token(token: str) -> date | None:
    token = token.strip().replace("’", "'")
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            pass

    match = re.fullmatch(r"(\d{1,2})\s+([a-zA-Zéèêëàâäîïôöùûüç]+)\s+(\d{4})", token, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month_name = normalize_for_matching(match.group(2))
        year = int(match.group(3))
        month = MONTHS_FR.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def extract_pay_period(pdf_text: str) -> PayPeriod | None:
    """Extract dates only from a labelled 'période' range; do not guess from email date."""
    match = PERIOD_PATTERN.search(pdf_text)
    if not match:
        # A few PDFs put the phrase between the dates in a separate line. This
        # second pass accepts only a short, explicit date range near 'période'.
        text = re.sub(r"[ \t]+", " ", pdf_text)
        labelled = re.search(r"\bp[eé]riode\b", text, re.IGNORECASE)
        if labelled:
            chunk = text[labelled.start(): labelled.start() + 320]
            dates = list(re.finditer(DATE_PATTERN, chunk, re.IGNORECASE))
            for left, right in itertools.pairwise(dates):
                between = chunk[left.end():right.start()]
                if re.search(r"\b(?:au|à|jusqu['’ ]?au)\b|[-–—]", between, re.IGNORECASE):
                    start = parse_date_token(left.group(0))
                    end = parse_date_token(right.group(0))
                    if start and end:
                        return PayPeriod(start, end)
        return None

    start = parse_date_token(match.group("start"))
    end = parse_date_token(match.group("end"))
    if not start or not end or end < start:
        return None
    return PayPeriod(start, end)


def _clean_field_value(value: str) -> str:
    value = re.split(
        r"\s{2,}|\b(?:pr[eé]nom|nom|n[o°]\s*ss|matricule|n[o°]\s*de s[eé]curit[eé])\s*[:#]",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\s+", " ", value).strip(" \t:;-|,.")
    value = re.sub(r"^(?:M(?:me|lle|r)?\.?|Madame|Monsieur)\s+", "", value, flags=re.IGNORECASE)
    return value


def _clean_name_piece(value: str) -> str:
    value = _clean_field_value(value)
    value = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -'")
    return value


def _format_surname(surname: str) -> str:
    return "-".join(part.upper() for part in re.split(r"[ -]+", surname.strip()) if part)


def _format_given_names(given_names: str) -> str:
    parts = re.split(r"([ -]+)", given_names.strip())
    formatted: list[str] = []
    for part in parts:
        if part and re.fullmatch(r"[ -]+", part):
            formatted.append("-")
        elif part:
            formatted.append(part[:1].upper() + part[1:].lower())
    return "".join(formatted)


def _safe_name_pair(surname: str, given_names: str) -> str | None:
    surname = _clean_name_piece(surname)
    given_names = _clean_name_piece(given_names)
    if not surname or not given_names:
        return None
    if len(surname) > 50 or len(given_names) > 50:
        return None
    return f"{_format_surname(surname)}-{_format_given_names(given_names)}"


STREET_TYPES = (
    r"(?:rue|avenue|av\.?|boulevard|bd\.?|impasse|chemin|route|all[eé]e|place|quai|"
    r"square|cours|voie|hameau|clos|r[eé]sidence|b[aâ]timent|lieu[- ]dit)"
)

ADDRESS_INDICATORS = re.compile(
    rf"^(?:"
    rf"\d+[\s,-]+(?:bis|ter|quater[\s,-]+)?{STREET_TYPES}\b|"
    rf"{STREET_TYPES}\b|"
    rf"\d{{5}}\s+[A-Za-zÀ-ÖØ-öø-ÿ]|"
    rf"bp\s*\d+|cs\s*\d+|"
    rf"zi\b|za\b|zone\b|cedex\b"
    rf")",
    re.IGNORECASE,
)

TECHNICAL_INDICATORS = re.compile(
    r"^(?:siret\b|urssaf\b|ape\b|naf\b|convention\b|n[o°]\s*(?:employeur|ss|de\s*s[eé]curit[eé])\b|bulletin\b|matricule\b|paiement\b|virement\b)",
    re.IGNORECASE,
)


def _is_address_or_technical(line: str) -> bool:
    line_clean = line.strip()
    return bool(ADDRESS_INDICATORS.match(line_clean) or TECHNICAL_INDICATORS.match(line_clean))


def _clean_corporate_name(raw: str) -> str | None:
    val = _clean_field_value(raw)
    if _is_address_or_technical(val):
        return None
    # Préservation de &, ., chiffres, lettres, tirets et apostrophes (non destructif)
    val = re.sub(r"[^A-Za-z0-9À-ÖØ-öø-ÿÆæŒœ&.' -]", "", val)
    val = re.sub(r"\s+", "-", val.strip(" -.'"))
    if not val or len(val) < 2 or len(val) > 60:
        return None
    return val


def extract_employer_name(pdf_text: str) -> str | None:
    """Return NOM-Prénom or corporate identity for the employer when explicitly labelled in the PDF.

    Strict contract:
    - Never falls back to employee identity.
    - If absent, ambiguous or multiple distinct employers are found: returns None.
    - Lines containing addresses or technical headers are rejected.
    """
    text = pdf_text.replace("\x00", "\n")

    # 1. Détection préalable d'ambiguïté : co-employeur ou employeur secondaire explicite
    ambiguous_mentions = re.findall(
        r"(?im)^\s*(?:co[- ]employeur|employeur\s+secondaire|autre\s+employeur)\s*[:\-]?\s*([^\r\n]{2,60})",
        text,
    )
    if ambiguous_mentions:
        return None

    # 2. Raison sociale explicitement libellée
    m = re.search(r"(?im)^\s*raison\s+sociale\s*[:\-]\s*([^\r\n]{2,60})", text)
    if m:
        comp = _clean_corporate_name(m.group(1))
        if comp:
            return comp

    # 3. Nom de l'employeur explicitement libellé en champ unique
    m = re.search(r"(?im)^\s*nom\s+de\s+l['’]employeur\s*[:\-]\s*([^\r\n]{1,60})", text)
    if m:
        val = _clean_field_value(m.group(1))
        tokens = val.split()
        if len(tokens) >= 2:
            pair = _safe_name_pair(tokens[0], " ".join(tokens[1:]))
            if pair:
                return pair
        comp = _clean_corporate_name(m.group(1))
        if comp:
            return comp

    # 4. Bloc multiligne ou en ligne "Employeur" / "Particulier employeur"
    block_matches = list(
        re.finditer(
            r"(?im)^\s*(?:particulier\s+employeur|employeur)\s*[:\-]?\s*(?:\r?\n|\s+)\s*([^\r\n]{2,80})",
            text,
        )
    )
    if not block_matches:
        return None

    candidates_found: list[str] = []
    for match in block_matches:
        candidate_line = match.group(1).strip()
        if _is_address_or_technical(candidate_line):
            continue

        # Cas A : Format personne physique avec virgule "NOM, Prénom"
        comma_m = re.match(
            r"^([A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]+),\s*([A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]+)$",
            candidate_line,
        )
        if comma_m:
            surname_piece = comma_m.group(1)
            given_piece = comma_m.group(2)
            if not _is_address_or_technical(surname_piece) and not _is_address_or_technical(given_piece):
                pair = _safe_name_pair(surname_piece, given_piece)
                if pair:
                    candidates_found.append(pair)
                    continue

        # Cas B : Personne morale avec forme juridique explicite, raison sociale ou dénomination commerciale
        corporate = _clean_corporate_name(candidate_line)
        if corporate and any(
            re.search(p, candidate_line, re.IGNORECASE)
            for p in (
                r"\b(?:SASU?|SARL|EURL|SA|SNC|SCI|EARL|SCEA|SCOP|ASSOCIATION|ADMR|ETS|ENTREPRISE|SERVICES?)\b",
                r"&",
                r"\d",
            )
        ):
            candidates_found.append(corporate)
            continue

        # Cas C : Personne physique sans virgule "NOM Prénom"
        words = candidate_line.split()
        if len(words) >= 2 and not _is_address_or_technical(words[0]):
            pair = _safe_name_pair(words[0], " ".join(words[1:]))
            if pair:
                candidates_found.append(pair)
                continue

        # Cas D : Autre personne morale sans forme juridique mais dénomination en majuscules
        if corporate and len(words) >= 2 and candidate_line.isupper():
            candidates_found.append(corporate)
            continue

    if not candidates_found:
        return None

    # Détection d'ambiguïté : plusieurs employeurs différents trouvés
    unique_candidates = sorted(set(candidates_found))
    if len(unique_candidates) > 1:
        return None

    return unique_candidates[0]


def extract_employee_name(pdf_text: str) -> str | None:
    """Return NOM-Prénom only when the PDF has reasonably explicit labels.

    Payslip layouts differ. For ambiguous layouts the caller should use
    --employee-name or manually inspect the PDF instead of guessing.
    """
    text = pdf_text.replace("\x00", "\n")

    # Multiline block: "Salarié\nNOM, Prénom" or "Salariée\nNOM, Prénom"
    m = re.search(
        r"(?im)^\s*(?:salari[eé](?:e)?)\s*[:\-]?\s*\r?\n\s*([A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]+),\s*([A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]+)",
        text,
    )
    if m:
        pair = _safe_name_pair(m.group(1), m.group(2))
        if pair:
            return pair

    # Single line: "Salarié : NOM, Prénom"
    m = re.search(
        r"(?im)^\s*(?:salari[eé](?:e)?)\s*[:\-]\s*([A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]+),\s*([A-Za-zÀ-ÖØ-öø-ÿÆæŒœ'’ -]+)",
        text,
    )
    if m:
        pair = _safe_name_pair(m.group(1), m.group(2))
        if pair:
            return pair

    # Most reliable case: separately labelled surname and given name fields.
    surname_patterns = [
        r"(?im)^\s*(?:nom\s+de\s+famille|nom\s+du\s+salari[eé](?:e)?|nom\s+de\s+la\s+salari[eé]e)\s*[:\-]\s*([^\r\n]{1,60})",
        r"(?im)^\s*nom\s*[:\-]\s*([^\r\n]{1,60})",
    ]
    given_patterns = [
        r"(?im)^\s*pr[eé]nom(?:s)?\s*[:\-]\s*([^\r\n]{1,60})",
        r"(?im)^\s*pr[eé]nom\s+du\s+salari[eé](?:e)?\s*[:\-]\s*([^\r\n]{1,60})",
    ]
    surname = None
    given = None
    for pattern in surname_patterns:
        m = re.search(pattern, text)
        if m:
            surname = _clean_name_piece(m.group(1))
            break
    for pattern in given_patterns:
        m = re.search(pattern, text)
        if m:
            given = _clean_name_piece(m.group(1))
            break
    if surname and given:
        pair = _safe_name_pair(surname, given)
        if pair:
            return pair

    # A combined field explicitly labelled for the employee.
    combined_patterns = [
        r"(?im)^\s*(?:nom\s+et\s+pr[eé]nom(?:s)?\s+du\s+salari[eé](?:e)?|nom\s+et\s+pr[eé]nom(?:s)?\s+de\s+la\s+salari[eé]e)\s*[:\-]?\s*([^\r\n]{3,90})",
        r"(?im)^\s*identit[eé]\s+du\s+salari[eé](?:e)?\s*[:\-]\s*([^\r\n]{3,90})",
    ]
    for pattern in combined_patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        value = _clean_name_piece(m.group(1))
        tokens = value.split()
        if len(tokens) >= 2:
            # A field labelled 'Nom et prénom' is interpreted in that order.
            return _safe_name_pair(tokens[0], " ".join(tokens[1:]))

    # Generic 'Nom et prénom' only when it is clearly a field, not prose.
    m = re.search(r"(?im)^\s*nom\s+et\s+pr[eé]nom(?:s)?\s*[:\-]\s*([^\r\n]{3,90})", text)
    if m:
        value = _clean_name_piece(m.group(1))
        tokens = value.split()
        if len(tokens) >= 2:
            return _safe_name_pair(tokens[0], " ".join(tokens[1:]))

    return None


from fiche_paye.namer import expected_filename, sanitize_filename_component


def read_pdf_text(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValueError("la pièce jointe ne semble pas être un PDF")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError(f"PDF trop volumineux ({len(data) // (1024 * 1024)} Mio, limite 30 Mio)")

    if PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                try:
                    unlocked = reader.decrypt("")
                except Exception as exc:
                    raise ValueError("PDF chiffré ou protégé par mot de passe") from exc
                if not unlocked:
                    raise ValueError("PDF chiffré ou protégé par mot de passe")
            pages: list[str] = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    pages.append("")
            text = "\n".join(pages)
            if text.strip():
                return text
        except Exception:
            pass  # Fallback to pdftotext if pypdf is unavailable or failed.

    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-", "-"],
                input=data,
                capture_output=True,
                check=True,
                timeout=20,
            )
            text = proc.stdout.decode("utf-8", errors="replace")
            if text.strip():
                return text
        except Exception as exc:
            raise ValueError(f"Échec de l'extraction de texte via pdftotext : {exc}") from exc

    if PdfReader is None and not pdftotext_bin:
        raise ValueError("Aucun extracteur PDF disponible (installez pypdf ou le binaire système pdftotext)")
    return ""


def load_candidates_from_path(input_path: Path) -> list[AttachmentCandidate]:
    candidates: list[AttachmentCandidate] = []
    if not input_path.exists():
        raise FileNotFoundError(f"Chemin d'entrée introuvable : {input_path}")

    if input_path.is_file() and zipfile.is_zipfile(input_path):
        with zipfile.ZipFile(input_path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                filename = Path(member.filename).name
                if not filename.lower().endswith(".pdf"):
                    continue
                data = zf.read(member)
                date_str = f"{member.date_time[0]:04d}-{member.date_time[1]:02d}-{member.date_time[2]:02d}"
                candidates.append(
                    AttachmentCandidate(
                        message_id=f"zip_{Path(member.filename).stem}",
                        message_date=date_str,
                        filename=filename,
                        data=data,
                    )
                )
    elif input_path.is_file() and input_path.suffix.lower() == ".pdf":
        data = input_path.read_bytes()
        candidates.append(
            AttachmentCandidate(
                message_id=input_path.stem,
                message_date="local",
                filename=input_path.name,
                data=data,
            )
        )
    elif input_path.is_dir():
        for file in sorted(input_path.glob("*.pdf")):
            data = file.read_bytes()
            candidates.append(
                AttachmentCandidate(
                    message_id=file.stem,
                    message_date="local",
                    filename=file.name,
                    data=data,
                )
            )
    else:
        raise ValueError(
            f"Type d'entrée non pris en charge (attendu : fichier .zip, fichier .pdf ou dossier) : {input_path}"
        )
    return candidates


def authenticate(credentials_path: Path, token_path: Path, reauth: bool = False):
    token_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token_path.parent.chmod(0o700)
    except OSError:
        pass
    if reauth and token_path.exists():
        token_path.unlink()

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.is_file():
                raise FileNotFoundError(
                    f"Fichier OAuth introuvable : {credentials_path}\n"
                    "Crée un identifiant OAuth de type Application de bureau dans Google Cloud, "
                    "active l'API Gmail et place le JSON téléchargé à cet emplacement. "
                    "Voir README.md."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
    return creds


def list_message_ids(service, query: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token
        ).execute()
        ids.extend(item["id"] for item in result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return ids


def walk_parts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    stack = [payload]
    while stack:
        part = stack.pop()
        yield part
        # Reverse keeps the MIME sequence order when using a stack.
        stack.extend(reversed(part.get("parts", []) or []))


def decode_attachment(service, message_id: str, part: dict[str, Any]) -> bytes | None:
    body = part.get("body") or {}
    data = body.get("data")
    attachment_id = body.get("attachmentId")
    if not data and attachment_id:
        attachment = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        data = attachment.get("data")
    if not data:
        return None
    encoded = data.encode("ascii")
    encoded += b"=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded)


def safe_attachment_filename(name: str) -> str:
    name = Path(name or "piece-jointe.pdf").name
    name = re.sub(r"[^\w.() éèêëàâäîïôöùûüçÉÈÊËÀÂÄÎÏÔÖÙÛÜÇ'’-]+", "_", name, flags=re.UNICODE)
    name = name.strip("._ ")
    return name or "piece-jointe.pdf"


def fetch_pdf_attachments(service, message_ids: list[str]) -> Iterable[AttachmentCandidate]:
    for message_id in message_ids:
        try:
            msg = service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
        except HttpError as exc:
            print(f"AVERTISSEMENT : message Gmail {message_id} inaccessible : {exc}", file=sys.stderr)
            continue
        payload = msg.get("payload") or {}
        internal_ms = int(msg.get("internalDate", "0") or 0)
        message_date = datetime.fromtimestamp(internal_ms / 1000).astimezone().date().isoformat() if internal_ms else "date inconnue"
        for part in walk_parts(payload):
            filename = part.get("filename", "")
            mime_type = (part.get("mimeType") or "").lower()
            if not filename or not (filename.lower().endswith(".pdf") or mime_type == "application/pdf"):
                continue
            try:
                data = decode_attachment(service, message_id, part)
                if data:
                    yield AttachmentCandidate(message_id, message_date, safe_attachment_filename(filename), data)
            except Exception as exc:
                print(f"AVERTISSEMENT : pièce jointe ignorée ({safe_attachment_filename(filename)}) : {exc}", file=sys.stderr)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_private_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    # Exclusive creation prevents accidental overwrites, including race conditions.
    with path.open("xb") as f:
        f.write(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def next_review_path(review_dir: Path, candidate: AttachmentCandidate) -> Path:
    base = sanitize_filename_component(Path(candidate.filename).stem)
    suffix = candidate.message_id[:10]
    path = review_dir / f"A_VERIFIER_{suffix}_{base}.pdf"
    index = 2
    while path.exists():
        path = review_dir / f"A_VERIFIER_{suffix}_{base}_{index}.pdf"
        index += 1
    return path


def process_candidates(
    candidates: Iterable[AttachmentCandidate],
    output_dir: Path,
    target_month: int | None,
    target_year: int | None,
    dry_run: bool,
    employer_name_override: str | None = None,
    year_digits: int = 4,
    naming_format: str = "YYYY-MM",
) -> tuple[int, int, int]:
    saved = skipped = review = 0
    # Avoid treating multiple duplicate copies of the same PDF as conflicts.
    seen_hashes: set[str] = set()
    planned_destinations: dict[str, str] = {}

    for candidate in candidates:
        digest = sha256(candidate.data)
        if digest in seen_hashes:
            print(f"DOUBLON ignoré : {candidate.filename} (même contenu déjà traité)")
            skipped += 1
            continue
        seen_hashes.add(digest)

        default_year_str = str(target_year) if target_year else "NON_CLASSE"
        fallback_review_dir = output_dir / default_year_str / "A_VERIFIER"

        try:
            pdf_text = read_pdf_text(candidate.data)
        except Exception as exc:
            print(f"À VÉRIFIER : {candidate.filename} — lecture PDF impossible : {exc}")
            destination = next_review_path(fallback_review_dir, candidate)
            if dry_run:
                print(f"  -> prévoir {destination}")
            else:
                write_private_file(destination, candidate.data)
                print(f"  -> conservé dans {destination}")
            review += 1
            continue

        period = extract_pay_period(pdf_text)
        if not period:
            print(f"À VÉRIFIER : {candidate.filename} — période de paie introuvable dans le PDF")
            destination = next_review_path(fallback_review_dir, candidate)
            if dry_run:
                print(f"  -> prévoir {destination}")
            else:
                write_private_file(destination, candidate.data)
                print(f"  -> conservé dans {destination}")
            review += 1
            continue

        file_year = period.start.year
        file_month = period.start.month

        if target_month is not None and target_year is not None:
            if (file_month, file_year) != (target_month, target_year) or (
                period.end.month, period.end.year
            ) != (target_month, target_year):
                print(
                    f"IGNORÉ : {candidate.filename} — période détectée "
                    f"{period.start:%d/%m/%Y} au {period.end:%d/%m/%Y}, "
                    f"cible {target_month:02d}/{target_year}"
                )
                skipped += 1
                continue

        output_year_dir = output_dir / str(file_year)
        review_dir = output_year_dir / "A_VERIFIER"

        # Règle métier : l'identifiant de classement est exclusivement l'employeur.
        # Ne jamais utiliser l'identité du salarié en repli.
        employer_name = employer_name_override or extract_employer_name(pdf_text)
        if not employer_name:
            print(
                f"À VÉRIFIER : {candidate.filename} — période {period.start:%d/%m/%Y} au {period.end:%d/%m/%Y}, "
                "mais employeur non identifié dans le PDF (l'identité du salarié n'est jamais utilisée en repli)"
            )
            destination = next_review_path(review_dir, candidate)
            if dry_run:
                print(f"  -> prévoir {destination}")
            else:
                write_private_file(destination, candidate.data)
                print(f"  -> conservé dans {destination}")
            review += 1
            continue

        filename = expected_filename(
            file_year,
            file_month,
            employer_name,
            year_digits=year_digits,
            naming_format=naming_format,
        )
        destination = output_year_dir / filename
        destination_key = str(destination.resolve())
        if destination_key in planned_destinations and planned_destinations[destination_key] != digest:
            print(f"CONFLIT : plusieurs PDF différents correspondent à {destination} ; aucun écrasement")
            review_path = next_review_path(review_dir, candidate)
            if dry_run:
                print(f"  -> prévoir {review_path}")
            else:
                write_private_file(review_path, candidate.data)
                print(f"  -> version supplémentaire conservée dans {review_path}")
            review += 1
            continue
        planned_destinations[destination_key] = digest
        if destination.exists():
            try:
                existing_hash = sha256(destination.read_bytes())
            except OSError:
                existing_hash = ""
            if existing_hash == digest:
                print(f"DÉJÀ CLASSÉ : {destination}")
                skipped += 1
                continue
            print(f"CONFLIT : {destination} existe avec un contenu différent ; aucun écrasement")
            review_path = next_review_path(review_dir, candidate)
            if dry_run:
                print(f"  -> prévoir {review_path}")
            else:
                write_private_file(review_path, candidate.data)
                print(f"  -> nouvelle version conservée dans {review_path}")
            review += 1
            continue

        if dry_run:
            print(
                f"OK : période {period.start:%d/%m/%Y} au {period.end:%d/%m/%Y} | "
                f"employeur : {employer_name} | fichier prévu : {destination}"
            )
        else:
            try:
                write_private_file(destination, candidate.data)
                print(f"CLASSÉ : {destination}")
            except FileExistsError:
                print(f"CONFLIT : {destination} a été créé entre-temps ; aucun écrasement")
                review_path = next_review_path(review_dir, candidate)
                write_private_file(review_path, candidate.data)
                print(f"  -> conservé dans {review_path}")
                review += 1
                continue
        saved += 1

    return saved, skipped, review


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    month, year = previous_month()
    parser = argparse.ArgumentParser(
        prog="fiche-paye",
        description="Récupère les PDF de paie depuis Gmail ou une archive/dossier local et les classe localement par employeur.",
    )
    parser.add_argument("--input", type=Path, help="chemin vers une archive ZIP, un dossier ou un fichier PDF local")
    parser.add_argument("--dry-run", action="store_true", help="afficher les actions sans écrire de PDF")
    parser.add_argument("--month", metavar="AAAA-MM", help="mois cible à rechercher (ex: 2026-10 ; optionnel avec --input)")
    parser.add_argument("--sender", default=DEFAULT_SENDER, help=f"expéditeur Gmail (défaut : {DEFAULT_SENDER})")
    parser.add_argument("--output", type=Path, default=default_output_dir(), help="dossier de classement racine")
    parser.add_argument("--credentials", type=Path, default=config_dir() / "credentials.json", help="JSON OAuth Google")
    parser.add_argument("--token", type=Path, default=config_dir() / "token.json", help="fichier local du jeton OAuth")
    parser.add_argument("--employer-name", help="nom de l'employeur au format NOM-Prénom (force l'identité si non détectée)")
    parser.add_argument("--employee-name", help=argparse.SUPPRESS)
    parser.add_argument("--naming-format", choices=("YYYY-MM", "MM-Y"), default="YYYY-MM", help="format de nommage : YYYY-MM (ex: 2026-10_NOM-Prénom.pdf, défaut) ou MM-Y (ex: 10-6_NOM-Prénom.pdf)")
    parser.add_argument("--year-digits", type=int, choices=(1, 2, 4), default=4, help="chiffres de l'année pour le format MM-Y (défaut : 4)")
    parser.add_argument("--reauth", action="store_true", help="supprimer le jeton local et redemander l'autorisation Google")
    parser.add_argument("--max-age-days", type=int, default=180, help="limite de recherche Gmail en jours (défaut : 180)")
    args = parser.parse_args(argv)

    if args.month:
        match = re.fullmatch(r"(\d{4})-(\d{2})", args.month)
        if not match:
            parser.error("--month doit respecter le format AAAA-MM, par exemple 2026-09")
        args.target_year, args.target_month = int(match.group(1)), int(match.group(2))
        if not 1 <= args.target_month <= 12:
            parser.error("le mois doit être compris entre 01 et 12")
    elif args.input:
        # En mode local, sans --month explicite, on classe selon la période lue dans chaque PDF
        args.target_month, args.target_year = None, None
    else:
        args.target_month, args.target_year = month, year

    if args.employee_name and not args.employer_name:
        args.employer_name = args.employee_name

    if args.max_age_days < 1 or args.max_age_days > 3650:
        parser.error("--max-age-days doit être compris entre 1 et 3650")
    return args


def main(argv: list[str] | None = None) -> int:
    # Files containing payslips and local OAuth tokens should default to owner-only.
    os.umask(0o077)
    args = parse_args(argv)

    if args.input:
        print(f"Source locale : {args.input}")
        if args.target_month and args.target_year:
            print(f"Filtre période : {args.target_month:02d}/{args.target_year}")
        else:
            print("Filtre période : automatique (selon la période détectée dans chaque bulletin)")
        print(f"Format de nommage : {args.naming_format}")
        print(f"Destination : {args.output}")
        if args.dry_run:
            print("Mode simulation : aucun fichier PDF ne sera écrit.\n")

        try:
            candidates = load_candidates_from_path(args.input)
            print(f"Documents PDF trouvés : {len(candidates)}\n")
            if not candidates:
                print("Aucun bulletin PDF trouvé dans la source fournie.")
                return 0
            saved, skipped, review = process_candidates(
                candidates=candidates,
                output_dir=args.output,
                target_month=args.target_month,
                target_year=args.target_year,
                dry_run=args.dry_run,
                employer_name_override=args.employer_name,
                year_digits=args.year_digits,
                naming_format=args.naming_format,
            )
            verb = "à classer" if args.dry_run else "classé(s)"
            print(f"\nBilan : {saved} {verb}, {skipped} ignoré(s), {review} à vérifier.")
            if review:
                print(f"Les PDF nécessitant une vérification sont rangés dans les sous-dossiers 'A_VERIFIER'.")
            return 0
        except FileNotFoundError as exc:
            print(f"ERREUR : {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"ERREUR : {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    # Mode Gmail
    if GMAIL_DEPENDENCY_ERROR:
        missing = GMAIL_DEPENDENCY_ERROR.split("'")[1] if "'" in GMAIL_DEPENDENCY_ERROR else GMAIL_DEPENDENCY_ERROR
        print(
            f"Dépendance manquante ({missing}) requise pour l'accès Gmail. Installe le projet avec :\n"
            "  python -m pip install -e .\n"
            "Ou utilisez --input pour traiter des fichiers locaux ou des archives ZIP.",
            file=sys.stderr,
        )
        return 2

    first_of_month = date(args.target_year, args.target_month, 1)
    query_start = first_of_month.strftime("%Y/%m/%d")
    query = f"from:{args.sender} has:attachment filename:pdf after:{query_start} newer_than:{args.max_age_days}d"

    print(f"Cible : {args.target_month:02d}/{args.target_year}")
    print(f"Expéditeur : {args.sender}")
    print(f"Recherche Gmail : {query}")
    print(f"Destination : {args.output}")
    if args.dry_run:
        print("Mode simulation : aucun fichier PDF ne sera écrit.\n")

    try:
        creds = authenticate(args.credentials, args.token, args.reauth)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        message_ids = list_message_ids(service, query)
        print(f"Messages correspondants : {len(message_ids)}")
        if not message_ids:
            print("Aucun courriel trouvé. Vérifie le compte Google autorisé, l'expéditeur et la période de recherche.")
            return 0
        candidates = list(fetch_pdf_attachments(service, message_ids))
        print(f"Pièces jointes PDF récupérées en mémoire : {len(candidates)}\n")
        saved, skipped, review = process_candidates(
            candidates=candidates,
            output_dir=args.output,
            target_month=args.target_month,
            target_year=args.target_year,
            dry_run=args.dry_run,
            employer_name_override=args.employer_name,
            year_digits=args.year_digits,
            naming_format=args.naming_format,
        )
        verb = "à classer" if args.dry_run else "classé(s)"
        print(f"\nBilan : {saved} {verb}, {skipped} ignoré(s), {review} à vérifier.")
        if review:
            print(f"Les PDF nécessitant une vérification seront rangés dans : {args.output / str(args.target_year) / 'A_VERIFIER'}")
        return 0
    except FileNotFoundError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2
    except HttpError as exc:
        print(f"ERREUR Gmail API : {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"ERREUR : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
