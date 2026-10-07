"""Rwiza receivables model: matching layer + derived balances.

Pure Python (no Streamlit), so it can be unit-tested and reused.

Two parts:

1. build_data(): reads orders.csv, deliveries.csv, payments.csv and builds the six
   object types (Dealer, Order, OrderLine, Delivery, Payment, PaymentAllocation).
   Every inferred link carries a status (confirmed | proposed), who confirmed it
   ("rule" or a person) and the evidence ("basis"). Ambiguous links stay "proposed".

2. Model.compute(): derives every balance from the objects and the current review
   decisions. Nothing is stored: owed = delivered (net of returns) x order-line
   price - payment allocations.

The four review items (WA-073, WA-077/MM-5503, ORD-1044 price, Huye returns) are
specific to this March extract. They are the places where the three files cannot
settle the answer, so a person has to.
"""
from __future__ import annotations

import copy
import csv
import re
from datetime import datetime
from pathlib import Path

RETRO = "ORD-RETRO-1|NPK"          # line key of the order Finance raises for WA-077
RETRO_ORDER = "ORD-RETRO-1"
P1044_LINE = "ORD-1044|NPK"        # price recorded as 28 (a unit slip for 28,000)
UNREFERENCED_TXN = "MM-5503"       # M. UWASE, 2,400,000, no reference

# Dealer entity resolution: the one hand-built input (an alias table).
DEALERS = [
    dict(id="D-01", name="Musanze Agrovet", cust=["C-008"], aliases=["musanze agrovet"]),
    dict(id="D-02", name="Karongi Agrodealers", cust=["C-014", "C-041"],
         aliases=["karongi agrodealers", "karongi agro dealers"]),
    dict(id="D-03", name="Huye Seed Centre", cust=["C-021"], aliases=["huye seed centre", "huye seed ctr"]),
    dict(id="D-04", name="Rubavu Cross-Border Traders", cust=["C-033"],
         aliases=["rubavu cross border traders", "rubavu cross border"]),
    dict(id="D-05", name="Nyagatare Farmers Union", cust=["C-052"], aliases=["nyagatare farmers union"]),
    dict(id="D-06", name="Nyamasheke Coop", cust=[], aliases=["nyamasheke coop"]),
]


# --------------------------------------------------------------------------- matching layer
def _norm(s: str) -> str:
    s = s.lower().replace("-", " ")
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\bltd\b", "", s)
    return " ".join(s.split())


def _short(product: str) -> str:
    return product.split()[0]  # "NPK 17-17-17" -> "NPK", "Urea 46%" -> "Urea"


