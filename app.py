import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(
    page_title="Monitor Kredytu",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── helpers ───────────────────────────────────────────────────────────────────

def calc_payment(balance: float, mr: float, n: int) -> float:
    """Stała rata annuitetowa."""
    if mr == 0:
        return balance / n
    return balance * mr * (1 + mr) ** n / ((1 + mr) ** n - 1)


def next_25th() -> date:
    today = date.today()
    if today.day <= 25:
        return date(today.year, today.month, 25)
    y, m = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
    return date(y, m, 25)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, d.day)


def build_schedule(
    balance: float,
    annual_rate_pct: float,
    n_inst: int,
    start: date,
    overpayments: dict,
    mode: str = "reduce_term",
) -> pd.DataFrame:
    """
    Generuje harmonogram spłat.
    mode: 'reduce_term'  – skrócenie okresu (rata bez zmian)
          'reduce_installment' – zmniejszenie raty (okres bez zmian)
    """
    mr = annual_rate_pct / 100 / 12
    bal = balance
    payment = calc_payment(bal, mr, n_inst)
    rows = []
    total_interest = 0.0

    for nr in range(1, n_inst + 500):
        if bal < 0.01:
            break
        interest = bal * mr
        principal = min(payment - interest, bal)
        if principal < 0:
            break
        op = float(overpayments.get(nr, 0))
        op = max(0.0, min(op, bal - principal))
        new_bal = max(0.0, bal - principal - op)
        total_interest += interest
        rows.append(
            {
                "Rata nr": nr,
                "Data": add_months(start, nr - 1),
                "Saldo przed": round(bal, 2),
                "Odsetki": round(interest, 2),
                "Kapitał": round(principal, 2),
                "Nadpłata": round(op, 2),
                "Rata": round(interest + principal, 2),
                "Łączna wpłata": round(interest + principal + op, 2),
                "Saldo po": round(new_bal, 2),
                "Suma odsetek": round(total_interest, 2),
            }
        )
        bal = new_bal
        remaining = n_inst - nr
        if op > 0 and bal > 0 and remaining > 0 and mode == "reduce_installment":
            payment = calc_payment(bal, mr, remaining)

    return pd.DataFrame(rows)


def fmt_pln(x: float) -> str:
    return f"{x:,.2f} PLN".replace(",", " ")


# ── session state ─────────────────────────────────────────────────────────────

if "overpayments" not in st.session_state:
    st.session_state.overpayments: dict[int, float] = {}

# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Parametry kredytu")
    balance = st.number_input(
        "Saldo do spłaty (PLN)", value=236_600.0, step=100.0, format="%.2f", min_value=1.0
    )
    n_inst = st.number_input(
        "Pozostałe raty", value=72, min_value=1, max_value=480, step=1
    )
    rate_pct = st.number_input(
        "Oprocentowanie roczne (%)", value=5.74, step=0.01, format="%.4f", min_value=0.0
    )
    start_date = st.date_input("Data pierwszej raty w harmonogramie", value=next_25th())
    mode = st.radio(
        "Efekt nadpłat",
        ["reduce_term", "reduce_installment"],
        format_func=lambda x: (
            "Skrócenie okresu (rata stała)" if x == "reduce_term" else "Zmniejszenie raty (okres stały)"
        ),
    )
    st.divider()
    st.caption("Zmiany parametrów aktualizują harmonogram na bieżąco.")

# ── build schedules ───────────────────────────────────────────────────────────

mr = rate_pct / 100 / 12
base_payment = calc_payment(balance, mr, n_inst)

sched_base = build_schedule(balance, rate_pct, n_inst, start_date, {})
sched_op = build_schedule(balance, rate_pct, n_inst, start_date, st.session_state.overpayments, mode)

interest_base = sched_base["Odsetki"].sum()
interest_op = sched_op["Odsetki"].sum()
saved_interest = interest_base - interest_op
saved_installments = len(sched_base) - len(sched_op)
total_overpayments = sum(st.session_state.overpayments.values())

