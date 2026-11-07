#!/usr/bin/env bash
# Déploie le projet fiche-paye sur Linux Mint et lance les tests unitaires à distance.
# Cible prévue : gerard@192.168.1.178 ; empreinte ED25519 connue depuis l'installation SSH.
set -Eeuo pipefail

HOST="${FICHE_PAYE_HOST:-192.168.1.178}"
REMOTE_USER="${FICHE_PAYE_USER:-gerard}"
TARGET="${REMOTE_USER}@${HOST}"
EXPECTED_ED25519_FP="SHA256:SAbDVVsNa3gG8ldCn0OqwkhZ0Bxqb5Ii9DItDww2uEA"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
SSH_DIR="$HOME/.ssh"
SSH_KEY="$SSH_DIR/id_ed25519_fiche_paye_mint"
KNOWN_HOSTS="$SSH_DIR/known_hosts"
SCAN_FILE=""
AGENT_STARTED=0

fail() { printf '\nERREUR : %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ "$AGENT_STARTED" -eq 1 && -n "${SSH_AGENT_PID:-}" ]]; then
    ssh-agent -k >/dev/null 2>&1 || true
  fi
  if [[ -n "$SCAN_FILE" && -f "$SCAN_FILE" ]]; then rm -f -- "$SCAN_FILE"; fi
}
trap cleanup EXIT

printf '\n=== Déploiement et test de fiche-paye sur Linux Mint ===\n\n'
printf 'Machine cible : %s\n' "$TARGET"
printf 'Empreinte ED25519 attendue : %s\n\n' "$EXPECTED_ED25519_FP"
printf 'Ce script va :\n'
printf '  1. vérifier l’identité SSH du PC du bas ;\n'
printf '  2. configurer une clé SSH dédiée ;\n'
printf '  3. installer les prérequis Python sur Linux Mint ;\n'
printf '  4. transférer le code et lancer les tests.\n\n'
printf 'Il ne transfère aucun identifiant Google, jeton OAuth ou bulletin de paie.\n'
printf 'Il ne se connecte pas à Gmail et ne modifie aucun courriel.\n\n'
read -r -p 'Continuer ? [O/n] ' answer
case "${answer,,}" in n|non) printf 'Annulé.\n'; exit 0 ;; esac

[[ -d "$PROJECT_DIR" ]] || fail "Dossier projet introuvable : $PROJECT_DIR. Vérifie que tu as extrait l’archive complète."
for cmd in ssh ssh-keyscan ssh-keygen ssh-copy-id ssh-agent ssh-add tar awk; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Commande requise absente : $cmd. Sur CachyOS, installe openssh et tar avec pacman."
done

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
touch "$KNOWN_HOSTS"
chmod 600 "$KNOWN_HOSTS"

# Vérifie la clé d'hôte avant de l'ajouter à known_hosts. Le fingerprint vient
# de la sortie d'installation OpenSSH fournie par l'utilisateur.
SCAN_FILE="$(mktemp)"
printf '\n[1/5] Vérification de l’identité SSH du PC du bas...\n'
if ! ssh-keyscan -T 8 -t ed25519 "$HOST" >"$SCAN_FILE" 2>/dev/null; then
  fail "Impossible de lire la clé SSH de $HOST. Vérifie que les deux PC sont sur le même réseau et que Linux Mint est allumé."
fi
SCANNED_FP="$(ssh-keygen -lf "$SCAN_FILE" -E sha256 2>/dev/null | awk 'NR==1 {print $2}')"
[[ -n "$SCANNED_FP" ]] || fail "La clé SSH du PC cible n’a pas pu être identifiée."
if [[ "$SCANNED_FP" != "$EXPECTED_ED25519_FP" ]]; then
  printf 'Empreinte reçue : %s\n' "$SCANNED_FP" >&2
  printf 'Empreinte attendue : %s\n' "$EXPECTED_ED25519_FP" >&2
  fail "L’empreinte ne correspond pas au PC installé. Aucune connexion n’a été faite. Ne modifie pas la vérification sans contrôler l’empreinte directement sur Linux Mint."
fi
# Remplace seulement l'entrée de l'adresse visée après avoir vérifié son fingerprint.
ssh-keygen -R "$HOST" -f "$KNOWN_HOSTS" >/dev/null 2>&1 || true
cat "$SCAN_FILE" >> "$KNOWN_HOSTS"
chmod 600 "$KNOWN_HOSTS"
printf 'Identité vérifiée : %s\n' "$SCANNED_FP"

