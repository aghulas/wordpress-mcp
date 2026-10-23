<?php
/**
 * Snippet WPCode — « MCP Tableaux TablePress »
 *
 * TablePress range ses tableaux dans un type de contenu privé, sans API REST (version gratuite).
 * Ce code ajoute trois routes réservées aux administrateurs autorisés à écrire du HTML non filtré
 * (manage_options + unfiltered_html), qui passent par le modèle PHP de TablePress
 * (TablePress::$model_table) :
 *
 *   GET  /wp-json/wpmcp/v1/tablepress/tables
 *        tableaux : id, nom, description, nombre de lignes et de colonnes, date de modification
 *   GET  /wp-json/wpmcp/v1/tablepress/tables/<id>
 *        un tableau : id, nom, description, cellules (lignes × colonnes, contenu brut), sha256
 *   POST /wp-json/wpmcp/v1/tablepress/tables/<id>/cellules
 *        corps JSON : { "sha256_actuel": "…", "cellules": [ { "ligne": 1, "colonne": 1, "valeur": "…" } ] }
 *        remplace le contenu de cellules EXISTANTES (lignes et colonnes numérotées à partir de 1).
 *        Refusé si le tableau a changé depuis la lecture (empreinte sha256 des cellules), si une
 *        cellule n'existe pas, ou au-delà de 200 cellules. Ni ajout ni suppression de ligne, de
 *        colonne ou de tableau ; nom, description et options inchangés.
 *
 * L'enregistrement passe par TablePress (save), qui vide son cache de rendu et celui des extensions
 * de cache de page. Empreinte : sha256 de wp_json_encode( cellules ).
 *
 * WPCode : Code Snippets → Ajouter un snippet → « Ajouter votre code personnalisé (nouveau
 * snippet) » → type « Extrait PHP » → coller ce code SANS la balise <?php → Insertion :
 * « Exécuter partout » → Activer → Enregistrer. Le désactiver coupe l'accès aux tableaux.
 */
