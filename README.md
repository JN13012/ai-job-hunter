# AI Job Hunter

Agent Python de recherche, d'analyse et de préparation de candidatures à des
offres d'emploi et d'alternance. **SCRIPT FIRST, AI ONLY WHEN NECESSARY.**

Le MVP visera les alternances en IA, cybersécurité et blockchain, principalement
dans les grands groupes. **M0 fournit le socle local ; M1 ajoute la collecte
Greenhouse et SQLite.** Aucun LLM, scraping ou envoi de candidature.

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

La CLI, la configuration et SQLite utilisent la bibliothèque standard ; `httpx`
effectue les appels à l'API publique. `pytest` sert aux tests ; Hatchling construit le paquet Python.
`uv.lock` est versionné pour reproduire l'installation. Windows et Linux sont
visés ; une exécution sur Linux ne prouve pas une validation native Windows.

L'architecture est décrite dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Les tests bloquent les connexions réseau et utilisent des fixtures synthétiques.

## Collecter et consulter les offres (M1)

Copier `ajh.example.toml` vers `ajh.toml` comme indiqué ci-dessus. L'exemple
contient uniquement le board réel Cloudflare. Chaque entreprise et son token
doivent être explicitement configurés :

```toml
[greenhouse]
timeout_seconds = 15
request_interval_seconds = 1

[[greenhouse.boards]]
company = "Cloudflare"
board_token = "cloudflare"
```

