<?php
/**
 * Snippet WPCode — « Claude : lecture des réponses Forminator »
 *
 * Forminator range les réponses à ses formulaires dans ses propres tables et ne les expose pas
 * dans l'API REST. Ce code ajoute deux routes en LECTURE SEULE, réservées aux administrateurs
 * (capacité manage_options), qui passent par l'API PHP officielle de Forminator :
 *
 *   GET /wp-json/wpmcp/v1/forminator/forms
 *       formulaires : id, nom, statut, nombre de réponses, champs (slug, libellé, type)
 *   GET /wp-json/wpmcp/v1/forminator/forms/<id>/entries?page=1&per_page=20&after=YYYY-MM-DD
 *       réponses d'un formulaire, la plus récente en premier : id, date, valeurs par champ
 *
 * Les métadonnées internes (clés commençant par « _ », dont l'adresse IP) ne sont pas renvoyées.
 * Rien n'est créé, modifié ni supprimé. Le snippet ne gère pas l'authentification : il s'ajoute
 * à wpcode-snippet.php quand l'hébergeur retire l'en-tête Authorization.
 *
 * WPCode : Code Snippets → Ajouter un snippet → « Ajouter votre code personnalisé (nouveau
 * snippet) » → type « Extrait PHP » → coller ce code SANS la balise <?php → Insertion :
 * « Exécuter partout » → Activer → Enregistrer. Le désactiver coupe l'accès aux réponses.
 */
add_action( 'rest_api_init', function () {
	$is_admin = function () {
		return current_user_can( 'manage_options' );
	};

	$unavailable = function () {
		return new WP_Error( 'forminator_absent', "Forminator n'est pas actif sur ce site.", array( 'status' => 503 ) );
	};

	// Valeur d'un champ en texte : champs composés (nom, adresse) réunis, fichiers → URL.
	$flatten = function ( $value ) use ( &$flatten ) {
		if ( is_array( $value ) ) {
			if ( isset( $value['file'] ) && is_array( $value['file'] ) ) {
				$url = isset( $value['file']['file_url'] ) ? $value['file']['file_url'] : '';
				return is_array( $url ) ? implode( ', ', $url ) : (string) $url;
			}
			$parts = array();
			foreach ( $value as $item ) {
				$text = $flatten( $item );
				if ( '' !== $text ) {
					$parts[] = $text;
				}
			}
			return implode( ' ', $parts );
		}
		return is_scalar( $value ) ? (string) $value : '';
	};

	$describe = function ( $form ) {
		$fields = array();
		foreach ( (array) ( method_exists( $form, 'get_fields' ) ? $form->get_fields() : array() ) as $field ) {
			$raw = method_exists( $field, 'to_formatted_array' ) ? (array) $field->to_formatted_array() : array();
			$fields[] = array(
				'slug'    => isset( $field->slug ) ? $field->slug : ( isset( $raw['element_id'] ) ? $raw['element_id'] : '' ),
				'libelle' => isset( $raw['field_label'] ) ? wp_strip_all_tags( $raw['field_label'] ) : '',
				'type'    => isset( $raw['type'] ) ? $raw['type'] : '',
			);
		}
		$settings = is_array( $form->settings ) ? $form->settings : array();
		return array(
			'id'       => (int) $form->id,
			'nom'      => ! empty( $settings['formName'] ) ? $settings['formName'] : $form->name,
			'statut'   => $form->status,
			'reponses' => (int) Forminator_API::count_entries( $form->id ),
			'champs'   => $fields,
		);
	};

	register_rest_route( 'wpmcp/v1', '/forminator/forms', array(
		'methods'             => 'GET',
		'permission_callback' => $is_admin,
		'callback'            => function () use ( $unavailable, $describe ) {
			if ( ! class_exists( 'Forminator_API' ) ) {
				return $unavailable();
			}
			$forms = Forminator_API::get_forms( null, 1, 100 );
			if ( is_wp_error( $forms ) ) {
				return $forms;
			}
			return rest_ensure_response( array_map( $describe, (array) $forms ) );
		},
	) );

	register_rest_route( 'wpmcp/v1', '/forminator/forms/(?P<id>\d+)/entries', array(
		'methods'             => 'GET',
		'permission_callback' => $is_admin,
		'args'                => array(
			'page'     => array( 'default' => 1, 'sanitize_callback' => 'absint' ),
			'per_page' => array( 'default' => 20, 'sanitize_callback' => 'absint' ),
			'after'    => array( 'default' => '', 'sanitize_callback' => 'sanitize_text_field' ),
		),
		'callback'            => function ( WP_REST_Request $request ) use ( $unavailable, $flatten ) {
			if ( ! class_exists( 'Forminator_API' ) ) {
				return $unavailable();
			}
			$form_id = (int) $request['id'];
			$form    = Forminator_API::get_form( $form_id );
			if ( is_wp_error( $form ) ) {
				return $form;
			}
			$entries = (array) Forminator_API::get_entries( $form_id );
			$after   = (string) $request['after'];
			if ( '' !== $after ) {
				$entries = array_filter( $entries, function ( $e ) use ( $after ) {
					return strcmp( substr( (string) $e->date_created_sql, 0, 10 ), $after ) >= 0;
				} );
			}
			usort( $entries, function ( $a, $b ) {
				return strcmp( (string) $b->date_created_sql, (string) $a->date_created_sql );
			} );
			$per_page = min( 100, max( 1, (int) $request['per_page'] ) );
			$page     = max( 1, (int) $request['page'] );
			$rows     = array();
			foreach ( array_slice( $entries, ( $page - 1 ) * $per_page, $per_page ) as $entry ) {
				$values = array();
				foreach ( (array) $entry->meta_data as $key => $meta ) {
					if ( '' === (string) $key || '_' === substr( (string) $key, 0, 1 ) ) {
						continue; // métadonnées internes (adresse IP…)
					}
					$values[ $key ] = $flatten( is_array( $meta ) && array_key_exists( 'value', $meta ) ? $meta['value'] : $meta );
				}
				$rows[] = array(
					'id'     => (int) $entry->entry_id,
					'date'   => $entry->date_created_sql,
					'champs' => $values,
				);
			}
			return rest_ensure_response( array(
				'formulaire' => $form_id,
				'total'      => count( $entries ),
				'page'       => $page,
				'reponses'   => $rows,
			) );
		},
	) );
} );
