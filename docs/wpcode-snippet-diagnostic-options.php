<?php
/**
 * MCP Diagnostic des options chargées automatiquement — snippet WPCode ÉPHÉMÈRE, lecture seule.
 *
 * Rôle : répondre à l'alerte « Les options chargées automatiquement peuvent affecter les performances »
 * de la Santé du site, en listant les options autoloadées les plus lourdes (nom et taille seulement,
 * jamais la valeur).
 *
 * Route : GET /wp-json/wpmcp/v1/diagnostic/autoload?limite=40
 *   -> { nombre, taille_totale, options: [ { nom, taille, autoload } ] }
 * Accès : administrateurs (manage_options) uniquement.
 *
 * WPCode : type « Extrait PHP », emplacement « Exécuter partout ». À désactiver puis supprimer
 * une fois le diagnostic fait.
 */
add_action( 'rest_api_init', function () {
	register_rest_route( 'wpmcp/v1', '/diagnostic/autoload', array(
		'methods'             => 'GET',
		'permission_callback' => function () {
			return current_user_can( 'manage_options' );
		},
		'callback'            => function ( WP_REST_Request $request ) {
			global $wpdb;
			$limite = max( 1, min( 100, (int) $request->get_param( 'limite' ) ?: 40 ) );
			$valeurs = function_exists( 'wp_autoload_values_to_autoload' )
				? wp_autoload_values_to_autoload()
				: array( 'yes', 'on', 'auto-on', 'auto' );
			$in = implode( ',', array_fill( 0, count( $valeurs ), '%s' ) );
			$total = $wpdb->get_row( $wpdb->prepare(
				"SELECT COUNT(*) AS nombre, SUM(LENGTH(option_value)) AS taille FROM {$wpdb->options} WHERE autoload IN ($in)",
				$valeurs
			) );
			$lignes = $wpdb->get_results( $wpdb->prepare(
				"SELECT option_name AS nom, LENGTH(option_value) AS taille, autoload FROM {$wpdb->options} WHERE autoload IN ($in) ORDER BY taille DESC LIMIT %d",
				array_merge( $valeurs, array( $limite ) )
			) );
			return array(
				'nombre'        => (int) $total->nombre,
				'taille_totale' => (int) $total->taille,
				'options'       => array_map( function ( $l ) {
					return array( 'nom' => $l->nom, 'taille' => (int) $l->taille, 'autoload' => $l->autoload );
				}, $lignes ),
			);
		},
	) );
} );
