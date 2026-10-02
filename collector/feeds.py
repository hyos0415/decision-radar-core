"""Atom·RSS 파싱 (표준 라이브러리 xml). 항목마다 평평한 dict를 돌려준다."""
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"


def parse(body):
    root = ET.fromstring(body)
    if root.tag == ATOM + "feed":
        return [_atom_entry(e) for e in root.findall(ATOM + "entry")]
    return [_rss_item(i) for i in root.iter("item")]


def _atom_entry(e):
    d = {"id": _text(e, "id"), "title": _text(e, "title"), "published": _text(e, "published"),
         "updated": _text(e, "updated"), "content": _text(e, "content") or _text(e, "summary"),
         "link": None, "category": None, "author": None}
    for c in e:
        tag = c.tag.replace(ATOM, "")
        if tag == "link" and d["link"] is None and c.get("rel", "alternate") == "alternate":
            d["link"] = c.get("href")
        elif tag == "category" and d["category"] is None:
            d["category"] = c.get("term")
        elif tag == "author":
            d["author"] = _text(c, "name")
    return d


def _rss_item(i):
    return {"id": (i.findtext("guid") or i.findtext("link") or "").strip(), "title": (i.findtext("title") or "").strip(),
            "published": (i.findtext("pubDate") or "").strip(), "updated": "",
            "content": i.findtext("description") or "", "link": (i.findtext("link") or "").strip(),
            "category": i.findtext("category"), "author": i.findtext("author")}


def _text(e, tag):
    x = e.find(ATOM + tag)
    return (x.text or "").strip() if x is not None and x.text else ""
