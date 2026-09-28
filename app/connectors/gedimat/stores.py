"""Lecture publique du référentiel magasins Gedimat.

La fiche et la carte portent gedimat_id. Le store_id Algolia est lu après
changeMagasin.php?magDest={gedimat_id}, dans `algoliaIdM`. Les deux nombres
ne sont pas supposés égaux. idEntrepot est ignoré.
"""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from http.cookiejar import CookieJar

from app.connectors.gedimat.public_client import USER_AGENT

MAGASINS_URL = "https://www.gedimat.fr/magasins.php"
CHANGE_URL = "https://www.gedimat.fr/changeMagasin.php"
ECOMMERCE_TYPE = "MAG_ECOMMERCE"

_MARKER = re.compile(
    r"map\.addMarker\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)\s*;\s*"
    r"mtmp\.idtf\s*=\s*'(\d+)'\s*;"
    r"(.*?)"
    r"mtmp\.title\s*=\s*'((?:\\'|[^'])*)'\s*;"
    r"(.*?)"
    r"mtmp\.textInfo\s*=\s*'((?:\\'|[^'])*)'\s*;"
    r"(.*?)"
    r"mtmp\.magcode\s*=\s*'([^']*)'\s*;",
    re.S,
)
_ALGOLIA_ID = re.compile(r"const algoliaIdM = (\d+);")
_PAGE_ECOM = re.compile(r"const isEcommerce = (true|false);")
_POSTAL = re.compile(r"^(\d{5})\s+(.+)$")
_STATUS = re.compile(r"^(ouvert|ferme)", re.I)


@dataclass(frozen=True)
class PublicGedimatStore:
    gedimat_id: int
    name: str
    address: str
    postal_code: str
    city: str
    latitude: Decimal | None
    longitude: Decimal | None
    ecommerce: bool
    store_type: str


@dataclass(frozen=True)
class AlgoliaAssignment:
    algolia_id: int | None
    page_ecommerce: bool | None


def own_catalog_id(*, ecommerce: bool, algolia_id: int | None, page_ecommerce: bool | None) -> int | None:
    """Catalogue propre au magasin.

    Un magasin non e-commerce dont la page renvoie algoliaIdM=1 utilise le
    catalogue par défaut du site : on n'enregistre pas 1 comme son store_id.
    """
    if algolia_id is None:
        return None
    uses_default = algolia_id == 1 and page_ecommerce is not True
    if not ecommerce and uses_default:
        return None
    if not ecommerce and page_ecommerce is False and algolia_id == 1:
        return None
    return algolia_id


def parse_store_listing(page_html: str) -> list[PublicGedimatStore]:
    text = page_html
    stores: list[PublicGedimatStore] = []
    seen: set[int] = set()
    for match in _MARKER.finditer(text):
        gedimat_id = int(match.group(3))
        if gedimat_id in seen:
            continue
        seen.add(gedimat_id)
        store_type = match.group(9)
        address, postal_code, city = _address(urllib.parse.unquote(match.group(7)))
        stores.append(
            PublicGedimatStore(
                gedimat_id=gedimat_id,
                name=_clip(_unescape_js(match.group(5)), 200) or f"Gedimat {gedimat_id}",
                address=_clip(address, 300),
                postal_code=_clip(postal_code, 10),
                city=_clip(city, 120),
                latitude=_coord(match.group(1)),
                longitude=_coord(match.group(2)),
                ecommerce=store_type == ECOMMERCE_TYPE,
                store_type=_clip(store_type, 40),
            )
        )
    return stores


def parse_algolia_assignment(page_html: str) -> AlgoliaAssignment:
    found = _ALGOLIA_ID.search(page_html)
    ecommerce = _PAGE_ECOM.search(page_html)
    return AlgoliaAssignment(
        algolia_id=int(found.group(1)) if found else None,
        page_ecommerce=None if ecommerce is None else ecommerce.group(1) == "true",
    )


class GedimatStoreDirectory:
    """Client HTTP public. Aucune écriture base, aucun token stocké."""

    def __init__(
        self,
        *,
        opener: urllib.request.OpenerDirector | None = None,
        timeout_s: float = 30,
    ):
        self.opener = opener or urllib.request.build_opener()
        self.timeout_s = timeout_s

    def fetch_listing(self) -> list[PublicGedimatStore]:
        html_text = self._read(MAGASINS_URL, self.opener)
        return parse_store_listing(html_text)

    def fetch_assignment(self, gedimat_id: int) -> AlgoliaAssignment:
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        url = f"{CHANGE_URL}?magDest={int(gedimat_id)}"
        html_text = self._read(url, opener)
        return parse_algolia_assignment(html_text)

    def _read(self, url: str, opener: urllib.request.OpenerDirector) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        delay = 0.4
        last: Exception | None = None
        for _attempt in range(5):
            try:
                with opener.open(request, timeout=self.timeout_s) as response:
                    raw = response.read()
                return raw.decode("iso-8859-15", errors="replace")
            except urllib.error.HTTPError as exc:
                exc.read()
                last = urllib.error.URLError(f"HTTP {exc.code} {url}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            time.sleep(delay)
            delay = min(delay * 2, 8)
        raise urllib.error.URLError(str(last))


def _address(text_info: str) -> tuple[str, str, str]:
    decoded = text_info.replace("\\/", "/").replace('\\"', '"').replace("\\'", "'")
    with_breaks = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.I)
    plain = re.sub(r"<[^>]+>", "", with_breaks)
    lines = [html.unescape(line).strip() for line in plain.splitlines()]
    lines = [line for line in lines if line]
    postal_at = next((i for i, line in enumerate(lines) if _POSTAL.match(line)), None)
    if postal_at is None:
        return "", "", ""
    postal, city = _POSTAL.match(lines[postal_at]).groups()
    start = 0
    for index, line in enumerate(lines[:postal_at]):
        if _STATUS.match(line):
            start = index + 1
    street = ", ".join(line for line in lines[start:postal_at] if line)
    return street, postal, city.strip()


def _coord(value: str) -> Decimal | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.000001"))
    except Exception:
        return None


def _unescape_js(value: str) -> str:
    return html.unescape(value.replace("\\'", "'").replace('\\"', '"')).strip()


def _clip(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]
