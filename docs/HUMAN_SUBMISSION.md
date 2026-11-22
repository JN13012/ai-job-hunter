# M7.5 — Transfert humain et suivi de l'envoi

**Statut : implémenté sur branche, tests et intégration locale à exécuter.**

## Limite technique fondamentale

M7.2–M7.4 utilise Chromium dans un contexte éphémère, JavaScript désactivé,
une seule requête GET autorisée et toutes les autres requêtes bloquées.
La saisie Playwright ne persiste pas après la fermeture de ce navigateur.
Il serait donc incorrect de prétendre qu'un formulaire rempli demeure
prêt à soumettre dans le navigateur habituel.

M7.5 fournit un transfert **manuel** : il revalide le dossier M6 et la
politique du domaine, verrouille la candidature contre tout autre worker,
puis fournit l'URL autorisée. L'option --open-browser ouvre cette URL dans
le navigateur habituel ; l'utilisateur doit vérifier et, si nécessaire,
**renseigner de nouveau** les champs et les pièces jointes. Aucun clic
« Envoyer » n'est produit par le bot.

## Commandes

```bash
uv run ajh submission handoff --id ID --policy .ajh/browser-policy.toml --open-browser
uv run ajh submission status --id ID
uv run ajh submission clicked --id ID
uv run ajh submission confirm --id ID --source confirmation_page \
  --evidence .ajh/receipts/confirmation.png --reference REQ-123456 --reviewed
uv run ajh submission events --id ID
uv run ajh submission list
uv run ajh submission stats
```

Pour abandonner une candidature transférée **avant** tout clic déclaré :

```bash
uv run ajh submission abandon --id ID
```

Ne pas utiliser `queue cancel` pour un transfert commencé. Ne pas répéter
un envoi sous prétexte d'une absence de confirmation.

## Modèle d'états (table SQLite additive)

| État | Sens |
| --- | --- |
| awaiting_manual_action | Destination transmise, aucune soumission attestée |
| clicked_unconfirmed | L'utilisateur dit avoir cliqué, réception inconnue |
| submitted_confirmed | L'utilisateur a fourni et revu une preuve de confirmation |
| abandoned | Transfert abandonné avant clic déclaré |

Le statut `submitted_confirmed` est une **attestation humaine documentée**,
pas une vérification indépendante ou automatique auprès de l'ATS. La preuve
peut être une page de confirmation ou un email de réception. Le fichier
original n'est pas importé en SQLite ; seul son SHA-256, son type,
une référence courte et les horodatages sont enregistrés. Un simple clic
ou une capture d'écran non examinée ne suffit pas à confirmer.

## Barrières et propriétés

- Transitions atomiques avec SQLite BEGIN IMMEDIATE.
- Les anciens états M7.1 restent intacts : migration additive uniquement.
- Aucune candidature liée à un transfert ne peut être reprise par un worker
  Playwright ni remise dans la file via `queue resume`.
- La destination est vérifiée contre le dossier M6 intact, le contact RH
  approuvé s'il y a lieu et la politique HTTPS explicitement autorisée.
- Aucun email, cookie, CV, preuve brute ou secret de navigateur dans les logs.
- Un transfert identique est idempotent ; les états ultérieurs ne reviennent
  jamais automatiquement en attente.
- Le navigateur personnel est ouvert uniquement sur --open-browser.
- Le clic final reste individuel et réalisé sur le site de destination.

## Ce qui reste à consolider

Une session navigateur où le bot pourrait remplir les champs puis suspendre
son activité, laisser les requêtes strictement humaines partir et observer
une confirmation authentique exigerait une architecture supplémentaire.
Elle **n'est pas implémentée**. Les protections M7.2 ne doivent pas être
désactivées simplement pour rendre le bouton Envoyer utilisable.

Avant usage sur des sites réels, valider les permissions du site, les tests
de non-envoi, la compatibilité navigateur et la preuve de confirmation.
Les tests hors réseau ne valident pas une soumission sur un véritable ATS.

## Validation

```bash
uv run pytest -q
uv run ajh submission --help
```

Tester notamment les clics non confirmés, les preuves absentes ou altérées,
la duplication de candidature, l'annulation, la non-réattribution à un
autre worker et l'absence d'action réseau dans le suivi.
