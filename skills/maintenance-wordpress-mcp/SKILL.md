---
name: maintenance-wordpress-mcp
description: Faire évoluer, tester ou dépanner le connecteur MCP wordpress-mcp (authentification, snippets WPCode, outils de lecture et d'écriture, tests, publication sur GitHub, serveur MCP Elementor).
---

# Maintenance du connecteur wordpress-mcp

Connecteur MCP pour un site WordPress (Elementor V3, Modern Events Calendar Lite, Redirection, Forminator, WPCode, TablePress). Lecture par défaut ; écritures seulement si `WP_ENABLE_WRITES=1`, toujours en deux temps (aperçu puis confirmation). Le dépôt est générique : aucun identifiant de site, de personne ou d'organisation dans le code, les tests, `docs/` ou `outils/`.

## Méthode de travail
- Si l'environnement d'exécution ne peut pas joindre le site (proxy, pas de réseau), coder et tester hors ligne (`pytest`, `respx`, site fictif `https://wp.test`), puis lancer les tests et la validation réelle sur la machine de l'utilisateur, avec le venv du projet (`./.venv/bin/python`).
- Scripts de validation réelle : les ranger **hors du dépôt**. Un petit module charge les variables de l'entrée `wordpress` du fichier de configuration de Claude Desktop dans `os.environ`, puis le script appelle `server` / `write_tools` (`asyncio.run`, ou `mcp.call_tool(nom, arguments)` pour voir ce que reçoit le modèle). Ne jamais afficher les mots de passe.
- Préparation et confirmation d'une écriture se font dans le même processus (les jetons vivent en mémoire). Séparer l'aperçu de l'écriture par un argument `--confirmer`, et vérifier que l'aperçu correspond à ce qui a été approuvé (empreintes) avant de confirmer.
- Appels longs : les lancer en arrière-plan (`nohup … &`, `caffeinate -i` sur macOS) et suivre par des appels courts. Ne pas relancer une écriture non idempotente sans avoir vérifié son effet.
- Avant de modifier la configuration de Claude Desktop : copie datée `.bak-AAAA-MM-JJ-sujet`, puis vérifier que le JSON est valide. Chemins complets de `npx` et de `python` dans les commandes.
- Après une modification du code, relancer le connecteur ; dans une conversation déjà ouverte, rafraîchir la liste des outils MCP ou passer par un script.
- Tests : un test hors ligne par garde-fou et par cas limite rencontré. `Settings` est figé : `dataclasses.replace(client.settings, …)`. Mettre à jour `test_tool_count` à chaque outil ajouté.
- Commit et push uniquement sur demande. Dans le même commit : README (tableaux et nombre d'outils, variables, snippets de `docs/`, limites) et version (`pyproject.toml`, User-Agent). Avant chaque commit, vérifier qu'aucun identifiant propre à un site n'est entré dans le dépôt (`git grep -niE "<nom du site>|<nom de l'organisation>"`).

## Authentification : diagnostic
- `rest_not_logged_in` même avec un mauvais mot de passe : l'hébergeur retire l'en-tête `Authorization`. Si l'en-tête arrive, un mauvais mot de passe donne `incorrect_password`.
- Solution : `WP_AUTH_HEADER=X-Claude-Auth` + snippet WPCode `docs/wpcode-snippet.php`. Il copie l'en-tête vers `PHP_AUTH_USER/PW` sur `determine_current_user` (priorité 1) et réévalue l'utilisateur sur `rest_api_init` (priorité 1) avec `wp_set_current_user`, car une autre extension peut déterminer l'utilisateur trop tôt.
- L'identifiant et le mot de passe d'application doivent venir du même compte (administrateur dédié).
- Le mode `cookie` (wp-login.php + nonce) est bloqué par un captcha de connexion : ne jamais contourner un captcha.

## Garde-fous
- Lecture : `check_read_only` (GET/HEAD, `ALLOWED_NAMESPACES`). Écriture : `client.write` refuse si `WP_ENABLE_WRITES≠1`, puis liste blanche `ALLOWED_WRITES`. Ne jamais élargir sans accord explicite du propriétaire du site.
- Jamais : suppression de pages, d'articles, d'événements, de menus ou de snippets ; utilisateurs, extensions, réglages. Seule suppression prévue : les fichiers de la médiathèque, avec copie locale préalable.
- Toute erreur prévue hérite de `ToolError` (sinon le client MCP la masque). Les erreurs réseau sont traduites en `delai_depasse` / `site_injoignable` ; pour une écriture, le message précise qu'elle a peut-être été appliquée.

## Ajouter une écriture
1. Vérifier par `OPTIONS` que l'API la permet, sinon écrire une route de snippet (la plus étroite possible, `manage_options`, empreinte de l'état lu exigée, 409 si changé, aucune suppression).
2. Ajouter l'entrée la plus étroite possible à `ALLOWED_WRITES`.
3. Outil `*_preparer` : lit l'état, construit l'opération exacte, renvoie aperçu, avertissements (« visible immédiatement ») et un jeton avec garde (date de modification, statut, empreinte).
4. Exécuteur : revérifie la garde, sauvegarde, écrit, relit et compare, journalise sans contenu.
5. Tests (liste blanche autorisée et refusée, écritures désactivées, fraîcheur, erreurs), puis validation réelle sur un brouillon ou un élément de test, jamais d'abord sur une page publiée.

## Snippets WPCode
- Un snippet = un fichier `docs/wpcode-snippet-xxx.php` générique, avec en-tête (rôle, routes, marche à suivre). Lecture seule si possible. Vérifier `php -l`.
- Installation par `wp_snippet_installer_preparer` (création inactive, ou différences), activation après accord qui nomme le snippet ; jamais de code saisi dans la conversation. Les snippets d'authentification et de gestion se mettent à jour à la main dans WPCode.
- Pièges : WPCode peut perdre les antislashs du code (le snippet de gestion les restaure) ; `save()` teste le code à l'activation ; `rest_pre_dispatch` ne bloque pas toujours `/wp/v2/users`, retirer plutôt les routes avec `rest_endpoints` ; un cache de pages en mode simple n'exécute pas WordPress, donc les en-têtes d'un snippet manquent sauf si le cache des en-têtes est activé.
- Les snippets ponctuels (diagnostic) sont désactivés juste après usage, puis supprimés par le propriétaire.

## Limites connues
- MEC Lite : pas de dates en REST ; `wp_evenements` lit le JSON-LD `Event` de la page publique de chaque événement (même domaine, sans identifiants).
- Forminator : pas de lecture des réponses en REST, d'où un snippet. TablePress et WPCode : pas d'API REST, d'où des snippets.
- Elementor V3 : écriture de `_elementor_data` par REST (JSON conservé à l'identique) ; le serveur MCP d'Elementor (`/wp-json/elementor/mcp/`) ne modifie que des éléments V4 ; ses espaces de noms (`mcp`, `elementor-mcp-composer`) restent hors des listes blanches.

## Sécurité
- Mots de passe d'application : révocables, jamais dans le dépôt. S'il est collé dans une conversation, le révoquer en fin d'essai.
- Secrets de tiers (secret client OAuth, codes) : jamais saisis ni affichés par Claude ; le propriétaire les colle lui-même.
- Couper les écritures : retirer `WP_ENABLE_WRITES` de la configuration et redémarrer Claude Desktop.
