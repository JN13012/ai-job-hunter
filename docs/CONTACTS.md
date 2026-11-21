# M5 — Contacts de recrutement professionnels

Statut : M5.1 validé localement (202 tests), M5.2 implémenté à tester ; M5.3–M5.4 à développer.

## Objectif

Relier les entreprises découvertes en M4 à leurs canaux de recrutement officiels,
en conservant la provenance et sans supposer qu'un contact est disponible.
La phase M5.1 permet un import local contrôlé. Elle n'effectue ni collecte
automatique de contacts, ni scraping, ni envoi de candidature.

## Sous-étapes

| Mission | Portée | État |
| --- | --- | --- |
| M5.1 | Registre SQLite, import JSON, source HTTPS, SIREN M4, déduplication, approbation et opposition | À tester |
| M5.2 | Qualification statique de formulaires déclarés, sans exploration de site | Implémenté, à tester |
| M5.3 | Recherche de canaux via API ou sources explicitement autorisées, avec limites de fréquence | À étudier par site |
| M5.4 | Classement et transmission des canaux vérifiés aux dossiers M6/M7 | À développer |

## Schéma et contraintes

- Lien obligatoire : SIREN existant dans la table des entreprises M4.
- Canaux : email institutionnel explicite ou formulaire de recrutement HTTPS.
- Rôle : recrutement, RH, talent acquisition, hiring manager ou carrières.
- Source : page carrières, page contact officielle ou export autorisé, avec URL HTTPS.
- Nom d'une personne facultatif, uniquement si public, pertinent et professionnel.
- Statuts : pending, approved, blocked. Import nouveau = pending.
- Fichier JSON limité à 1 Mio et 200 contacts, import atomique.
- Dédoublonnage par SIREN, canal et destination, sans deviner les emails.
- Une modification de provenance révoque l'approbation.
- La commande block efface coordonnées, nom et provenance, et conserve
  une empreinte technique minimale évitant la réimportation identique.

## CLI

```bash
uv run ajh companies list --limit 20
uv run ajh contacts import --file .ajh/contacts.json
uv run ajh contacts stats
uv run ajh contacts list --status pending --limit 20
uv run ajh contacts approve --id IDENTIFIANT_SHA256
uv run ajh contacts block --id IDENTIFIANT_SHA256
```

Exemple fictif : examples/contacts.example.json. Remplacer le SIREN et les
adresses par des données vérifiées avant import.

## Confidentialité

Une donnée professionnelle publique n'est pas automatiquement librement
réutilisable. Vérifier la pertinence de la candidature, la source, les droits
d'opposition, les durées de conservation et les conditions de la plateforme.
La recherche d'alternance n'est pas assimilée d'office à la prospection B2B.
Références : https://www.cnil.fr/fr/recommandations-reutilisateurs-donnees-internet
et https://www.cnil.fr/fr/les-bases-legales/interet-legitime.

Interdits : collecte indiscriminée, génération d'adresses présumées, extraction
non autorisée de profils LinkedIn, données personnelles privées, envoi en masse.
L'approbation d'un contact ne vaut pas autorisation d'expédier un message.

## Critères d'acceptation

1. Réimportation identique : pas de doublon.
2. SIREN absent, provenance invalide ou entrée personnelle : rejet atomique.
3. Nouveaux contacts en attente d'une revue.
4. Changement de source : retour à pending.
5. Block irréversible via la CLI : données effacées et réimportation sans effet.
6. Jobs et entreprises M0–M4 préservés.
7. Tests pytest sans accès réseau ; aucun envoi ni navigateur.

La règle M7 reste distincte : le bot cherche et prépare les candidatures,
mais le dernier clic Envoyer sur le site reste une action humaine.

## Qualification M5.2

Une cible peut déclarer explicitement `siren` et `application_form_url`.
Le formulaire doit être HTTPS, sur le même hôte que `career_url`, sans query
ou fragment. Cette déclaration n'est pas une validation de la page.

```bash
uv run ajh contacts catalog --file examples/catalog.toml
uv run ajh contacts catalog --file .ajh/mon-catalogue.toml --register
uv run ajh contacts list --status pending
```

Statuts du diagnostic : `missing_siren`, `company_not_in_m4`,
`form_not_declared`, `ready_for_review`. Seuls les formulaires explicites
avec SIREN présent dans M4 sont inscrits, toujours en attente de contrôle.
Les URL de candidature par offre ATS ne sont pas promues en canaux génériques.
Les formulaires sur hôte tiers exigent un import manuel avec provenance propre.
Aucune connexion réseau, détection DOM ou soumission n'est effectuée.
