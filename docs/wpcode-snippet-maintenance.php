<?php
/**
 * Snippet WPCode — « MCP Maintenance des extensions »
 *
 * Ce code ajoute deux routes réservées aux administrateurs autorisés à gérer les extensions
 * (manage_options + update_plugins / delete_plugins) :
 *
 *   POST /wp-json/wpmcp/v1/maintenance/extensions/maj
 *        corps JSON : { "extensions": [ "captcha-bws/captcha-bws.php", ... ] }
 *        met à jour les extensions spécifiées depuis wordpress.org via Plugin_Upgrader.
 *        Si "extensions" est vide ou omis, met à jour toutes les extensions ayant une mise à jour disponible.
 *
 *   POST /wp-json/wpmcp/v1/maintenance/extensions/supprimer
 *        corps JSON : { "extension": "royal-elementor-addons/wpr-addons.php" }
 *        supprime une extension INACTIVE via delete_plugins(). Refusé si l'extension est active.
 *
 * WPCode : Code Snippets → Ajouter un snippet → « Ajouter votre code personnalisé »
 * → type « Extrait PHP » → coller ce code SANS la balise <?php → Insertion : « Exécuter partout »
 * → Activer → Enregistrer.
 */

add_action( 'rest_api_init', function () {
	$allowed = function () {
		return current_user_can( 'manage_options' );
	};

	register_rest_route( 'wpmcp/v1', '/maintenance/extensions/maj', array(
		'methods'             => 'POST',
		'callback'            => function ( WP_REST_Request $request ) {
			if ( ! current_user_can( 'update_plugins' ) && ! current_user_can( 'manage_options' ) ) {
				return new WP_Error( 'rest_forbidden', 'Droits insuffisants pour mettre à jour les extensions.', array( 'status' => 403 ) );
			}

			require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
			require_once ABSPATH . 'wp-admin/includes/plugin.php';
			require_once ABSPATH . 'wp-admin/includes/plugin-install.php';
			require_once ABSPATH . 'wp-admin/includes/file.php';
			require_once ABSPATH . 'wp-admin/includes/misc.php';

			// Rafraîchir les transients de mise à jour
			wp_update_plugins();

			$params = $request->get_json_params();
			$targets = isset( $params['extensions'] ) && is_array( $params['extensions'] ) ? $params['extensions'] : array();

			// Si non spécifié, prendre toutes les extensions avec mise à jour disponible
			$current = get_site_transient( 'update_plugins' );
			if ( empty( $targets ) ) {
				if ( ! empty( $current->response ) ) {
					$targets = array_keys( $current->response );
				}
			}

			if ( empty( $targets ) ) {
				return rest_ensure_response( array(
					'message'      => 'Aucune mise à jour requise ou disponible.',
					'mises_a_jour' => array(),
				) );
			}

			// Vérifier que les extensions cibles existent
			$installed = get_plugins();
			$valides = array();
			foreach ( $targets as $t ) {
				if ( isset( $installed[ $t ] ) ) {
					$valides[] = $t;
				} else {
					foreach ( array_keys( $installed ) as $inst_file ) {
						if ( $inst_file === $t || dirname( $inst_file ) === $t || strpos( $inst_file, $t . '/' ) === 0 ) {
							$valides[] = $inst_file;
							break;
						}
					}
				}
			}

			if ( empty( $valides ) ) {
				return new WP_Error( 'extensions_introuvables', 'Aucune des extensions cibles n’a été trouvée sur le site.', array( 'status' => 404 ) );
			}

			// Classe skin silencieuse pour capturer les messages sans émettre de HTML
			if ( ! class_exists( 'WPMCP_Silent_Upgrader_Skin' ) ) {
				class WPMCP_Silent_Upgrader_Skin extends WP_Upgrader_Skin {
					public $messages = array();
					public function feedback( $string, ...$args ) {
						if ( is_string( $string ) ) {
							$this->messages[] = $args ? vsprintf( $string, $args ) : $string;
						}
					}
					public function header() {}
					public function footer() {}
					public function error( $errors ) {
						if ( is_wp_error( $errors ) ) {
							$this->messages[] = $errors->get_error_message();
						} elseif ( is_string( $errors ) ) {
							$this->messages[] = $errors;
						}
					}
				}
			}

			$skin     = new WPMCP_Silent_Upgrader_Skin();
			$upgrader = new Plugin_Upgrader( $skin );
			$result   = $upgrader->bulk_upgrade( $valides );

			// Vider le cache de page si WP Super Cache est présent
			if ( function_exists( 'wp_cache_clean_cache' ) ) {
				global $file_prefix;
				wp_cache_clean_cache( $file_prefix, true );
			}

			// Relever les nouvelles versions installées
			wp_clean_plugins_cache();
			$installed_after = get_plugins();
			$details = array();
			foreach ( $valides as $v ) {
				$status_raw = isset( $result[ $v ] ) ? $result[ $v ] : null;
				$success    = ( true === $status_raw || ( is_array( $status_raw ) && ! empty( $status_raw ) && empty( $status_raw['error'] ) ) );
				$ver_apres  = isset( $installed_after[ $v ]['Version'] ) ? $installed_after[ $v ]['Version'] : null;
				$nom        = isset( $installed_after[ $v ]['Name'] ) ? $installed_after[ $v ]['Name'] : $v;

				$details[] = array(
					'fichier' => $v,
					'nom'     => $nom,
					'succes'  => $success,
					'version' => $ver_apres,
					'erreur'  => is_wp_error( $status_raw ) ? $status_raw->get_error_message() : null,
				);
			}

			return rest_ensure_response( array(
				'succes'       => true,
				'mises_a_jour' => $details,
				'messages'     => $skin->messages,
			) );
		},
		'permission_callback' => $allowed,
	) );

	register_rest_route( 'wpmcp/v1', '/maintenance/extensions/supprimer', array(
		'methods'             => 'POST',
		'callback'            => function ( WP_REST_Request $request ) {
			if ( ! current_user_can( 'delete_plugins' ) && ! current_user_can( 'manage_options' ) ) {
				return new WP_Error( 'rest_forbidden', 'Droits insuffisants pour supprimer des extensions.', array( 'status' => 403 ) );
			}

			require_once ABSPATH . 'wp-admin/includes/plugin.php';
			require_once ABSPATH . 'wp-admin/includes/file.php';

			$params = $request->get_json_params();
			$target = isset( $params['extension'] ) ? sanitize_text_field( $params['extension'] ) : '';

			if ( empty( $target ) ) {
				return new WP_Error( 'parametre_manquant', 'L’extension à supprimer est obligatoire.', array( 'status' => 400 ) );
			}

			$installed = get_plugins();
			$plugin_file = null;
			if ( isset( $installed[ $target ] ) ) {
				$plugin_file = $target;
			} elseif ( isset( $installed[ $target . '.php' ] ) ) {
				$plugin_file = $target . '.php';
			} else {
				foreach ( array_keys( $installed ) as $inst_file ) {
					if ( $inst_file === $target || $inst_file === $target . '.php' || dirname( $inst_file ) === $target || strpos( $inst_file, $target ) === 0 ) {
						$plugin_file = $inst_file;
						break;
					}
				}
			}

			if ( ! $plugin_file ) {
				return new WP_Error( 'extension_introuvable', 'Extension introuvable sur le site : ' . $target, array( 'status' => 404 ) );
			}

			if ( is_plugin_active( $plugin_file ) ) {
				return new WP_Error( 'extension_active', 'Impossible de supprimer une extension active. Désactivez-la d’abord.', array( 'status' => 400 ) );
			}

			$nom = isset( $installed[ $plugin_file ]['Name'] ) ? $installed[ $plugin_file ]['Name'] : $plugin_file;
			$result = delete_plugins( array( $plugin_file ) );

			if ( is_wp_error( $result ) ) {
				return $result;
			}
			if ( false === $result ) {
				return new WP_Error( 'suppression_echec', 'Échec de la suppression des fichiers de l’extension.', array( 'status' => 500 ) );
			}

			// Vider le cache de page
			if ( function_exists( 'wp_cache_clean_cache' ) ) {
				global $file_prefix;
				wp_cache_clean_cache( $file_prefix, true );
			}

			return rest_ensure_response( array(
				'succes'    => true,
				'supprime'  => true,
				'fichier'   => $plugin_file,
				'nom'       => $nom,
			) );
		},
		'permission_callback' => $allowed,
	) );
} );
