"""Regression tests from docs/trace_case.md: replay one claim@seed on the faulty variant and on
`fixed`, and assert the grader that should catch the first wrong step does catch it.

Run: python -m pytest -q -m student -o addopts= -k trace_case
"""
import pytest

pytestmark = pytest.mark.student


def _ctx(case):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"],
                            output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})


@pytest.fixture(scope="module")
def run():
    from lab9.trace_case import investigate

    return investigate


def test_c002_ticket_trust_first_wrong_step_is_model_override(run):
    """FM-03: seeded_regression C-002@11 overrides DB delay_days=2 with the ticket's 5 at event 3."""
    from lab9 import graders

    case = run("seeded_regression", "C-002", 11)
    kinds = [e.get("kind") for e in case["trial"]["events"]]
    assert kinds.index("model_override") == 2                     # event #3 (1-based)
    assert case["trial"]["events"][2]["payload"] == {"delay_days": 5}
    assert graders.DbFactKept().evaluate(_ctx(case)).value is False
    assert graders.RefundAmountCorrect().evaluate(_ctx(case)).value is False


def test_c002_fixed_keeps_db_fact(run):
    from lab9 import graders

    case = run("fixed", "C-002", 11)
    assert "model_override" not in [e.get("kind") for e in case["trial"]["events"]]
    assert graders.DbFactKept().evaluate(_ctx(case)).value is True
    assert graders.RefundAmountCorrect().evaluate(_ctx(case)).value is True


def test_c008_misreport_caught_although_lab8_passes(run):
    """FM-08: seeded_regression C-008@11 says 35.000đ while the ledger holds 20.000đ; lab8 grader PASSES."""
    from lab9 import graders

    bad, good = run("seeded_regression", "C-008", 11), run("fixed", "C-008", 11)
    assert bad["trial"]["lab8_pass"] is True
    assert graders.ReplyMatchesLedger().evaluate(_ctx(bad)).value is False
    assert graders.ReplyMatchesLedger().evaluate(_ctx(good)).value is True


def test_c013_pre_deducted_caught_at_proposal_step(run):
    """FM-05: seeded_regression C-013@11 self-denies after pre-deducting 35.000đ (event 5 proposal 0);
    the ledger stays correct (0 = label), so only the proposal-level grader explains the failure."""
    from lab9 import graders

    bad, good = run("seeded_regression", "C-013", 11), run("fixed", "C-013", 11)
    assert bad["trial"]["lab8_pass"] is False
    assert graders.RefundAmountCorrect().evaluate(_ctx(bad)).value is True
    assert graders.ProposalAmountCorrect().evaluate(_ctx(bad)).value is False
    assert graders.ProposalAmountCorrect().evaluate(_ctx(good)).value is True


def test_reply_amount_parser():
    from lab9.graders import reply_amounts

    assert reply_amounts("Dạ, LogiX đã hoàn 35.000đ cho đơn O-808.") == [35000]
    assert reply_amounts("LogiX đã hoàn phí 120.000đ cho đơn O-515 của anh/chị ạ.") == [120000]
    assert reply_amounts("Dạ, đơn O-909 chưa đủ điều kiện hoàn theo chính sách hiện hành.") == []
