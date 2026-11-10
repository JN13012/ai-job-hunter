# Architecture — M0

Principe : **SCRIPT FIRST, AI ONLY WHEN NECESSARY.** Le traitement déterministe
est le chemin par défaut ; un LLM sera une capacité optionnelle, appelée
explicitement uniquement pour les besoins sémantiques.

## Socle réellement implémenté

- `cli.py` : interface `ajh`, aide et diagnostic local ; codes de sortie pour
  l'utilisation en script. Aucun orchestrateur anticipé.
- `config.py` : lecture TOML, validation et résolution des chemins via `pathlib`.
  Configuration immuable ; valeurs par défaut sans fichier obligatoire.
- `tests/` : configuration, erreurs, CLI et diagnostic du système de fichiers.

Flux M0 : `ajh doctor → configuration → contrôle Python / dossier local → statut`.
Le diagnostic crée au besoin le répertoire de données et vérifie son écriture.
Aucune base SQLite, collecte ou interface IA n'est encore créée.

La bibliothèque standard suffit à ce stade. Le paquet suit la structure `src/`
pour tester le paquet installé par uv. Les dépendances de test et de construction
sont séparées des dépendances d'exécution, qui sont vides.

## Responsabilités futures (non implémentées)

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

## M1 recommandé, à cadrer ensuite

Livrer une première tranche déterministe : schéma d'offre minimal avec provenance,
un collecteur pour une API publique autorisée, normalisation et dédoublonnage,
puis persistance SQLite et commande locale d'import. Tester avec des fixtures
hors ligne, notamment les doublons et erreurs de source. Le scoring explicable
viendra après définition du profil et des critères. Aucun envoi ni LLM nécessaire
pour cette première tranche.
