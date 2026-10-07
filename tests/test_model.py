"""Checks the model against figures recomputed independently from the raw CSVs
(the same numbers used on the slides). Run: pytest -q"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import model as M  # noqa: E402

DATA = ROOT / "data"


@pytest.fixture(scope="module")
def model():
    return M.Model(M.build_data(DATA / "orders.csv", DATA / "deliveries.csv", DATA / "payments.csv"))


def owed(model, s):
    return {k: v["owed"] for k, v in model.compute(s)["D"].items()}


def test_links_and_allocations(model):
    dv = {d["ref"]: d for d in model.data["deliveries"]}
    assert dv["WA-071"]["link"]["status"] == "confirmed"
    assert dv["WA-073"]["link"]["status"] == "proposed"            # driver note vs arithmetic
    assert dv["WA-073"]["link"]["line"] == "ORD-1043|Urea"
    assert dv["WA-073"]["link"]["alts"] == ["ORD-1040|Urea"]
    assert dv["WA-077"]["link"]["line"] is None and dv["WA-077"]["dealerId"] == "D-06"
    assert dv["WA-074"]["returned"] == 15
    unmatched = [p for p in model.data["payments"] if not p["allocs"]]
    assert [p["txn"] for p in unmatched] == ["MM-5503"]


def test_default_balances(model):
    o = owed(model, M.fresh_state())
    assert o["D-03"] == 740_000                       # Huye: the one confirmed receivable
    assert o["D-01"] == 0 and o["D-05"] == 0 and o["D-04"] == 0
    assert o["D-02"] == 0                              # Karongi, WA-073 -> ORD-1043
    assert o["D-06"] is None                           # Nyamasheke: 80 bags unpriced


def test_kpis(model):
    k = model.compute(M.fresh_state())["kpi"]
    assert k["owed"] == 740_000 and k["owed_n"] == 1
    assert k["review"] == 3 and k["info"] == 1
    assert k["unshipped"] == 7_595_000                 # ordered, never shipped
    assert k["unmatched"] == 2_400_000 and k["unmatched_n"] == 1


def test_karongi_other_reading_and_guard_rail(model):
    s = M.fresh_state()
    s["wa073"] = dict(line="ORD-1040|Urea", committed=True)
    C = model.compute(s)
    d = C["D"]["D-02"]
    assert d["owed"] == 90_000
    assert d["prepaid"] == 2_370_000                   # ORD-1043 paid, nothing delivered against it
    assert d["can_hold"] is False                      # hold is blocked: re-allocate first


def test_nyamasheke_scenarios(model):
    s = M.fresh_state()
    s["nyam"] = dict(price=30_000, alloc=True, committed=True)
    assert owed(model, s)["D-06"] == 0
    s["nyam"] = dict(price=32_000, alloc=False, committed=True)
    assert owed(model, s)["D-06"] == 2_560_000
    s["nyam"] = dict(price=30_000, alloc=False, committed=True)
    assert owed(model, s)["D-06"] == 2_400_000


def test_rubavu_price_and_huye_returns(model):
    s = M.fresh_state()
    s["p1044"] = dict(value=28, committed=True)        # as recorded
    d = model.compute(s)["D"]["D-04"]
    assert d["owed"] == 300 * 28 - 8_400_000 == -8_391_600 and d["status"] == "Credit balance"
    s = M.fresh_state()
    s["huye"] = dict(credited=False, committed=True)
    assert owed(model, s)["D-03"] == 1_017_500


def test_credit_hold_rules(model):
    C = model.compute(M.fresh_state())["D"]
    assert C["D-03"]["can_hold"] is True               # Huye: owes, no blocking item, nothing prepaid
    for blocked in ("D-02", "D-04", "D-06"):
        assert C[blocked]["can_hold"] is False and C[blocked]["status"] == "Needs review"
    assert C["D-01"]["can_hold"] is False and C["D-01"]["why"] == "Nothing owed"
