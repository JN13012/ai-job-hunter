# M7 — Candidatures autonomes avec dernier clic humain

**Statut : spécification validée, code non implémenté.**

## Objectif

AI Job Hunter doit travailler seul autant que possible : chercher, découvrir,
dédupliquer et classer les offres, préparer le dossier, ouvrir le formulaire
et renseigner les champs compatibles. L'utilisateur **ne cherche pas et
n'ouvre pas les offres manuellement** en fonctionnement nominal. Il contrôle
la candidature prête puis clique personnellement sur **« Envoyer »**.

Le bot **ne clique jamais** sur l'action finale, ne poste pas de dossier
par API et ne traite pas une approbation préalable comme une autorisation
d'envoyer. L'arrêt est individuel pour chaque candidature.

## Choix d'architecture

- Orchestrateur Python local et file persistante SQLite.
- Playwright moteur principal de navigation et remplissage autorisés :
  recherche en amont via nos collecteurs, puis ouverture automatique d'URL
  depuis la file, contexte de navigateur isolé par session.
- Navigateur visible et formulaire complet pour le dernier clic humain.
  Une page uniquement headless ne peut pas recueillir ce geste.
- Extension Chrome Manifest V3 en complément ultérieur si l'accès à la
  session existante est nécessaire et permis ; elle communiquera avec une
  API locale authentifiée, sans récupération de mots de passe ou cookies.
- Scripts déterministes d'abord, IA facultative pour reformuler des
  messages sans inventer de qualifications ni réponses personnelles.

## Workflow

1. **Recherche autonome** : collecteurs officiels / imports permis,
   recherches planifiées, aucune navigation manuelle préalable.
2. **Sélection** : matcher, statut du contrat, localisation, rôle et
   dédoublonnage ; exclusion des offres déjà candidaturées.
3. **Préparation** : CV approprié, lettre, pièces jointes et réponses
   issus des faits explicitement connus du profil candidat.
4. **File d'attente** : identifiant unique offre + destination, gestion des
   reprises et annulations, pas de duplication en cas de redémarrage.
5. **Navigation** : Playwright ouvre lui-même l'URL, contrôle le domaine,
   les redirections, l'authentification et les autorisations du site.
6. **Remplissage** : adaptateur propre à la plateforme, mapping des champs,
   vérification, gestion prudente des étapes intermédiaires non soumissives.
7. **Arrêt obligatoire** : formulaire visible et complet ; état
   ready_for_human_submit ; aucune action de soumission automatisée.
8. **Clic humain** : sur le vrai bouton final du site. Le bot observe
   la confirmation et met à jour SQLite, ou signale unconfirmed si
   l'envoi n'est pas prouvé.

Plusieurs dossiers peuvent être préparés à l'avance ; la validation est
toujours individuelle. Aucun bouton « approuver tous » ou envoi groupé.

## Compatibilité par plateforme

| Cas | Recherche | Remplissage | Finalisation |
| --- | --- | --- | --- |
| ATS et sites carrières autorisant l'automatisation | Collecteur/API permis | Playwright, adaptateur testé | Clic humain |
| La Bonne Alternance | GET officiel selon habilitation | Formulaire si automatisation permise | Clic humain, pas d'API POST |
| LinkedIn, Indeed, HelloWork ou sites interdisant des bots | Imports ou intégrations autorisées | Aucune automatisation non autorisée | Assistance manuelle |
| Site inconnu, MFA, CAPTCHA, protection ou doute | Source permise si possible | Arrêt en mode fail-closed | Intervention humaine |

Un connecteur de lecture ne donne **aucun** droit de remplir ou soumettre.
Le support est décidé par site et par flux. Ne pas contourner les limites
contractuelles et techniques, ni résoudre des défis anti-bot automatiquement.

## Barrière de soumission

Tout élément qui déclenche un dépôt irréversible doit être traité comme
un envoi final : bouton Submit, raccourci Enter, formulaire qui se soumet
automatiquement, requête POST, modal de confirmation ou étape multi-page
ambiguë. Il faut **empêcher le bot** de les déclencher. Si une étape
intermédiaire a un effet incertain, arrêter avant cette étape.

Les adaptateurs ne sont déclarés compatibles qu'après tests de non-envoi
en présence d'erreur, timeout, DOM modifié, double action et reprise.
Une page inconnue reste en préparation / assistance, jamais en
automatisation supposée sûre.

