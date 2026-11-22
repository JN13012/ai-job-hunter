# M7.2 — Aperçu Playwright en lecture seule

**Implémentation à valider localement.**

Le worker M7.2 prélève une candidature réservée via M7.1, vérifie les
fichiers M6 et la destination, puis ouvre automatiquement un navigateur
Chromium visible. Cette étape ne remplit aucun champ et n'envoie rien.

## Autorisation et garde-fous

Le fichier de politique privé doit lister des hôtes HTTPS exacts,
un préfixe de chemin et une justification documentée par règle.
Une URL collectée en M1–M5 ne constitue pas une autorisation.

- Le document HTML principal est chargé en GET uniquement, sans suivre
  de redirection et sans transférer les cookies de la réponse.
- JavaScript, service workers, ressources secondaires et téléchargements
  sont bloqués. Les pages modernes peuvent s'afficher incomplètement.
- Aucune session Chrome utilisateur n'est importée.
- Un email ne peut pas ouvrir de navigateur.
- En cas d'erreur ou de redirection, la candidature passe à blocked/failed.
- Fermeture du contexte et du navigateur avant libération de la réservation.
- Si le nettoyage est incertain, la réservation reste retenue.

## Exécution WSL2

~~~bash
uv sync --extra browser
uv run playwright install chromium
uv run ajh preview --policy .ajh/browser-policy.toml --max-seconds 180
~~~

Le navigateur visible exige WSLg ou un affichage graphique compatible.
Le modèle de configuration fictif est disponible dans
examples/browser-policy.example.toml. Copier et adapter seulement après
avoir confirmé que le site autorise cette navigation.

Les tests sont hors réseau ; l'intégration graphique réelle doit être
validée localement. Aucune soumission ne sera implémentée avant M7.5.
M7.3 devra isoler les adaptateurs de formulaires et leurs permissions.

**Limites :** l'interception Playwright n'est pas une isolation réseau
OS parfaite. Ne pas ouvrir des destinations inconnues ou non maîtrisées
avant mise en place de garde-fous complémentaires.