add_action( 'rest_api_init', function () {
	$allowed = function () {
		return current_user_can( 'manage_options' ) && current_user_can( 'unfiltered_html' );
	};

	$model = function () {
		if ( ! class_exists( 'TablePress' ) || empty( TablePress::$model_table ) ) {
			return new WP_Error( 'tablepress_absent', "TablePress n'est pas actif sur ce site.", array( 'status' => 503 ) );
		}
		return TablePress::$model_table;
	};

	$sha = function ( $data ) {
		return hash( 'sha256', (string) wp_json_encode( array_values( array_map( 'array_values', (array) $data ) ) ) );
	};

	$modified = function ( $table_id ) {
		$post_id = 0;
		$map     = get_option( 'tablepress_tables' );
		if ( is_string( $map ) ) {
			$map = json_decode( $map, true );
		}
		if ( is_array( $map ) && isset( $map['table_post'][ $table_id ] ) ) {
			$post_id = (int) $map['table_post'][ $table_id ];
		}
		return $post_id ? get_post_modified_time( 'Y-m-d H:i:s', false, $post_id ) : null;
	};

	$load = function ( $table_id, $with_data ) use ( $model ) {
		$m = $model();
		if ( is_wp_error( $m ) ) {
			return $m;
		}
		if ( ! $m->table_exists( $table_id ) ) {
			return new WP_Error( 'tableau_introuvable', "Tableau {$table_id} introuvable.", array( 'status' => 404 ) );
		}
		$table = $m->load( $table_id, $with_data, $with_data );
		if ( is_wp_error( $table ) ) {
			return new WP_Error( 'tableau_illisible', "Tableau {$table_id} illisible.", array( 'status' => 500 ) );
		}
		return $table;
	};

	$id_arg = array(
		'id' => array(
			'validate_callback' => function ( $value ) {
				return is_string( $value ) && preg_match( '/^[A-Za-z0-9_-]{1,32}$/', $value );
			},
		),
	);

	register_rest_route( 'wpmcp/v1', '/tablepress/tables', array(
		'methods'             => 'GET',
		'permission_callback' => $allowed,
		'callback'            => function () use ( $model, $load, $modified ) {
			$m = $model();
			if ( is_wp_error( $m ) ) {
				return $m;
			}
			$out = array();
			foreach ( $m->load_all( true, false ) as $table_id ) {
				$table = $load( (string) $table_id, true );
				if ( is_wp_error( $table ) ) {
					continue;
				}
				$data  = (array) $table['data'];
				$out[] = array(
					'id'          => (string) $table['id'],
					'nom'         => (string) $table['name'],
					'description' => (string) $table['description'],
					'lignes'      => count( $data ),
					'colonnes'    => $data ? count( (array) reset( $data ) ) : 0,
					'modifie'     => $modified( (string) $table['id'] ),
				);
			}
			return $out;
		},
	) );

	register_rest_route( 'wpmcp/v1', '/tablepress/tables/(?P<id>[A-Za-z0-9_-]{1,32})', array(
		'methods'             => 'GET',
		'permission_callback' => $allowed,
		'args'                => $id_arg,
		'callback'            => function ( WP_REST_Request $request ) use ( $load, $sha, $modified ) {
			$table = $load( (string) $request['id'], true );
			if ( is_wp_error( $table ) ) {
				return $table;
			}
			$data = array_values( array_map( 'array_values', (array) $table['data'] ) );
			return array(
				'id'          => (string) $table['id'],
				'nom'         => (string) $table['name'],
				'description' => (string) $table['description'],
				'lignes'      => count( $data ),
				'colonnes'    => $data ? count( $data[0] ) : 0,
				'modifie'     => $modified( (string) $table['id'] ),
				'cellules'    => $data,
				'sha256'      => $sha( $data ),
			);
		},
	) );

	register_rest_route( 'wpmcp/v1', '/tablepress/tables/(?P<id>[A-Za-z0-9_-]{1,32})/cellules', array(
		'methods'             => 'POST',
		'permission_callback' => function ( WP_REST_Request $request ) use ( $allowed ) {
			return $allowed() && current_user_can( 'tablepress_edit_table', (string) $request['id'] );
		},
		'args'                => $id_arg,
		'callback'            => function ( WP_REST_Request $request ) use ( $model, $load, $sha, $modified ) {
			$table_id = (string) $request['id'];
			$body     = (array) $request->get_json_params();
			$changes  = isset( $body['cellules'] ) && is_array( $body['cellules'] ) ? $body['cellules'] : array();
			if ( ! $changes || count( $changes ) > 200 ) {
				return new WP_Error( 'parametre', 'De 1 à 200 cellules à modifier.', array( 'status' => 400 ) );
			}
			$table = $load( $table_id, true );
			if ( is_wp_error( $table ) ) {
				return $table;
			}
			$data = array_values( array_map( 'array_values', (array) $table['data'] ) );
			if ( empty( $body['sha256_actuel'] ) || ! hash_equals( $sha( $data ), (string) $body['sha256_actuel'] ) ) {
				return new WP_Error( 'modifie_entre_temps', 'Le tableau a changé depuis la lecture : relire puis recommencer.', array( 'status' => 409 ) );
			}
			foreach ( $changes as $change ) {
				$row = isset( $change['ligne'] ) ? (int) $change['ligne'] - 1 : -1;
				$col = isset( $change['colonne'] ) ? (int) $change['colonne'] - 1 : -1;
				if ( ! isset( $data[ $row ][ $col ] ) || ! isset( $change['valeur'] ) || ! is_string( $change['valeur'] ) ) {
					return new WP_Error( 'cellule_inconnue', 'Cellule inexistante ou valeur non textuelle : rien n\'a été modifié.', array( 'status' => 400 ) );
				}
				$data[ $row ][ $col ] = $change['valeur'];
			}
			$table['data'] = $data;
			$saved         = $model()->save( $table );
			if ( is_wp_error( $saved ) ) {
				return new WP_Error( 'enregistrement_refuse', 'TablePress a refusé l\'enregistrement : ' . $saved->get_error_code(), array( 'status' => 500 ) );
			}
			$reloaded = $load( $table_id, true );
			if ( is_wp_error( $reloaded ) ) {
				return $reloaded;
			}
			$after = array_values( array_map( 'array_values', (array) $reloaded['data'] ) );
			return array(
				'id'       => $table_id,
				'modifie'  => $modified( $table_id ),
				'cellules' => $after,
				'sha256'   => $sha( $after ),
				'integre'  => $sha( $after ) === $sha( $data ),
			);
		},
	) );
} );
