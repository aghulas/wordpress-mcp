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
 *   « Ongoing... » → « En cours ».
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
	}
	return $translation;
}, 20, 2 );

// Page de résultats : ni nom ni avatar d'auteur sous chaque résultat (pas d'appel à Gravatar).
add_filter( 'get_avatar', function ( $avatar ) {
	return ( ! is_admin() && is_search() ) ? '' : $avatar;
}, 20 );

add_action( 'wp_head', function () {
	if ( is_search() ) {
		echo '<style id="wpmcp-recherche">.search .post-author,.search .post-author + .meta-sep,.search .meta-sep + .post-author{display:none!important}</style>' . "\n";
	}
}, 99 );
