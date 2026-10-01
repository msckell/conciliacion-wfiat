"""The verifier must reject every way an explanation can carry a figure or a conclusion
the templates did not produce, and accept clean text."""

import pytest

from cierre.agent.verifier import check, render
from cierre.agent.verifier_cases import REJECT_CASES

FACTS = {
    "wARS.outstanding.2026-09-30": "14.931.194.589",
    "wARS.outstanding.2026-06-30": "7.619.996.077",
    "wARS.primary_mints.2026Q3": "9.729.100.030",
    "wBRL.outstanding.2026-09-30": "13.807.477",
}
PERIODS = {"2026-06-30", "2026-09-30", "2026Q3"}


def codes(text: str, token: str = "wARS") -> set[str]:
    return {p.code for p in check(text, token, FACTS, PERIODS)}


@pytest.mark.parametrize("text, expected", [(t, c) for t, c, _ in REJECT_CASES])
def test_rejects(text, expected):
    assert expected in codes(text)


def test_accepts_valid_fact_ids_and_renders_them():
    text = (
        "La circulación pasó de {wARS.outstanding.2026-06-30} a "
        "{wARS.outstanding.2026-09-30}, sobre todo por emisión primaria "
        "({wARS.primary_mints.2026Q3})."
    )
    assert codes(text) == set()
    assert "14.931.194.589" in render(text, FACTS)


def test_accepts_paraphrase_without_figures():
    text = "El crecimiento vino casi todo de emisión primaria. El puente movió tokens entre redes."
    assert codes(text) == set()


def test_agent_summary_may_quote_hashes_but_not_amounts():
    from cierre.agent.verifier import check_free_text

    ok = "La cuenta 0x6c3acdc8c93d13087e3348732a1894e5f3c164b3 quemó el monto en un ERC1967Proxy."
    assert check_free_text(ok) == []
    bad = "Mandó 37112040867826365798 wARS en el bloque 20356249."
    assert {p.code for p in check_free_text(bad)} == {"raw_figure"}
