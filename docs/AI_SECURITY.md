# Sécurité IA — M6

Statut : implémenté sur branche ; validation locale et revue nécessaires.

## Principe

Optimiser les mots-clés ATS à partir des compétences déclarées, sans transformer
une compétence en cours d'apprentissage en compétence acquise.
Ne jamais inventer une certification, un diplôme, un employeur ou une expérience.
Les déclarations du profil restent sous la responsabilité du candidat.

## Défenses appliquées

| Contrôle | Mesure |
| --- | --- |
| Entrées | Profil TOML strict, taille et types bornés ; distinctions skills / learning |
| Annonces externes | Considérées comme données non fiables, jamais comme instructions |
| Moteur | Brouillons déterministes, aucun LLM par défaut |
| IA facultative | Ollama loopback, sans outil, navigateur, ni soumission |
| Budget | Réservation de tokens estimés et plafond par requête |
| Cache | Clé de version, prompt et modèle ; stockage local privé |
| Vie privée | Reformulation limitée aux phrases professionnelles, pas au CV complet |
| Sortie | Suggestion IA séparée, jamais promotion automatique |
| Fichiers | Dossiers dans .ajh/drafts, ignorés par Git, chmod 0600 sous Linux |
| Publication | Manifeste non validé et aucun envoi automatique |

## Niveaux de compétences

- skills : compétences que le candidat déclare pratiquer.
- learning : technologies en cours d'acquisition, toujours étiquetées.
- certifications : uniquement les titres réellement obtenus déclarés.
- experience et education : formulations factuelles rédigées par le candidat.

Il est acceptable de valoriser une expérience en projet ou une compétence
en progression. M6 ne fabrique pas de preuves de formation ou d'emploi.

## Menaces et limites

Une prompt injection dans une annonce est possible mais secondaire.
La priorité est l'intégrité des candidatures et le cloisonnement de l'IA.
Le prompt défensif ne garantit pas des sorties exactes : revue humaine.
Le modèle local pourrait reformuler de façon trompeuse ; ne jamais utiliser
sa sortie sans vérification. Le port Ollama 11434 doit rester local.
Le cache peut contenir des faits professionnels ; garder .ajh privé.
Les permissions Windows et le cycle de rétention sont à revoir avant M7.

## Suivi M7

Prévoir contrôles d'origine, refus de scripts et commandes provenant des pages,
barrière anti-submit, protection des secrets de session, formulaires autorisés
et validation manuelle de chaque dernière action Envoyer.

Tests : uv run pytest -q
