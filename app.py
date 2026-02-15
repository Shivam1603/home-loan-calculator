import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

# ---------------- CONFIG ----------------
DB = "loan.db"
ANNUAL_RATE = 0.0725


# ---------------- INDIAN NUMBER FORMAT ----------------
def format_inr(x):
    x = round(float(x), 2)
    sign = "-" if x < 0 else ""
    x = abs(x)

    integer, dot, fraction = f"{x:.2f}".partition(".")

    if len(integer) > 3:
        last3 = integer[-3:]
        rest = integer[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        integer = ",".join(parts + [last3])

    return f"{sign}₹ {integer}.{fraction}"


# ---------------- DATABASE ----------------
def get_conn():
    return sqlite3.connect(DB, check_same_thread=False)


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            type TEXT,
            amount REAL,
            note TEXT
        )
    """)
    conn.commit()


def add_transaction(date, ttype, amount, note):
    conn = get_conn()
    conn.execute(
        "INSERT INTO transactions(date,type,amount,note) VALUES (?,?,?,?)",
        (date, ttype, amount, note),
    )
    conn.commit()


def delete_transaction(tx_id):
    conn = get_conn()
    conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    conn.commit()


def load_transactions():
    conn = get_conn()
    return pd.read_sql(
        "SELECT * FROM transactions ORDER BY date, id",
        conn
    )


# ---------------- SIGN HANDLING ----------------
def signed_amount(row):
    t = row["type"]
    amt = row["amount"]

    if t in ["Disbursement", "Interest", "Charge (Debit)"]:
        return amt
    elif t in ["EMI", "Prepayment", "Charge (Credit)"]:
        return -amt
    return amt


# ---------------- LOAN ENGINE ----------------
def build_daily_table(df):
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["signed_amount"] = df.apply(signed_amount, axis=1)

    start = pd.to_datetime(df["date"].min())
    end = pd.to_datetime(datetime.today())

    dates = pd.date_range(start, end)

    tx_map = df.groupby("date")["signed_amount"].sum().to_dict()

    balance = 0
    monthly_interest = 0
    current_month = None

    rows = []

    for d in dates:
        ds = d.strftime("%Y-%m-%d")
        month_key = d.strftime("%Y-%m")

        if current_month is not None and month_key != current_month:
            balance += monthly_interest
            monthly_interest = 0

        current_month = month_key

        if ds in tx_map:
            balance += tx_map[ds]

        interest = balance * ANNUAL_RATE / 365
        monthly_interest += interest

        rows.append({
            "Date": d,
            "Outstanding": round(balance, 2),
            "Interest": round(interest, 2),
            "MonthLabel": d.strftime("%B %Y"),
            "MonthKey": month_key,
        })

    daily = pd.DataFrame(rows)
    daily["SNo"] = daily.groupby("MonthKey").cumcount() + 1
    return daily


# ---------------- STREAMLIT UI ----------------
init_db()

st.set_page_config(page_title="Loan Tracker", layout="wide")

menu = st.sidebar.radio(
    "Menu",
    ["Dashboard", "Transactions", "Add Transaction"]
)

# ---------------- DASHBOARD ----------------
if menu == "Dashboard":
    st.title("🏠 Home Loan Dashboard")

    tx = load_transactions()
    daily = build_daily_table(tx)

    if daily.empty:
        st.info("No transactions yet.")
    else:
        latest_balance = daily.iloc[-1]["Outstanding"]

        # ---- Interest metrics ----
        total_interest_paid = daily["Interest"].sum()

        emi_only_tx = tx[tx["type"].isin(["Disbursement", "EMI"])]
        emi_daily = build_daily_table(emi_only_tx)

        interest_without_prepay = (
            emi_daily["Interest"].sum()
            if not emi_daily.empty else 0
        )

        interest_saved = (
            interest_without_prepay - total_interest_paid
        )

        col1, col2, col3, col4 = st.columns(4)

        col1.metric("Outstanding Balance",
                    format_inr(latest_balance))

        col2.metric("Today's Interest",
                    format_inr(daily.iloc[-1]["Interest"]))

        col3.metric("Interest Paid So Far",
                    format_inr(total_interest_paid))

        col4.metric(
            "Interest Saved",
            format_inr(max(interest_saved, 0)),
            help="Interest saved compared to a scenario where only EMIs were paid and no prepayments were made."
        )

        st.subheader("Daily Loan Table")

        for month_key, group in daily.groupby("MonthKey"):
            month_label = group.iloc[0]["MonthLabel"]

            with st.expander(month_label, expanded=False):

                display = group[
                    ["SNo", "Date", "Outstanding", "Interest"]
                ].copy()

                display["Date"] = display["Date"].dt.strftime("%d-%m-%Y")

                display.columns = [
                    "S.No",
                    "Date",
                    "Outstanding Balance",
                    "Interest Accrued (Day)"
                ]

                display["Outstanding Balance"] = \
                    display["Outstanding Balance"].apply(format_inr)

                display["Interest Accrued (Day)"] = \
                    display["Interest Accrued (Day)"].apply(format_inr)

                st.dataframe(
                    display,
                    use_container_width=True,
                    hide_index=True
                )

                total_interest = group["Interest"].sum()

                st.markdown(
                    f"**Total interest accrued in {month_label}: "
                    f"{format_inr(total_interest)}**"
                )

# ---------------- TRANSACTIONS ----------------
elif menu == "Transactions":
    st.title("📜 Transactions")

    tx = load_transactions()

    if tx.empty:
        st.write("No transactions recorded.")
    else:
        for _, row in tx.iterrows():
            cols = st.columns([2, 2, 2, 3, 1])

            cols[0].write(row["date"])
            cols[1].write(row["type"])
            cols[2].write(format_inr(row["amount"]))
            cols[3].write(row["note"])

            if cols[4].button("❌", key=row["id"]):
                delete_transaction(row["id"])
                st.rerun()

# ---------------- ADD TRANSACTION ----------------
elif menu == "Add Transaction":
    st.title("➕ Add Transaction")

    date = st.date_input("Transaction Date")

    ttype = st.selectbox(
        "Transaction Type",
        [
            "Disbursement",
            "EMI",
            "Prepayment",
            "Interest",
            "Charge (Debit)",
            "Charge (Credit)",
        ]
    )

    amount = st.number_input(
        "Amount",
        min_value=0.0,
        step=1000.0
    )

    note = st.text_input("Note")

    if st.button("Save Transaction"):
        add_transaction(str(date), ttype, amount, note)
        st.success("Transaction added!")
        st.rerun()
