# M7.1 — File locale persistante de candidatures

Statut : implémentation sur branche, tests non encore exécutés localement.

## Finalité

Réserver de manière exclusive les dossiers M6 avant la navigation M7.2.
Aucun worker navigateur n'est encore connecté. Aucun email, POST, formulaire
ou bouton Envoyer n'est déclenché par M7.1.

## États

| État | Sens |
| --- | --- |
| queued | Dossier M6 inscrit, prêt à être réservé |
| reserved | Réservation exclusive par un worker, jeton éphémère |
| interrupted | Réservation expirée interrompue explicitement |
| blocked | Manifeste, documents ou contact devenus invalides |
| failed | Travail signalé en échec par le worker |
| cancelled | Annulation demandée et prise en compte |

Une réservation active est bloquante même après expiration : elle ne passe
pas automatiquement à un autre worker. Après l'expiration, la commande
recover met le dossier en interrupted ; resume est ensuite explicite.
L'arrêt réel d'un ancien worker devra être prouvé par M7.2 avant de reprendre.
Une expiration de bail seule ne garantit pas que le processus s'est arrêté.

## Enregistrement et contrôle

Chaque entrée pointe vers un dossier direct dans .ajh/drafts. Le manifeste
doit être issu de M6, non approuvé pour soumission et marqué human only.
CV, lettre, réponses et manifeste doivent exister, avoir une taille limitée,
être des fichiers ordinaires et ne pas être des liens symboliques.
Un hash SHA-256 de leurs contenus est conservé : toute modification entraîne
un blocage avant réservation. Un lien d'offre doit correspondre exactement
à l'URL d'offre SQLite ; un canal RH exige une fiche approved non révoquée.
Les doublons identifiés par M3 sont associés à la même entrée grâce à
application_job_aliases. SQLite assure les insertions atomiques.

## CLI utilisateur (sans navigateur)

```bash
uv run ajh queue add --draft .ajh/drafts/NOM_DU_DOSSIER
uv run ajh queue list
uv run ajh queue stats
uv run ajh queue events --id IDENTIFIANT
uv run ajh queue cancel --id IDENTIFIANT
uv run ajh queue recover --id IDENTIFIANT
uv run ajh queue resume --id IDENTIFIANT
```

Le worker utilisera ultérieurement les méthodes Python claim, renew et
release avec jeton de réservation ; ces méthodes ne sont pas exposées
en CLI utilisateur pour éviter les manipulations accidentelles.

## Limites et sécurité

- Aucune soumission automatique ; clic final réservé à l'utilisateur (M7.5).
- Aucun secret, contenu de CV ou réponse personnelle dans la table d'événements.
- Le jeton de réservation n'apparaît pas dans la commande queue list.
- Une réinscription identique d'un dossier annulé ne le réactive pas.
- Les transitions reposent sur BEGIN IMMEDIATE, pas sur des états en RAM.
- Les dossiers et la base restent locaux, sous .ajh, ignorés par Git.
- Le contrôle du contenu du manifest est structurel, pas cryptographique
  vis-à-vis d'un acteur qui pourrait modifier à la fois DB et disque.
- Les autorisations d'automatisation propres à chaque site relèvent de M7.2/3.
- Les éventuelles pièces jointes PDF/Docx restent à traiter par M7.4.

## Validation

pytest doit tester idempotence, concurrence, stale token, modification de
dossier, révocation, reprise explicite, annulation et absence d'envoi.
Le test réel de plusieurs processus et l'intégration Playwright sont différés.
