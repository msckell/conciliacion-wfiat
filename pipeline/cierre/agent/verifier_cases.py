"""Texts the verifier must reject, with the problem code it must report.

tests/test_verifier.py runs every case, and the page shows the same table under "El
verificador de IA", so what the page claims is exactly what the tests prove.
"""

REJECT_CASES: list[tuple[str, str, str]] = [
    # (text written by the LLM, problem code, what is wrong, in Spanish for the page)
    ("Cerró el trimestre en 14.931.194.589 tokens.", "raw_figure", "Cifra escrita a mano"),
    (
        "La circulación casi se duplicó, con siete mil millones más.",
        "raw_figure",
        "Cifra escrita en palabras",
    ),
    ("Cerró en {wBRL.outstanding.2026-09-30} tokens.", "wrong_token", "Dato de otra moneda"),
    (
        "Al cierre anterior había {wARS.outstanding.2026-03-31}.",
        "wrong_period",
        "Dato de otro período",
    ),
    ("Las emisiones netas fueron {wARS.net_mints.2026Q3}.", "unknown_fact", "Dato inventado"),
    ("Se revisaron todas las redes verificadas.", "banned_claim", 'Dice "todas las redes"'),
    (
        "El token tiene respaldo completo y cobertura total.",
        "banned_claim",
        "Afirma respaldo o cobertura",
    ),
    (
        "El saldo concilia en cada red.",
        "banned_claim",
        "Da una conclusión que no salió de las plantillas",
    ),
    (
        "Creció por emisión primaria; el puente casi no cambió.",
        "style",
        "Usa punto y coma",
    ),
    (
        "Creció por emisión primaria — el puente casi no cambió.",
        "style",
        "Usa guion largo",
    ),
]