printf '\n[2/5] Préparation de la clé SSH dédiée...\n'
if [[ ! -f "$SSH_KEY" ]]; then
  printf 'Une clé dédiée sera créée. Il est conseillé de choisir une phrase secrète.\n'
  ssh-keygen -t ed25519 -a 64 -f "$SSH_KEY" -C "fiche-paye-cachyos-vers-linux-mint"
elif [[ ! -f "$SSH_KEY.pub" ]]; then
  printf 'Clé publique absente : reconstruction à partir de la clé privée existante.\n'
  ssh-keygen -y -f "$SSH_KEY" > "$SSH_KEY.pub"
  chmod 644 "$SSH_KEY.pub"
fi
chmod 600 "$SSH_KEY"
chmod 644 "$SSH_KEY.pub"

# Agent temporaire : permet de saisir la phrase secrète une fois par lancement.
eval "$(ssh-agent -s)" >/dev/null
AGENT_STARTED=1
ssh-add "$SSH_KEY"

printf '\n[3/5] Autorisation de la clé sur Linux Mint...\n'
printf 'Si demandé, saisis le mot de passe du compte Linux Mint « %s ».\n' "$REMOTE_USER"
ssh-copy-id -i "$SSH_KEY.pub" "$TARGET"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" 'printf "Connexion SSH confirmée : "; id -un; printf "\nSystème : "; . /etc/os-release; printf "%s\n" "$PRETTY_NAME"'

printf '\n[4/5] Installation des prérequis sur Linux Mint...\n'
printf 'Saisis le mot de passe sudo de Linux Mint si demandé.\n'
ssh -t "$TARGET" 'sudo apt-get update && sudo apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates'

printf '\n[5/5] Transfert du code, installation Python et tests...\n'
tar \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='credentials.json' \
  --exclude='token.json' \
  -czf - -C "$PROJECT_DIR" . \
| ssh -o BatchMode=yes "$TARGET" 'mkdir -p "$HOME/.local/src/fiche-paye" && tar -xzf - -C "$HOME/.local/src/fiche-paye" --no-same-owner'

ssh -o BatchMode=yes "$TARGET" 'bash -s' <<'REMOTE_SCRIPT'
set -Eeuo pipefail
SRC="$HOME/.local/src/fiche-paye"
VENV="$HOME/.local/share/fiche-paye-venv"

printf '\n--- Diagnostic cible ---\n'
cat /etc/os-release | grep -E '^(PRETTY_NAME|VERSION_ID)=' || true
printf 'Architecture : '; uname -m
printf 'Python système : '; python3 --version
printf 'Source transférée : %s\n' "$SRC"
printf 'Environnement virtuel : %s\n' "$VENV"

mkdir -p "$HOME/.local/share"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -e "$SRC"

printf '\n--- Tests unitaires sur Linux Mint ---\n'
"$VENV/bin/python" -m unittest discover -s "$SRC/tests" -v

printf '\n--- Vérification de la commande ---\n'
"$VENV/bin/fiche-paye" --help >/dev/null
printf 'La commande fiche-paye démarre correctement (--help).\n'
printf '\n--- État de la configuration Gmail (aucun secret affiché) ---\n'
if [[ -s "$HOME/.config/fiche-paye/credentials.json" ]]; then
  printf 'credentials.json : présent\n'
else
  printf 'credentials.json : absent (la configuration Google reste à faire sur la machine cible)\n'
fi
if [[ -s "$HOME/.config/fiche-paye/token.json" ]]; then
  printf 'token.json : présent\n'
else
  printf 'token.json : absent (aucun test Gmail n’a été lancé)\n'
fi
printf '\nSUCCÈS : installation et tests unitaires terminés sur cette machine.\n'
REMOTE_SCRIPT

printf '\n============================================================\n'
printf 'TERMINÉ : Linux Mint a été configuré et les tests ont été lancés.\n\n'
printf 'Pour redéployer tes prochaines modifications, relance simplement :\n'
printf '  cd "%s"\n  ./deploy-test.sh\n\n' "$SCRIPT_DIR"
printf 'La commande sur Linux Mint est installée ici :\n  ~/.local/share/fiche-paye-venv/bin/fiche-paye\n'
printf 'Les résultats ci-dessus concernent les tests unitaires ; le test Gmail réel reste séparé.\n'
