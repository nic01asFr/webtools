"""
Extracteur PDF - palier dedie, avant tout usage de navigateur.

Un PDF n'est pas une page web : le rendu navigateur en produit une page
blanche (constate en usage reel sur des documents d'urbanisme), et l'agent
IA qui prend le relais consomme du temps et des tokens pour ne rien
extraire. Un telechargement direct + parsing texte est la seule voie
correcte, et de loin la moins chere.
"""

import logging
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.api.models import WebResult

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


class PdfExtractor:
    """Telecharge et extrait le texte d'un PDF, sans navigateur."""

    async def try_extract(self, url: str, timeout: int = 60) -> Optional[WebResult]:
        """
        Retourne None si l'URL n'est pas un PDF (l'appelant passe alors au
        palier suivant), un WebResult sinon (succes ou erreur explicite).
        """
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=timeout,
                headers={"User-Agent": HONEST_UA}
            ) as client:
                # HEAD d'abord : evite de telecharger des dizaines de Mo pour
                # decouvrir que ce n'est pas un PDF. Certains serveurs
                # refusent HEAD - on retombe alors sur l'extension.
                content_type = None
                try:
                    head = await client.head(url)
                    content_type = head.headers.get("content-type")
                except Exception:
                    pass

                if not looks_like_pdf(url, content_type):
                    return None

                logger.info(f"PDF detecte, telechargement direct: {url}")
                resp = await client.get(url)
                resp.raise_for_status()

                data = resp.content
                if len(data) > MAX_PDF_BYTES:
                    return WebResult.from_error(
                        url=url,
                        error_message=f"PDF trop volumineux ({len(data) // (1024*1024)} Mo, max {MAX_PDF_BYTES // (1024*1024)} Mo)"
                    )

                # Confirmation par la signature du fichier : une page d'erreur
                # HTML servie avec un content-type menteur ne passera pas.
                if not data.startswith(b"%PDF"):
                    logger.info(f"Contenu annonce PDF mais signature absente: {url}")
                    return None

        except Exception as e:
            logger.warning(f"Echec telechargement PDF {url}: {e}")
            return None

        # Parsing : pdfplumber d'abord (meilleure restitution de la mise en
        # page, notamment les tableaux), pypdf en repli (plus tolerant aux
        # PDF malformes).
        text, method = self._extract_text(data)

        if not text or len(text.strip()) < 50:
            return WebResult.from_error(
                url=url,
                error_message="PDF lu mais sans texte exploitable (probablement scanne - un OCR serait necessaire)"
            )

        title = urlparse(url).path.split("/")[-1] or "Document PDF"
        logger.info(f"Extraction PDF reussie ({method}): {len(text)} caracteres")

        return WebResult(
            success=True,
            url=url,
            content_type="pdf",
            title=title,
            content=text.strip(),
            metadata={
                "extraction_method": f"pdf_direct_{method}",
                "content_length": len(text),
                "size_bytes": len(data),
            }
        )

    def _extract_text(self, data: bytes) -> tuple[str, str]:
        try:
            import pdfplumber
            parts = []
            with pdfplumber.open(BytesIO(data)) as pdf:
                for page in pdf.pages[:MAX_PAGES]:
                    parts.append(page.extract_text() or "")
            text = "\n\n".join(p for p in parts if p.strip())
            if text.strip():
                return text, "pdfplumber"
        except Exception as e:
            logger.warning(f"pdfplumber a echoue, repli sur pypdf: {e}")

        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data))
            parts = [(p.extract_text() or "") for p in reader.pages[:MAX_PAGES]]
            return "\n\n".join(p for p in parts if p.strip()), "pypdf"
        except Exception as e:
            logger.error(f"pypdf a echoue aussi: {e}")
            return "", "none"
