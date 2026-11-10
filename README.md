# AI Job Hunter

Agent Python de recherche, d'analyse et de préparation de candidatures à des
offres d'emploi et d'alternance. **SCRIPT FIRST, AI ONLY WHEN NECESSARY.**

Le MVP visera les alternances en IA, cybersécurité et blockchain, principalement
dans les grands groupes. **M0 livre uniquement le socle local** : aide CLI,
configuration et diagnostic. Aucun appel réseau, LLM, scraping ou envoi de candidature.

## Installation et démarrage

Prérequis : Git et [uv](https://docs.astral.sh/uv/getting-started/installation/).
Depuis un clone du dépôt, avec un terminal Linux ou Windows (PowerShell) :

```console
uv sync
uv run ajh --help
uv run ajh doctor
uv run pytest -q
```

`uv sync` installe Python 3.12 si nécessaire, crée automatiquement `.venv` et
installe le projet et les dépendances de développement selon `uv.lock`.
Le projet accepte Python >= 3.12 ; `.python-version` choisit 3.12 par défaut.
Aucune activation manuelle de l'environnement n'est nécessaire avec `uv run`.
Le premier démarrage peut nécessiter un accès réseau pour les téléchargements.

## Configuration locale

Sans fichier, les valeurs par défaut s'appliquent : données dans `.ajh/` du
dossier courant. Pour personnaliser, copier `ajh.example.toml` vers `ajh.toml`
(`cp ajh.example.toml ajh.toml` sous Linux, `Copy-Item ajh.example.toml ajh.toml`
sous PowerShell), puis modifier :

```toml
[paths]
data_dir = ".ajh"
```

Le fichier `ajh.toml` du dossier courant est chargé automatiquement. Les chemins
relatifs sont résolus par rapport au dossier de ce fichier. Les chemins absolus
et `~` sont également acceptés. Sous Windows, utiliser des `/` ou une chaîne TOML
entre apostrophes pour les chemins contenant des `\`.

Pour sélectionner explicitement un fichier :

```console
uv run ajh --config chemin/vers/ajh.toml doctor
```

`doctor` vérifie Python >= 3.12, lit et valide la configuration, crée le dossier
de données au besoin et teste l'écriture avec un fichier temporaire supprimé
ensuite. Il ne crée aucune base SQLite. Un diagnostic réussi renvoie `0`, un
échec `1` et une erreur d'arguments `2`. Une configuration explicite absente,
un TOML invalide ou une option inconnue provoque un échec avec un message.

`ajh.toml`, `.ajh/`, les fichiers `.env` et les bases locales sont ignorés par
Git. Si `data_dir` est personnalisé, utiliser un emplacement hors du dépôt ou
ajouter ce chemin au `.gitignore`. Les CV, profils et secrets restent locaux.

## Développement

La CLI utilise uniquement la bibliothèque standard (`argparse`, `tomllib`,
`pathlib`). `pytest` sert aux tests ; Hatchling construit le paquet Python.
`uv.lock` est versionné pour reproduire l'installation. Windows et Linux sont
visés ; une exécution sur Linux ne prouve pas une validation native Windows.

L'architecture et la suite proposée sont décrites dans
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). M1 n'est pas implémenté.
