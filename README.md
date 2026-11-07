# fiche-paye

Petit outil Linux qui recherche dans Gmail les PDF envoyés par `admin@ouihelp.fr`, lit leur texte localement et classe uniquement les bulletins dont la période correspond au mois précédent.

Nom par défaut : `MM-D_NOM-Prénom.pdf`, où `D` est le dernier chiffre de l'année. Exemple : `09-6_MARTIN-Jeanne.pdf` pour septembre 2026. Pour utiliser deux chiffres d'année (`09-26_...`), passer `--year-digits 2`.

## Installation rapide (toute machine Linux)

Lance simplement depuis le dossier du projet :

```bash
./install.sh
```

Le script :
1. Installe `pipx` si absent (détecte pacman / apt / dnf)
2. Installe la commande `fiche-paye` globalement (disponible depuis n'importe quel répertoire)
3. Place `credentials.json` dans `~/.config/fiche-paye/` avec les bonnes permissions
4. Crée le dossier `~/Documents/Fiches-de-paie/`

### Fournir le fichier credentials.json au script

Le script cherche le fichier dans l'ordre suivant :

| Priorité | Source | Remarque |
|----------|--------|----------|
| 1 | `credentials.json.gpg` à la racine du dépôt | Chiffré GPG, **peut être commité** |
| 2 | `credentials.json` à la racine du dépôt | Non commité (exclu par `.gitignore`) |
| 3 | Clé USB montée (scan automatique) | Cherche dans `/run/media`, `/media`, `/mnt` |
| 4 | Saisie manuelle du chemin | Demandé si rien n'est trouvé |

**Pour chiffrer le fichier avec GPG (recommandé) :**
```bash
gpg --symmetric --cipher-algo AES256 -o credentials.json.gpg credentials.json
```
Ensuite `credentials.json.gpg` peut être versionné dans le dépôt ; le script le déchiffrera automatiquement au prochain `./install.sh`.

---

## Sécurité et limites

- Gmail est consulté avec l'autorisation OAuth `gmail.readonly`. Le programme ne modifie, n'envoie et ne supprime aucun courriel.
- Les PDF sont téléchargés en mémoire et écrits localement uniquement s'ils correspondent au mois ciblé, ou s'ils nécessitent une vérification.
- L'extraction PDF se fait localement avec `pypdf`, sans OCR ni service externe.
- Les fichiers et le jeton OAuth sont créés avec des permissions limitées au compte local autant que possible.
- Ne partage pas `credentials.json`, `token.json` ou les bulletins de paie.
- Les modèles de bulletins varient. Si le nom ou la période n'est pas reconnu, le script ne devine pas : il ignore le bulletin ou le place dans `A_VERIFIER` selon le cas. Le parseur de nom devra éventuellement être ajusté à partir d'un bulletin anonymisé.

## Installation (Arch/CachyOS/Linux)

Depuis le dossier du projet :

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

Cela crée la commande `fiche-paye` dans `.venv/bin/`. Pour la rendre directement disponible dans le terminal sans activer l'environnement à chaque fois, tu peux aussi installer l'outil avec `pipx install .` depuis ce dossier, si `pipx` est installé.

## Configuration OAuth Google (à faire une fois)

1. Ouvre https://console.cloud.google.com/ et crée ou sélectionne un projet.
2. Active **Gmail API** dans « API et services ».
3. Dans Google Auth Platform, configure la marque et l'audience. Pour un compte Gmail personnel et une application non publiée, sélectionne **Externe** si demandé et ajoute le compte Google autorisé dans **Utilisateurs tests**.
4. Dans **Clients**, crée un client OAuth de type **Application de bureau** et télécharge le JSON.
5. Place-le à l'emplacement suivant :

```bash
mkdir -p ~/.config/fiche-paye
chmod 700 ~/.config/fiche-paye
cp ~/Téléchargements/client_secret_*.json ~/.config/fiche-paye/credentials.json
chmod 600 ~/.config/fiche-paye/credentials.json
```

Le nom précis du fichier téléchargé dépend de Google ; adapte le chemin du `cp` si nécessaire. Le premier lancement ouvre le navigateur pour que le titulaire de la boîte Gmail choisisse le compte et autorise la lecture. Le jeton local sera créé sous `~/.config/fiche-paye/token.json`.

## Utilisation

Toujours commencer par la simulation :

```bash
.venv/bin/fiche-paye --dry-run
```

Puis, si les périodes et noms proposés sont corrects :

```bash
.venv/bin/fiche-paye
```

Par défaut, le script vise le mois calendaire précédent et enregistre dans `~/Documents/Fiches-de-paie/AAAA/`.

Quelques options :

```bash
# Traiter explicitement septembre 2026
.venv/bin/fiche-paye --month 2026-09 --dry-run

# Employer deux chiffres pour l'année
.venv/bin/fiche-paye --year-digits 2 --dry-run

# Si le nom du salarié n'est pas extrait correctement, fournir un nom de repli
.venv/bin/fiche-paye --employee-name "MARTIN-Jeanne" --dry-run

# Choisir un autre dossier de destination
.venv/bin/fiche-paye --output "$HOME/Paie" --dry-run

# Révoquer localement le jeton enregistré et refaire la connexion la prochaine fois
.venv/bin/fiche-paye --reauth --dry-run
```

Pour obtenir la commande `fiche-paye` sans préfixe `.venv/bin/`, active l'environnement :

```bash
source .venv/bin/activate
fiche-paye --dry-run
```

## Tests locaux

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Les tests couvrent le parsing des dates, le changement d'année et le nom de fichier. Ils ne remplacent pas un essai sur un bulletin anonymisé réel.
