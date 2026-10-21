"""Logique pure : HTML → texte et extraction Elementor."""
from __future__ import annotations

import json

from wordpress_mcp import elementor as E

from ._helpers import BASE, ELEMENTOR_DATA


def test_html_to_text_basic():
    assert E.html_to_text("<p>Un</p><p>Deux&nbsp;&amp; trois</p>") == "Un\n\nDeux & trois"
    assert E.html_to_text("a<br>b<br/>c") == "a\nb\nc"
    assert E.html_to_text("<ul><li>x</li><li>y</li></ul>") == "• x\n• y"
    assert E.html_to_text(None) == ""
    assert E.html_to_text("<p>   trop    d'espaces \n\n\n\n</p>") == "trop d'espaces"


def test_parse_elementor_data_tolerant():
    assert E.parse_elementor_data("") == []
    assert E.parse_elementor_data(None) == []
    assert E.parse_elementor_data("pas du json") == []
    assert E.parse_elementor_data('{"pas":"une liste"}') == []
    assert E.parse_elementor_data([{"id": "x"}]) == [{"id": "x"}]


def test_extract_widgets():
    widgets = E.extract_widgets(ELEMENTOR_DATA)
    by_id = {w["id"]: w for w in widgets}
    # le spacer sans texte n'apparaît pas
    assert "w6" not in by_id
    assert by_id["w1"]["widget"] == "heading"
    assert by_id["w1"]["champs"]["title"] == "Bienvenue à l'école"
    assert by_id["w1"]["profondeur"] == 2
    assert by_id["w2"]["champs"]["editor"] == "Premier paragraphe.\n\nSecond paragraphe."
    assert by_id["w3"]["champs"] == {"text": "Nous contacter", "link": "/contact/"}
    assert by_id["w4"]["champs"]["icon_list"] == [{"text": "Maternelle", "link": "/maternelle/"}, {"text": "Élémentaire"}]
    assert by_id["w5"]["champs"]["image"] == {"url": BASE + "/wp-content/uploads/logo.png", "id": 77, "alt": "Logo"}


def test_structure_summary():
    s = E.structure_summary(ELEMENTOR_DATA)
    assert s["elements"] == 8
    assert s["par_type"]["section"] == 1
    assert s["par_type"]["column"] == 1
    assert s["par_type"]["widget:heading"] == 1
    assert s["par_type"]["widget:spacer"] == 1


def test_widgets_to_text():
    txt = E.widgets_to_text(E.extract_widgets(ELEMENTOR_DATA))
    assert "[heading #w1]" in txt
    assert "title: Bienvenue à l'école" in txt
    assert "- text: Maternelle | link: /maternelle/" in txt
    assert "image: " + BASE + "/wp-content/uploads/logo.png" in txt


def test_shortcode_and_posts_widget():
    raw = json.dumps([{"id": "c", "elType": "container", "settings": {}, "elements": [
        {"id": "sc", "elType": "widget", "widgetType": "shortcode",
         "settings": {"shortcode": '[MEC id="793"]'}, "elements": []},
        {"id": "po", "elType": "widget", "widgetType": "posts",
         "settings": {"classic_posts_per_page": 8, "classic_read_more_text": "En savoir plus &raquo;",
                      "cards_read_more_text": "Read More »", "pagination_prev_label": "&laquo; Précédent"},
         "elements": []},
        {"id": "lg", "elType": "widget", "widgetType": "loop-grid",
         "settings": {"template_id": 42, "post_query_post_type": "mec-events", "posts_per_page": 3}, "elements": []},
    ]}])
    widgets = {w["id"]: w for w in E.extract_widgets(raw)}
    assert widgets["sc"]["champs"] == {"shortcode": '[MEC id="793"]'}
    assert widgets["po"]["champs"] == {"requete": {"contenu": "liste d'articles", "source": "post", "nombre": 8,
                                                   "lien_suite": "En savoir plus »"}}
    assert widgets["lg"]["champs"]["requete"] == {"contenu": "grille de contenus", "source": "mec-events",
                                                  "nombre": 3, "modele": 42}
    text = E.widgets_to_text(list(widgets.values()))
    assert "requete: contenu=liste d'articles, source=post, nombre=8" in text

