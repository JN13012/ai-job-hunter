# Architecture — M0 à M2.1

Principe : **SCRIPT FIRST, AI ONLY WHEN NECESSARY.** Le traitement déterministe
est le chemin par défaut ; un LLM sera une capacité optionnelle, appelée
explicitement uniquement pour les besoins sémantiques.

## Socle réellement implémenté

- `cli.py` : interface `ajh`, aide et diagnostic local ; codes de sortie pour
  l'utilisation en script, collecte et consultation des offres.
- `config.py` : lecture TOML, validation et résolution des chemins via `pathlib`.
  Configuration immuable ; valeurs par défaut sans fichier obligatoire.
- `models.py` : offre typée, identité interne et empreinte déterministe du contenu.
- `collectors/greenhouse.py` : GET public borné avec `httpx`, sans retry automatique.
- `normalizer.py` : champs inconnus conservés comme tels, retrait contrôlé du HTML,
  ID SHA-256 de `[source, board_token, external_id]`, JSON source conservé.
- `storage.py` : `sqlite3`, schéma local automatique, unicité sur identité source,
  transaction atomique par lot et conservation des dates d'observation en UTC.
- `collection.py` : chaîne de traitement, isolation des erreurs par board et bilan.
- `tests/` : fixtures synthétiques, transports HTTP simulés et réseau réel interdit.

Flux M0 : `ajh doctor → configuration → contrôle Python / dossier local → statut`.
Le diagnostic crée au besoin le répertoire de données et vérifie son écriture.
Flux M1 : `ajh collect → configuration des boards → API Greenhouse → normalisation
→ dédoublonnage dans le board → transaction SQLite → bilan`.
`ajh jobs list/stats` consulte la base locale. Aucun module IA n'est créé.

`httpx` est l'unique dépendance directe d'exécution ; le reste utilise la bibliothèque
standard. Le paquet suit la structure `src/` pour tester le paquet installé par uv.
Les dépendances de test et de construction restent séparées.

L'entreprise provient explicitement du TOML, pas d'une inférence depuis le token.
L'identité reste stable lors des changements de titre ou de description. Une
empreinte du contenu et de sa provenance distingue mises à jour et observations
inchangées ; les dates d'observation en sont exclues. La base conserve l'état
le plus récemment collecté et les bornes de première/dernière observation, sans
historique complet des versions. Les réponses partielles ne déclenchent aucune
suppression ; les offres disparues ne sont pas interprétées comme fermées.

## Responsabilités et extensions

Les collecteurs et la persistance ont été étendus par les étapes M2 à M4
sur cette branche (validation progressive). Les composants de navigation,
remplissage, soumission humaine et suivi M7 restent non implémentés.

| Composant | Responsabilité |
| --- | --- |
| Collectors | Acquisition depuis API publiques et sites carrières autorisés ; conserver source, identifiant et date de collecte. |
| Normalizer | Standardiser les champs, conserver les données sources utiles et dédoublonner sans perdre la provenance. |
| Matcher | Filtres explicites et score déterministe explicable à partir du profil candidat. |
| Storage | SQLite via `sqlite3` : offres, résultats de traitement, historique des candidatures et réponses. |
| AI | Interface facultative pour l'analyse sémantique ; appels ciblés, traçables et désactivés par défaut. |
| Applications | Orchestrer la file, préparer CV et dossiers factuels, ouvrir et remplir les formulaires autorisés ; arrêter impérativement avant le dernier clic humain. |
| CLI | Déclencher progressivement ces traitements locaux et afficher leurs résultats. |

Flux cible :

```text
Sources autorisées → Collectors → Normalizer → Storage (SQLite)
Profil candidat + offres normalisées → Matcher → résultats dans Storage
Besoin sémantique explicite → AI optionnelle → analyse tracée dans Storage
Offres retenues + profil factuel → Applications → préparation et file
→ Playwright local → ouverture automatique et remplissage autorisés
→ barrière de soumission → navigateur visible et clic humain sur le site
→ observation d'une confirmation → suivi SQLite
                   ↘ Extension Chrome optionnelle (session existante)
```

Chaque étape conserve les liens avec l'offre source. Filtres, dédoublonnage,
scoring, persistance et suivi n'ont pas besoin de LLM. Les inconnues restent
explicites ; aucune compétence ne doit être inventée. Les capacités seront
ajoutées au fil des besoins, sans framework de plugins ni fichiers vides.

M1 s'arrête à la collecte et à la persistance. M2.1 étend les mêmes interfaces
aux ATS Lever (global/EU), Ashby et SmartRecruiters, sans migration SQLite.
La collecte, la normalisation, les transactions et le bilan restent partagés.
Les détails SmartRecruiters sont limités aux 100 premiers résultats par board ;
les autres restent partiels et une erreur de limite est signalée.
Les profils et candidatures restent hors périmètre. Voir [ROADMAP.md](ROADMAP.md).

## M2.2 — La Bonne Alternance

