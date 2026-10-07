"""Rwiza Agri Distributors: receivables view (Streamlit prototype).

Run locally:  streamlit run app.py
The model lives in model.py; this file is only the interface.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

import model as M
from model import dshort, fmt, rwf

st.set_page_config(page_title="Rwiza Receivables Prototype", page_icon=":material/grain:", layout="wide")

DATA_DIR = Path(__file__).parent / "data"
CAT = timezone(timedelta(hours=2))  # Rwanda time, no daylight saving
TABS = {"monday": "Monday view", "review": "Review queue", "model": "Model objects", "log": "Activity"}


@st.cache_resource
def get_model() -> M.Model:
    return M.Model(M.build_data(DATA_DIR / "orders.csv", DATA_DIR / "deliveries.csv", DATA_DIR / "payments.csv"))


model = get_model()
ss = st.session_state
if "S" not in ss:
    ss.S = M.fresh_state()
    ss.pending_hold = None
    ss.selected = "D-03"
    ss.tab = "monday"
S = ss.S


# ----------------------------------------------------------------------------- actions (callbacks)
def now() -> str:
    return datetime.now(CAT).strftime("%H:%M CAT")


def log(msg: str) -> None:
    S["log"].insert(0, dict(t=now(), m=msg))


def clear_pending() -> None:
    ss.pending_hold = None


def reset_demo() -> None:
    for k in [k for k in ss if k.startswith("stage_")]:
        del ss[k]
    ss.S = M.fresh_state()
    ss.pending_hold, ss.selected, ss.tab = None, "D-03", "monday"


def set_nyam_price(v: int) -> None:
    ss.stage_nyam_price = v


def commit(item: str) -> None:
    ss.pending_hold = None
    if item == "wa073":
        S["wa073"] = dict(line=ss.stage_wa073, committed=True)
        log(f"Finance confirmed WA-073 → **{ss.stage_wa073.split('|')[0]}**.")
    elif item == "nyam":
        price = ss.get("stage_nyam_price")
        if not price or price <= 0:
            return
        alloc = bool(ss.get("stage_nyam_alloc", False))
        S["nyam"] = dict(price=int(price), alloc=alloc, committed=True)
        log(f"Finance raised an order for WA-077 at {rwf(price)} a bag"
            + (" and allocated MM-5503 to it." if alloc else "."))
    elif item == "p1044":
        S["p1044"] = dict(value=int(ss.stage_p1044), committed=True)
        log(f"Finance confirmed ORD-1044 price: {rwf(ss.stage_p1044)} a bag.")
    elif item == "huye":
        S["huye"] = dict(credited=bool(ss.stage_huye), committed=True)
        log(f"Finance confirmed Huye's 15 returned bags were {'' if ss.stage_huye else 'not '}credited.")


def start_hold(did: str) -> None:
    ss.pending_hold = did


def place_hold(did: str) -> None:
    d = model.compute(S)["D"][did]
    S["holds"][did] = dict(amount=d["owed"], time=now())
    ss.pending_hold = None
    log(f"Credit hold placed on **{d['name']}** for {rwf(d['owed'])} confirmed owed.")


def lift_hold(did: str) -> None:
    S["holds"].pop(did, None)
    ss.pending_hold = None
    log(f"Credit hold lifted on **{model.dealers[did]['name']}**.")


# ----------------------------------------------------------------------------- small helpers
STATUS_LABEL = {"Settled": "✓ Settled", "Owes": "● Owes", "Needs review": "▲ Needs review",
                "Credit balance": "◆ Credit balance", "On credit hold": "■ On credit hold"}


TONE_MARK = {"green": "✓", "red": "●", "orange": "▲", "gray": "○"}


def link_badge(link: dict) -> str:
    if link["status"] == "confirmed":
        return f"**✓ confirmed · {link['by']}**"
    return "**▲ proposed**"


def evidence(items: list[str]) -> None:
    st.markdown("\n".join(f"- {x}" for x in items))


def df(rows: list[dict], **kw) -> None:
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", **kw)


# ----------------------------------------------------------------------------- views
def view_monday(C: dict) -> None:
    k = C["kpi"]
    cols = st.columns(4)
    tiles = [
        ("Confirmed owed", rwf(k["owed"]),
         f"{k['owed_n']} dealer{'' if k['owed_n'] == 1 else 's'}, delivered goods with no open review item"),
        ("Review items blocking a call", str(k["review"]),
         (f"{k['info']} more is informational" if k["info"] else "none informational") + " (see Review queue)"),
        ("Ordered, not yet shipped", rwf(k["unshipped"]), "Not owed: no goods have left the warehouse"),
        ("Payments with no dealer", rwf(k["unmatched"]),
         f"{k['unmatched_n']} payment{'' if k['unmatched_n'] == 1 else 's'}"
         + (": " + ", ".join(f"{p['txn']} ({p['payer']})" for p in C["unmatched"]) if k["unmatched_n"] else "")),
    ]
    for col, (label, value, note) in zip(cols, tiles):
        with col, st.container(border=True):
            st.metric(label, value)
            st.caption(note)

    st.info("All amounts in RWF. Dealers marked ▲ have figures that depend on a review item that is still "
            "open, so treat their numbers as provisional. Pick a dealer below to see its orders, deliveries and "
            "payments as the model links them.")

    rows = []
    for d in C["D"].values():
        open_items = [M.ITEM_SHORT[b] for b in d["blockers"]]
        notes = [M.ITEM_SHORT[b] for b in d["infos"]]
        rows.append({
            "Dealer": d["name"],
            "Delivered (net)": fmt(d["delivered"]) if d["owed"] is not None else "unpriced",
            "Paid": fmt(d["paid"]),
            "Owed": fmt(d["owed"]) if d["owed"] is not None else "unpriced",
            "Paid vs delivered": d["pct"],
            "Not yet shipped": fmt(d["unshipped"]) if d["unshipped"] else "—",
            "Status": STATUS_LABEL[d["status"]],
            "Open items": ("open: " + ", ".join(open_items)) if open_items else ("note: " + ", ".join(notes) if notes else ""),
        })
    cc = st.column_config
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Dealer": cc.Column("Dealer", width=190),
        "Delivered (net)": cc.TextColumn("Delivered (net)", alignment="right", width=120, help="Delivered value net of returned bags"),
        "Paid": cc.TextColumn("Paid", alignment="right", width=100),
        "Owed": cc.TextColumn("Owed", alignment="right", width=100, help="Delivered (net of returns) minus payments allocated"),
        "Paid vs delivered": cc.ProgressColumn("Paid vs delivered", min_value=0, max_value=100, format="%d%%", width=135),
        "Not yet shipped": cc.TextColumn("Not yet shipped", alignment="right", width=120),
        "Status": cc.Column("Status", width=135),
        "Open items": cc.Column("Open items"),
    })

    st.selectbox("Dealer detail", list(C["D"]), key="selected", format_func=lambda i: model.dealers[i]["name"],
                 on_change=clear_pending)
    view_dealer(C)


def view_dealer(C: dict) -> None:
    did = ss.selected
    d, dl = C["D"][did], model.dealers[did]
    with st.container(border=True):
        st.subheader(d["name"])
        st.caption(f"Dealer {did} · accounting IDs: {', '.join(dl['cust']) or 'none in orders.csv'} · "
                   f"name variants: {', '.join(dl['aliases'])}")
        st.write(f"The balance is the sum of these links: {rwf(d['delivered'])} delivered − {rwf(d['paid'])} paid "
                 f"= **{rwf(d['owed']) if d['owed'] is not None else 'unpriced'}**.")
        action_panel(d)

    for x in model.data["deliveries"]:
        if x["dealerId"] == did and not model.eff_line(x, S):
            with st.container(border=True):
                st.markdown(f"**{x['ref']}** · {x['bags']} bags {x['short']}, {dshort(x['date'])}, "
                            f"note: “{x['notes']}” · **▲ No order linked**")
                st.caption(x["link"]["basis"])

    for o in d["orders"]:
        with st.container(border=True):
            sub = "raised retroactively" if o.get("retro") else f"ordered {dshort(o['orderDate'])}, promised {dshort(o['promised'])}"
            st.markdown(f"**{o['id']}** · {sub} · {TONE_MARK[o['tone']]} **{o['status']}** · "
                        f"Delivered {rwf(o['delivered'])} · Paid {rwf(o['allocated'])}")
            md = ["| OrderLine | Ordered | Price (RWF) | Deliveries | To ship |", "|---|---:|---:|---|---:|"]
            for l in o["lines"]:
                price = "unpriced" if l["price"] is None else fmt(l["price"])
                if l["key"] == M.P1044_LINE and not S["p1044"]["committed"]:
                    price += " (recorded 28, proposed fix)"
                refs = []
                for r in l["refs"]:
                    ret = ""
                    if r["returned"]:
                        ret = f" ({r['returned']} returned{'' if S['huye']['credited'] else ', not credited'})"
                    refs.append(f"{r['ref']} · {r['bags']} bags{ret} {link_badge(model.eff_link(r, S))}")
                md.append(f"| {l['product']} | {l['bags']} | {price} | {'; '.join(refs) or 'no delivery yet'} | {l['rem'] or '—'} |")
            st.markdown("\n".join(md))
            if o["allocs"]:
                st.markdown("**PaymentAllocation**")
                for a in o["allocs"]:
                    st.markdown(f"`{a['txn']}` · {rwf(a['amount'])} · {link_badge(a)}  \n{a['basis']}")
            else:
                st.caption("No payment allocated.")


def action_panel(d: dict) -> None:
    did = d["id"]
    if d["hold"]:
        st.warning(f"On credit hold since {d['hold']['time']}: {rwf(d['hold']['amount'])}. "
                   "(Demo: nothing is sent to Operations or Sales.)")
        st.button("Lift hold", key=f"lift_{did}", on_click=lift_hold, args=(did,))
    elif d["can_hold"]:
        if ss.pending_hold == did:
            st.error(f"Confirm a credit hold on {d['name']} for {rwf(d['owed'])}? "
                     "Open orders would be flagged to Operations and Sales (demo: nothing is sent).")
            c1, c2, _ = st.columns([2, 1, 4])
            c1.button(f"Confirm hold: {rwf(d['owed'])}", key=f"confirm_{did}", type="primary",
                      on_click=place_hold, args=(did,))
            c2.button("Cancel", key=f"cancel_{did}", on_click=clear_pending)
        else:
            st.button("Place on credit hold", key=f"hold_{did}", type="primary", on_click=start_hold, args=(did,))
    else:
        st.button(d["why"], key=f"off_{did}", disabled=True)


# ---- review queue
def preview(dealer_id: str, mutate) -> str:
    d = model.what_if(S, mutate)["D"][dealer_id]
    return f"{model.dealers[dealer_id]['name'].split()[0]} {M.describe(d)}"


def item_resolved(item: str) -> bool:
    return next(i for i in M.ITEMS if i["id"] == item)["resolved"](S)


def item_wa073() -> None:
    with st.container(border=True):
        if item_resolved("wa073"):
            st.markdown(f"**WA-073: resolved.** Linked to **{S['wa073']['line'].split('|')[0]}**, confirmed by Finance.")
            return
        st.markdown("#### Q2 · WA-073 (60 bags Urea, 17 Mar): which order did it fulfil?  **▲ blocking Karongi**")
        evidence(['deliveries.csv WA-073 says "balance of previous", but the balance of ORD-1040 would be **80** bags '
                  '(200 − 120), not 60.',
                  "orders.csv ORD-1043 (customer C-041, 60 bags at 39,500) matches the quantity exactly.",
                  "payments.csv MM-5508 paid ORD-1043 in full on 20 Mar, three days after WA-073."])
        opts = ["ORD-1043|Urea", "ORD-1040|Urea"]
        names = {"ORD-1043|Urea": "It fulfils **ORD-1043** (the model's proposal)",
                 "ORD-1040|Urea": "It completes **ORD-1040** (as the driver's note says)"}
        st.radio("Which order did WA-073 fulfil?", opts, key="stage_wa073", format_func=names.get,
                 captions=[preview("D-02", lambda c, o=o: c["wa073"].update(line=o)) for o in opts])
        st.button("Confirm link", key="commit_wa073", type="primary", on_click=commit, args=("wa073",))


def item_nyam() -> None:
    with st.container(border=True):
        if item_resolved("nyam"):
            n = S["nyam"]
            st.markdown(f"**WA-077: resolved.** Order raised at {rwf(n['price'])} a bag"
                        + ("; MM-5503 allocated to it" if n["alloc"] else "; MM-5503 not attributed to it")
                        + ". Confirmed by Finance.")
            return
        st.markdown("#### Q1 · WA-077 (80 bags NPK, 14 Mar) has no order. Who agreed it, at what price?  "
                    "**▲ blocking Nyamasheke Coop**")
        evidence(['deliveries.csv WA-077: Nyamasheke Coop, 80 NPK, note "urgent, agreed by phone". Nyamasheke does not '
                  "appear in orders.csv, so there is no price.",
                  "payments.csv MM-5503: M. UWASE, RWF 2,400,000 on 13 Mar 16:40, no reference. That is 80 × 30,000, "
                  "and it arrived the day before the delivery.",
                  "The model does not link them on its own: M. UWASE matches no dealer."])
        st.number_input("Price per bag (RWF)", min_value=0, step=500, value=None, key="stage_nyam_price",
                        placeholder="e.g. 30000")
        c1, c2, _ = st.columns([2, 2, 3])
        c1.button("30,000 (implied by MM-5503)", key="preset_30", on_click=set_nyam_price, args=(30000,))
        c2.button("32,000 (NPK list price)", key="preset_32", on_click=set_nyam_price, args=(32000,))
        st.checkbox("MM-5503 (M. UWASE, RWF 2,400,000) pays for this delivery", key="stage_nyam_alloc")
        price = ss.get("stage_nyam_price")
        if price and price > 0:
            alloc = bool(ss.get("stage_nyam_alloc", False))
            st.caption(preview("D-06", lambda c: c.update(nyam=dict(price=int(price), alloc=alloc, committed=True))))
        else:
            st.caption("Enter a price to see the effect on Nyamasheke Coop's balance.")
        st.button("Raise order and confirm", key="commit_nyam", type="primary", on_click=commit, args=("nyam",),
                  disabled=not (price and price > 0))


def item_p1044() -> None:
    with st.container(border=True):
        if item_resolved("p1044"):
            st.markdown(f"**ORD-1044 price: resolved.** Price set to {rwf(S['p1044']['value'])} a bag, confirmed by Finance.")
            return
        st.markdown("#### Q3 · ORD-1044 is priced at RWF 28 a bag. What is the real price?  **▲ blocking Rubavu**")
        evidence(["orders.csv ORD-1044: 300 bags NPK at unit price **28**. Every other NPK order is 32,000.",
                  "payments.csv MM-5506 paid RWF 8,400,000 = 300 × 28,000."])
        opts = [28000, 32000, 28]
        names = {28000: "28,000 (implied by the payment; the model's proposal)",
                 32000: "32,000 (domestic NPK price)", 28: "28 (as recorded)"}
        st.radio("Price per bag (RWF)", opts, key="stage_p1044", format_func=names.get,
                 captions=[preview("D-04", lambda c, o=o: c["p1044"].update(value=o)) for o in opts])
        st.button("Confirm price", key="commit_p1044", type="primary", on_click=commit, args=("p1044",))


def item_huye() -> None:
    with st.container(border=True):
        if item_resolved("huye"):
            st.markdown("**Huye return: resolved.** " + ("The 15 torn bags were credited." if S["huye"]["credited"]
                                                           else "The 15 torn bags were not credited.")
                        + " Confirmed by Finance.")
            return
        st.markdown("#### Q4 · WA-074: Huye returned 15 torn bags. Was it credited?  "
                    "**○ informational: Huye owes at least RWF 740,000 either way**")
        evidence(['deliveries.csv WA-074: 80 bags, note "returned 15 bags torn" (free text only).',
                  "payments.csv MM-5505 paid RWF 1,202,500 = 65 × 18,500, not 80 × 18,500."])
        opts = [True, False]
        names = {True: "Credited: the payment is net of the returned bags (the model's proposal)",
                 False: "Not credited: the returned bags are still owed"}
        st.radio("Was the return credited?", opts, key="stage_huye", format_func=names.get,
                 captions=[preview("D-03", lambda c, o=o: c["huye"].update(credited=o)) for o in opts])
        st.button("Confirm", key="commit_huye", type="primary", on_click=commit, args=("huye",))


def view_review() -> None:
    open_n = sum(1 for i in M.ITEMS if not i["resolved"](S))
    if open_n:
        st.info(f"{open_n} open item{'s' if open_n > 1 else ''}. Each shows what the data says, the options, and what "
                "changes in the balances. Nothing is decided until Finance confirms it.")
    else:
        st.success("All items resolved. The Monday view now contains only confirmed links.")
    item_nyam()
    item_wa073()
    item_p1044()
    item_huye()


# ---- model tab and log
def view_model() -> None:
    st.info("The six object types of the proposed model, populated from the three files. Dealer and PaymentAllocation "
            "exist in no file; the rest map to one file each. Link status updates as you confirm items.")
    data = model.data

    def section(title: str, source: str, rows: list[dict], wide: tuple = ()) -> None:
        st.markdown(f"#### {title}")
        st.caption(source)
        df(rows, column_config={c: st.column_config.Column(c, width="large") for c in wide})

    section("Dealer", "created by the model (alias table resolves names and customer IDs)",
            [{"dealer_id": d["id"], "name": d["name"], "accounting IDs": ", ".join(d["cust"]) or "—",
              "name variants": "; ".join(d["aliases"]), "credit hold": "yes" if S["holds"].get(d["id"]) else "no"}
             for d in data["dealers"]])
    orders = [{"order_id": o["id"], "dealer": o["dealerId"], "order date": o["orderDate"], "promised": o["promised"]}
              for o in data["orders"]]
    if S["nyam"]["committed"]:
        orders.append({"order_id": M.RETRO_ORDER, "dealer": "D-06", "order date": "raised by Finance", "promised": ""})
    section("Order", "from orders.csv", orders)
    lines = []
    for o in data["orders"]:
        for l in o["lines"]:
            price = fmt(model.line_price(l["key"], S))
            if l["key"] == M.P1044_LINE:
                price += " (confirmed)" if S["p1044"]["committed"] else " (recorded 28, proposed fix)"
            lines.append({"order_id + product": l["key"].replace("|", " + "), "bags ordered": l["bags"],
                          "unit price (RWF)": price})
    if S["nyam"]["committed"]:
        lines.append({"order_id + product": "ORD-RETRO-1 + NPK", "bags ordered": 80, "unit price (RWF)": fmt(S["nyam"]["price"])})
    section("OrderLine", "from orders.csv (price is per line, not per product)", lines)
    dels = []
    for d in data["deliveries"]:
        key, lk = model.eff_line(d, S), model.eff_link(d, S)
        dels.append({"WA ref": d["ref"], "dealer": f"{d['dealerId']} ({d['dealerText']})", "product": d["short"],
                     "bags": d["bags"], "returned": d["returned"] or None, "date": d["date"], "driver": d["driver"],
                     "note": d["notes"], "order line": key.replace("|", " + ") if key else "none",
                     "link": lk["status"] + (f" · {lk['by']}" if lk["status"] == "confirmed" else "")})
    section("Delivery", "from deliveries.csv (the order-line link is inferred)", dels, wide=("note", "dealer"))
    section("Payment", "from payments.csv (payer and reference as typed)",
            [{"txn_id": p["txn"], "payer": p["payer"], "amount (RWF)": p["amount"], "timestamp": p["ts"],
              "reference": p["ref"] or "—", "dealer": p["dealerId"] or "unmatched"} for p in data["payments"]])
    allocs = [{"txn_id + order_id": f"{p['txn']} + {a['order']}", "amount (RWF)": a["amount"], "status": a["status"],
               "basis": a["basis"]} for p in data["payments"] for a in p["allocs"]]
    if S["nyam"]["committed"] and S["nyam"]["alloc"]:
        allocs.append({"txn_id + order_id": f"{M.UNREFERENCED_TXN} + {M.RETRO_ORDER}", "amount (RWF)": 2400000,
                       "status": "confirmed · Finance", "basis": "Confirmed by Finance."})
    section("PaymentAllocation", "created by the model (many-to-many between Payment and Order)", allocs, wide=("basis",))


def view_log() -> None:
    with st.container(border=True):
        st.subheader("Activity")
        if not S["log"]:
            st.caption("No actions yet. Confirm a review item or place a credit hold and it is recorded here.")
        for entry in S["log"]:
            st.markdown(f"`{entry['t']}` {entry['m']}")


# ----------------------------------------------------------------------------- page
head, btn = st.columns([6, 1], vertical_alignment="top")
with head:
    st.title("Rwiza Agri Distributors: receivables view")
    st.caption("Prototype of the proposed model on the March extracts (orders.csv, deliveries.csv, payments.csv). "
               "Every balance is derived from the objects and the links; nothing is stored. Links the data cannot "
               "settle stay *proposed* until a person confirms them.")
with btn:
    st.button("Reset demo", on_click=reset_demo, width="stretch")

C = model.compute(S)
open_n = C["kpi"]["review"] + C["kpi"]["info"]
st.segmented_control("View", list(TABS), key="tab", format_func=TABS.get, label_visibility="collapsed",
                     on_change=clear_pending)
if open_n:
    st.markdown(f"**▲ {open_n} open review item{'s' if open_n > 1 else ''}** (see Review queue)")
tab = ss.tab or "monday"   # segmented_control returns None if the active segment is clicked again

if tab == "monday":
    view_monday(C)
elif tab == "review":
    view_review()
elif tab == "model":
    view_model()
else:
    view_log()

st.caption("Built from the files provided. “Received” here means “left the warehouse” (deliveries.csv comes from "
           "drivers' WhatsApp messages). No file has an extract date or payment terms, so nothing is marked overdue. "
           "Decisions live in this browser session only and reset when you reload.")