def _read(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_data(orders_csv, deliveries_csv, payments_csv) -> dict:
    orders_raw, deliv_raw, pays_raw = _read(orders_csv), _read(deliveries_csv), _read(payments_csv)
    by_alias = {a: d["id"] for d in DEALERS for a in d["aliases"]}
    by_cust = {c: d["id"] for d in DEALERS for c in d["cust"]}
    dealer_of_name = lambda n: by_alias.get(_norm(n))

    # Order + OrderLine (price lives on the line, not on the product)
    orders: dict[str, dict] = {}
    lines: dict[str, dict] = {}
    for r in orders_raw:
        oid = r["order_id"]
        o = orders.setdefault(oid, dict(
            id=oid, dealerId=by_cust[r["cust_id"]], custId=r["cust_id"], customerName=r["customer_name"],
            orderDate=r["order_date"], promised=r["promised_date"], lines=[]))
        key = f"{oid}|{_short(r['product'])}"
        line = dict(key=key, order=oid, short=_short(r["product"]), product=r["product"],
                    bags=int(r["qty_bags"]), price=int(r["unit_price_rwf"]))
        o["lines"].append(line)
        lines[key] = line

    # Delivery -> OrderLine (rules applied in file order)
    deliveries, delivered_on = [], {}
    for r in deliv_raw:
        dealer = dealer_of_name(r["dealer"])
        ret = re.search(r"returned (\d+) bags", r["notes"])
        d = dict(ref=r["ref"], dealerId=dealer, dealerText=r["dealer"], short=_short(r["item"]),
                 bags=int(r["bags"]), returned=int(ret.group(1)) if ret else 0,
                 date=datetime.strptime(r["date"], "%d/%m/%Y").date().isoformat(),
                 driver=r["driver"], notes=r["notes"],
                 link=dict(line=None, status="proposed", by=None, basis="", alts=[]))
        cands = [l for l in lines.values() if orders[l["order"]]["dealerId"] == dealer and l["short"] == d["short"]]
        open_cands = [l for l in cands if delivered_on.get(l["key"], 0) < l["bags"]]
        exact = [l for l in open_cands if l["bags"] - delivered_on.get(l["key"], 0) == d["bags"]]
        L = d["link"]
        if not cands:
            L["basis"] = f"No order for this dealer in orders.csv ({r['dealer']} does not appear there)."
        elif "balance of" in r["notes"]:
            # the note says it completes an earlier part load: check the arithmetic
            prev = [l for l in cands if delivered_on.get(l["key"], 0) > 0]
            remaining = prev[0]["bags"] - delivered_on[prev[0]["key"]] if prev else None
            L.update(status="proposed", by=None, line=exact[0]["key"] if exact else None,
                     alts=[prev[0]["key"]] if prev else [],
                     basis=(f"Note says '{r['notes']}', but the balance of {prev[0]['order']} would be {remaining} bags, "
                            f"not {d['bags']}. {d['bags']} bags equals {exact[0]['order']} exactly, and that order was paid "
                            f"3 days later (MM-5508)."))
        elif len(exact) == 1:
            l = exact[0]
            L.update(line=l["key"], status="confirmed", by="rule",
                     basis=f"Dealer + product + quantity match ({d['bags']} bags = {l['order']} line).")
        elif "part load" in r["notes"]:
            l = min([c for c in open_cands if c["bags"] > d["bags"]], key=lambda c: orders[c["order"]]["promised"])
            # corroborate with a payment that names this order and equals bags x price
            pm = next((q for q in pays_raw
                       if f"ORD-{''.join(ch for ch in q['reference'] if ch.isdigit())}" == l["order"]
                       and int(q["amount_rwf"]) == d["bags"] * l["price"]), None)
            if pm:
                L.update(line=l["key"], status="confirmed", by="rule",
                         basis=(f"Note says part load; payment {pm['txn_id']} '{pm['reference']}' = {d['bags']} bags x "
                                f"{l['price']:,} confirms {l['order']}."))
            else:
                L.update(line=l["key"], status="proposed", by=None,
                         basis=f"Note says part load; earliest open order for this product is {l['order']}. "
                               f"No payment corroborates it.")
        if L["line"]:
            delivered_on[L["line"]] = delivered_on.get(L["line"], 0) + d["bags"]
        deliveries.append(d)

    # Payment -> Order (PaymentAllocation)
    payments = []
    for r in pays_raw:
        dealer = dealer_of_name(r["payer"])
        p = dict(txn=r["txn_id"], payer=r["payer"], dealerId=dealer, amount=int(r["amount_rwf"]),
                 ts=r["timestamp"], ref=r["reference"], allocs=[], note="")
        digits = "".join(c for c in r["reference"] if c.isdigit())
        if digits and f"ORD-{digits}" in orders:
            o = orders[f"ORD-{digits}"]
            assert o["dealerId"] == dealer, (p["txn"], "payer and order belong to different dealers")
            total = sum(l["bags"] * l["price"] for l in o["lines"])
            on_order = [d for d in deliveries if d["link"]["line"] and lines[d["link"]["line"]]["order"] == o["id"]]
            deliv_val = sum(d["bags"] * lines[d["link"]["line"]]["price"] for d in on_order)
            net_val = sum((d["bags"] - d["returned"]) * lines[d["link"]["line"]]["price"] for d in on_order)
            if p["amount"] == total:
                basis = f"Reference {digits} + amount equals the order total ({total:,})."
            elif "part" in r["reference"].lower() and p["amount"] == deliv_val:
                basis = f"Reference says 'part'; amount equals the value delivered so far ({deliv_val:,})."
            elif p["amount"] == net_val and net_val != deliv_val:
                basis = (f"Reference {digits}; amount equals delivered value net of the returned bags ({net_val:,}), "
                         f"not the full {deliv_val:,}.")
            else:
                basis = (f"Reference {digits}; amount does not equal the order total at recorded prices ({total:,}) "
                         f"- see review item on ORD-1044's price.")
            p["allocs"].append(dict(order=o["id"], amount=p["amount"], status="confirmed", by="rule", basis=basis))
        else:
            p["note"] = f"No reference, and payer '{r['payer']}' matches no known dealer."
            p["hint"] = "2,400,000 / 80 bags = 30,000 per bag: could be WA-077 (Nyamasheke Coop, 80 NPK, next day)."
        payments.append(p)

    return dict(dealers=copy.deepcopy(DEALERS), orders=list(orders.values()),
                deliveries=deliveries, payments=payments)


# --------------------------------------------------------------------------- review state
def fresh_state() -> dict:
    """Decisions Finance has confirmed so far, plus credit holds and the activity log."""
    return dict(
        wa073=dict(line="ORD-1043|Urea", committed=False),
        nyam=dict(price=None, alloc=False, committed=False),
        p1044=dict(value=28000, committed=False),
        huye=dict(credited=True, committed=False),
        holds={}, log=[],
    )


# id, dealers affected, does it block a call on those dealers?, is it resolved?
ITEMS = [
    dict(id="wa073", dealers=["D-02"], blocking=True, resolved=lambda s: s["wa073"]["committed"]),
    dict(id="nyam", dealers=["D-06"], blocking=True, resolved=lambda s: s["nyam"]["committed"]),
    dict(id="p1044", dealers=["D-04"], blocking=True, resolved=lambda s: s["p1044"]["committed"]),
    dict(id="huye", dealers=["D-03"], blocking=False, resolved=lambda s: s["huye"]["committed"]),
]
ITEM_SHORT = {"wa073": "WA-073 order", "nyam": "WA-077 no order",
              "p1044": "ORD-1044 price", "huye": "15 returned bags"}


def fmt(n) -> str:
    n = int(round(n))
    return ("−" if n < 0 else "") + f"{abs(n):,}"


def rwf(n) -> str:
    return "unpriced" if n is None else "RWF " + fmt(n)


def dshort(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b").lstrip("0") if iso else ""


def describe(d: dict) -> str:
    """One-line summary of a dealer's balance, used in what-if previews."""
    if d["owed"] is None:
        head = "the balance cannot be priced"
    elif d["owed"] > 0:
        head = "owes " + rwf(d["owed"])
    elif d["owed"] < 0:
        head = "has a credit of " + rwf(-d["owed"])
    else:
        head = "owes nothing"
    if d["prepaid"] > 0 and d["owed"] is not None:
        head += "; " + rwf(d["prepaid"]) + " paid ahead of delivery"
    if d["unshipped"]:
        head += "; " + rwf(d["unshipped"]) + " still to ship"
    return head


# --------------------------------------------------------------------------- the model
class Model:
    def __init__(self, data: dict):
        self.data = data
        self.dealers = {d["id"]: d for d in data["dealers"]}
        self.orders = {o["id"]: o for o in data["orders"]}
        self.lines = {l["key"]: l for o in data["orders"] for l in o["lines"]}

    # -- effective links and prices under the current decisions
    def order_of_line(self, key):
        return RETRO_ORDER if key == RETRO else self.lines[key]["order"]

    def line_price(self, key, s):
        if key == RETRO:
            return s["nyam"]["price"] if s["nyam"]["committed"] else None
        if key == P1044_LINE:
            return s["p1044"]["value"]
        return self.lines[key]["price"]

    def eff_line(self, d, s):
        if d["ref"] == "WA-073":
            return s["wa073"]["line"]
        if d["ref"] == "WA-077":
            return RETRO if s["nyam"]["committed"] else None
        return d["link"]["line"]

    def eff_link(self, d, s):
        if d["ref"] == "WA-073":
            return dict(status="confirmed", by="Finance") if s["wa073"]["committed"] else dict(status="proposed", by=None)
        if d["ref"] == "WA-077":
            return dict(status="confirmed", by="Finance") if s["nyam"]["committed"] else dict(status="proposed", by=None)
        return dict(status=d["link"]["status"], by=d["link"]["by"])

    # -- derived balances
    def compute(self, s: dict) -> dict:
        data = self.data
        D = {x["id"]: dict(id=x["id"], name=x["name"], delivered=0, paid=0,
                           unpriced_bags=0, prepaid=0, unshipped=0) for x in data["dealers"]}
        ord_del, ord_alloc, line_ship, line_refs = {}, {}, {}, {}

        for d in data["deliveries"]:
            key = self.eff_line(d, s)
            if not key:
                D[d["dealerId"]]["unpriced_bags"] += d["bags"]
                continue
            line_ship[key] = line_ship.get(key, 0) + d["bags"]
            line_refs.setdefault(key, []).append(d)
            price = self.line_price(key, s)
            if price is None:
                D[d["dealerId"]]["unpriced_bags"] += d["bags"]
                continue
            credited = d["returned"] if s["huye"]["credited"] else 0
            oid = self.order_of_line(key)
            value = (d["bags"] - credited) * price
            ord_del[oid] = ord_del.get(oid, 0) + value
            D[d["dealerId"]]["delivered"] += value

        unmatched = []
        for p in data["payments"]:
            if p["allocs"]:
                for a in p["allocs"]:
                    ord_alloc[a["order"]] = ord_alloc.get(a["order"], 0) + a["amount"]
                    D[self.orders[a["order"]]["dealerId"]]["paid"] += a["amount"]
            elif s["nyam"]["committed"] and s["nyam"]["alloc"] and p["txn"] == UNREFERENCED_TXN:
                ord_alloc[RETRO_ORDER] = ord_alloc.get(RETRO_ORDER, 0) + p["amount"]
                D["D-06"]["paid"] += p["amount"]
            else:
                unmatched.append(p)

        order_list = [dict(o, lines=[dict(l, price=self.line_price(l["key"], s)) for l in o["lines"]])
                      for o in data["orders"]]
        if s["nyam"]["committed"]:
            order_list.append(dict(
                id=RETRO_ORDER, dealerId="D-06", retro=True, orderDate=None, promised=None,
                lines=[dict(key=RETRO, short="NPK", product="NPK (order raised retroactively by Finance)",
                            bags=80, price=s["nyam"]["price"])]))

        O = {}
        for o in order_list:
            shipped_all = True
            lines = []
            for l in o["lines"]:
                shipped = line_ship.get(l["key"], 0)
                rem = max(0, l["bags"] - shipped)
                if rem:
                    shipped_all = False
                    if l["price"] is not None:
                        D[o["dealerId"]]["unshipped"] += rem * l["price"]
                lines.append(dict(l, shipped=shipped, rem=rem, refs=line_refs.get(l["key"], [])))
            delivered, allocated = ord_del.get(o["id"], 0), ord_alloc.get(o["id"], 0)
            if not delivered and not allocated:
                status, tone = "Not shipped yet", "gray"
            elif allocated > delivered:
                status, tone = "Paid ahead of delivery: " + rwf(allocated - delivered), "orange"
                D[o["dealerId"]]["prepaid"] += allocated - delivered
            elif delivered > allocated:
                status, tone = "Delivered, " + rwf(delivered - allocated) + " unpaid", "red"
            else:
                status = "Delivered and paid" if shipped_all else "Part-shipped, paid for what shipped"
                tone = "green"
            allocs = [dict(a, txn=p["txn"]) for p in data["payments"] for a in p["allocs"] if a["order"] == o["id"]]
            if o.get("retro") and s["nyam"]["alloc"]:
                allocs.append(dict(txn=UNREFERENCED_TXN, amount=2400000, status="confirmed", by="Finance",
                                   basis="Confirmed by Finance: M. UWASE paid for WA-077."))
            O[o["id"]] = dict(o, lines=lines, delivered=delivered, allocated=allocated,
                              status=status, tone=tone, allocs=allocs)

        for did, d in D.items():
            d["owed"] = None if d["unpriced_bags"] else d["delivered"] - d["paid"]
            d["blockers"] = [i["id"] for i in ITEMS if i["blocking"] and did in i["dealers"] and not i["resolved"](s)]
            d["infos"] = [i["id"] for i in ITEMS if not i["blocking"] and did in i["dealers"] and not i["resolved"](s)]
            d["hold"] = s["holds"].get(did)
            if d["hold"]:
                d["status"] = "On credit hold"
            elif d["blockers"] or d["owed"] is None:
                d["status"] = "Needs review"
            elif d["owed"] > 0:
                d["status"] = "Owes"
            elif d["owed"] < 0:
                d["status"] = "Credit balance"
            else:
                d["status"] = "Settled"
            d["can_hold"] = d["status"] == "Owes" and d["prepaid"] == 0
            if d["hold"]:
                d["why"] = "On hold"
            elif d["blockers"]:
                n = len(d["blockers"])
                d["why"] = f"Resolve {n} review item{'s' if n > 1 else ''} first"
            elif d["owed"] is not None and d["owed"] > 0 and d["prepaid"] > 0:
                d["why"] = rwf(d["prepaid"]) + " is paid ahead of delivery on another order: re-allocate it first"
            else:
                d["why"] = "Nothing owed"
            d["pct"] = min(100, int(d["paid"] / d["delivered"] * 100 + 0.5)) if d["delivered"] > 0 else None
            d["orders"] = [o for o in O.values() if o["dealerId"] == did]

        owing = [d for d in D.values() if d["status"] in ("Owes", "On credit hold") and d["owed"] > 0]
        kpi = dict(
            owed=sum(d["owed"] for d in owing), owed_n=len(owing),
            review=sum(1 for i in ITEMS if i["blocking"] and not i["resolved"](s)),
            info=sum(1 for i in ITEMS if not i["blocking"] and not i["resolved"](s)),
            unshipped=sum(d["unshipped"] for d in D.values()),
            unmatched=sum(p["amount"] for p in unmatched), unmatched_n=len(unmatched))
        return dict(D=D, O=O, unmatched=unmatched, kpi=kpi)

    def what_if(self, s: dict, mutate) -> dict:
        """Compute the balances as if `mutate` were applied to a copy of the state."""
        c = copy.deepcopy(s)
        mutate(c)
        return self.compute(c)
