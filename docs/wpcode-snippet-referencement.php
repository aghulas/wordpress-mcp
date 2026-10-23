<?php
/**
 * Référencement et recherche interne (snippet WPCode, sans extension SEO).
 *
 * - Balise <meta name="description"> sur les pages et articles qui ont un extrait saisi à la main
 *   (champ « Extrait », écrit par le connecteur avec wp_extraits_preparer), et sur la page
 *   d'accueil statique (son extrait). Rien n'est ajouté sans extrait manuel, ni si une extension
 *   SEO connue (Yoast, Rank Math, All in One SEO, SEOPress, The SEO Framework) est active.
 * - Recherche du site : textes en français dans le formulaire du thème (« Type & hit Enter... »,
 *   « Search... » → « Rechercher… », « read more » → « lire la suite »), et nom et avatar de l'auteur masqués dans
 *   la page de résultats.
 * - Agenda (Modern Events Calendar, libellés non traduits) : « Expired! » → « Terminé »,
 *   « Ongoing... » → « En cours » ; traduction fautive « Vu en liste » → « Vue en liste ».
 * - Page 404 et articles du thème en français (« Page not found! », « You May Also Like »…).
 * - Affichage : sur mobile, les tableaux TablePress défilent horizontalement au lieu d'être
 *   coupés ; dans les résultats de recherche, ni date de publication ni boutons de partage.
 *
 * Installation : WPCode › Ajouter un extrait › Extrait PHP ; coller ce fichier SANS la ligne
 * « <?php » ; Insertion « Exécuter partout » ; Activer. Pour tout annuler : désactiver le snippet.
 * Après activation ou désactivation : vider le cache de page.
 */

add_action( 'wp_head', function () {
	if ( defined( 'WPSEO_VERSION' ) || defined( 'RANK_MATH_VERSION' ) || defined( 'AIOSEO_VERSION' )
		|| defined( 'SEOPRESS_VERSION' ) || defined( 'THE_SEO_FRAMEWORK_VERSION' ) ) {
		return;
	}
	$post_id = 0;
	if ( is_front_page() && 'page' === get_option( 'show_on_front' ) ) {
		$post_id = (int) get_option( 'page_on_front' );
	} elseif ( is_singular() ) {
		$post_id = (int) get_queried_object_id();
	}
	if ( ! $post_id || ! has_excerpt( $post_id ) ) {
		return;
	}
	$text = trim( preg_replace( '/\s+/', ' ', wp_strip_all_tags( get_post_field( 'post_excerpt', $post_id ) ) ) );
	if ( '' !== $text ) {
		echo '<meta name="description" content="' . esc_attr( $text ) . '" />' . "\n";
	}
}, 1 );

// Textes du thème (recherche, résumés) et de l'agenda MEC laissés en anglais.
add_filter( 'gettext', function ( $translation, $text ) {
	switch ( $text ) {
		case 'Type &amp; hit Enter...':
		case 'Type & hit Enter...':
		case 'Search...':
			return 'Rechercher…';
		case 'read more':
		case 'Read More':
			return 'lire la suite';
		case 'Expired!': // Modern Events Calendar : événement passé
			return 'Terminé';
		case 'Ongoing...': // Modern Events Calendar : événement en cours
			return 'En cours';
		case 'Page not found!':
		case 'Page Not Found!':
			return 'Page introuvable';
		case 'Homepage':
			return "la page d'accueil";
		case 'You May Also Like':
			return 'À lire aussi';
	}
	if ( 0 === strpos( $text, 'It seems we can' ) ) { // page 404 du thème
		return 'La page demandée n\'existe pas ou a été déplacée. Utilisez la recherche ou revenez à ';
	}
	if ( 'Vu en liste' === $translation ) { // traduction fautive de MEC
		return 'Vue en liste';
	}
	return $translation;
}, 20, 2 );

// Affichage : tableaux TablePress lisibles sur mobile ; résultats de recherche sans date ni partage.
add_action( 'wp_head', function () {
	echo '<style id="wpmcp-affichage">@media (max-width:767px){.tablepress{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}}'
		. '.search .post-date,.search .post-meta .meta-sep,.search .post-share{display:none!important}</style>' . "\n";
}, 99 );

// Page de résultats : ni nom ni avatar d'auteur sous chaque résultat (pas d'appel à Gravatar).
add_filter( 'get_avatar', function ( $avatar ) {
	return ( ! is_admin() && is_search() ) ? '' : $avatar;
}, 20 );

add_action( 'wp_head', function () {
	if ( is_search() ) {
		echo '<style id="wpmcp-recherche">.search .post-author,.search .post-author + .meta-sep,.search .meta-sep + .post-author{display:none!important}</style>' . "\n";
	}
}, 99 );
