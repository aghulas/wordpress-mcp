# wordpress-mcp

Serveur [MCP](https://modelcontextprotocol.io) pour un site WordPress, pensé pour le site d'une école (WordPress + Elementor + Modern Events Calendar + Redirection), mais utilisable sur n'importe quel site qui expose l'API REST `/wp-json`.

Il permet à Claude (Claude Desktop) de relire le contenu du site, de retrouver un texte, d'auditer les extensions, les redirections, les 404 et les soumissions de formulaires, et de faire le bilan de santé du site. Par défaut il est en **lecture seule** : seule la méthode `GET` est autorisée, et seuls quelques espaces de noms REST sont joignables (cœur `wp/v2`, Elementor, MEC, Redirection, santé du site).

Avec `WP_ENABLE_WRITES=1`, il peut aussi **écrire, toujours en deux temps** : un outil `*_preparer` montre l'aperçu (avant → après) et renvoie un jeton ; rien ne change tant que l'utilisateur n'a pas donné son accord et que `wp_ecriture_confirmer` n'a pas été appelé. Aucune suppression n'est possible.

## Installation

```bash
git clone https://github.com/aghulas/wordpress-mcp.git ~/dev/wordpress-mcp
cd ~/dev/wordpress-mcp
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest -q      # tests hors ligne
```

## Configuration

Dans WordPress : **Utilisateurs → Profil → Mots de passe d'application**, créer un mot de passe nommé par exemple `Claude (lecture)`. WordPress l'affiche une seule fois, par groupes de 4 caractères (les espaces sont ignorés).

Variables d'environnement :

| Variable | Rôle |
|---|---|
| `WP_BASE_URL` | URL du site, ex. `https://www.exemple.fr` (obligatoire) |
| `WP_USER` | identifiant WordPress (login). Sans lui : accès anonyme, contenu publié seulement |
| `WP_AUTH_MODE` | `basic` (défaut) ou `cookie` — voir « Modes d'authentification » |
| `WP_AUTH_HEADER` | (mode basic) en-tête de remplacement, ex. `X-Claude-Auth`, quand l'hébergeur retire `Authorization` — voir `docs/wpcode-snippet.php` |
| `WP_APP_PASSWORD` | (mode basic) mot de passe d'application de cet utilisateur |
| `WP_PASSWORD` | (mode cookie) mot de passe WordPress du compte |
| `WP_TIMEOUT` | délai HTTP en secondes (défaut 30). Au-delà, l'outil répond « le site n'a pas répondu dans le délai » (code `delai_depasse`) ; pour une écriture, il précise qu'elle a peut-être été appliquée |
| `WP_PER_PAGE_MAX` | plafond d'éléments renvoyés par un outil de liste (défaut 200) |
| `WP_ENABLE_WRITES` | `1` pour autoriser les écritures avec confirmation (défaut : désactivées) |
| `WP_WRITE_TTL` | validité d'un jeton de confirmation, en secondes (défaut 600) |
| `WP_BACKUP_DIR` | sauvegardes locales des pages modifiées et journal des écritures (défaut `~/.wordpress-mcp/sauvegardes`) |
| `WP_UPLOAD_ROOTS` | dossiers d'où un fichier peut être envoyé, séparés par `:` (défaut `~/Documents:~/Downloads:~/Desktop`) |
| `WP_UPLOAD_MAX_MB` | taille maximale d'un fichier envoyé (défaut 20) |
| `WP_SNIPPETS_DIR` | dossier des snippets WPCode relus (`wpcode-snippet-*.php`) que le connecteur peut installer (défaut : `docs/` du connecteur) |

`claude_desktop_config.json` :

```json
"wordpress": {
  "command": "/Users/moi/dev/wordpress-mcp/.venv/bin/python",
  "args": ["-m", "wordpress_mcp"],
  "env": {
    "WP_BASE_URL": "https://www.exemple.fr",
    "WP_USER": "mon-login",
    "WP_APP_PASSWORD": "xxxx xxxx xxxx xxxx xxxx xxxx"
  }
}
```

## Outils de lecture (19)

| Outil | Rôle | Compte requis | Validé en réel |
|---|---|---|---|
| `wp_statut` | connexion, site, extensions REST présentes, compte et rôles | non | ✔ |
| `wp_reglages` | réglages généraux (titre, langue, fuseau, page d'accueil…) | admin | ✔ |
| `wp_pages_list` | pages : titre, slug, statut, parent, ordre, `elementor` | brouillons : oui | ✔ |
| `wp_arborescence` | plan du site : arbre des pages, orphelines | non | ✔ |
| `wp_articles_list` | articles avec extrait, catégories, média mis en avant | brouillons : oui | ✔ |
| `wp_contenu_lire` | contenu d'une page/article/événement/modèle : `texte`, `html`, `brut`, `elementor` (widget par widget) | `brut`/`elementor` : oui | ✔ |
| `wp_revisions` | historique des révisions | oui | ✔ |
| `wp_recherche` | recherche plein texte dans le contenu publié | non | ✔ |
| `wp_medias_list` | médiathèque (images, PDF…), alt, dimensions | non | ✔ |
| `wp_menus` | menus de navigation classiques, avec hiérarchie | oui | ✔ |
| `wp_categories` | catégories, étiquettes, catégories d'événements | non | ✔ |
| `wp_evenements` | événements Modern Events Calendar ; dates lues dans le JSON-LD de la page publique | non | ✔ |
| `wp_formulaires_soumissions` | réponses aux formulaires Forminator (avec le snippet `docs/wpcode-snippet-forminator.php`) et Elementor Pro | admin | ✔ (snippet à installer) |
| `wp_redirections` | règles Redirection et journal des 404 | oui | ✔ |
| `wp_snippets` | snippets WPCode : titre, actif, type, emplacement, empreinte SHA-256 du code, protégé, erreur (avec le snippet `docs/wpcode-snippet-gestion.php`) | admin | ✔ (snippet à installer) |
| `wp_tableaux` | tableaux TablePress : liste, ou cellules d'un tableau (contenu brut, lignes et colonnes numérotées à partir de 1, empreinte SHA-256) (avec le snippet `docs/wpcode-snippet-tablepress.php`) | admin | ✔ (snippet à installer) |
| `wp_extensions` | extensions installées, inactives, thème actif | admin | ✔ |
| `wp_utilisateurs` | comptes et rôles | admin | ✔ |
| `wp_sante` | tests de santé WordPress, taille des dossiers et de la base, versions installées comparées à wordpress.org | admin | ✔ |

## Outils d'écriture (21, avec `WP_ENABLE_WRITES=1`)

| Outil | Rôle |
|---|---|
| `wp_article_preparer` | créer ou modifier un article (titre, texte HTML ou brut, catégories existantes, image à la une, extrait, statut : brouillon, en attente, publié, programmé, privé) |
| `wp_widget_texte_preparer` | remplacer le texte d'un widget Elementor (`title`, `editor`, `icon_list.2.text`…) d'une page ou d'un article — en entier, ou seulement un extrait avec `remplacer` (le reste du bloc et sa mise en forme sont conservés ; espaces insécables et apostrophes tolérés) ; sauvegarde locale de l'ancien contenu, relecture, vidage du cache Elementor |
| `wp_page_restaurer_preparer` | revenir à une sauvegarde locale (le contenu actuel est sauvegardé avant) |
| `wp_page_regenerer_preparer` | équivalent du bouton « Mettre à jour » d'Elementor : régénère le texte de secours d'une page Elementor (recherche, flux RSS, résumé affiché au partage du lien) à partir de son contenu, puis vide le cache Elementor ; le contenu affiché ne change pas. `wp_widget_texte_preparer` le fait aussi automatiquement quand l'ancien texte ne peut pas être remplacé directement |
| `wp_page_creer_preparer` | créer une page vide, **toujours en brouillon** (titre, adresse souhaitée, page parente) ; refuse un titre déjà utilisé. À enchaîner avec `wp_page_modele_preparer`, puis publication par `wp_page_preparer` |
| `wp_page_modele_preparer` | reprendre la mise en page Elementor d'une page modèle dans une page existante **en brouillon** (créée avec `wp_page_creer_preparer` ou dans wp-admin) : structure copiée avec de nouveaux identifiants, `textes={widget du modèle: texte}` pour remplir titres et blocs en une seule écriture, `exclure=[…]` pour laisser des éléments de côté ; métas Elementor (`builder`, type de modèle, réglages de page) et texte de secours posés, cache vidé ; refusé sur une page publiée. Pour mettre une page V3 en forme sans Elementor ni éditeur V4 |
| `wp_page_preparer` | changer le titre, le statut (brouillon, privé, en attente, publié) et/ou l'adresse (`slug`, refusée si déjà prise ; rappel de créer la redirection de l'ancienne adresse) d'une page ou d'un article existant ; signale les entrées de menu qui pointent vers la page |
| `wp_menu_entree_preparer` | modifier une entrée de menu : la faire pointer vers une autre page publiée ou une adresse, changer son libellé, la masquer (statut brouillon, réversible) |
| `wp_menu_entree_ajouter_preparer` | ajouter une entrée à un menu existant (identifiant, nom ou slug) : page publiée (libellé = son titre par défaut) ou lien personnalisé, au premier niveau ou sous une entrée (`parent`), en dernier ou après une entrée sœur (`apres`) ; refuse un doublon au même endroit ; relecture après création. Pour la retirer : `wp_menu_entree_preparer(masquer=True)` |
| `wp_medias_alt_preparer` | textes alternatifs (et titres, légendes) de 1 à 50 images en une fois |
| `wp_extraits_preparer` | extrait (résumé) de 1 à 50 pages ou articles en une fois : texte brut, 160 caractères conseillés, 300 au plus ; sert de description pour les moteurs (avec `docs/wpcode-snippet-referencement.php`), de résumé au partage du lien et dans la recherche du site |
| `wp_media_envoyer_preparer` | envoyer une image (JPG, PNG, GIF, WEBP) ou un PDF du poste dans la médiathèque, avec titre, texte alternatif, légende |
| `wp_redirection_preparer` | créer une règle de l'extension Redirection (301, 302, 307, 308) |
| `wp_redirection_activer_preparer` | activer ou désactiver des règles (jamais de suppression) |
| `wp_snippet_installer_preparer` | installer un snippet WPCode PHP « MCP … » **depuis un fichier relu** du dossier `WP_SNIPPETS_DIR` (nom simple `wpcode-snippet-xxx.php`, vérifié par `php -l`) : création **inactive** avec le code complet dans l'aperçu, ou remplacement du code d'un snippet existant de même titre avec les différences complètes et sauvegarde de l'ancien code ; refusé si le fichier change entre l'aperçu et la confirmation. Jamais de code saisi dans la conversation |
| `wp_snippet_activer_preparer` | activer (WPCode teste le code et refuse s'il est en erreur) ou désactiver un snippet ; « MCP Gestion des snippets » et « MCP Auth » ne peuvent pas être désactivés à distance |
| `wp_tableau_cellules_preparer` | corriger des cellules existantes d'un tableau TablePress : `[{ligne, colonne, remplacer, nouveau}]` (seul l'extrait `remplacer` change, il doit figurer une seule fois) ou cellule entière ; ni ajout ni suppression de ligne, de colonne ou de tableau ; scripts refusés ; sauvegarde locale du tableau, empreinte vérifiée côté site, relecture. TablePress vide lui-même le cache des pages |
| `wp_medias_supprimer_preparer` | supprimer **définitivement** de 1 à 50 fichiers de la médiathèque (WordPress n'a pas de corbeille pour les médias) : refusé si un fichier est l'image à la une d'un article, d'une page ou d'un événement ; l'aperçu signale les contenus qui citent encore le nom du fichier ; à la confirmation, chaque fichier est d'abord copié dans `WP_BACKUP_DIR/medias/`, puis supprimé avec ses miniatures (`DELETE …?force=true`) ; motif consigné dans le journal |
| `wp_ecriture_confirmer` | exécuter une écriture préparée, après accord explicite de l'utilisateur |
| `wp_ecriture_annuler` / `wp_ecritures_en_attente` | abandonner une écriture préparée / lister celles en attente |

Verrous (`writes.py`, `client.py`) :
1. `WP_ENABLE_WRITES=1` obligatoire, sinon même la préparation est refusée.
2. Jeton aléatoire, à usage unique, valable 10 minutes ; l'écriture exécutée est exactement celle de l'aperçu.
3. Liste blanche `ALLOWED_WRITES` (méthode + chemin exact) : créer/modifier un article, créer une page en brouillon, contenu, mise en page, titre et statut d'une page ou d'un article existant, envoi d'un média et de ses textes, ajout et modification d'une entrée dans un menu existant (pas de création ni de suppression de menu, pas de suppression d'entrée), création et activation de redirections, vidage du cache Elementor, création, mise à jour et activation de snippets WPCode « MCP … » (routes `wpmcp/v1/snippets` du snippet de gestion), correction de cellules TablePress (`wpmcp/v1/tablepress/tables/<id>/cellules`), suppression d'un média (`DELETE /wp/v2/media/<id>`, seule suppression autorisée, avec copie locale préalable). Aucun autre `DELETE`, aucune modification d'utilisateurs, d'extensions ni de réglages.
4. Contrôle de fraîcheur : à la confirmation l'élément est relu ; s'il a changé depuis l'aperçu (date de modification, statut, empreinte du contenu, de l'entrée de menu, des textes d'une image ou du fichier), l'écriture est refusée.
5. Les erreurs du connecteur héritent de `ToolError` : leur message (extrait introuvable, jeton expiré, page modifiée entre-temps…) est transmis au modèle au lieu d'un « Error executing tool » muet.
6. Avant toute modification d'un contenu Elementor : sauvegarde JSON dans `WP_BACKUP_DIR` ; après : relecture et comparaison. Journal `journal.jsonl` (date, opération, identifiants, issue — jamais le contenu).

Remplacer un PDF en ligne : WordPress donne une nouvelle adresse à chaque envoi ; envoyer le nouveau fichier puis mettre à jour les liens avec `wp_widget_texte_preparer`.

## Modes d'authentification

- **`basic`** (défaut, recommandé) : mot de passe d'application en HTTP Basic. Révocable, ne donne pas accès à l'interface. Ne fonctionne que si l'hébergeur transmet l'en-tête `Authorization` à PHP. Sinon WordPress répond `rest_not_logged_in` même avec un faux mot de passe : ajouter en tête du `.htaccess` `RewriteEngine On` puis `RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]`.
- **`basic` + `WP_AUTH_HEADER=X-Claude-Auth`** : mêmes identifiants, envoyés dans un en-tête que l'hébergeur ne retire pas. Il faut alors installer côté site le snippet `docs/wpcode-snippet.php` (extension WPCode, sans accès au serveur) qui le recopie vers les variables que WordPress lit pour valider le mot de passe d'application. Le mot de passe reste révocable.
- **`cookie`** : le serveur se connecte à `wp-login.php` avec `WP_USER` / `WP_PASSWORD`, lit le jeton REST sur `admin-ajax.php?action=rest-nonce` et l'envoie dans `X-WP-Nonce`. Une reconnexion automatique (une seule par requête) si la session ou le jeton expire. Contourne le problème de l'en-tête `Authorization`, mais :
  - il faut le **mot de passe complet** du compte : utiliser un compte dédié (rôle Éditeur, ou Administrateur seulement si on veut lire extensions, utilisateurs et réglages), jamais le compte personnel d'un administrateur ;
  - ne fonctionne pas si la connexion est protégée par une double authentification, un captcha ou une extension de sécurité (cas du site de l'école : Captcha by BestWebSoft sur `wp-login.php`) ;
  - le POST vers `wp-login.php` est la seule requête non-GET du connecteur (chemin fixe, hors `request`).

Sans identifiants (`WP_USER` absent), le serveur fonctionne en anonyme : contenu publié seulement, pas de métas (`_elementor_data`, `mec_*`), pas de menus, formulaires, extensions ni utilisateurs.

## Limites constatées sur le site de l'école (21/10/2026)

- **Événements MEC** : MEC Lite range les dates dans sa propre table, hors de l'API REST ; `wp_evenements` les lit dans le JSON-LD (`schema.org/Event`) de chaque page publique d'événement.
  - Écriture : pas d'outil du connecteur ; MEC Lite importe son flux XML (wp-admin › M.E. Calendar › Importer/Exporter › « Import MEC XML Feed »). Un `<item>` avec un `ID` de flux déjà connu (méta `mec_feed_event_id`) met à jour l'événement, y compris s'il est à la corbeille (il est republié) ; les catégories s'ajoutent.
  - Image à la une : `<featured_image><full>URL</full></featured_image>` est posée si l'événement n'en a pas ; MEC réutilise le média dont le `guid` est cette URL. Envoyer d'abord l'image avec `wp_media_envoyer_preparer` (texte alternatif compris), puis importer avec son URL : aucun doublon. Pour la changer : événement à la corbeille, `wp_medias_supprimer_preparer` sur l'ancien média (WordPress retire aussi les `_thumbnail_id`), nouveau média sous le même nom, réimport du même `ID`.
  - `outils/illustrations_agenda.py` produit des illustrations sans texte (1200×675, pictogramme Tabler centré sur fond de couleur, confettis), qui résistent aux recadrages de l'agenda.
- **Formulaires** : le site utilise **Forminator**, qui n'expose pas ses réponses en REST. Le snippet `docs/wpcode-snippet-forminator.php` (WPCode, à côté de celui d'authentification) ajoute deux routes en lecture seule, réservées aux administrateurs (`wpmcp/v1/forminator/forms` et `…/forms/<id>/entries`), qui passent par l'API PHP de Forminator et ne renvoient pas l'adresse IP. Sans lui, `wp_formulaires_soumissions` le signale dans `notes`. Les formulaires Elementor Pro, s'il y en a, sont lus en plus.
- **Snippets WPCode** : WPCode n'a pas d'API REST. Le snippet `docs/wpcode-snippet-gestion.php` (à installer une fois à la main, titre « MCP Gestion des snippets », « Exécuter partout ») ajoute les routes `wpmcp/v1/snippets` réservées aux administrateurs ayant `unfiltered_html` : lecture, création de snippets PHP « MCP … » toujours inactifs, remplacement du code (empreinte SHA-256 vérifiée, refus si le code a changé depuis la lecture), activation/désactivation par l'API de WPCode (test du code avant activation). WPCode enregistre le code sans le protéger des antislashs : le snippet impose le code exact pendant l'enregistrement et renvoie `integre`. Aucune suppression ; désactiver ce snippet dans WPCode coupe l'accès.
- **Tableaux TablePress** : la version gratuite n'a pas d'API REST. Le snippet `docs/wpcode-snippet-tablepress.php` (« MCP Tableaux TablePress ») ajoute `wpmcp/v1/tablepress/tables` (liste), `…/tables/<id>` (cellules + SHA-256) et `POST …/tables/<id>/cellules` (remplacement de cellules existantes, empreinte vérifiée, 200 au plus), réservés à `manage_options` + `unfiltered_html`, via `TablePress::$model_table` (load / save).
- **Snippets facultatifs** (dans `docs/`, installables avec `wp_snippet_installer_preparer`, puis à activer) :
  - `wpcode-snippet-securite.php` : en-têtes de sécurité (HSTS, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy), balises « generator » retirées, zoom mobile réactivé, auteurs et routes `/wp/v2/users` fermés aux visiteurs non connectés, index REST allégé, cookies de session sécurisés. Avec WP Super Cache en mode simple, cocher « Mettre en cache les en-têtes HTTP » pour que les en-têtes accompagnent les pages en cache.
  - `wpcode-snippet-performances.php` (visiteurs non connectés) : préchargement de la première image du diaporama d'en-tête du thème (`fetchpriority=high`) ; lien Google Fonts du thème remplacé par le fichier local d'Elementor quand il existe (doublon retiré) ; styles de Captcha by BestWebSoft et Dashicons non chargés sur les pages publiques ; hors des pages d'événement, blocs JSON-LD `Event` de Modern Events Calendar retirés (le mini-calendrier de la barre latérale en écrit un par jour d'événement, avec une description qui recopie le contenu de la page). Les pages d'événement les gardent : `wp_evenements` y lit les dates.
  - `wpcode-snippet-referencement.php` : balise meta description à partir de l'extrait manuel (écrit avec `wp_extraits_preparer`), sauf si une extension SEO est active ; textes restés en anglais traduits par `gettext` (formulaire de recherche, « read more », libellés « Expired! » / « Ongoing... » de Modern Events Calendar) ; ni nom ni avatar d'auteur, ni date ni partage dans les résultats de recherche ; page 404 et « You May Also Like » en français ; tableaux TablePress défilables horizontalement sur mobile.

## Pages Elementor

Une page Elementor stocke sa mise en page dans la méta `_elementor_data` (JSON). `wp_contenu_lire(format="elementor")` parcourt cet arbre et renvoie, pour chaque widget, ses textes, liens et images (`wordpress_mcp/elementor.py`), ce qui donne une lecture bien plus fidèle que le HTML rendu. Les widgets Royal Addons / Elementor Pro sont couverts par les mêmes clés de réglages (`title`, `editor`, `text`, `shortcode`, listes…) ; une clé manquante s'ajoute dans `TEXT_KEYS` / `LIST_KEYS`. Les widgets de listes calculées à l'affichage (`posts`, `loop-grid`, grilles Royal Addons… : `DYNAMIC_WIDGETS`) n'ont pas de texte en dur : on renvoie leur requête dans `requete` (type de contenu, nombre, termes, texte du lien « lire la suite », modèle).

## Sécurité

- Lecture : `client.check_read_only` refuse toute méthode autre que `GET`/`HEAD` et tout espace de noms hors liste blanche — avant même d'ouvrir une connexion (test `test_request_refuses_write_before_any_http`). Les espaces d'export/import/installation (`batch/v1`, `ai1wm`, `elementor-one`, `image-optimizer`, `hub-connector`…) sont volontairement exclus.
- Le mot de passe d'application et le mot de passe du mode cookie ne sont jamais renvoyés par un outil ni journalisé (`httpx` au niveau WARNING, logs sur stderr).
- Les soumissions de formulaires contiennent des données personnelles : à ne consulter qu'au besoin.
- `wp_sante` interroge aussi `api.wordpress.org` (module `wporg.py`) : hôte et chemins fixes, GET seulement, aucun identifiant envoyé ; seul le nom public (slug) des extensions et thèmes installés part. `mises_a_jour=False` évite cet appel.
- Écriture : seulement par `client.write`, appelée par les exécuteurs de `write_tools.py` après confirmation, et limitée à `ALLOWED_WRITES` (voir « Outils d'écriture »). Tests : `tests/test_writes.py`.
- Le transport `streamable-http` est désactivé tant qu'il n'y a pas d'authentification par utilisateur (voir `mcp-entra-auth`).

## Évolutions prévues

- Événements Modern Events Calendar en écriture (dates hors API REST : nécessite une route côté site).
- Version PHP et tests « directs » de Santé du site (route côté site).
- Déploiement distant.

## Erreurs réseau

Délai dépassé (`delai_depasse`), site injoignable (`site_injoignable`) et erreurs 5xx sont traduits en `WordPressError` avec un message explicite : sans cela, Claude Desktop n'affiche que « Error executing tool ». Quand l'hébergement mutualisé sature, une image statique répond elle aussi lentement : c'est le signe que le problème vient du serveur et non de WordPress.

## Outils annexes

- `outils/illustrations_agenda.py evenements.json sortie/` : illustrations PNG colorées pour l'agenda (voir l'en-tête du fichier ; pictogrammes `@tabler/icons` via npm, rendu par Playwright/Chromium).

## Développement

Conventions identiques aux autres connecteurs de l'auteur : outils en français, tests hors ligne (`respx`) pour chaque garde-fou, aucune donnée réelle dans le dépôt, commit uniquement après validation en réel.
