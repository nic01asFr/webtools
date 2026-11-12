"""
Extracteur PDF - palier dedie, avant tout usage de navigateur.

Un PDF n'est pas une page web : le rendu navigateur en produit une page
blanche (constate en usage reel sur des documents d'urbanisme), et l'agent
IA qui prend le relais consomme du temps et des tokens pour ne rien
extraire. Un telechargement direct + parsing texte est la seule voie
correcte, et de loin la moins chere.
"""

import asyncio
import logging
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import httpx

from app.api.models import WebResult
from app.utils.url_safety import Resolver, UnsafeURLError, safe_request_hooks

logger = logging.getLogger(__name__)

HONEST_UA = "WebtoolsBot/1.0 (+https://github.com/nic01asFr/webtools; veille documentaire automatisee)"

# Au-dela, on refuse de charger en memoire (les PDF d'urbanisme peuvent
# peser des dizaines de Mo ; 40 Mo couvre largement les cas legitimes).
MAX_PDF_BYTES = 40 * 1024 * 1024

# Un PDF de plusieurs centaines de pages produirait un texte ingerable en
# aval (prompts LLM). On plafonne au nombre de pages reellement utile.
MAX_PAGES = 120


def looks_like_pdf(url: str, content_type: str | None = None) -> bool:
    """Detecte un PDF par l'en-tete HTTP (fiable) ou l'extension (indicatif)."""
    if content_type and "application/pdf" in content_type.lower():
        return True
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


class _TooLarge(Exception):
    """Le PDF depasse la taille maximale autorisee."""


class PdfExtractor:
    """Telecharge et extrait le texte d'un PDF, sans navigateur."""

    async def try_extract(
        self,
        url: str,
        timeout: int = 60,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        max_bytes: int = MAX_PDF_BYTES,
    ) -> WebResult | None:
        """
        Retourne None si l'URL n'est pas un PDF (l'appelant passe alors au
        palier suivant), un WebResult sinon (succes ou erreur explicite).
        Une URL reconnue comme PDF dont le telechargement echoue donne une
        erreur explicite : les paliers suivants ne feraient que masquer
        l'echec.

        `transport` et `resolver` sont injectables pour tester sans reseau.
        """
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": HONEST_UA},
            transport=transport,
            event_hooks=safe_request_hooks(resolver),
        ) as client:
            try:
                return await self._download_and_parse(client, url, max_bytes)
            except UnsafeURLError as e:
                logger.warning(f"Telechargement PDF refuse pour {url}: {e}")
                return WebResult.from_error(url=url, error_message=str(e))
            except httpx.InvalidURL as e:
                return WebResult.from_error(url=url, error_message=f"URL mal formee: {e}")

    async def _download_and_parse(
        self, client: httpx.AsyncClient, url: str, max_bytes: int
    ) -> WebResult | None:
        # HEAD d'abord : evite de telecharger des dizaines de Mo pour
        # decouvrir que ce n'est pas un PDF. Certains serveurs refusent HEAD :
        # l'echec est journalise et on essaie alors un GET.
        head_type: str | None = None
        try:
            head = await client.head(url)
            if head.status_code < 400:
                head_type = head.headers.get("content-type")
            else:
                logger.info(f"HEAD {url} refuse (HTTP {head.status_code}), essai en GET")
        except UnsafeURLError:
            raise
        except httpx.HTTPError as e:
            logger.info(f"HEAD {url} en echec ({e}), essai en GET")

        if head_type is not None and not looks_like_pdf(url, head_type):
            return None

        try:
            async with client.stream("GET", url) as resp:
                recognized = looks_like_pdf(url, head_type) or looks_like_pdf(
                    url, resp.headers.get("content-type")
                )
                if not recognized:
                    return None

                if resp.status_code >= 400:
                    return WebResult.from_error(
                        url=url,
                        error_message=f"Telechargement du PDF refuse (HTTP {resp.status_code})",
                    )

                logger.info(f"PDF detecte, telechargement direct: {url}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise _TooLarge(int(declared))

                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise _TooLarge(received)
                    chunks.append(chunk)
                data = b"".join(chunks)
        except _TooLarge as e:
            size = e.args[0]
            return WebResult.from_error(
                url=url,
                error_message=(
                    f"PDF trop volumineux (plus de {max_bytes // (1024*1024)} Mo, "
                    f"{size // (1024*1024)} Mo recus ou annonces)"
                ),
            )
        except UnsafeURLError:
            raise
        except httpx.HTTPError as e:
            if looks_like_pdf(url, head_type):
                logger.warning(f"Echec telechargement PDF {url}: {e}")
                return WebResult.from_error(
                    url=url, error_message=f"Echec du telechargement du PDF: {e}"
                )
            logger.info(f"Echec GET {url}, URL non reconnue comme PDF: {e}")
            return None

        # Confirmation par la signature du fichier : une page d'erreur
        # HTML servie avec un content-type menteur ne passera pas.
        if not data.startswith(b"%PDF"):
            logger.info(f"Contenu annonce PDF mais signature absente: {url}")
            return None

        # Parsing : CPU pur, hors de la boucle d'evenements.
        text, method, total_pages, used_pages = await asyncio.to_thread(self._parse, data)

        if not text or len(text.strip()) < 50:
            return WebResult.from_error(
                url=url,
                error_message="PDF lu mais sans texte exploitable (probablement scanne - un OCR serait necessaire)"
            )

        title = urlparse(url).path.split("/")[-1] or "Document PDF"
        logger.info(f"Extraction PDF reussie ({method}): {len(text)} caracteres")

        metadata: dict[str, Any] = {
            "extraction_method": f"pdf_direct_{method}",
            "content_length": len(text),
            "size_bytes": len(data),
            "pages_total": total_pages,
            "pages_extracted": used_pages,
            "truncated": total_pages > used_pages,
        }
        if total_pages > used_pages:
            logger.warning(f"PDF tronque a {used_pages} pages sur {total_pages}: {url}")

        return WebResult(
            success=True,
            url=url,
            content_type="pdf",
            title=title,
            content=text.strip(),
            metadata=metadata,
        )

    def _extract_text(self, data: bytes) -> tuple[str, str]:
        text, method, _, _ = self._parse(data)
        return text, method

    def _parse(self, data: bytes) -> tuple[str, str, int, int]:
        """Retourne (texte, methode, pages du document, pages lues)."""
        # pdfplumber d'abord (meilleure restitution de la mise en page,
        # notamment les tableaux), pypdf en repli (plus tolerant aux PDF
        # malformes).
        try:
            import pdfplumber
            parts = []
            with pdfplumber.open(BytesIO(data)) as pdf:
                total = len(pdf.pages)
                for page in pdf.pages[:MAX_PAGES]:
                    parts.append(page.extract_text() or "")
            text = "\n\n".join(p for p in parts if p.strip())
            if text.strip():
                return text, "pdfplumber", total, min(total, MAX_PAGES)
        except Exception as e:
            logger.warning(f"pdfplumber a echoue, repli sur pypdf: {e}")

        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data))
            total = len(reader.pages)
            parts = [(p.extract_text() or "") for p in reader.pages[:MAX_PAGES]]
            return "\n\n".join(p for p in parts if p.strip()), "pypdf", total, min(total, MAX_PAGES)
        except Exception as e:
            logger.error(f"pypdf a echoue aussi: {e}")
            return "", "none", 0, 0
