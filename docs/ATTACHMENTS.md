# M7.4 — CV, lettres et pièces jointes PDF contrôlées

**Statut : développement sur une branche, tests locaux et intégration navigateur réels à exécuter.**

## Fonctionnalités

Le bot peut convertir les fichiers `cv.md` et `letter.md` d'un dossier M6 en
documents PDF A4 locaux, avec ReportLab (dépendance optionnelle). Les fichiers
`cv.pdf`, `letter.pdf` et `attachments.json` sont créés dans le même dossier.

Chaque PDF est accompagné des empreintes SHA-256 du PDF et de son Markdown
d'origine, de sa taille et du statut `review_required`. La génération est
refusée si le dossier est déjà dans la file M7.1 ou si des fichiers de
pièces jointes existent. Aucun fichier existant n'est écrasé.

Une fois les pièces jointes générées, `queue add` intègre les PDF et le
manifeste de pièces jointes à l'empreinte immuable du dossier. Si le dossier
est changé avant l'utilisation, la réservation est bloquée. Si le dossier
est déjà en file, il faut en préparer un nouveau plutôt que modifier ses PDF.

## Commandes WSL2

```bash
uv sync --extra browser --extra documents
uv run playwright install chromium
uv run pytest -q
uv run ajh documents build --draft .ajh/drafts/NOM_DU_DOSSIER
uv run ajh documents verify --draft .ajh/drafts/NOM_DU_DOSSIER
uv run ajh queue add --draft .ajh/drafts/NOM_DU_DOSSIER
```

Pour les formulaires explicitement autorisés, déclarer les pièces jointes
dans un adaptateur TOML, après vérification du vrai DOM :

```toml
[[adapter.upload]]
name = "resume"
document = "cv"
required = true
```

Champs autorisés : `cv` ou `letter`, un champ de chaque au maximum.
L'entrée HTML doit être un `input type=file` unique, simple et présent
dans le formulaire reconnu. L'extension `.pdf` est obligatoire et les
fichiers sont relus et vérifiés avant `set_input_files`. Les champs
inconnus, multiples ou dont le type déclaré refuse le PDF sont bloqués.

```bash
uv run ajh fill --policy .ajh/browser-policy.toml \
  --adapters .ajh/form-adapters.toml \
  --candidate .ajh/candidate.toml --max-seconds 120
```

Le navigateur M7.2 conserve JavaScript désactivé et bloque toute requête
secondaire ou non-GET. La sélection locale des PDF dans un `input file`
**ne signifie pas que le serveur a reçu ces fichiers**. Le moteur
n'appelle jamais `submit`, n'actionne pas de bouton et ne clique pas
sur le bouton Envoyer. Il referme ensuite le navigateur.

## Sécurité et limites

- Aucun téléversement implicite par API, aucune soumission automatique.
- Les fichiers réels et leurs contenus ne sont pas inscrits dans les logs.
- Pas de prise en charge DOCX à M7.4 ; PDF seulement.
- Les brouillons originaux restent la source ; les PDF sont non modifiables
  au sein de la file et leurs empreintes sont contrôlées.
- ReportLab n'interprète pas les balises Markdown en code exécutable.
- Les adaptateurs statiques ne fonctionnent pas sur tous les ATS React.
- Les tests hors réseau ne démontrent pas la compatibilité d'une plateforme.
- La désactivation JS limite le fonctionnement des sites ; M7.5 devra
  définir une barrière robuste avant un éventuel mode dynamique.
- La protection est fondée sur des fichiers locaux et SHA-256 ; un accès
  malveillant en écriture au dossier et à la base dépasse ce modèle.
- Prévoir un test navigateur réel sur une page autorisée avant production.

## Validation

Tests offline : génération PDF, vérification SHA-256, refus des fichiers
modifiés, des symlinks, des champs inconnus et du déclenchement de submit.
Exécuter toute la suite pytest et vérifier le rendu PDF sur un dossier
fictif avant d'autoriser des documents personnels.
