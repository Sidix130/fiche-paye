#!/usr/bin/env bash
# =============================================================================
# install.sh — Installation automatisée de fiche-paye
#
# Usage depuis le dossier du dépôt :
#   ./install.sh
#
# Ce script :
#   1. Installe pipx si absent (pacman / apt / pip)
#   2. Installe la commande globale `fiche-paye` via pipx
#   3. Cherche credentials.json (dans le dépôt ou sur clé USB montée)
#      et le place au bon endroit avec les bonnes permissions
#   4. Crée le dossier de destination des fiches de paie
# =============================================================================
set -euo pipefail

# --- Couleurs -----------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${BOLD}==>${NC} $*"; }

# --- Chemins ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/fiche-paye"
CREDS_DEST="$CONFIG_DIR/credentials.json"
OUTPUT_DIR="$HOME/Documents/Fiches-de-paie"

# =============================================================================
# 1. PIPX : installation si absent
# =============================================================================
step "Vérification de pipx"
if ! command -v pipx &>/dev/null; then
    warn "pipx non trouvé. Installation..."

    if command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm python-pipx
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y pipx
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y pipx
    elif command -v pip &>/dev/null; then
        pip install --user pipx
        export PATH="$HOME/.local/bin:$PATH"
    else
        error "Impossible d'installer pipx automatiquement. Installe-le manuellement : https://pipx.pypa.io"
    fi

    # Assurer que ~/.local/bin est dans le PATH pour la session courante
    export PATH="$HOME/.local/bin:$PATH"
    pipx ensurepath --force || true
fi
info "pipx $(pipx --version) disponible"

# =============================================================================
# 2. INSTALLATION de fiche-paye via pipx
# =============================================================================
step "Installation de fiche-paye"
if pipx list 2>/dev/null | grep -q "fiche.paye"; then
    warn "fiche-paye déjà installé. Mise à jour..."
    pipx install --force "$SCRIPT_DIR"
else
    pipx install "$SCRIPT_DIR"
fi
info "Commande 'fiche-paye' disponible globalement"

# =============================================================================
# 3. DOSSIER DE CONFIGURATION (permissions strictes)
# =============================================================================
step "Création du dossier de configuration"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
info "Dossier config : $CONFIG_DIR (chmod 700)"

# =============================================================================
# 4. CREDENTIALS.JSON : détection et placement sécurisé
# =============================================================================
step "Recherche du fichier credentials.json"

place_credentials() {
    local src="$1"
    cp "$src" "$CREDS_DEST"
    chmod 600 "$CREDS_DEST"
    info "credentials.json placé dans $CREDS_DEST (chmod 600)"
}

if [[ -f "$CREDS_DEST" ]]; then
    info "credentials.json déjà présent dans $CREDS_DEST — aucune action."

# --- a) Fichier GPG chiffré dans le dépôt (recommandé pour versionner) --------
elif [[ -f "$SCRIPT_DIR/credentials.json.gpg" ]]; then
    info "Fichier GPG détecté : $SCRIPT_DIR/credentials.json.gpg"
    if ! command -v gpg &>/dev/null; then
        error "gpg est requis pour déchiffrer credentials.json.gpg. Installe gpg."
    fi
    gpg --quiet --decrypt "$SCRIPT_DIR/credentials.json.gpg" > "$CREDS_DEST"
    chmod 600 "$CREDS_DEST"
    info "credentials.json déchiffré et placé dans $CREDS_DEST (chmod 600)"

# --- b) Fichier en clair dans le dépôt (non commité, exclus par .gitignore) --
elif [[ -f "$SCRIPT_DIR/credentials.json" ]]; then
    warn "credentials.json en clair trouvé dans le dépôt (non commité)."
    place_credentials "$SCRIPT_DIR/credentials.json"

# --- c) Recherche sur une clé USB montée -------------------------------------
else
    warn "Aucun credentials.json trouvé dans le dépôt. Recherche sur clés USB..."
    found_usb=""

    # Points de montage courants selon les distros Linux
    for mount_base in "/run/media/$USER" "/media/$USER" "/mnt"; do
        if [[ -d "$mount_base" ]]; then
            while IFS= read -r -d '' candidate; do
                info "Trouvé sur USB : $candidate"
                found_usb="$candidate"
                break 2
            done < <(find "$mount_base" -maxdepth 3 -name "credentials.json" -print0 2>/dev/null)
        fi
    done

    if [[ -n "$found_usb" ]]; then
        place_credentials "$found_usb"
    else
        # --- d) Saisie manuelle du chemin -------------------------------------
        warn "Aucune clé USB détectée."
        echo ""
        echo "  Place ton credentials.json dans UNE de ces sources, puis relance install.sh :"
        echo "    • $SCRIPT_DIR/credentials.json          (non commité)"
        echo "    • $SCRIPT_DIR/credentials.json.gpg      (chiffré GPG, peut être commité)"
        echo "    • Sur une clé USB montée dans /run/media, /media ou /mnt"
        echo ""
        echo "  Ou saisis maintenant le chemin complet du fichier (Entrée pour ignorer) :"
        read -r -p "  Chemin : " manual_path
        if [[ -n "$manual_path" && -f "$manual_path" ]]; then
            place_credentials "$manual_path"
        else
            warn "Installation complète SANS credentials.json."
            warn "Tu pourras le placer manuellement dans : $CREDS_DEST"
        fi
    fi
fi

# =============================================================================
# 5. DOSSIER DE DESTINATION DES FICHES DE PAIE
# =============================================================================
step "Création du dossier de classement"
mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"
info "Répertoire fiches de paie : $OUTPUT_DIR (chmod 700)"

# =============================================================================
# RÉSUMÉ
# =============================================================================
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║            Installation terminée ✓                  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Commande disponible  : ${GREEN}fiche-paye${NC}"
echo -e "  Config               : ${GREEN}$CONFIG_DIR${NC}"
echo -e "  Fiches de paie       : ${GREEN}$OUTPUT_DIR${NC}"
echo ""
echo -e "  Commencer par une simulation :"
echo -e "  ${BOLD}fiche-paye --dry-run${NC}"
echo ""

# Indique si un nouveau shell est nécessaire pour que PATH soit actif
if ! command -v fiche-paye &>/dev/null; then
    warn "Lance un nouveau terminal (ou 'source ~/.bashrc') pour activer la commande fiche-paye."
fi
