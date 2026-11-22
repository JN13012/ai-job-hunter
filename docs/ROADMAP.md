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
| M2.3 | Catalogue ATS et cibles manuelles, import JSON local autorisé — implémentation à valider ; ingestion directe d'alertes et connecteurs spécifiques différés |
| M3 | Filtres déterministes, scoring explicable, sélection, regroupement strict intersources — implémentation à valider ; dates/diplômes et enrichissement ciblé différés |
| M4 | Découverte légale via API Recherche d'Entreprises, SIREN et croisement LBA/catalogue — implémentation à valider |
| M5 | M5.1 validé ; M5.2–M5.4 implémentés, tests M5.2–M5.4 à exécuter |
| M6 | CV/lettres factuels, réponses déclarées, suggestions Ollama locales avec cache et budget — implémenté, à tester |
| M7 | M7.1–M7.5 sur branches, non validés localement ; M7.5 transfert manuel + attestation, sans session Playwright persistante ; M7.6 à développer |
| M8 | Suivi des candidatures confirmées, relances limitées et planification des recherches |
| M9 | Tableau de bord local : file de candidatures, dossiers prêts, validation humaine et suivi |

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

## Candidatures — fonctionnement cible

**AI Job Hunter recherche et ouvre lui-même les offres.** L'utilisateur n'a
pas à chercher les annonces, ouvrir les pages ou remplir les champs en
fonctionnement nominal. Après classement et préparation des documents, le
bot navigue et remplit les formulaires sur les sites qui l'autorisent.

**Règle non négociable : aucun clic automatique final sur « Envoyer »**,
aucun appel API de soumission et aucune validation groupée. L'utilisateur
voit le vrai formulaire dans un navigateur visible, contrôle chaque dossier
et clique personnellement sur le bouton de soumission. Un clic ne prouve pas
l'envoi : seule une confirmation explicite permet de marquer « envoyé ».

Moteur principal : Playwright piloté localement en Python. Extension Chrome
Manifest V3 complémentaire, uniquement pour les sessions et sites compatibles.
Le passage en navigateur visible est obligatoire avant le clic humain. Les
sites interdisant cette automatisation restent en mode dossier préparé /
assistance manuelle ; ne jamais contourner CAPTCHA, MFA ou protections.

### Sous-étapes M7

| Mission | Livrable |
| --- | --- |
| M7.1 | File persistée, orchestrateur, verrouillage par offre/destination, annulation, reprise sûre |
| M7.2 | Navigation Playwright autonome depuis la file, domaines et redirections contrôlés, navigateur visible avant validation |
| M7.3 | Adaptateurs autorisés de formulaires, remplissage, vérification et étapes intermédiaires non soumissives |
| M7.4 | CV, lettres et réponses factuelles, pièces jointes locales et contrôle des données |
| M7.5 | Barrière anti-soumission, arrêt avant « Envoyer », clic humain obligatoire, preuve de confirmation et suivi |
| M7.6 | Extension Chrome optionnelle, connexion au backend local sécurisée, permissions minimales et mêmes garde-fous |

Détails et critères d'acceptation : [APPLICATION_AUTOMATION.md](APPLICATION_AUTOMATION.md).
**Ces fonctionnalités M7 sont planifiées et non implémentées.**

### Suivi M2.1 / M3

- Limite SmartRecruiters : filtrer les métadonnées avant enrichissement ciblé,
  distinguer avertissements de quota et erreurs, conserver les détails déjà acquis.
- La Bonne Alternance retourne deux catégories distinctes (`jobs`, `recruiters`) ;
  ne pas transformer les entreprises à potentiel en offres publiées.

### M5 — Contacts professionnels

Le socle M5.1 enregistre des canaux de recrutement déclarés, vérifie
le SIREN, conserve la provenance et empêche toute réimportation d'un
contact bloqué. L'identification automatique via sources autorisées reste
à développer. Voir [CONTACTS.md](CONTACTS.md).

#### M5.2 — Qualification des canaux déclarés

Le catalogue accepte `application_form_url` facultatif : HTTPS, même hôte que
`career_url`, sans paramètres ni fragment. Les cibles avec SIREN connu dans M4
et formulaire explicitement déclaré peuvent être inscrites en contacts M5.1
avec statut `pending`, jamais `approved` automatiquement. Ni réseau ni envoi.
Les URLs ATS par offre ne sont pas assimilées à des formulaires spontanés.

