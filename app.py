import streamlit as st
import pandas as pd
from datetime import datetime

import psycopg2
import os

# ---------------- CONFIG ----------------
# Annual interest rates, effective from the listed dates (inclusive).
# Add future rate revisions here without changing the calculation functions.
INTEREST_RATE_SCHEDULE = [
    (pd.Timestamp("1900-01-01"), 0.0725),
    (pd.Timestamp("2026-10-15"), 0.0750),
]


def get_annual_rate(on_date):
    """Return the annual loan interest rate applicable on a particular date."""
    on_date = pd.Timestamp(on_date).normalize()
    for effective_date, annual_rate in reversed(INTEREST_RATE_SCHEDULE):
        if on_date >= effective_date:
            return annual_rate
    raise ValueError(f"No interest rate configured for {on_date.date()}")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = st.secrets["DATABASE_URL"]


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
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            date DATE,
            type TEXT,
            amount NUMERIC,
            note TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def add_transaction(date, ttype, amount, note):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions(date,type,amount,note) VALUES (%s,%s,%s,%s)",
        (date, ttype, amount, note),
    )
    conn.commit()
    cur.close()
    conn.close()

def delete_transaction(tx_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE id=%s", (tx_id,))
    conn.commit()
    cur.close()
    conn.close()

def load_transactions():
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM transactions ORDER BY date, id", conn)
    conn.close()
    return df


# ---------------- SIGN HANDLING ----------------
def signed_amount(row):
    t = row["type"]
    amt = row["amount"]

    if t in ["Disbursement", "Interest", "Charge (Debit)"]:
        return amt
    elif t in ["EMI", "Prepayment", "Charge (Credit)"]:
        return -amt
    return amt

# ---------------- LIFETIME SIMULATION ----------------
def simulate_until_closure(df, emi_amount=90352, emi_only=False):
    if df.empty:
        raise ValueError("Simulation requires at least one disbursement transaction.")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["signed_amount"] = df.apply(signed_amount, axis=1)

    start = pd.to_datetime(df["date"].min())
    last_tx_date = pd.to_datetime(df["date"].max())

    tx_map = (
        df.groupby(df["date"].dt.strftime("%Y-%m-%d"))["signed_amount"]
        .sum()
        .to_dict()
    )

    current_date = start
    balance = 0
    monthly_interest = 0
    current_month = None

    rows = []

    while balance > 0 or current_date == start:

        ds = current_date.strftime("%Y-%m-%d")
        month_key = current_date.strftime("%Y-%m")

        monthly_capitalized = 0
        emi_applied = 0

        # Capitalize interest at month change
        if current_month is not None and month_key != current_month:
            balance += monthly_interest
            monthly_capitalized = monthly_interest
            monthly_interest = 0

        current_month = month_key

        # ---------------- HISTORICAL PHASE ----------------
        if current_date <= last_tx_date:
            if ds in tx_map:
                balance += tx_map[ds]

        # ---------------- EMI APPLICATION ----------------
        # EMI-only simulation → apply every 20th from start
        if emi_only:
            if current_date.day == 20:
                emi_applied = min(balance, emi_amount)
                balance -= emi_applied

        # Actual simulation → apply EMI only AFTER last tx date
        else:
            if current_date > last_tx_date and current_date.day == 20:
                emi_applied = min(balance, emi_amount)
                balance -= emi_applied

        if balance <= 0:
            break

        # Daily interest at the rate in force on this date
        annual_rate = get_annual_rate(current_date)
        interest = balance * annual_rate / 365
        monthly_interest += interest

        rows.append({
            "Date": current_date,
            "Outstanding": balance,
            "Interest Today": interest,
            "Interest Rate (%)": round(annual_rate * 100, 2),
            "EMI Applied": emi_applied,
            "Monthly Interest Capitalized": monthly_capitalized
        })

        current_date += pd.Timedelta(days=1)

    return pd.DataFrame(rows)

# ---------------- LOAN ENGINE ----------------
def build_daily_table(df):
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    df = df.copy()
    df["signed_amount"] = df.apply(signed_amount, axis=1)

    start = pd.to_datetime(df["date"].min())
    end = pd.to_datetime(datetime.today())

    dates = pd.date_range(start, end)

    tx_map = (
        df.groupby(df["date"].dt.strftime("%Y-%m-%d"))["signed_amount"]
        .sum()
        .to_dict()
    )


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

        annual_rate = get_annual_rate(d)
        interest = balance * annual_rate / 365
        monthly_interest += interest

        rows.append({
            "Date": d,
            "Outstanding": round(balance, 2),
            "Interest": round(interest, 2),
            "Interest Rate (%)": round(annual_rate * 100, 2),
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
    ["Dashboard", "Simulation", "Transactions", "Add Transaction"]
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

        # ---- Extrapolated lifetime comparison ----
        emi_amount = 90352

        emi_only_schedule = simulate_until_closure(
            emi_only_tx, emi_amount
        )

        actual_schedule = simulate_until_closure(
            tx, emi_amount
        )

        emi_only_lifetime_interest = (
            emi_only_schedule["Interest Today"].sum()
            if not emi_only_schedule.empty else 0
        )

        actual_lifetime_interest = (
            actual_schedule["Interest Today"].sum()
            if not actual_schedule.empty else 0
        )

        interest_saved_extrapolated = (
            emi_only_lifetime_interest - actual_lifetime_interest
        )


        col1, col2, col3, col4, col5 = st.columns(5)

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

        col5.metric(
            "Interest Saved (Extrapolated)",
            format_inr(max(interest_saved_extrapolated, 0)),
            help="Total lifetime interest saved assuming EMI of ₹90,352 continues and no future prepayments are made."
        )


        st.subheader("Daily Loan Table")

        for month_key, group in daily.groupby("MonthKey"):
            month_label = group.iloc[0]["MonthLabel"]

            with st.expander(month_label, expanded=False):

                display = group[
                    ["SNo", "Date", "Outstanding", "Interest Rate (%)", "Interest"]
                ].copy()

                display["Date"] = display["Date"].dt.strftime("%d-%m-%Y")

                display.columns = [
                    "S.No",
                    "Date",
                    "Outstanding Balance",
                    "Interest Rate (%)",
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

elif menu == "Simulation":
    st.title("📊 Loan Simulation Dashboard")

    tx = load_transactions()

    if tx.empty:
        st.info("No transactions available.")
    else:
        emi_amount = 90352

        # EMI Only
        emi_only_tx = tx[tx["type"].isin(["Disbursement"])]
        emi_only_tx = tx[tx["type"] == "Disbursement"]
        emi_schedule = simulate_until_closure(emi_only_tx, emi_amount=90352, emi_only=True)

        # Actual Scenario
        actual_schedule = simulate_until_closure(tx, emi_amount=90352, emi_only=False)

        emi_total_interest = emi_schedule["Interest Today"].sum()
        actual_total_interest = actual_schedule["Interest Today"].sum()

        interest_saved_extrapolated = emi_total_interest - actual_total_interest

        emi_closure = emi_schedule.iloc[-1]["Date"]
        actual_closure = actual_schedule.iloc[-1]["Date"]

        col1, col2, col3 = st.columns(3)

        col1.metric("EMI-Only Total Interest",
                    format_inr(emi_total_interest))

        col2.metric("Actual Total Interest",
                    format_inr(actual_total_interest))

        col3.metric("Interest Saved (Extrapolated)",
                    format_inr(max(interest_saved_extrapolated, 0)))

        st.write("EMI-Only Closure Date:", emi_closure.date())
        st.write("Actual Closure Date:", actual_closure.date())

        st.divider()

        with st.expander("📘 EMI-Only Full Simulation", expanded=False):
            display_emi = emi_schedule.copy()
            display_emi["Outstanding"] = display_emi["Outstanding"].apply(format_inr)
            display_emi["Interest Today"] = display_emi["Interest Today"].apply(format_inr)
            display_emi["EMI Applied"] = display_emi["EMI Applied"].apply(format_inr)
            display_emi["Monthly Interest Capitalized"] = \
                display_emi["Monthly Interest Capitalized"].apply(format_inr)

            st.dataframe(display_emi, use_container_width=True, hide_index=True)

        with st.expander("📗 Actual Full Simulation", expanded=False):
            display_actual = actual_schedule.copy()
            display_actual["Outstanding"] = display_actual["Outstanding"].apply(format_inr)
            display_actual["Interest Today"] = display_actual["Interest Today"].apply(format_inr)
            display_actual["EMI Applied"] = display_actual["EMI Applied"].apply(format_inr)
            display_actual["Monthly Interest Capitalized"] = \
                display_actual["Monthly Interest Capitalized"].apply(format_inr)

            st.dataframe(display_actual, use_container_width=True, hide_index=True)