end_base = sched_base["Data"].iloc[-1]
end_op = sched_op["Data"].iloc[-1]

# ── header ────────────────────────────────────────────────────────────────────

st.title("🏠 Monitor Kredytu Hipotecznego")
st.caption(
    f"Pierwsza rata: **{start_date.strftime('%d.%m.%Y')}** · "
    f"Oprocentowanie: **{rate_pct}%** · "
    f"Saldo: **{fmt_pln(balance)}**"
)

# ── tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(
    ["📊 Podsumowanie", "📅 Harmonogram", "💰 Nadpłaty", "📂 Historia (CSV)"]
)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 – Podsumowanie
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo do spłaty", fmt_pln(balance))
    c2.metric("Miesięczna rata", fmt_pln(base_payment))
    c3.metric(
        "Pozostałe raty",
        str(len(sched_op)),
        delta=f"-{saved_installments} rat" if saved_installments > 0 else None,
        delta_color="inverse",
    )
    c4.metric(
        "Łączne odsetki",
        fmt_pln(interest_op),
        delta=f"-{fmt_pln(saved_interest)}" if saved_interest > 0 else None,
        delta_color="inverse",
    )

    st.divider()

    # Saldo w czasie
    fig_bal = go.Figure()
    fig_bal.add_trace(
        go.Scatter(
            x=sched_base["Data"],
            y=sched_base["Saldo po"],
            name="Bez nadpłat",
            line=dict(color="#94a3b8", dash="dash"),
            fill="tozeroy",
            fillcolor="rgba(148,163,184,0.08)",
        )
    )
    fig_bal.add_trace(
        go.Scatter(
            x=sched_op["Data"],
            y=sched_op["Saldo po"],
            name="Z nadpłatami",
            line=dict(color="#6366f1", width=2),
            fill="tozeroy",
            fillcolor="rgba(99,102,241,0.12)",
        )
    )
    fig_bal.update_layout(
        title="Saldo kredytu w czasie",
        xaxis_title="Data",
        yaxis_title="PLN",
        hovermode="x unified",
        height=380,
        legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99),
    )
    st.plotly_chart(fig_bal, use_container_width=True)

    col_l, col_r = st.columns(2)

    with col_l:
        fig_bar = go.Figure()
        fig_bar.add_trace(
            go.Bar(
                x=sched_op["Data"],
                y=sched_op["Odsetki"],
                name="Odsetki",
                marker_color="#f43f5e",
            )
        )
        fig_bar.add_trace(
            go.Bar(
                x=sched_op["Data"],
                y=sched_op["Kapitał"],
                name="Kapitał",
                marker_color="#6366f1",
            )
        )
        if total_overpayments > 0:
            fig_bar.add_trace(
                go.Bar(
                    x=sched_op["Data"],
                    y=sched_op["Nadpłata"],
                    name="Nadpłata",
                    marker_color="#10b981",
                )
            )
        fig_bar.update_layout(
            barmode="stack",
            title="Struktura miesięcznych wpłat",
            xaxis_title="Data",
            yaxis_title="PLN",
            height=340,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_r:
        labels = ["Kapitał", "Odsetki"]
        values = [balance, interest_op]
        colors = ["#6366f1", "#f43f5e"]
        if total_overpayments > 0:
            labels.append("Nadpłaty")
            values.append(total_overpayments)
            colors.append("#10b981")
        fig_pie = go.Figure(
            go.Pie(
                labels=labels,
                values=values,
                hole=0.45,
                marker_colors=colors,
            )
        )
        fig_pie.update_layout(title="Łączny koszt kredytu", height=340)
        st.plotly_chart(fig_pie, use_container_width=True)

    # Info box
    if saved_installments > 0 or saved_interest > 0:
        st.success(
            f"Dzięki nadpłatom oszczędzasz **{fmt_pln(saved_interest)}** na odsetkach "
            f"i kończysz kredyt **{saved_installments} rat wcześniej** "
            f"({end_op.strftime('%m.%Y')} zamiast {end_base.strftime('%m.%Y')})."
        )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 – Harmonogram
# ═══════════════════════════════════════════════════════════════════════════════

with tab2:
    st.subheader("Harmonogram spłat")

    display = sched_op.copy()
    display["Data"] = display["Data"].apply(lambda d: d.strftime("%d.%m.%Y"))
    currency_cols = [
        "Saldo przed", "Odsetki", "Kapitał", "Nadpłata",
        "Rata", "Łączna wpłata", "Saldo po", "Suma odsetek",
    ]
    for col in currency_cols:
        display[col] = display[col].apply(lambda x: f"{x:,.2f}")

    st.dataframe(display, use_container_width=True, hide_index=True, height=520)

    csv_bytes = sched_op.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Pobierz harmonogram CSV",
        csv_bytes,
        "harmonogram_kredytu.csv",
        "text/csv",
    )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 – Nadpłaty