GET authentifié par clé d'environnement AJH_LBA_API_KEY vers l'API officielle.
Le profil de recherche est explicitement configuré (ROME, départements et niveau
facultatif). Une requête par collecte, pas de retry automatique. Les offres
utilisent l'identité partenaire + identifiant partenaire ; les entreprises à
potentiel utilisent une table additive lba_recruiters, sans confusion avec jobs.
Les deux lots sont transactionnels séparément. Les avertissements de couverture
ne signifient pas une panne. Les listes plafonnées ne sont pas exhaustives.
La collecte réelle et l'exécution des tests restent à valider.


## M2.3 — Catalogue et import local

catalog.py valide les liens ATS et tokens déclarés, sélectionne les boards
à collecter avec les connecteurs existants, sans requête de découverte.
Les pages de carrière manuelles ne déclenchent aucun accès réseau.
imports.py normalise les annonces JSON sélectionnées ou exportées avec
autorisation, utilise JobStore et conserve leur provenance et disponibilité
non vérifiée. Aucun shell, navigateur, email, scraping ou LLM.
Le dédoublonnage intersources, l'import direct de messagerie et les nouveaux
connecteurs spécifiques (non ATS) restent à développer. Validation locale requise.


## M3 — Matcher déterministe

matcher.py charge un profil TOML indépendant, normalise accents et casse,
détecte les termes aux frontières de mots, examine le type de contrat explicite
et classe les offres SQLite en match/review/excluded. Les données d'origine
restent inchangées. Résultats recalculés à la lecture, pas de cache ni de
migration. Regroupement strict de doublons intersources en lecture uniquement.
Les offres SmartRecruiters sans description sont à vérifier (sauf exclusion
explicite). Enrichissement ciblé, conservation des anciens détails, filtrage
des dates et niveaux de diplôme restent à développer. Validation locale requise.

## M4 — Découverte d'entreprises

company_discovery.py : client GET borné de l'API publique Recherche
d'Entreprises, recherche textuelle explicitement ciblée, pagination plafonnée,
filtres administratifs (actives et hors entrepreneurs individuels), rythme
réduit et arrêt sans retry sur 429. Les données sont enregistrées dans
company_targets (SIREN unique), table additive à SQLite, sans altérer jobs.
On ne déduit pas l'appétence pour l'alternance du code NAF. Un indicateur
lba_potential est activé uniquement si un SIRET LBA rejoint le SIREN,
et catalog_sources uniquement si la cible déclare un SIREN explicite.
Pas de liaison floue ni de collecte de dirigeants ou de coordonnées personnelles.


## Architecture cible M7 : autonomie avec dernier clic humain

Spécification, **non implémentée**. Le collecteur et le matcher alimentent
une file persistante de candidatures, sans intervention humaine pour trouver
les annonces. Le moteur Playwright lance les URL des annonces, valide
leurs destinations, prépare et remplit les formulaires uniquement là où
l'automatisation est autorisée. L'extension Chrome MV3 reste facultative
pour les sessions existantes, sans extraction de cookies ni mots de passe.

Composants futurs :
- ApplicationQueue : file, annulation, reprise, états et idempotence par offre.
- ApplicationPreparer : CV et réponses factuelles ; LLM facultatif.
- BrowserRunner : session Playwright isolée, limites de temps et de domaine.
- SiteAdapters : formulaires testés et autorisés, sans contournement des protections.
- SubmissionGuard : arrêt fail-closed avant tout submit ou étape équivalente,
  y compris clavier, formulaire multi-pages ou script déclenché par automation.
- ApplicationObserver : preuve explicite après clic humain ; sinon à confirmer.
- ChromeExtension : permissions minimales, connexion locale authentifiée,
  aucune capacité de soumission automatique.

États : discovered → matched → queued → prepared → filling →
ready_for_human_submit → submitted_confirmed. Autres issues : blocked,
needs_manual_action, unconfirmed, cancelled, failed. Remplir un formulaire
ne signifie jamais l'avoir envoyé.

Une validation humaine est toujours individuelle et faite directement
sur le bouton « Envoyer » du site dans une fenêtre visible. Aucune API
de soumission automatique n'est prévue. Le backend local doit rester sur
loopback, contrôler les origines, les droits et les journaux sensibles ;
ni profil Chrome ni secrets de session n'y sont copiés.

Spécification complète : [APPLICATION_AUTOMATION.md](APPLICATION_AUTOMATION.md).

## M5.1 — Registre local de recrutement

Le module contacts.py crée une table additive recruitment_contacts, liée
par SIREN aux entreprises company_targets de M4. Import JSON explicite,
provenance HTTPS obligatoire, déduplication et trois statuts : pending,
approved, blocked. La revue est humaine. Le blocage efface les coordonnées
et conserve une empreinte de refus pour éviter une réimportation identique.
Aucune collecte de contact sur Internet et aucun envoi ne sont implémentés.
Voir [CONTACTS.md](CONTACTS.md).

## M5.2 — Qualification statique des canaux de candidature

`catalog_channels.py` classe les entrées du catalogue, sans HTTP : page
carrières, board ATS, formulaire déclaré. `contacts catalog --file` produit
un bilan JSON Lines. L'option `--register` ne conserve que les formulaires
explicitement déclarés et rattachés au SIREN d'une entreprise M4.
Les entrées sont persistées via le registre M5.1 : déduplication, statut
`pending` et maintien des oppositions. Une URL déclarée ne prouve pas
que le formulaire existe ou autorise une automatisation.
