"""Tests de app.utils.chunking (constat C73)."""

from app.utils.chunking import (
    MIN_CHUNK_CHARS,
    document_to_chunk_items,
    split_into_chunks,
)

CHAPO = "Selon l'INSEE, la part des 65 ans et plus atteint 28,9 % en 2021."


def _phrases(n: int) -> str:
    return " ".join(f"Phrase numero {i} du long paragraphe de test." for i in range(n))


def _chapo_puis_long_paragraphe() -> str:
    return CHAPO + "\n\n" + _phrases(60)


def test_vide_et_blanc() -> None:
    assert split_into_chunks("") == []
    assert split_into_chunks("   \n\n ") == []
    assert split_into_chunks(None) == []  # type: ignore[arg-type]


def test_texte_court_un_seul_chunk() -> None:
    assert split_into_chunks("Bonjour le monde") == ["Bonjour le monde"]


def test_decoupe_longue_respecte_la_taille() -> None:
    texte = "\n\n".join(_phrases(20) for _ in range(30))
    chunks = split_into_chunks(texte)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 + 300 for c in chunks)


def test_chevauchement_entre_chunks() -> None:
    texte = "\n\n".join(f"Paragraphe {i}. " + _phrases(25) for i in range(12))
    chunks = split_into_chunks(texte)
    assert len(chunks) > 2
    assert chunks[1][:40] in chunks[0]


def test_texte_sans_ponctuation_coupe_net_par_defaut() -> None:
    chunks = split_into_chunks("mot " * 1500)
    assert len(chunks) >= 3
    assert len(chunks[0]) in (1999, 2000)


def test_document_de_100000_caracteres() -> None:
    texte = "\n\n".join(_phrases(18) for _ in range(150))
    assert len(texte) > 100_000
    chunks = split_into_chunks(texte)
    assert len(chunks) >= 50
    assert all(len(c) >= MIN_CHUNK_CHARS for c in chunks)


def test_texte_non_latin() -> None:
    texte = "\n\n".join("これは日本語の文章です。" * 40 for _ in range(30))
    chunks = split_into_chunks(texte)
    assert len(chunks) > 1
    assert "日本語" in chunks[0]


def test_resultat_jamais_vide_si_tout_est_court() -> None:
    texte = "a. " * 5 + "\n\n" + "b" * 1990
    assert split_into_chunks(texte, chunk_chars=100)


# --- C73 : comportement par defaut conserve (type B), puis mode v2 ---------


def test_defaut_supprime_le_chapo_court() -> None:
    chunks = split_into_chunks(_chapo_puis_long_paragraphe())
    assert not any("28,9 % en 2021" in c for c in chunks)


def test_v2_conserve_le_chapo_court() -> None:
    chunks = split_into_chunks(_chapo_puis_long_paragraphe(), v2=True)
    assert any("28,9 % en 2021" in c for c in chunks)
    assert all(len(c) >= MIN_CHUNK_CHARS for c in chunks)


def test_v2_dernier_fragment_court_rattache_au_precedent() -> None:
    texte = _phrases(60) + "\n\nConclusion : 12 345 cas."
    chunks = split_into_chunks(texte, v2=True)
    assert "12 345 cas" in chunks[-1]
    assert all(len(c) >= MIN_CHUNK_CHARS for c in chunks)


def test_v2_texte_sans_ponctuation_coupe_sur_espace_avec_chevauchement() -> None:
    nombres = " ".join(str(100000 + i) for i in range(1200))
    chunks = split_into_chunks(nombres, v2=True)
    assert len(chunks) >= 3
    valides = set(nombres.split())
    for c in chunks:
        assert set(c.split()) <= valides
    for a, b in zip(chunks, chunks[1:]):  # noqa: RUF007
        assert a.split()[-1] in b.split()


def test_v2_sans_effet_sur_texte_court_ou_vide() -> None:
    assert split_into_chunks("", v2=True) == []
    assert split_into_chunks("court", v2=True) == ["court"]


def test_document_to_chunk_items_metadonnees() -> None:
    contenu = _phrases(200)
    items = document_to_chunk_items("http://x", None, contenu)  # type: ignore[arg-type]
    assert len(items) > 1
    assert items[0]["title"] == ""
    assert items[0]["source"] == "http://x"
    meta = items[1]["metadata"]
    assert meta["chunk_index"] == 1
    assert meta["chunk_total"] == len(items)
    assert meta["parent_length"] == len(contenu)


def test_document_to_chunk_items_vide() -> None:
    assert document_to_chunk_items("s", "t", "") == []
    assert document_to_chunk_items("s", "t", None) == []  # type: ignore[arg-type]


def test_document_to_chunk_items_v2() -> None:
    contenu = _chapo_puis_long_paragraphe()
    defaut = document_to_chunk_items("s", "t", contenu)
    v2 = document_to_chunk_items("s", "t", contenu, v2=True)
    assert not any("28,9 % en 2021" in i["content"] for i in defaut)
    assert any("28,9 % en 2021" in i["content"] for i in v2)
