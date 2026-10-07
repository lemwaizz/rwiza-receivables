"""Walks the Streamlit app headlessly (streamlit.testing). Run: pytest -q"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def fresh(tab="monday", selected=None):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    if tab != "monday" or selected:
        at.session_state["tab"] = tab
        if selected:
            at.session_state["selected"] = selected
        at.run()
        assert not at.exception, at.exception
    return at


def text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [m.value for m in at.metric]
    parts += [str(m.label) for m in at.metric] + [s.value for s in at.subheader]
    return "\n".join(str(p) for p in parts)


def test_monday_view_opens_with_confirmed_owed():
    at = fresh()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Confirmed owed"] == "RWF 740,000"
    assert metrics["Review items blocking a call"] == "3"
    assert metrics["Ordered, not yet shipped"] == "RWF 7,595,000"
    assert metrics["Payments with no dealer"] == "RWF 2,400,000"
    assert "Huye Seed Centre" in text(at)


def test_review_flow_resolves_karongi_and_blocks_hold():
    at = fresh("review")
    at.radio(key="stage_wa073").set_value("ORD-1040|Urea").run()
    at.button(key="commit_wa073").click().run()
    assert not at.exception
    at.session_state["tab"] = "monday"
    at.session_state["selected"] = "D-02"
    at.run()
    t = text(at)
    assert "RWF 90,000" in t
    disabled = [b for b in at.button if b.disabled]
    assert any("paid ahead of delivery" in b.label for b in disabled), [b.label for b in at.button]
    assert not any(b.label == "Place on credit hold" for b in at.button)


def test_nyamasheke_needs_a_price_then_resolves():
    at = fresh("review")
    assert at.button(key="commit_nyam").disabled
    at.button(key="preset_30").click().run()
    assert at.number_input(key="stage_nyam_price").value == 30000
    at.checkbox(key="stage_nyam_alloc").check().run()
    assert not at.button(key="commit_nyam").disabled
    at.button(key="commit_nyam").click().run()
    assert not at.exception
    assert any("WA-077" in e["m"] for e in at.session_state["S"]["log"])
    at.session_state["tab"] = "monday"
    at.run()
    assert {m.label: m.value for m in at.metric}["Review items blocking a call"] == "2"


def test_credit_hold_is_two_step_and_liftable():
    at = fresh(selected="D-03")
    assert at.session_state["S"]["holds"] == {}
    at.button(key="hold_D-03").click().run()
    assert at.session_state["S"]["holds"] == {}          # first click only asks for confirmation
    at.button(key="confirm_D-03").click().run()
    assert at.session_state["S"]["holds"]["D-03"]["amount"] == 740_000
    at.button(key="lift_D-03").click().run()
    assert at.session_state["S"]["holds"] == {}
    assert not at.exception


def test_hold_is_not_offered_where_a_review_item_blocks_it():
    at = fresh(selected="D-04")                          # Rubavu: ORD-1044 price still open
    assert not any(b.label == "Place on credit hold" for b in at.button)
    assert any(b.disabled and "Resolve 1 review item" in b.label for b in at.button)


@pytest.mark.parametrize("tab", ["review", "model", "log"])
def test_every_tab_renders(tab):
    at = fresh(tab)
    assert not at.exception


def test_reset_restores_defaults():
    at = fresh("review")
    at.radio(key="stage_p1044").set_value(32000).run()
    at.button(key="commit_p1044").click().run()
    assert at.session_state["S"]["p1044"]["committed"]
    [b for b in at.button if b.label == "Reset demo"][0].click().run()
    assert not at.session_state["S"]["p1044"]["committed"]
    assert at.session_state["tab"] == "monday"
