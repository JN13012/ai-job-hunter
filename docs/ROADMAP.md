# AI Job Hunter — Roadmap

Principe : scripts déterministes d'abord, LLM uniquement si nécessaire.
Périmètre initial : alternances IA, cyber/pentest et blockchain, grands groupes
en priorité, France et zones géographiques configurables.

| Mission | Objectif |
| --- | --- |
| M0 | Socle local Python, CLI, uv — réalisé |
| M1 | Greenhouse, normalisation, SQLite — réalisé |
| M2.1 | Lever (global / EU), Ashby, SmartRecruiters — collecte réelle validée ; détails SmartRecruiters partiels |
| M2.2 | La Bonne Alternance : accès officiel, offres et entreprises à potentiel — implémentation à valider |
| M2.3 | Catalogue d'entreprises, sites carrières autorisés, import d'alertes et de liens |
| M3 | Filtres, scoring explicable, sélection, dédoublonnage intersources |
| M4 | Identification d'entreprises cibles (SIRENE et sources autorisées) |
| M5 | Contacts de recrutement professionnels publiés, provenance et minimisation |
| M6 | CV et messages factuels, templates, LLM optionnel, cache et budget |
| M7 | Préparation et envoi supervisé, preuve d'envoi, prévention des doublons |
| M8 | Suivi, relances limitées, planification |
| M9 | Tableau de bord local |

## Politique des sources

- Greenhouse, Lever, Ashby, SmartRecruiters : GET des annonces publiques ; vérifier
  le droit d'accès et les tokens de boards. Collecte ≠ droit de soumission.
- La Bonne Alternance : https://api.apprentissage.beta.gouv.fr/fr/explorer ;
  lire via GET /api/job/v1/search avec clé Bearer personnelle, usage non lucratif ;
  soumission POST réservée aux services habilités, pas à l'usage individuel.
- Welcome to the Jungle, LinkedIn, Indeed : import de liens/alertes autorisés,
  pas de scraping automatisé non autorisé.
- Free-Work : annuaire des ESN et offres via accès permis ; pas d'extraction en masse.
- Sites carrières officiels, SIRENE : vérifier l'autorisation et la provenance.

## Candidatures

Offre sélectionnée ou entreprise cible → dossier factuel → validation humaine →
API habilitée, formulaire Playwright si permis, email professionnel publié,
ou candidature manuelle assistée. Aucun contournement de CAPTCHA, aucun email
deviné, aucun envoi en masse ; arrêt sur protection, erreur ou doute.
Aucune soumission n'est implémentée à M2.1.

### Suivi M2.1 / M3

- Limite SmartRecruiters : filtrer les métadonnées avant enrichissement ciblé,
  distinguer avertissements de quota et erreurs, conserver les détails déjà acquis.
- La Bonne Alternance retourne deux catégories distinctes (`jobs`, `recruiters`) ;
  ne pas transformer les entreprises à potentiel en offres publiées.
