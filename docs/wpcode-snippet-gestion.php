<?php
/**
 * MCP Gestion des snippets — routes REST pour que le connecteur wordpress-mcp lise et maintienne
 * les snippets WPCode (WPCode n'a pas d'API REST).
 *
 * Routes (espace wpmcp/v1), réservées aux administrateurs ayant le droit unfiltered_html :
 *   GET  /wpmcp/v1/snippets                 liste : id, titre, actif, type, emplacement, empreinte
 *   GET  /wpmcp/v1/snippets/<id>            idem + code
 *   POST /wpmcp/v1/snippets                 créer un snippet PHP « MCP … », toujours INACTIF
 *        { titre, code, sha256 }
 *   POST /wpmcp/v1/snippets/<id>            remplacer le code d'un snippet « MCP … »
 *        { code, sha256, sha256_actuel }    (s'il est actif, WPCode teste le nouveau code ; en cas
 *                                            d'erreur il est désactivé, le site reste intact)
 *   POST /wpmcp/v1/snippets/<id>/statut     activer / désactiver { actif: true|false }
 *
 * Garde-fous : seuls les snippets dont le titre commence par « MCP » peuvent être créés ou
 * modifiés ; ce snippet et « MCP Auth » ne peuvent pas être désactivés à distance ; l'empreinte
 * SHA-256 du code reçu doit correspondre (pas de code altéré en route) ; mise à jour refusée si
 * le code a changé depuis la lecture (sha256_actuel). Aucune suppression.
 *
 * WPCode enregistre le code sans le « slasher » (wp_insert_post retire alors les antislashs) :
 * pendant chaque enregistrement, un filtre wp_insert_post_data impose le code exact attendu,
 * puis l'empreinte du code stocké est vérifiée (champ « integre »).
 *
 * Installation : WPCode › Ajouter un extrait › Extrait PHP ; titre « MCP Gestion des snippets » ;
 * coller ce fichier SANS la ligne « <?php » ; Insertion « Exécuter partout » ; Activer.
 * Pour couper l'accès : désactiver ce snippet.
 */

if ( ! function_exists( 'wpmcp_snip_view' ) ) {
	function wpmcp_snip_protected( $title ) {
		return in_array( trim( (string) $title ), array( 'MCP Gestion des snippets', 'MCP Auth' ), true );
	}

	function wpmcp_snip_view( $post, $with_code = false, $expected_sha = null ) {
		$snippet = new WPCode_Snippet( $post );
		$code    = (string) $post->post_content;
		$out     = array(
			'id'          => (int) $post->ID,
			'titre'       => $post->post_title,
			'actif'       => 'publish' === $post->post_status,
			'type'        => $snippet->get_code_type(),
			'emplacement' => $snippet->get_location(),
			'modifie'     => $post->post_modified,
			'sha256'      => hash( 'sha256', $code ),
			'taille'      => strlen( $code ),
			'protege'     => wpmcp_snip_protected( $post->post_title ),
			'erreur'      => get_post_meta( $post->ID, '_wpcode_last_error', true ) ? true : false,
		);
		if ( null !== $expected_sha ) {
			$out['integre'] = hash_equals( $out['sha256'], $expected_sha );
		}
		if ( $with_code ) {
			$out['code'] = $code;
		}
		return $out;
	}

	function wpmcp_snip_get( $id ) {
		$post = get_post( (int) $id );
		if ( ! $post || 'wpcode' !== $post->post_type || 'trash' === $post->post_status ) {
			return new WP_Error( 'snippet_introuvable', 'Snippet introuvable.', array( 'status' => 404 ) );
		}
		return $post;
	}

	/**
	 * Exécute $fn pendant qu'un filtre impose $code exact au snippet $id (0 = création, repérée
	 * par son titre). Renvoie le résultat de $fn.
	 */
	function wpmcp_snip_with_exact_code( $id, $title, $code, $fn ) {
		$filter = function ( $data, $postarr ) use ( $id, $title, $code ) {
			$post_id = isset( $postarr['ID'] ) ? (int) $postarr['ID'] : 0;
			if ( 'wpcode' === $data['post_type']
				&& ( $id ? $post_id === (int) $id : ( 0 === $post_id && wp_unslash( $data['post_title'] ) === $title ) ) ) {
				$data['post_content'] = wp_slash( $code ); // les données sont « slashées » à ce stade
			}
			return $data;
		};
		add_filter( 'wp_insert_post_data', $filter, 999, 2 );
		try {
			return $fn();
		} finally {
			remove_filter( 'wp_insert_post_data', $filter, 999 );
		}
	}

	function wpmcp_snip_check_code( $code, $sha ) {
		if ( ! is_string( $code ) || '' === trim( $code ) ) {
			return new WP_Error( 'code_vide', 'Code vide.', array( 'status' => 400 ) );
		}
		if ( preg_match( '/^\s*<\?php/i', $code ) ) {
			return new WP_Error( 'code_invalide', 'Retirer la balise <?php du début.', array( 'status' => 400 ) );
		}
		if ( ! is_string( $sha ) || ! hash_equals( hash( 'sha256', $code ), strtolower( $sha ) ) ) {
			return new WP_Error( 'empreinte', 'Empreinte SHA-256 du code incorrecte.', array( 'status' => 400 ) );
		}
		return true;
	}
}