## États métier et preuves

| État | Signification |
| --- | --- |
| discovered / matched | Offre collectée et pertinente |
| queued / prepared | Candidate retenue ; dossier factuel préparé |
| filling | Remplissage de formulaire permis en cours |
| ready_for_human_submit | Formulaire complet, automatisation suspendue |
| submitted_confirmed | Confirmation explicite observée après clic humain |
| unconfirmed | Geste humain ou résultat insuffisant pour prouver l'envoi |
| needs_manual_action | Connexion, question, consentement ou fichier manquant |
| blocked | Restriction du site, CAPTCHA/MFA ou domaine imprévu |
| cancelled / failed | Annulation ou échec, sans présumer de l'envoi |

Horodater les transitions et conserver un identifiant de destination,
des événements minimaux et une preuve de confirmation lorsqu'elle existe.
Ne pas conserver les CV complets, secrets de session, cookies ou données
personnelles dans des journaux de diagnostic.

## Sécurité et confidentialité

Le serveur local écoutera uniquement sur loopback, avec authentification
de l'extension, vérification de l'origine et protection CSRF. Limiter
les permissions Chrome aux domaines nécessaires. Les mots de passe, cookies,
tokens et profils de navigateur restent hors du backend et de SQLite.
Le bot refuse les redirections non autorisées, pièces jointes inconnues,
questionnaires imprévus, CAPTCHA et MFA non résolus par l'utilisateur.
Aucune automatisation de soumission via HTTP direct n'est prévue.

## Plan de réalisation

- **M7.1** : orchestrateur et file SQLite, états, idempotence, reprise/annulation.
- **M7.2** : navigateur Playwright isolé ouvrant lui-même les offres ;
  vérifications URL et affichage visible avant le dernier clic.
- **M7.3** : adaptateurs versionnés de formulaires et remplissage prudent.
- **M7.4** : CV et fichiers locaux, réponses contrôlées, IA facultative.
- **M7.5** : garde de soumission, clic humain obligatoire, confirmation
  observable et suivi des candidatures.
- **M7.6** : extension Chrome optionnelle, permissions minimales, backend
  local authentifié et mêmes règles anti-envoi.

## Critères d'acceptation

1. Sur un site de test autorisé, le bot va d'une offre collectée à une
   candidature préparée et remplie sans recherche ou navigation humaine.
2. Le bot n'émet jamais un submit, même en cas de timeout, changement de
   DOM, double-clic, redémarrage ou étape multi-page ambiguë.
3. L'utilisateur voit la vraie page de candidature et effectue le
   dernier clic lui-même ; il ne clique pas dans une simulation.
4. Un formulaire rempli n'est jamais compté comme envoyé. L'état envoyé
   exige une confirmation vérifiable ; à défaut, unconfirmed.
5. Le bot s'arrête sans contournement en cas de protection ou de flux
   interdit ; aucun envoi en masse ni duplication.
6. Le code M7 et les tests sont exécutés localement avant toute fusion.

## M7.3 — Statut intermédiaire

Des adaptateurs déterministes peuvent renseigner des formulaires statiques
autorisés, en navigateur visible et JavaScript désactivé, sans clic ni
soumission. Le workflow multi-étapes et les pièces jointes restent
non implémentés. Voir [FORM_FILLING.md](FORM_FILLING.md).

## M7.4 — État intermédiaire

CV et lettres sont générables en PDF local, puis sélectionnables uniquement
dans les champs de fichiers explicitement déclarés. Les empreintes PDF
sont vérifiées à chaque réservation et avant le remplissage. Le réseau reste
bloqué après le GET HTML initial ; aucune soumission n'est effectuée.
Voir [ATTACHMENTS.md](ATTACHMENTS.md).

## M7.5 — Première étape de suivi humain

L'implémentation actuelle distingue transfert manuel, clic déclaré sans
confirmation et réception attestée par l'utilisateur à l'aide d'une preuve
locale. Elle ne dispose pas encore d'un navigateur conservant le formulaire
prérempli jusqu'au clic de l'utilisateur : le contexte Playwright M7.3 se
ferme volontairement avant la finalisation. Aucune preuve n'est automatiquement
validée auprès de l'ATS. Voir [HUMAN_SUBMISSION.md](HUMAN_SUBMISSION.md).
