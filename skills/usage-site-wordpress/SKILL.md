---
name: "usage-site-wordpress"
description: "Consulter, auditer ou mettre à jour un site WordPress via le connecteur wordpress-mcp (pages, widgets Elementor, tableaux, menus, médias, redirections, agenda), écritures avec aperçu et confirmation."
---

# Utiliser le connecteur WordPress

Le connecteur `wordpress` lit tout le site. Quand `wp_statut` indique que les écritures sont activées, il peut modifier certains contenus, **toujours en deux temps** : un outil `*_preparer` renvoie un aperçu (avant → après) et un jeton ; `wp_ecriture_confirmer` exécute, uniquement après l'accord explicite de la personne. Il ne supprime rien, sauf des fichiers de la médiathèque (avec copie locale).

## Démarrage
1. `wp_statut` : connexion, compte, mode. Erreur `rest_not_logged_in` ou outils absents : skill `maintenance-wordpress-mcp`. « Site injoignable » ou délai dépassé : voir « Site lent ».
2. Choisir l'outil : retrouver un texte (`wp_recherche` puis `wp_contenu_lire`, format `texte` ou `elementor`), plan du site (`wp_arborescence`, `wp_pages_list`), articles (`wp_articles_list`), historique (`wp_revisions`), agenda (`wp_evenements`), tableaux (`wp_tableaux`), menus (`wp_menus`), fichiers (`wp_medias_list`), formulaires (`wp_formulaires_soumissions`), snippets (`wp_snippets`), santé (`wp_sante`), audit (`wp_extensions`, `wp_utilisateurs`, `wp_redirections`, `wp_reglages`).

## Règle commune pour modifier
- Montrer à la personne l'aperçu **et les avertissements** (en particulier « visible immédiatement »), attendre un accord explicite pour **cette** modification, puis confirmer. Sans accord : annuler le jeton. Ne jamais confirmer de sa propre initiative.
- Plusieurs éléments différents : montrer tous les aperçus ensemble. Plusieurs modifications de la **même page** : une par une (après une écriture, les jetons préparés avant sur cette page sont refusés).
- Un jeton vaut quelques minutes et ne sert qu'une fois : s'il a expiré, en préparer un nouveau et vérifier qu'il est identique à l'aperçu approuvé.
- « Le site n'a pas répondu dans le délai » sur une écriture : elle a peut-être été appliquée. Relire l'état avant de recommencer. Un 500 isolé est souvent une surcharge passagère : réessayer une fois.

## Corriger du texte (Elementor V3)
- Lire la page en `format="elementor"` pour repérer le widget et le champ (`title`, `editor`, `text`, ou `liste.2.text`).
- `wp_widget_texte_preparer(id, widget_id, champ, nouveau_texte, remplacer="extrait exact")` : seul l'extrait change, la mise en forme est conservée. L'extrait doit être unique et ne pas traverser une balise (gras, lien) ; inclure la balise dans l'extrait si besoin. Sur une page publiée, la modification est en ligne dès la confirmation ; l'ancien contenu est sauvegardé.
- Plusieurs dizaines de corrections : script avec liste JSON (page, ancien, nouveau) qui signale d'abord les extraits ambigus ; montrer la liste complète et n'appliquer qu'après accord.

## Tableaux (TablePress)
`wp_tableaux()` pour la liste, `wp_tableaux(id)` pour les cellules (à partir de 1). `wp_tableau_cellules_preparer(id, [{ligne, colonne, remplacer?, nouveau}])` : pas d'ajout ni de suppression de ligne ou de colonne, ne pas toucher aux marqueurs `#colspan#` / `#rowspan#`. Les montants se changent seulement sur décision de l'organisation.

## Nouvelle page
Rester dans le format du site (V3) : créer la page vide en brouillon (`wp_page_creer_preparer`), copier la structure d'une page publiée proche (`wp_page_modele_preparer` avec les textes), faire relire l'aperçu, écrire le résumé (`wp_extraits_preparer`, 120 à 160 caractères, texte brut), puis publier (`wp_page_preparer`), ajouter l'entrée de menu, redirections éventuelles, vider le cache.

## Autres écritures
Régénérer le texte de secours (`wp_page_regenerer_preparer`), restaurer une sauvegarde (`wp_page_restaurer_preparer`), titre / statut / adresse (`wp_page_preparer`, créer ensuite la redirection de l'ancienne adresse), menus (`wp_menu_entree_ajouter_preparer`, `wp_menu_entree_preparer`, pas de suppression), articles (`wp_article_preparer`, brouillon par défaut), médias (`wp_media_envoyer_preparer`, `wp_medias_alt_preparer`, `wp_medias_supprimer_preparer`), extensions (mises à jour depuis wordpress.org avec `wp_extensions_maj_preparer`, suppression d'une extension inactive avec `wp_extension_supprimer_preparer`, avec le snippet `docs/wpcode-snippet-maintenance.php`), redirections (`wp_redirection_preparer`, `wp_redirection_activer_preparer`), snippets (`wp_snippet_installer_preparer`, `wp_snippet_activer_preparer`, depuis des fichiers relus du dépôt).

## Agenda (Modern Events Calendar Lite)
Lecture par `wp_evenements` (dates lues sur la page de chaque événement). Le connecteur n'écrit pas dans MEC : passer par wp-admin (import du flux XML MEC, mise à jour par identifiant de flux sans doublon). Journée entière par défaut, pas d'événements de plusieurs mois, pas de rendez-vous internes. Illustrations colorées sans texte (l'agenda recadre les images et affiche déjà le titre).

## Formulaires
`wp_formulaires_soumissions(formulaire="<id>")`. Beaucoup de réponses peuvent être du spam : les écarter sans les recopier. Données de familles ou de personnes : ne restituer que ce qui est demandé, ne pas les recopier dans un fichier ou la mémoire sans demande explicite, respecter la durée de conservation annoncée.

## Cache
Une page modifiée par le connecteur est vidée automatiquement. Menus, widgets, réglages du thème, extensions, agenda, fenêtres popup : vider tout le cache de l'extension de cache, puis vérifier en visiteur (fenêtre non connectée ou `curl`).

## Site lent
Mesurer une image statique, `/wp-json/` et une page en cache. Statique lent aussi : serveur saturé (hébergement). Seul WordPress lent : base de données, extension, tâche planifiée. Attendre, ne pas enchaîner les écritures, vérifier l'effet d'une écriture en délai dépassé.

## Travailler dans wp-admin (navigateur intégré)
La personne se connecte elle-même ; ne jamais saisir de mot de passe, secret ou code d'autorisation. Réglages et formulaires : modifier seulement avec un accord précis qui nomme le réglage. Mise à la corbeille possible ; suppressions définitives par la personne. Les fenêtres de confirmation natives sont bloquées par le navigateur intégré : donner le lien direct à la personne.