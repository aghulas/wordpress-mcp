<?php
/**
 * MCP Options orphelines non autoloadées — snippet WPCode PONCTUEL.
 *
 * Rôle : ne plus charger automatiquement, à chaque page, des options laissées par des thèmes ou
 * extensions qui ne sont plus installés. Les options ne sont PAS supprimées : seul leur indicateur
 * « autoload » passe à non (réversible avec wp_set_options_autoload( $noms, true )).
 *
 * Liste à adapter au site d'après le diagnostic (wpcode-snippet-diagnostic-options.php) ; n'y mettre
 * que des options dont le thème ou l'extension d'origine est absent.
 *
 * Fonctionnement : au prochain chargement d'une page d'administration (admin_init), pour un
 * administrateur, passe l'autoload à non pour les options listées qui l'ont encore. Sans effet ensuite.
 * WPCode : « Extrait PHP », « Exécuter partout » ; activer, ouvrir une page de wp-admin, désactiver.
 */
add_action( 'admin_init', function () {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	$noms = array(
		'extend_builder_theme_1567023592', // thème Colibri / ExtendThemes (absent)
		'extend_builder_theme',            // idem
		'colibri_page_builder_demo_sites', // idem
		'um_cache_fonticons',              // extension Ultimate Member (absente)
		'um_options',                      // idem
		'wallstreet_pro_options',          // thème Wallstreet Pro (absent)
		'mp3FoxAdminOptions',              // extension MP3-jPlayer (absente)
		'tribe_events_calendar_options',   // extension The Events Calendar (absente)
		'graphene_settings',               // thème Graphene (absent)
	);
	if ( function_exists( 'wp_set_options_autoload' ) ) {
		wp_set_options_autoload( $noms, false );
	} else {
		global $wpdb;
		foreach ( $noms as $nom ) {
			$wpdb->update( $wpdb->options, array( 'autoload' => 'no' ), array( 'option_name' => $nom ) );
		}
		wp_cache_delete( 'alloptions', 'options' );
	}
} );
