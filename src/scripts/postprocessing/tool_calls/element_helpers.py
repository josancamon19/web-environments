"""Helper functions for extracting element information from DOM snapshots."""

from html.parser import HTMLParser
from typing import List, Dict, Any, Optional


class ElementExtractor(HTMLParser):
    """Extract element attributes from DOM HTML."""

    def __init__(self, target_id: str = None, target_classes: List[str] = None):
        super().__init__()
        self.target_id = target_id
        self.target_classes = set(target_classes) if target_classes else set()
        self.found_element = None
        self.current_attrs = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        element_id = attrs_dict.get("id", "")
        element_classes = set(attrs_dict.get("class", "").split())

        # Check if this is our target element
        if (self.target_id and element_id == self.target_id) or (
            self.target_classes and self.target_classes.issubset(element_classes)
        ):
            self.found_element = {
                "tag": tag,
                "id": element_id,
                "class": attrs_dict.get("class", ""),
                "name": attrs_dict.get("name", ""),
                "type": attrs_dict.get("type", ""),
                "role": attrs_dict.get("role", ""),
                "aria-label": attrs_dict.get("aria-label", ""),
                "placeholder": attrs_dict.get("placeholder", ""),
                "title": attrs_dict.get("title", ""),
                "value": attrs_dict.get("value", ""),
                "href": attrs_dict.get("href", ""),
                "text": attrs_dict.get("text", ""),
            }
            # Remove empty attributes
            self.found_element = {k: v for k, v in self.found_element.items() if v}


def extract_element_context(
    dom_snapshot: str, event_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Extract rich context about an element from DOM snapshot."""
    if not dom_snapshot:
        return {}

    if not dom_snapshot.lstrip().startswith("<"):
        return {}

    element_id = event_data.get("id", "")
    class_name = event_data.get("className", "")

    if not element_id and not class_name:
        return {}

    classes = class_name.split() if class_name else []

    try:
        parser = ElementExtractor(target_id=element_id, target_classes=classes)
        parser.feed(dom_snapshot)

        if parser.found_element:
            return parser.found_element
    except Exception:
        pass

    return {}


def create_selector(event_data: Dict[str, Any]) -> str:
    """Create a CSS selector from event data."""
    tag = event_data.get("tag", "").lower()
    element_id = event_data.get("id", "")
    class_name = event_data.get("className", "")

    if element_id:
        return f"#{element_id}"
    elif class_name:
        classes = class_name.strip().split()
        if classes:
            return f"{tag}.{'.'.join(classes)}"
    return tag if tag else "*"


def _extract_xy_pair(source: Any) -> Optional[list]:
    """Extract [x, y] pair from a source dictionary."""
    if not isinstance(source, dict):
        return None

    x = source.get("x")
    y = source.get("y")

    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return [x, y]
    return None


def extract_coordinates_from_event(event_data: Dict[str, Any]) -> Optional[list]:
    """Reduce raw event coordinates to a simple [x, y] pair."""
    if not isinstance(event_data, dict):
        return None

    raw_coordinates = event_data.get("coordinates")
    if isinstance(raw_coordinates, dict):
        for candidate in (
            raw_coordinates.get("page"),
            raw_coordinates.get("client"),
            raw_coordinates.get("offset"),
        ):
            pair = _extract_xy_pair(candidate)
            if pair:
                return pair

    fallback_pair = _extract_xy_pair(
        {"x": event_data.get("x"), "y": event_data.get("y")}
    )
    if fallback_pair:
        return fallback_pair

    return None


def merge_coordinates(params: Dict[str, Any], coordinates: Optional[list]):
    """Add coordinates to params if valid."""
    if coordinates is not None and len(coordinates) == 2:
        params["coordinates"] = coordinates
