<?php
/**
 * Durcissement léger d'un site WordPress (snippet WPCode, sans accès au serveur).
 *
 * - En-têtes de sécurité : HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
 *   Permissions-Policy (pas de Content-Security-Policy : elle casse facilement Elementor,
 *   les bandeaux cookies et les vidéos ; à étudier à part).
 * - Retire l'en-tête « X-Powered-By: PHP/x.y » (l'en-tête « Server: Apache » dépend de
 *   l'hébergeur et ne peut pas être retiré depuis WordPress).
 * - Retire les balises « generator » (versions de WordPress, Elementor…) et réactive le zoom
 *   sur mobile (retire maximum-scale / user-scalable=no de la balise viewport du thème).
 * - Ne liste plus les auteurs : sitemap des utilisateurs supprimé, archives d'auteur
 *   redirigées vers l'accueil, routes REST /wp/v2/users fermées aux visiteurs non
 *   connectés.
 * - Index REST (/wp-json/) : pour les visiteurs non connectés, n'affiche plus la liste des
 *   espaces de noms (extensions). Les routes elles-mêmes restent accessibles (formulaires,
 *   bandeau cookies, agenda, connecteurs).
 * - Cookies de session PHP : attributs Secure, HttpOnly, SameSite=Lax (si la session n'a pas
 *   déjà été ouverte par une extension avant ce snippet).
 *
 * Installation : WPCode › Ajouter un extrait › Extrait PHP ; coller ce fichier SANS la ligne
 * « <?php » ; Insertion : « Exécuter partout » ; Activer. Pour tout annuler : désactiver le snippet.
 */

if ( PHP_SAPI !== 'cli' && session_status() === PHP_SESSION_NONE ) {
	@ini_set( 'session.cookie_secure', '1' );
	@ini_set( 'session.cookie_httponly', '1' );
	@ini_set( 'session.cookie_samesite', 'Lax' );
}

add_action( 'send_headers', function () {
	if ( headers_sent() ) {
		return;
	}
	header_remove( 'X-Powered-By' );
	if ( is_ssl() ) {
		header( 'Strict-Transport-Security: max-age=31536000' );
	}
	header( 'X-Frame-Options: SAMEORIGIN' );
	header( 'X-Content-Type-Options: nosniff' );
	header( 'Referrer-Policy: strict-origin-when-cross-origin' );
	header( 'Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()' );
} );

// Les réponses REST passent aussi par ici (send_headers n'est pas toujours appelé pour elles).
add_filter( 'rest_post_dispatch', function ( $response ) {
	if ( ! headers_sent() ) {
		header_remove( 'X-Powered-By' );
		header( 'X-Content-Type-Options: nosniff' );
	}
	return $response;
} );

// Versions affichées dans le code des pages et les flux.
remove_action( 'wp_head', 'wp_generator' );
add_filter( 'the_generator', '__return_empty_string' );

// Zoom mobile + balises « generator » ajoutées par les extensions (Elementor…), sur les pages publiques.
add_action( 'template_redirect', function () {
	if ( is_admin() || wp_doing_ajax() || ( defined( 'REST_REQUEST' ) && REST_REQUEST ) || is_feed() ) {
		return;
	}
	ob_start( function ( $html ) {
		if ( ! is_string( $html ) || stripos( $html, '<head' ) === false ) {
			return $html;
		}
		$html = preg_replace( '#<meta\s+name=["\']generator["\'][^>]*>\s*#i', '', $html );
		$html = preg_replace_callback( '#<meta\s+name=["\']viewport["\']\s+content=["\']([^"\']*)["\']#i', function ( $m ) {
			$parts = array_filter( array_map( 'trim', explode( ',', $m[1] ) ), function ( $p ) {
				return $p !== '' && stripos( $p, 'maximum-scale' ) !== 0 && stripos( $p, 'user-scalable' ) !== 0;
			} );
			return str_replace( $m[1], implode( ', ', $parts ), $m[0] );
		}, $html );
		return $html;
	} );
}, 0 );

// Auteurs : pas de sitemap, pas d'archives publiques, routes REST réservées.
add_filter( 'wp_sitemaps_add_provider', function ( $provider, $name ) {
	return 'users' === $name ? false : $provider;
}, 10, 2 );

add_action( 'template_redirect', function () {
	if ( is_author() || ( isset( $_GET['author'] ) && ! is_admin() ) ) {
		wp_safe_redirect( home_url( '/' ), 301 );
		exit;
	}
}, 1 );

// Routes /wp/v2/users retirées pour les visiteurs non connectés seulement (l'éditeur de WordPress
// des comptes connectés, auteurs compris, a besoin de /wp/v2/users/me). Un filtre rest_pre_dispatch
// ne suffisait pas sur ce site : la route répondait toujours.
add_filter( 'rest_endpoints', function ( $endpoints ) {
	if ( is_user_logged_in() ) {
		return $endpoints;
	}
	foreach ( array_keys( $endpoints ) as $route ) {
		if ( preg_match( '#^/wp/v2/users(/|$)#', $route ) ) {
			unset( $endpoints[ $route ] );
		}
	}
	return $endpoints;
}, 99 );

// Index REST : ne plus énumérer les extensions pour les visiteurs non connectés.
add_filter( 'rest_index', function ( $response ) {
	if ( is_user_logged_in() ) {
		return $response;
	}
	$data = $response->get_data();
	unset( $data['namespaces'], $data['routes'] );
	$response->set_data( $data );
	return $response;
} );