# ═══════════════════════════════════════════════════════════════════════════════

with tab3:
    st.subheader("Planuj nadpłaty")

    col_form, col_list = st.columns([1, 2])

    with col_form:
        with st.form("add_overpayment", clear_on_submit=True):
            st.markdown("**Dodaj nadpłatę**")
            inst_nr = st.number_input(
                "Numer raty",
                min_value=1,
                max_value=n_inst + 100,
                value=1,
                step=1,
            )
            op_amount = st.number_input(
                "Kwota nadpłaty (PLN)",
                min_value=0.0,
                step=100.0,
                format="%.2f",
            )
            hint_date = add_months(start_date, int(inst_nr) - 1)
            st.caption(f"Data raty nr {inst_nr}: {hint_date.strftime('%d.%m.%Y')}")
            submitted = st.form_submit_button("➕ Dodaj")
            if submitted:
                if op_amount > 0:
                    st.session_state.overpayments[int(inst_nr)] = float(op_amount)
                    st.success(f"Dodano: rata {inst_nr} → {fmt_pln(op_amount)}")
                    st.rerun()
                else:
                    st.warning("Kwota musi być większa od 0.")

        # Remove specific
        if st.session_state.overpayments:
            with st.form("remove_overpayment"):
                st.markdown("**Usuń nadpłatę**")
                keys = sorted(st.session_state.overpayments.keys())
                del_nr = st.selectbox("Rata nr", options=keys)
                if st.form_submit_button("🗑️ Usuń"):
                    del st.session_state.overpayments[del_nr]
                    st.rerun()

    with col_list:
        if st.session_state.overpayments:
            op_rows = [
                {
                    "Rata nr": k,
                    "Data": add_months(start_date, k - 1).strftime("%d.%m.%Y"),
                    "Nadpłata (PLN)": f"{v:,.2f}",
                }
                for k, v in sorted(st.session_state.overpayments.items())
            ]
            st.dataframe(pd.DataFrame(op_rows), use_container_width=True, hide_index=True)
            st.metric("Suma zaplanowanych nadpłat", fmt_pln(total_overpayments))

            if st.button("🗑️ Wyczyść wszystkie nadpłaty"):
                st.session_state.overpayments = {}
                st.rerun()
        else:
            st.info("Brak zaplanowanych nadpłat. Dodaj pierwszą po lewej stronie.")

    # Comparison
    if st.session_state.overpayments:
        st.divider()
        st.subheader("Efekt nadpłat")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Oszczędność – odsetki",
            fmt_pln(saved_interest),
            delta=f"-{fmt_pln(saved_interest)}",
            delta_color="inverse",
        )
        c2.metric(
            "Skrócenie okresu",
            f"{saved_installments} rat",
            delta=f"-{saved_installments}",
            delta_color="inverse",
        )
        c3.metric("Koniec kredytu", end_op.strftime("%m.%Y"))
        c4.metric("Bez nadpłat", end_base.strftime("%m.%Y"))

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 – Historia CSV
# ═══════════════════════════════════════════════════════════════════════════════

