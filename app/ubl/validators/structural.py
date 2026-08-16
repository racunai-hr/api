from __future__ import annotations

from lxml import etree


def _canonical_element(element: etree._Element) -> list:
    tag = etree.QName(element).localname
    attrs = tuple(sorted((k, v) for k, v in element.attrib.items()))
    text = (element.text or '').strip()
    children = [_canonical_element(child) for child in element]
    return [tag, attrs, text, children]


def structural_compare(xml_a: str, xml_b: str) -> list[str]:
    """Usporedba strukture XML-a (ignorira whitespace/formatiranje)."""
    root_a = etree.fromstring(xml_a.encode('utf-8'))
    root_b = etree.fromstring(xml_b.encode('utf-8'))
    if _canonical_element(root_a) != _canonical_element(root_b):
        return ['Strukturalna razlika između generiranog i golden XML-a']
    return []
