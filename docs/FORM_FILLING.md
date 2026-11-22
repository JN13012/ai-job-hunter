# M7.3 — Reconnaissance et remplissage contrôlés

**Statut : implémenté sur branche, tests locaux et intégration réelle non exécutés.**

## Périmètre

Le bot prend une réservation M7.1, vérifie la destination selon la politique
M7.2 et un adaptateur spécifique, puis ouvre un Chromium visible et remplit
des champs connus à partir du profil candidat et des réponses préparées M6.

Le fonctionnement est volontairement limité à une seule page HTML statique :
JavaScript est désactivé ; les requêtes secondaires et les navigations sont
bloquées par StrictDocumentRoute. Le bot ne clique aucun bouton, ne presse
pas Entrée, ne téléverse aucun fichier et n'envoie aucune candidature.

## Sécurité des adaptateurs

Les adaptateurs TOML sont explicitement autorisés, par hôte HTTPS exact,
préfixe de chemin, identifiant de formulaire et justification. Ils définissent
les correspondances entre name HTML, type de contrôle et fait du profil.

Sources acceptées : identité, coordonnées, disponibilité, rythme, motivation
et answers.<clé> de la préparation M6. Ne sont pas acceptés : mot de passe,
compétence inventée, consentement automatique, case à cocher, radio, upload,
bouton, pièces jointes ou champs d'authentification.

Le moteur inspecte l'intégralité du formulaire avant de remplir :
- un seul élément form et le form_id exact sont requis ;
- tout contrôle inscriptible inattendu provoque un arrêt ;
- chaque champ déclaré doit exister une seule fois et être visible et actif ;
- les options select doivent correspondre exactement à une valeur déclarée ;
- les entrées HTML hidden sont ignorées, non renseignées ;
- les contrôles submit sont ignorés, jamais activés ;
- après fermeture, les données remplies ne persistent pas dans le site.

Les formulaires React, multi-étapes, CAPTCHA, MFA et validations JavaScript
ne sont pas gérés par M7.3. Ils nécessiteront un adaptateur plus spécialisé,
après consolidation des protections réseau et du verrou de soumission.

## Installation et commandes

```bash
uv sync --extra browser
uv run playwright install chromium
uv run pytest -q
uv run ajh fill --help
```

Copier les modèles fictifs uniquement après vérification de l'autorisation :

```bash
cp examples/browser-policy.example.toml .ajh/browser-policy.toml
cp examples/form-adapters.example.toml .ajh/form-adapters.toml
```

Adapter les hôtes, chemins, form_id et field.name aux pages autorisées ;
les fichiers exemples ne correspondent pas à une offre réelle.

```bash
uv run ajh fill \
  --policy .ajh/browser-policy.toml \
  --adapters .ajh/form-adapters.toml \
  --candidate .ajh/candidate.toml \
  --max-seconds 120
```

Le worker prend la première candidature dont le canal est offer_link ou
company_form ; un email n'est jamais ouvert. Il relit le manifeste M6 et
answers.json, vérifie l'empreinte et referme le navigateur avant release.

## États

Succès technique : l'aperçu est fermé et le dossier revient à queued,
**pas** à submitted. Refus d'adaptateur ou de page : blocked.
Erreur technique : failed. Nettoyage incertain : réservation conservée.

Le résultat indique le nombre de champs remplis sans afficher leurs valeurs.
Aucun mécanisme M7.3 ne garantit qu'un site tiers acceptera un formulaire.

## Tests et limites

Les tests de mapping et d'absence de soumission sont hors réseau.
Vérifier sous WSLg l'ouverture Chromium et les permissions de la cible
avant toute démonstration. Pour l'instant M7.3 n'est pas utilisable
comme postulateur autonome de production : les formulaires dynamiques,
les fichiers CV/lettre et la revue humaine finale restent à développer.

**M7.4** étendra les dossiers et pièces jointes ; **M7.5** implémentera
la barrière et le suivi de la soumission réalisée exclusivement par l'humain.
