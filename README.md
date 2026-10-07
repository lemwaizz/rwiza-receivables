# Rwiza receivables prototype (Streamlit)

A working sketch of the proposed data model for Rwiza Agri Distributors. It answers the managing
director's question, "which dealers owe us money for goods they have actually received?", from the three
March extracts (`data/orders.csv`, `data/deliveries.csv`, `data/payments.csv`).

Every balance is derived, never stored: `owed = delivered (net of returns) x order-line price - payments
allocated`. Links the data cannot settle stay **proposed** until a person confirms them in the Review queue.

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

It opens at http://localhost:8501.

## Deploy to Streamlit Community Cloud

1. Put this folder's contents at the root of a GitHub repository (`app.py`, `model.py`, `requirements.txt`,
   `data/`, `.streamlit/`):

   ```bash
   git init
   git add .
   git commit -m "Rwiza receivables prototype"
   git branch -M main
   git remote add origin https://github.com/<your-username>/rwiza-receivables.git
   git push -u origin main
   ```

2. Go to https://share.streamlit.io and sign in with GitHub.
3. Choose **Create app**, pick the repository, branch `main`, and main file path `app.py`. Optionally set a
   custom subdomain such as `rwiza-receivables`.
4. Choose **Deploy**. The first build takes a few minutes and gives you a URL of the form
   `https://<subdomain>.streamlit.app`. Put that URL on your slide.

Apps that get no visitors for a while go to sleep and show a "wake up" button, so open the link a few
minutes before anyone needs it.

## What is in the app

| Tab | What it shows |
|---|---|
| Monday view | Confirmed owed, review items blocking a call, ordered-not-shipped, unmatched payments; a balance table per dealer; drill-down to each dealer's orders, deliveries and payments; two-step credit hold with guard rails |
| Review queue | The four items the files cannot settle (WA-073, WA-077 + MM-5503, ORD-1044 price, Huye returns). Each option shows what it does to the balances before you confirm |
| Model objects | The six object types (Dealer, Order, OrderLine, Delivery, Payment, PaymentAllocation) populated from the files, with link status |
| Activity | Every confirmation and credit hold, with a time |

Guard rails worth pointing out: a credit hold is not offered while a blocking review item is open on that
dealer, and it is also blocked when the dealer has money paid ahead of delivery on another order. Under the
second reading of WA-073, Karongi shows RWF 90,000 owed but RWF 2,370,000 paid ahead on ORD-1043, so the hold
stays blocked until that payment is re-allocated.

## Files

```
app.py              the interface (Streamlit)
model.py            matching layer + derived balances (pure Python, no Streamlit)
data/               the three CSV extracts
tests/              model figures checked against the raw CSVs; headless walk of the app
requirements.txt
.streamlit/config.toml
```

Run the checks (they need `pytest`, which is not needed for deployment):

```bash
pip install pytest
pytest -q
```

## Limits to know about

- Decisions live in the visitor's browser session only. Reloading the page resets them, and each visitor has
  their own copy. To keep them, write the `S` state in `app.py` to a database (for example with
  `st.connection`) and read it back on load.
- The four review items and the dealer alias table in `model.py` are specific to this extract. A new extract
  needs its own alias table and its own list of ambiguous links.
- "Received" means "left the warehouse": `deliveries.csv` comes from drivers' WhatsApp messages. No file has
  an extract date or payment terms, so nothing is marked overdue.
- Credit holds are simulated. Nothing is sent to Operations or Sales.
