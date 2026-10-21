"""Dates d'un événement Modern Events Calendar lues dans la page publique (JSON-LD schema.org).

L'API REST de MEC Lite n'expose ni les dates ni le lieu (tables propres à MEC, pas de métas) ;
chaque page d'événement publie en revanche un bloc `application/ld+json` de type Event.
Logique pure, sans réseau.
"""
from __future__ import annotations

import json
import re
from typing import Any

_LD_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def parse_event_jsonld(html: str) -> dict[str, Any]:
    """Premier bloc JSON-LD de type Event → {debut, fin, lieu, adresse} (clés absentes si vides)."""
    for m in _LD_RE.finditer(html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Event":
                place = node.get("location") if isinstance(node.get("location"), dict) else {}
                out = {"debut": node.get("startDate"), "fin": node.get("endDate"),
                       "lieu": place.get("name"), "adresse": place.get("address")}
                return {k: v for k, v in out.items() if v}
    return {}
