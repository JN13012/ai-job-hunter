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

## Responsabilités et extensions futures

Collectors, Normalizer, Storage et CLI existent pour la tranche Greenhouse M1.
Matcher, AI et Applications restent non implémentés.

| Composant | Responsabilité |
| --- | --- |
| Collectors | Acquisition depuis API publiques et sites carrières autorisés ; conserver source, identifiant et date de collecte. |
| Normalizer | Standardiser les champs, conserver les données sources utiles et dédoublonner sans perdre la provenance. |
| Matcher | Filtres explicites et score déterministe explicable à partir du profil candidat. |
| Storage | SQLite via `sqlite3` : offres, résultats de traitement, historique des candidatures et réponses. |
| AI | Interface facultative pour l'analyse sémantique ; appels ciblés, traçables et désactivés par défaut. |
| Applications | Préparer CV et dossiers depuis les faits du profil ; suivre les états et réponses ; exiger une validation humaine avant tout envoi. |
| CLI | Déclencher progressivement ces traitements locaux et afficher leurs résultats. |

Flux cible :

```text
Sources autorisées → Collectors → Normalizer → Storage (SQLite)
Profil candidat + offres normalisées → Matcher → résultats dans Storage
Besoin sémantique explicite → AI optionnelle → analyse tracée dans Storage
Offres retenues + profil factuel → Applications → préparation
→ validation humaine → envoi sur plateforme compatible → suivi dans Storage
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