add_action( 'rest_api_init', function () {
	$perm = function () {
		return class_exists( 'WPCode_Snippet' ) && current_user_can( 'manage_options' ) && current_user_can( 'unfiltered_html' );
	};

	register_rest_route( 'wpmcp/v1', '/snippets', array(
		array(
			'methods'             => 'GET',
			'permission_callback' => $perm,
			'callback'            => function () {
				$posts = get_posts( array(
					'post_type'   => 'wpcode',
					'post_status' => array( 'publish', 'draft' ),
					'numberposts' => 200,
					'orderby'     => 'ID',
					'order'       => 'DESC',
				) );
				return array_map( 'wpmcp_snip_view', $posts );
			},
		),
		array(
			'methods'             => 'POST',
			'permission_callback' => $perm,
			'callback'            => function ( WP_REST_Request $req ) {
				$title = trim( (string) $req->get_param( 'titre' ) );
				$code  = $req->get_param( 'code' );
				if ( 0 !== strpos( $title, 'MCP ' ) ) {
					return new WP_Error( 'titre_refuse', 'Le titre doit commencer par « MCP ».', array( 'status' => 400 ) );
				}
				$check = wpmcp_snip_check_code( $code, $req->get_param( 'sha256' ) );
				if ( is_wp_error( $check ) ) {
					return $check;
				}
				$same = get_posts( array( 'post_type' => 'wpcode', 'post_status' => array( 'publish', 'draft' ),
					'title' => $title, 'numberposts' => 1 ) );
				if ( $same ) {
					return new WP_Error( 'deja_present', 'Un snippet porte déjà ce titre (id ' . $same[0]->ID . ').', array( 'status' => 409 ) );
				}
				$snippet = new WPCode_Snippet( array(
					'title'       => $title,
					'code'        => $code,
					'code_type'   => 'php',
					'location'    => 'everywhere',
					'auto_insert' => 1,
					'active'      => false,
				) );
				$id = wpmcp_snip_with_exact_code( 0, $title, $code, function () use ( $snippet ) {
					return $snippet->save();
				} );
				if ( ! $id ) {
					return new WP_Error( 'echec', 'WPCode a refusé l’enregistrement.', array( 'status' => 500 ) );
				}
				return wpmcp_snip_view( get_post( $id ), false, hash( 'sha256', $code ) );
			},
		),
	) );

	register_rest_route( 'wpmcp/v1', '/snippets/(?P<id>\d+)', array(
		array(
			'methods'             => 'GET',
			'permission_callback' => $perm,
			'callback'            => function ( WP_REST_Request $req ) {
				$post = wpmcp_snip_get( $req['id'] );
				return is_wp_error( $post ) ? $post : wpmcp_snip_view( $post, true );
			},
		),
		array(
			'methods'             => 'POST',
			'permission_callback' => $perm,
			'callback'            => function ( WP_REST_Request $req ) {
				$post = wpmcp_snip_get( $req['id'] );
				if ( is_wp_error( $post ) ) {
					return $post;
				}
				if ( 0 !== strpos( $post->post_title, 'MCP ' ) || wpmcp_snip_protected( $post->post_title ) ) {
					return new WP_Error( 'snippet_refuse', 'Seuls les snippets « MCP … » non protégés peuvent être modifiés à distance.', array( 'status' => 403 ) );
				}
				if ( ! hash_equals( hash( 'sha256', (string) $post->post_content ), strtolower( (string) $req->get_param( 'sha256_actuel' ) ) ) ) {
					return new WP_Error( 'modifie_entre_temps', 'Le code a changé depuis la lecture.', array( 'status' => 409 ) );
				}
				$code  = $req->get_param( 'code' );
				$check = wpmcp_snip_check_code( $code, $req->get_param( 'sha256' ) );
				if ( is_wp_error( $check ) ) {
					return $check;
				}
				$snippet       = new WPCode_Snippet( (int) $post->ID );
				$snippet->code = $code;
				if ( 'publish' === $post->post_status ) {
					$snippet->active = true; // WPCode re-teste le code ; en cas d'erreur, il le désactive.
				}
				$saved = wpmcp_snip_with_exact_code( $post->ID, $post->post_title, $code, function () use ( $snippet ) {
					return $snippet->save();
				} );
				if ( ! $saved ) {
					return new WP_Error( 'echec', 'WPCode a refusé l’enregistrement.', array( 'status' => 500 ) );
				}
				clean_post_cache( $post->ID );
				$view = wpmcp_snip_view( get_post( $post->ID ), false, hash( 'sha256', $code ) );
				if ( 'publish' === $post->post_status && ! $view['actif'] ) {
					$view['avertissement'] = 'Nouveau code en erreur : WPCode a désactivé le snippet. ' . wpcode()->error->get_last_error_message();
				}
				return $view;
			},
		),
	) );

	register_rest_route( 'wpmcp/v1', '/snippets/(?P<id>\d+)/statut', array(
		'methods'             => 'POST',
		'permission_callback' => $perm,
		'callback'            => function ( WP_REST_Request $req ) {
			$post = wpmcp_snip_get( $req['id'] );
			if ( is_wp_error( $post ) ) {
				return $post;
			}
			$actif = rest_sanitize_boolean( $req->get_param( 'actif' ) );
			if ( ! $actif && wpmcp_snip_protected( $post->post_title ) ) {
				return new WP_Error( 'snippet_protege', 'Ce snippet ne peut pas être désactivé à distance (il coupe l’accès du connecteur).', array( 'status' => 403 ) );
			}
			$code    = (string) $post->post_content;
			$snippet = new WPCode_Snippet( (int) $post->ID );
			wpmcp_snip_with_exact_code( $post->ID, $post->post_title, $code, function () use ( $snippet, $actif ) {
				if ( $actif ) {
					$snippet->activate();
				} else {
					$snippet->deactivate();
				}
			} );
			clean_post_cache( $post->ID );
			$view = wpmcp_snip_view( get_post( $post->ID ), false, hash( 'sha256', $code ) );
			if ( $actif && ! $view['actif'] ) {
				return new WP_Error( 'activation_refusee', 'WPCode a refusé l’activation (erreur dans le code) : ' . wpcode()->error->get_last_error_message(), array( 'status' => 422 ) );
			}
			return $view;
		},
	) );
} );
