# wordpress-mcp

Serveur [MCP](https://modelcontextprotocol.io) en **lecture seule** pour un site WordPress, pensé pour le site d'une école (WordPress + Elementor + Modern Events Calendar + Redirection), mais utilisable sur n'importe quel site qui expose l'API REST `/wp-json`.

Il permet à Claude (Claude Desktop) de relire le contenu du site, de retrouver un texte, d'auditer les extensions, les redirections, les 404 et les soumissions de formulaires, et de préparer des mises à jour — que l'utilisateur fera ensuite lui-même dans `wp-admin`. **Rien n'est créé, modifié ni supprimé** : seule la méthode `GET` est autorisée, et seuls quelques espaces de noms REST sont joignables (cœur `wp/v2`, Elementor, MEC, Redirection, santé du site).

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

## Outils (16, lecture seule)

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

- `client.check_read_only` refuse toute méthode autre que `GET`/`HEAD` et tout espace de noms hors liste blanche — avant même d'ouvrir une connexion (test `test_request_refuses_write_before_any_http`). Les espaces d'export/import/installation (`batch/v1`, `ai1wm`, `elementor-one`, `image-optimizer`, `hub-connector`…) sont volontairement exclus.
- Le mot de passe d'application et le mot de passe du mode cookie ne sont jamais renvoyés par un outil ni journalisé (`httpx` au niveau WARNING, logs sur stderr).
- Les soumissions de formulaires contiennent des données personnelles : à ne consulter qu'au besoin.
- Le transport `streamable-http` est désactivé tant qu'il n'y a pas d'authentification par utilisateur (voir `mcp-entra-auth`).

## Évolutions prévues

- Écriture avec garde-fou aperçu / confirmation (brouillon d'article, texte d'un widget Elementor, remplacement d'un PDF), comme dans `edumoov-mcp`.
- Santé du site (`wp-site-health/v1`) : versions PHP/WordPress, mises à jour disponibles.
- Déploiement distant.

## Développement

Conventions identiques aux autres connecteurs de l'auteur : outils en français, tests hors ligne (`respx`) pour chaque garde-fou, aucune donnée réelle dans le dépôt, commit uniquement après validation en réel.
