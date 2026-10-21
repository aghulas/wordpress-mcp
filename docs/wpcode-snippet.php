<?php
/**
 * Snippet WPCode — « Claude : en-tête d'authentification de remplacement »
 *
 * Certains hébergements (Apache en CGI/FastCGI) retirent l'en-tête HTTP `Authorization` avant
 * que PHP ne le voie : les mots de passe d'application de WordPress sont alors toujours refusés
 * (erreur rest_not_logged_in, même avec un faux mot de passe).
 *
 * Ce code recopie un en-tête de remplacement, `X-Claude-Auth`, dans les variables que WordPress
 * lit pour valider un mot de passe d'application (PHP_AUTH_USER / PHP_AUTH_PW). Rien d'autre
 * n'est modifié : le mot de passe d'application reste vérifié par WordPress, révocable, et
 * limité à l'API REST. Le connecteur doit être configuré avec WP_AUTH_HEADER=X-Claude-Auth.
 *
 * WPCode : Code Snippets → Ajouter un snippet → « Ajouter votre code personnalisé (nouveau
 * snippet) » → type « Extrait PHP » → coller ce code SANS la balise <?php → Insertion :
 * « Exécuter partout » → Activer → Enregistrer.
 */
add_filter( 'determine_current_user', function ( $user_id ) {
	if ( empty( $_SERVER['PHP_AUTH_USER'] ) && ! empty( $_SERVER['HTTP_X_CLAUDE_AUTH'] ) ) {
		$header = trim( wp_unslash( $_SERVER['HTTP_X_CLAUDE_AUTH'] ) );
		if ( 0 === stripos( $header, 'basic ' ) ) {
			$decoded = base64_decode( substr( $header, 6 ), true );
			if ( false !== $decoded && false !== strpos( $decoded, ':' ) ) {
				list( $_SERVER['PHP_AUTH_USER'], $_SERVER['PHP_AUTH_PW'] ) = explode( ':', $decoded, 2 );
			}
		}
	}
	return $user_id;
}, 1 );

/**
 * Si un autre module a demandé l'utilisateur courant trop tôt (avant que la requête soit identifiée
 * comme une requête d'API REST), WordPress ne refait pas la vérification du mot de passe
 * d'application. On la refait une fois REST chargé, uniquement quand l'en-tête de remplacement
 * est présent et qu'aucun utilisateur n'est encore connecté.
 */
add_action( 'rest_api_init', function () {
	if ( ! empty( $_SERVER['HTTP_X_CLAUDE_AUTH'] ) && 0 === get_current_user_id() ) {
		$user_id = apply_filters( 'determine_current_user', false );
		if ( $user_id ) {
			wp_set_current_user( $user_id );
		}
	}
}, 1 );
