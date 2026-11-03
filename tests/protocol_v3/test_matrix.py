"""M2 test matrix: every row has development coverage, lexicon checks are live."""
from __future__ import annotations

from mirror.evaluate.protocol_v3.access import DEV_LANGUAGE_CASES, load_dev_cases
from mirror.evaluate.protocol_v3.matrix import ROWS, absent_term_status, absent_terms_in, coverage


def test_every_row_has_development_coverage_and_none_independent_yet():
    cov = coverage([load_dev_cases(), load_dev_cases(DEV_LANGUAGE_CASES)])
    assert set(cov) == set(ROWS)
    for row, c in cov.items():
        assert c["dev_cases"], f"no development case for {row}"
        assert c["independent_cases"] == []


def test_absent_term_status_reads_live_lexicons():
    from mirror.interpret import guard
    assert absent_term_status("jar") == "regression" and absent_term_status("bowl") == "regression"
    assert absent_term_status("mug") == "kind_alias"
    assert absent_term_status(guard.ABSENT_NOUNS[0]) in ("lexicon", "regression")
    assert absent_term_status("thermos") == "unseen"


def test_absent_cases_carry_matching_status_tags():
    lang = load_dev_cases(DEV_LANGUAGE_CASES)
    for c in lang.cases:
        if "absent_object" in c.scope_tags:
            tag = next(t for t in c.scope_tags if t.startswith("absent:"))
            words = [w.strip(".,'").lower() for w in c.turns[0].text.split()]
            statuses = {absent_term_status(w) for w in words}
            assert tag.split(":")[1] in statuses
    assert set(absent_terms_in(lang).values()) <= {"regression", "lexicon"}