#### M5.3 — Découverte opt-in limitée

Le module `contact_discovery.py` inspecte au maximum une page par entreprise,
sous condition de permission explicite dans le catalogue (`allow_contact_scan = true`
et `permission_note`). Le SIREN doit être connu de M4. Vérification robots.txt,
GET HTTPS, aucun suivi de redirection, pas d'exploration de liens, limitation
de requêtes et de taille, aucune adresse email reconstruite. Seuls des
liens de candidature spontanée ou boîtes de recrutement génériques publiées
sont proposés. `--fetch` active les accès HTTP ; `--register` les inscrit en
attente, sans approbation ni envoi. Les données ne sont pas vérifiées en réel.

#### M5.4 — Routage déterministe des candidatures

`channel_routing.py` classe les canaux connus sans réseau : URL de candidature
par offre (priorité 100), formulaire RH approuvé (80), email professionnel
approuvé (60). Un canal pending/blocked est exclu. Le rattachement au SIREN
s'effectue uniquement par le SIRET explicite LBA ou le board ATS déclaré dans
le catalogue avec SIREN. Aucun appariement flou par raison sociale.
Les URLs directes restent non vérifiées ; priorité ne signifie pas droit
d'automatisation. Les résultats sont JSON Lines, en lecture seule, pour M6/M7.

### M6 — Brouillons sécurisés

Le profil candidat TOML sépare compétences acquises et en cours. Un dossier
local par offre contient CV Markdown, lettre, réponses et manifeste non validé.
Ollama reste facultatif, local et sans accès navigateur. Ses suggestions sont
conservées séparément et jamais promues automatiquement. Détails :
[AI_SECURITY.md](AI_SECURITY.md).

### M7.1 — File et réservation exclusives

`application_queue.py` conserve des candidatures préparées M6, leur état et
un journal minimal en SQLite. Une réservation ne peut être prise que par un
worker à la fois ; l'annulation d'une réservation active attend que le worker
reconnaisse la demande. Le bail expiré n'est jamais réattribué automatiquement.
Reprise uniquement explicite, après vérification de l'arrêt réel du worker.
Tout changement de document ou révocation du contact bloque le traitement.
L'API de file ne soumet rien : [APPLICATION_QUEUE.md](APPLICATION_QUEUE.md).

### M7.2 — Aperçu navigateur

Navigation Playwright limitée à un document HTML GET, sans JavaScript ni
saisie, derrière une politique d'hôtes explicitement autorisés. Le worker
réserve, vérifie, affiche, puis ferme Chromium avant de libérer la file.
Voir [BROWSER_PREVIEW.md](BROWSER_PREVIEW.md).

### M7.3 — Adaptateurs statiques de formulaires

Le moteur `form_filling.py` reconnaît un formulaire unique déclaré dans
`form_adapters.py` et remplit uniquement les champs préautorisés à partir
des faits M6. Un précontrôle complet interdit tout champ inconnu ou
spécial. Les boutons, pièces jointes, transitions et soumissions restent
hors périmètre. Voir [FORM_FILLING.md](FORM_FILLING.md).

### M7.4 — Gestion des pièces jointes

Le module `attachment_documents.py` convertit les brouillons M6 en PDF
A4 sans service externe et associe les fichiers à leurs SHA-256. La file
M7.1 vérifie leur intégrité et bloque les modifications après inscription.
Les adaptateurs M7.3 peuvent sélectionner uniquement les PDF explicitement
autorisés dans les champs `input[type=file]`. JavaScript reste désactivé
et aucune candidature n'est soumise. Voir [ATTACHMENTS.md](ATTACHMENTS.md).

### M7.5 — Transfert humain et preuve locale

`submission_tracking.py` bloque les reprises et candidatures en doublon dès
le début d'un transfert, puis distingue `awaiting_manual_action`,
`clicked_unconfirmed`, `submitted_confirmed` (preuve revue par l'humain) et
`abandoned`. L'URL peut être ouverte par le navigateur habituel sur demande
explicite. Contrairement à la cible initiale, **les champs préremplis dans
la session Chromium isolée ne sont pas transmis au navigateur habituel** ;
une véritable reprise du formulaire rempli reste à réaliser. Aucune action
`submit` n'est exécutée par le programme. Voir [HUMAN_SUBMISSION.md](HUMAN_SUBMISSION.md).
