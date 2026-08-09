"""
Extracteur RSS/Atom - premier palier, avant tout usage de navigateur ou d'IA.

Un flux RSS est une voie de syndication publiee par le site lui-meme :
son usage n'a aucune ambiguite de legitimite (contrairement au contournement
de protections anti-bot), et il est plus rapide, moins cher et souvent plus
fiable que le rendu navigateur pour les pages de listing/section.
"""

import logging
import httpx
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.api.models import WebResult

logger = logging.getLogger(__name__)

# Chemins de flux les plus courants, testes dans cet ordre
COMMON_FEED_PATHS = ["/feed", "/rss", "/rss.xml", "/feed/rss", "/?feed=rss2", "/atom.xml"]

HONEST_UA = "WebtoolsBot/1.0 (+https://github.com/nic01asFr/webtools; veille documentaire automatisee)"


class RssExtractor:
    """Detecte et exploite un flux RSS/Atom pour une page de type listing/section."""

    async def try_extract(self, url: str, timeout: int = 10) -> Optional[WebResult]:
        """
        Tente de trouver et d'exploiter un flux RSS pour le domaine de `url`.
        Retourne None si aucun flux exploitable n'est trouve (pas une erreur :
        c'est un palier optionnel, l'appelant doit alors passer au palier suivant).
        """
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": HONEST_UA}
        ) as client:
            for path in COMMON_FEED_PATHS:
                feed_url = urljoin(base, path)
                try:
                    resp = await client.get(feed_url)
                except Exception:
                    continue

                if resp.status_code != 200:
                    continue

                content_type = resp.headers.get("content-type", "")
                if "xml" not in content_type and not resp.text.strip().startswith("<?xml"):
                    continue

                items = self._parse_feed(resp.text)
                if not items:
                    continue

                logger.info(f"Flux RSS trouve et exploite: {feed_url} ({len(items)} entrees)")
                return self._build_result(url, feed_url, items)

        return None

    def _parse_feed(self, xml_text: str) -> list[dict]:
        """Parse un flux RSS 2.0 ou Atom en une liste d'items simplifies."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        items = []

        # RSS 2.0 : <rss><channel><item>...
        for item in root.findall(".//item")[:30]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()
            if title:
                items.append({"title": title, "link": link, "date": pub_date, "description": description})

        # Atom : <feed><entry>...
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns)[:30]:
                title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href") if link_el is not None else ""
                pub_date = (entry.findtext("atom:updated", namespaces=ns) or "").strip()
                summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
                if title:
                    items.append({"title": title, "link": link, "date": pub_date, "description": summary})

        return items

    def _build_result(self, original_url: str, feed_url: str, items: list[dict]) -> WebResult:
        lines = []
        for it in items:
            line = f"- {it['title']}"
            if it.get("date"):
                line += f" ({it['date']})"
            if it.get("description"):
                desc = it["description"][:200].replace("\n", " ")
                line += f"\n  {desc}"
            lines.append(line)

        content = "\n\n".join(lines)

        return WebResult.from_success(
            url=original_url,
            content_type="rss_feed",
            title=f"Flux RSS ({len(items)} entrees)",
            content=content,
            metadata={
                "extraction_method": "rss_feed",
                "feed_url": feed_url,
                "items_count": len(items),
                "items_raw": items,
            }
        )