Depuis le site carrière officiel de l'employeur, suivre une offre vers
`https://job-boards.greenhouse.io/<board_token>/jobs/<id>` (ou l'ancien domaine
`boards.greenhouse.io`) et relever le segment `<board_token>`. Ne pas déduire un
token du nom de l'entreprise. Exemple vérifiable :
[offres Cloudflare](https://www.cloudflare.com/careers/jobs/) et
[offre sur Greenhouse](https://job-boards.greenhouse.io/cloudflare/jobs/7736431).
Les liens d'offres peuvent expirer ; vérifier l'employeur avant d'ajouter un board.
`company` vient de la configuration et cette origine est tracée ; l'API ne
fournit pas systématiquement le nom de l'entreprise.

```console
uv run ajh collect --source greenhouse
uv run ajh jobs list --limit 5
uv run ajh jobs stats
```

Les offres sont enregistrées dans `.ajh/jobs.sqlite3` (ou le `data_dir` configuré).
`list` produit du JSON Lines, avec `null` pour les informations inconnues ;
`stats` retourne un objet JSON avec le total et les comptes par source/board/entreprise.
Le bilan de collecte distingue reçues, nouvelles, mises à jour, inchangées,
doublons et erreurs. Les doublons identiques ne sont pas retraités ; en cas de
doublons contradictoires, la première occurrence est retenue et une erreur signalée.
Les observations progressent même lorsque le contenu est inchangé. La première
observation n'est jamais perdue. Les offres absentes d'une collecte ultérieure
ne sont ni supprimées ni déclarées fermées ; les comptes représentent l'historique
enregistré, pas une garantie d'offres encore ouvertes.

L'[API officielle Greenhouse](https://docs.greenhouse.io/job-board.html) expose
les GET publics sans authentification. M1 fait un seul GET `jobs?content=true`
par board, sans requête par offre, avec au maximum 10 boards par exécution,
au moins 1 seconde entre les appels, un timeout de 15 secondes par défaut et
des limites de 20 Mio / 10 000 offres par réponse. Ces limites sont celles de
l'application, pas un quota annoncé par Greenhouse. Aucun retry ni redirection
automatique ; HTTP 429 arrête la collecte, affiche `Retry-After` si présent,
et exige d'attendre avant une relance manuelle. Configurer uniquement les boards
autorisés, respecter les conditions d'accès de l'employeur et de Greenhouse et
éviter les relances rapprochées.

Un lot valide est écrit dans une transaction par board. Les réponses partielles
et les enregistrements invalides sont signalés ; les offres valides sont conservées
et la commande retourne `1` si une erreur est survenue. Un échec SQLite annule
tout le lot concerné. Le JSON brut de chaque offre, l'endpoint et les dates
d'observation sont conservés ; `updated_at` reste la valeur source, sans date
inventée. Le HTML des descriptions est retiré avec décodage borné des entités,
sans charger de ressource externe. Aucun dédoublonnage entre plateformes.


## M2.1 — Sources ATS supplémentaires

Les commandes suivantes nécessitent une section TOML correspondante et des
boards réels vérifiés sur les sites carrières des entreprises :

~~~bash
uv run ajh collect --source lever
uv run ajh collect --source lever_eu
uv run ajh collect --source ashby
uv run ajh collect --source smartrecruiters
~~~

Chaque section reprend la structure de greenhouse : boards, timeout_seconds,
request_interval_seconds. Les instances Lever EU et globale sont séparées
pour éviter les collisions d'identifiants. Ashby ne collecte pas les offres
explicitement non listées. SmartRecruiters récupère les listes publiques paginées
et jusqu'à 100 détails par board ; le reste est conservé en état partiel.
Ne pas interpréter les offres absentes comme fermées.

Documentation :
- https://github.com/lever/postings-api
- https://developers.ashbyhq.com/docs/public-job-posting-api
- https://developers.smartrecruiters.com/docs/posting-api
- [Roadmap](docs/ROADMAP.md)

Validation réelle M2.1 (16 novembre 2026, WSL Linux, Python 3.12.15) :
127 tests réussis ; 3 025 offres enregistrées, aucun doublon signalé.
Greenhouse 471, Lever 302, Lever EU 13, Ashby 143, SmartRecruiters 2 096.
SmartRecruiters signale volontairement une collecte partielle des détails :
100 détails récupérés au maximum, 1 996 offres restantes sans enrichissement.
La réexécution réelle et la protection contre la perte de détails déjà acquis
lors de collectes partielles restent à vérifier. Windows natif non vérifié.

## M2.2 — La Bonne Alternance (à valider)

Créer une clé de production depuis https://api.apprentissage.beta.gouv.fr/fr/explorer.
La lecture est autorisée avec une clé valide, à usage non lucratif. La soumission
nécessite une habilitation et n'est pas implémentée.

Configurer [lba] dans ajh.toml (voir exemple), avec codes ROME et départements.
La recherche est volontairement ciblée et plafonnée côté serveur ; ne pas la
considérer comme exhaustive. La clé ne figure jamais dans le TOML ni dans Git.

Sous Bash/WSL :

~~~bash
read -rs -p "Clé LBA : " AJH_LBA_API_KEY; echo
export AJH_LBA_API_KEY
uv run ajh collect --source lba
uv run ajh jobs stats
uv run ajh recruiters stats
uv run ajh recruiters list --limit 5
unset AJH_LBA_API_KEY
~~~

Les offres sont intégrées à jobs.sqlite3 ; les entreprises à potentiel sont dans
une table lba_recruiters distincte. Pas de stockage explicite des téléphones ni
recipient_id, pas de POST. Aucune disparition ne signifie une clôture.


## M2.3 — Catalogue et import local (à valider)

~~~bash
uv run ajh catalog --file examples/catalog.toml
uv run ajh collect --source lever --catalog examples/catalog.toml
uv run ajh import --file .ajh/mes-offres.json
uv run ajh jobs stats
~~~

Le catalogue contient cinq boards testés en M2.1 et deux pages alternance
officielles à consulter manuellement. Copier le fichier dans .ajh/ pour
personnaliser les cibles. Les boards d'une source sont sélectionnés explicitement
avec --catalog, en remplacement des boards TOML pour cette exécution seulement ;
maximum dix par collecte. Les cibles manual ne déclenchent aucune requête.

L'import accepte un tableau JSON, voir examples/import.example.json (fictif,
ne pas importer en production). Renseigner source (linkedin, indeed, wttj,
freework, career, other), url, company, title. location, description et
application_url sont facultatifs. Les données doivent être saisies manuellement
ou obtenues via un export autorisé. L'import ne télécharge pas les pages,
ne lit pas la messagerie et n'envoie aucun dossier. L'URL de source est en
provenance_json ; application_url n'est renseignée que si fournie explicitement.
La disponibilité n'est pas vérifiée. Dédoublonnage par source et URL sans
fragment, pas entre plateformes. Limites : 5 Mio et 1000 lignes par fichier.
Ne stocker ni liens confidentiels, ni jetons de session, ni informations personnelles.