with tab4:
    st.subheader("Historia wpłat")

    st.markdown(
        "Wgraj CSV z historią spłat. Wymagane kolumny: `data`, `kwota_raty`. "
        "Opcjonalne: `nadplata`, `uwagi`."
    )

    # Template
    template_df = pd.DataFrame(
        {
            "data": [
                add_months(start_date, i).strftime("%Y-%m-%d") for i in range(3)
            ],
            "kwota_raty": [round(base_payment, 2)] * 3,
            "nadplata": [500.0, 0.0, 1000.0],
            "uwagi": ["", "", "premioxa roczna"],
        }
    )
    st.download_button(
        "⬇️ Pobierz szablon CSV",
        template_df.to_csv(index=False).encode("utf-8"),
        "szablon_historia.csv",
        "text/csv",
    )

    uploaded = st.file_uploader("Wgraj plik CSV", type=["csv"])

    if uploaded:
        try:
            hist = pd.read_csv(uploaded)
            required = {"data", "kwota_raty"}
            missing = required - set(hist.columns)
            if missing:
                st.error(f"Brak wymaganych kolumn: {missing}")
            else:
                hist["data"] = pd.to_datetime(hist["data"]).dt.date
                hist = hist.sort_values("data").reset_index(drop=True)

                # Metrics
                total_paid = hist["kwota_raty"].sum()
                n_paid = len(hist)
                total_op_hist = hist["nadplata"].sum() if "nadplata" in hist.columns else 0.0

                m1, m2, m3 = st.columns(3)
                m1.metric("Opłacone raty", n_paid)
                m2.metric("Suma rat", fmt_pln(total_paid))
                m3.metric("Suma nadpłat", fmt_pln(total_op_hist))

                # Display history vs schedule
                sched_dict = {
                    row["Rata nr"]: row
                    for _, row in sched_base.iterrows()
                }
                hist_display = []
                for idx, row in hist.iterrows():
                    inst_row = sched_dict.get(idx + 1, {})
                    hist_display.append(
                        {
                            "Rata nr": idx + 1,
                            "Data wpłaty": row["data"].strftime("%d.%m.%Y"),
                            "Wpłacona rata": f"{row['kwota_raty']:,.2f}",
                            "Harmonogramowa rata": (
                                f"{inst_row.get('Rata', 0):,.2f}" if inst_row else "—"
                            ),
                            "Nadpłata": (
                                f"{row['nadplata']:,.2f}"
                                if "nadplata" in hist.columns
                                else "0,00"
                            ),
                            "Uwagi": row.get("uwagi", ""),
                        }
                    )
                st.dataframe(
                    pd.DataFrame(hist_display),
                    use_container_width=True,
                    hide_index=True,
                )

                # Nadpłata chart
                if "nadplata" in hist.columns and hist["nadplata"].sum() > 0:
                    fig_hist = px.bar(
                        hist,
                        x="data",
                        y="nadplata",
                        title="Nadpłaty z historii",
                        labels={"data": "Data", "nadplata": "Nadpłata (PLN)"},
                        color_discrete_sequence=["#10b981"],
                    )
                    st.plotly_chart(fig_hist, use_container_width=True)

                # Load overpayments into calculator
                if "nadplata" in hist.columns and hist["nadplata"].sum() > 0:
                    if st.button("📥 Załaduj nadpłaty z historii do kalkulatora"):
                        for idx, row in hist.iterrows():
                            op = float(row.get("nadplata", 0) or 0)
                            if op > 0:
                                st.session_state.overpayments[idx + 1] = op
                        st.success("Nadpłaty załadowane! Przejdź do zakładki Podsumowanie.")
                        st.rerun()

        except Exception as exc:
            st.error(f"Błąd wczytywania pliku: {exc}")
