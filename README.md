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
| `WP_TIMEOUT` | délai HTTP en secondes (défaut 30) |
| `WP_PER_PAGE_MAX` | plafond d'éléments renvoyés par un outil de liste (défaut 200) |
| `WP_ENABLE_WRITES` | `1` pour autoriser les écritures avec confirmation (défaut : désactivées) |
| `WP_WRITE_TTL` | validité d'un jeton de confirmation, en secondes (défaut 600) |
| `WP_BACKUP_DIR` | sauvegardes locales des pages modifiées et journal des écritures (défaut `~/.wordpress-mcp/sauvegardes`) |
| `WP_UPLOAD_ROOTS` | dossiers d'où un fichier peut être envoyé, séparés par `:` (défaut `~/Documents:~/Downloads:~/Desktop`) |
| `WP_UPLOAD_MAX_MB` | taille maximale d'un fichier envoyé (défaut 20) |

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

## Outils de lecture (17)

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
| `wp_extensions` | extensions installées, inactives, thème actif | admin | ✔ |
| `wp_utilisateurs` | comptes et rôles | admin | ✔ |
| `wp_sante` | tests de santé WordPress, taille des dossiers et de la base, versions installées comparées à wordpress.org | admin | ✔ |

## Outils d'écriture (13, avec `WP_ENABLE_WRITES=1`)

| Outil | Rôle |
|---|---|
| `wp_article_preparer` | créer ou modifier un article (titre, texte HTML ou brut, catégories existantes, image à la une, extrait, statut : brouillon, en attente, publié, programmé, privé) |
| `wp_widget_texte_preparer` | remplacer le texte d'un widget Elementor (`title`, `editor`, `icon_list.2.text`…) d'une page ou d'un article — en entier, ou seulement un extrait avec `remplacer` (le reste du bloc et sa mise en forme sont conservés ; espaces insécables et apostrophes tolérés) ; sauvegarde locale de l'ancien contenu, relecture, vidage du cache Elementor |
| `wp_page_restaurer_preparer` | revenir à une sauvegarde locale (le contenu actuel est sauvegardé avant) |
| `wp_page_regenerer_preparer` | équivalent du bouton « Mettre à jour » d'Elementor : régénère le texte de secours d'une page Elementor (recherche, flux RSS, résumé affiché au partage du lien) à partir de son contenu, puis vide le cache Elementor ; le contenu affiché ne change pas. `wp_widget_texte_preparer` le fait aussi automatiquement quand l'ancien texte ne peut pas être remplacé directement |
| `wp_page_preparer` | changer le titre et/ou le statut d'une page ou d'un article existant (brouillon, privé, en attente, publié) ; signale les entrées de menu qui pointent vers la page |
| `wp_menu_entree_preparer` | modifier une entrée de menu : la faire pointer vers une autre page publiée ou une adresse, changer son libellé, la masquer (statut brouillon, réversible) |
| `wp_medias_alt_preparer` | textes alternatifs (et titres, légendes) de 1 à 50 images en une fois |
| `wp_media_envoyer_preparer` | envoyer une image (JPG, PNG, GIF, WEBP) ou un PDF du poste dans la médiathèque, avec titre, texte alternatif, légende |
| `wp_redirection_preparer` | créer une règle de l'extension Redirection (301, 302, 307, 308) |
| `wp_redirection_activer_preparer` | activer ou désactiver des règles (jamais de suppression) |
| `wp_ecriture_confirmer` | exécuter une écriture préparée, après accord explicite de l'utilisateur |
| `wp_ecriture_annuler` / `wp_ecritures_en_attente` | abandonner une écriture préparée / lister celles en attente |

Verrous (`writes.py`, `client.py`) :
1. `WP_ENABLE_WRITES=1` obligatoire, sinon même la préparation est refusée.
2. Jeton aléatoire, à usage unique, valable 10 minutes ; l'écriture exécutée est exactement celle de l'aperçu.
3. Liste blanche `ALLOWED_WRITES` (méthode + chemin exact) : créer/modifier un article, contenu, titre et statut d'une page ou d'un article existant, envoi d'un média et de ses textes, modification d'une entrée de menu existante, création et activation de redirections, vidage du cache Elementor. Aucun `DELETE` de contenu, aucune modification d'utilisateurs, d'extensions ni de réglages.
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
- **Formulaires** : le site utilise **Forminator**, qui n'expose pas ses réponses en REST. Le snippet `docs/wpcode-snippet-forminator.php` (WPCode, à côté de celui d'authentification) ajoute deux routes en lecture seule, réservées aux administrateurs (`wpmcp/v1/forminator/forms` et `…/forms/<id>/entries`), qui passent par l'API PHP de Forminator et ne renvoient pas l'adresse IP. Sans lui, `wp_formulaires_soumissions` le signale dans `notes`. Les formulaires Elementor Pro, s'il y en a, sont lus en plus.

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

## Développement

Conventions identiques aux autres connecteurs de l'auteur : outils en français, tests hors ligne (`respx`) pour chaque garde-fou, aucune donnée réelle dans le dépôt, commit uniquement après validation en réel.
