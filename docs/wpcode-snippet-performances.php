<?php
/**
 * Performances d'affichage (snippet WPCode, sans accès au serveur), pour les visiteurs non connectés.
 *
 * - Image d'en-tête : précharge la première image du diaporama d'en-tête du thème (classe
 *   « entry-header-slider-item » ou « entry-header », image en arrière-plan CSS), avec
 *   fetchpriority="high". Sans cela, le navigateur ne la découvre qu'après les CSS et scripts :
 *   c'est l'élément le plus lent à s'afficher sur mobile (Largest Contentful Paint).
 * - Polices Google du thème : quand Elementor sert déjà la même famille depuis le site
 *   (uploads/elementor/google-fonts/css/<famille>.css, réglage « polices Google en local »), le
 *   lien vers fonts.googleapis.com est remplacé par ce fichier local, et le doublon d'Elementor
 *   n'est pas répété. Une requête bloquante vers Google en moins, et plus d'adresse IP des
 *   visiteurs transmise à Google pour ces polices.
 * - Captcha by BestWebSoft : ses feuilles de style (et Dashicons qu'elles entraînent) ne sont plus
 *   chargées sur les pages publiques pour les visiteurs non connectés ; le captcha reste sur la
 *   page de connexion. Si une autre extension a besoin de Dashicons, WordPress le charge quand même.
 *
 * Installation : WPCode › Ajouter un extrait › Extrait PHP ; coller ce fichier SANS la ligne
 * « <?php » ; Insertion « Exécuter partout » ; Activer. Pour tout annuler : désactiver le snippet.
 * Après activation ou désactivation : vider le cache de page.
 */

if ( ! function_exists( 'wpmcp_perf_front' ) ) {
	function wpmcp_perf_front() {
		return ! is_admin() && ! wp_doing_ajax() && ! ( defined( 'REST_REQUEST' ) && REST_REQUEST )
			&& ! is_user_logged_in();
	}

	/** Famille d'une URL fonts.googleapis.com/css?family=X:poids → nom de fichier local d'Elementor. */
	function wpmcp_perf_local_font( $src ) {
		if ( ! is_string( $src ) || false === strpos( $src, 'fonts.googleapis.com/css' ) ) {
			return null;
		}
		$query = wp_parse_url( html_entity_decode( $src ), PHP_URL_QUERY );
		parse_str( (string) $query, $args );
		if ( empty( $args['family'] ) || false !== strpos( $args['family'], '|' ) ) {
			return null; // plusieurs familles : on ne touche à rien
		}
		$family = strtolower( preg_replace( '/[^A-Za-z0-9]/', '', strtok( $args['family'], ':' ) ) );
		$up     = wp_get_upload_dir();
		$file   = trailingslashit( $up['basedir'] ) . 'elementor/google-fonts/css/' . $family . '.css';
		if ( '' === $family || ! file_exists( $file ) ) {
			return null;
		}
		return array(
			'family' => $family,
			'url'    => trailingslashit( $up['baseurl'] ) . 'elementor/google-fonts/css/' . $family . '.css?ver=' . filemtime( $file ),
		);
	}
}

// Polices : lien Google du thème → fichier local d'Elementor ; doublon d'Elementor retiré.
add_filter( 'style_loader_tag', function ( $tag, $handle, $href ) {
	static $done = array();
	if ( ! wpmcp_perf_front() ) {
		return $tag;
	}
	if ( 0 === strpos( $handle, 'elementor-gf-local-' ) ) {
		$family = substr( $handle, strlen( 'elementor-gf-local-' ) );
		return isset( $done[ $family ] ) ? '' : $tag;
	}
	$local = wpmcp_perf_local_font( $href );
	if ( ! $local ) {
		return $tag;
	}
	$done[ $local['family'] ] = true;
	return preg_replace( '#href=([\'"]).*?\1#', "href='" . esc_url( $local['url'] ) . "'", $tag, 1 );
}, 20, 3 );

// Captcha by BestWebSoft : pas de feuilles de style sur les pages publiques (visiteurs).
add_action( 'wp_enqueue_scripts', function () {
	if ( ! wpmcp_perf_front() ) {
		return;
	}
	foreach ( array( 'cptch_stylesheet', 'cptch_desktop_style', 'cptch_mobile_style' ) as $handle ) {
		wp_dequeue_style( $handle );
	}
	wp_dequeue_style( 'dashicons' );
}, 100 );

// Image d'en-tête : préchargement de la première image du diaporama.
add_action( 'template_redirect', function () {
	if ( ! wpmcp_perf_front() || is_feed() ) {
		return;
	}
	ob_start( function ( $html ) {
		if ( ! is_string( $html ) || false === stripos( $html, '<head' ) || false !== stripos( $html, 'rel="preload" as="image"' ) ) {
			return $html;
		}
		$pattern = '#class=["\'][^"\']*\bentry-header(?:-slider-item)?\b[^"\']*["\'][^>]*?style=["\'][^"\']*background-image\s*:\s*url\(\s*(?:&quot;|["\'])?([^"\')&]+)#i';
		if ( ! preg_match( $pattern, $html, $m ) ) {
			return $html;
		}
		$url = esc_url( $m[1] );
		if ( '' === $url ) {
			return $html;
		}
		$link = '<link rel="preload" as="image" href="' . $url . '" fetchpriority="high" />' . "\n";
		$out  = preg_replace( '#(<meta\s+charset=[^>]*>\s*)#i', '$1' . $link, $html, 1, $count );
		return $count ? $out : preg_replace( '#(<head[^>]*>)#i', '$1' . "\n" . $link, $html, 1 );
	} );
}, 1 );
