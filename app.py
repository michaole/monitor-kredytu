import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
from io import StringIO
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(
    page_title="Monitor Kredytu",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── helpers ───────────────────────────────────────────────────────────────────

_MIESIACE = [
    "", "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
    "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
]

def fmt_miesiac(d: date) -> str:
    return f"{_MIESIACE[d.month]} {d.year}"

def calc_payment(balance: float, mr: float, n: int) -> float:
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


# ── mBank CSV parser ──────────────────────────────────────────────────────────

def parse_mbank_csv(content: bytes):
    """
    Parsuje eksport historii z mBank Hipoteczny.
    Format: nagłówek metadanych, potem linia '#Data;#Data księgowania;...',
    separator ;, kodowanie cp1250.
    Zwraca None jeśli to nie jest format mBank.
    """
    text = None
    for enc in ("cp1250", "iso-8859-2", "utf-8-sig", "utf-8"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")

    lines = text.replace("\r", "").split("\n")

    # Szukaj linii nagłówka – zaczyna się od '#Data'
    header_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("#Data")),
        None,
    )
    if header_idx is None:
        return None  # Nie jest formatem mBank

    data_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(StringIO(data_text), sep=";", dtype=str, keep_default_na=False)

    # Usuń '#' z nazw kolumn
    df.columns = [c.strip().lstrip("#") for c in df.columns]

    # Zmień nazwy kolumn na wewnętrzne
    rename = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "data":
            rename[c] = "data"
        elif "ksi" in cl:
            rename[c] = "data_ks"
        elif "opis" in cl:
            rename[c] = "opis"
        elif "pozosta" in cl:
            rename[c] = "saldo"
        elif "kwota" in cl:
            rename[c] = "kwota"
    df = df.rename(columns=rename)

    # Typy danych
    df["data"] = pd.to_datetime(df["data"], format="%d-%m-%Y", errors="coerce").dt.date
    df["kwota"] = (
        pd.to_numeric(df["kwota"].str.replace(",", ".").str.strip(), errors="coerce")
        .fillna(0.0)
    )
    df["saldo"] = (
        pd.to_numeric(df["saldo"].str.replace(",", ".").str.strip(), errors="coerce")
        .fillna(0.0)
    )
    df = df.dropna(subset=["data"]).reset_index(drop=True)
    return df


def summarize_mbank(df: pd.DataFrame) -> dict:
    """Przetwarza surowy DataFrame mBank na zgrupowane statystyki."""
    opis = df["opis"].str.lower()

    # Regex toleruje różne kodowania/warianty znaków
    mask_odsetki_rata = opis.str.contains(r"sp.ata raty - odsetki", regex=True, na=False)
    mask_kapital_rata = opis.str.contains(r"sp.ata raty - kapita", regex=True, na=False)
    mask_kapital_nadp = opis.str.contains(r"cz.*sp.ata.*kapita", regex=True, na=False)
    mask_odsetki_nadp = opis.str.contains(r"cz.*sp.ata.*odsetki", regex=True, na=False)
    mask_otwarcie     = opis.str.contains("otwarcie", regex=False, na=False)

    total_odsetki  = df.loc[mask_odsetki_rata, "kwota"].sum()
    total_odsetki += df.loc[mask_odsetki_nadp, "kwota"].sum()
    total_kapital  = df.loc[mask_kapital_rata, "kwota"].sum()
    total_nadplaty = df.loc[mask_kapital_nadp, "kwota"].sum()
    n_rat          = int(df.loc[mask_kapital_rata, "data"].nunique())
    otwarcie_kwota = df.loc[mask_otwarcie, "kwota"].max() if mask_otwarcie.any() else 0.0
    otwarcie_data  = df.loc[mask_otwarcie, "data"].min() if mask_otwarcie.any() else None

    # Historia salda – ostatnia transakcja kapitałowa każdego dnia
    saldo_hist = (
        df.loc[(mask_kapital_rata | mask_kapital_nadp) & (df["saldo"] > 0)]
        .groupby("data")["saldo"]
        .min()  # po nadpłacie saldo jest niższe
        .reset_index()
        .rename(columns={"saldo": "Saldo"})
        .sort_values("data")
    )

    # Miesięczne nadpłaty (cz.spłata - kapitał)
    nadplaty_mies = (
        df.loc[mask_kapital_nadp]
        .groupby("data")["kwota"]
        .sum()
        .reset_index()
        .rename(columns={"kwota": "Nadpłata"})
        .sort_values("data")
    )

    # Miesięczne raty (odsetki + kapitał per data)
    raty_mies = (
        df.loc[mask_odsetki_rata | mask_kapital_rata]
        .groupby("data")["kwota"]
        .sum()
        .reset_index()
        .rename(columns={"kwota": "Rata"})
        .sort_values("data")
    )

    return {
        "total_odsetki": total_odsetki,
        "total_kapital": total_kapital,
        "total_nadplaty": total_nadplaty,
        "n_rat": n_rat,
        "otwarcie_kwota": otwarcie_kwota,
        "otwarcie_data": otwarcie_data,
        "saldo_hist": saldo_hist,
        "nadplaty_mies": nadplaty_mies,
        "raty_mies": raty_mies,
    }


# ── session state ─────────────────────────────────────────────────────────────

if "overpayments" not in st.session_state:
    st.session_state.overpayments: dict = {}

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
            "Skrócenie okresu (rata stała)"
            if x == "reduce_term"
            else "Zmniejszenie raty (okres stały)"
        ),
    )
    st.divider()
    st.caption("Zmiany parametrów aktualizują harmonogram na bieżąco.")

# ── build schedules ───────────────────────────────────────────────────────────

mr = rate_pct / 100 / 12
base_payment = calc_payment(balance, mr, n_inst)

sched_base = build_schedule(balance, rate_pct, n_inst, start_date, {})
sched_op   = build_schedule(
    balance, rate_pct, n_inst, start_date, st.session_state.overpayments, mode
)

interest_base      = sched_base["Odsetki"].sum()
interest_op        = sched_op["Odsetki"].sum()
saved_interest     = interest_base - interest_op
saved_installments = len(sched_base) - len(sched_op)
total_overpayments = sum(st.session_state.overpayments.values())

end_base = sched_base["Data"].iloc[-1]
end_op   = sched_op["Data"].iloc[-1]

# ── header ────────────────────────────────────────────────────────────────────

st.title("🏠 Monitor Kredytu Hipotecznego")
st.caption(
    f"Następna rata: **{start_date.strftime('%d.%m.%Y')}** · "
    f"Oprocentowanie: **{rate_pct}%** · "
    f"Saldo: **{fmt_pln(balance)}**"
)

# ── tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📊 Podsumowanie", "📅 Harmonogram", "💰 Nadpłaty", "🔮 Prognoza", "📂 Historia (mBank CSV)"]
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

    fig_bal = go.Figure()
    fig_bal.add_trace(
        go.Scatter(
            x=sched_base["Data"], y=sched_base["Saldo po"],
            name="Bez nadpłat",
            line=dict(color="#94a3b8", dash="dash"),
            fill="tozeroy", fillcolor="rgba(148,163,184,0.08)",
        )
    )
    fig_bal.add_trace(
        go.Scatter(
            x=sched_op["Data"], y=sched_op["Saldo po"],
            name="Z nadpłatami",
            line=dict(color="#6366f1", width=2),
            fill="tozeroy", fillcolor="rgba(99,102,241,0.12)",
        )
    )
    fig_bal.update_layout(
        title="Saldo kredytu w czasie (prognoza)",
        xaxis_title="Data", yaxis_title="PLN",
        hovermode="x unified", height=380,
        legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99),
    )
    st.plotly_chart(fig_bal, use_container_width=True)

    col_l, col_r = st.columns(2)

    with col_l:
        fig_bar = go.Figure()
        fig_bar.add_trace(
            go.Bar(x=sched_op["Data"], y=sched_op["Odsetki"],
                   name="Odsetki", marker_color="#f43f5e")
        )
        fig_bar.add_trace(
            go.Bar(x=sched_op["Data"], y=sched_op["Kapitał"],
                   name="Kapitał", marker_color="#6366f1")
        )
        if total_overpayments > 0:
            fig_bar.add_trace(
                go.Bar(x=sched_op["Data"], y=sched_op["Nadpłata"],
                       name="Nadpłata", marker_color="#10b981")
            )
        fig_bar.update_layout(
            barmode="stack", title="Struktura miesięcznych wpłat",
            xaxis_title="Data", yaxis_title="PLN", height=340,
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
            go.Pie(labels=labels, values=values, hole=0.45, marker_colors=colors)
        )
        fig_pie.update_layout(title="Łączny koszt kredytu (prognoza)", height=340)
        st.plotly_chart(fig_pie, use_container_width=True)

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
        csv_bytes, "harmonogram_kredytu.csv", "text/csv",
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
                "Numer raty", min_value=1, max_value=n_inst + 100, value=1, step=1
            )
            op_amount = st.number_input(
                "Kwota nadpłaty (PLN)", min_value=0.0, step=100.0, format="%.2f"
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

    if st.session_state.overpayments:
        st.divider()
        st.subheader("Efekt nadpłat")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Oszczędność – odsetki", fmt_pln(saved_interest),
            delta=f"-{fmt_pln(saved_interest)}", delta_color="inverse",
        )
        c2.metric(
            "Skrócenie okresu", f"{saved_installments} rat",
            delta=f"-{saved_installments}", delta_color="inverse",
        )
        c3.metric("Koniec kredytu", end_op.strftime("%m.%Y"))
        c4.metric("Bez nadpłat", end_base.strftime("%m.%Y"))

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 – Prognoza ze stałą nadpłatą
# ═══════════════════════════════════════════════════════════════════════════════

with tab4:
    st.subheader("Prognoza ze stałą miesięczną nadpłatą")

    col_inp, col_res = st.columns([1, 2])

    with col_inp:
        monthly_op = st.number_input(
            "Stała nadpłata miesięczna (PLN)",
            min_value=0.0,
            value=9_850.0,
            step=100.0,
            format="%.2f",
        )
        prog_mode = st.radio(
            "Efekt nadpłaty",
            ["reduce_term", "reduce_installment"],
            format_func=lambda x: (
                "Skrócenie okresu" if x == "reduce_term" else "Zmniejszenie raty"
            ),
            key="prog_mode",
        )

    # Buduj harmonogram ze stałą nadpłatą
    prog_overpayments = {nr: monthly_op for nr in range(1, n_inst + 1)}
    sched_prog = build_schedule(
        balance, rate_pct, n_inst, start_date, prog_overpayments, prog_mode
    )

    interest_prog      = sched_prog["Odsetki"].sum()
    saved_prog         = interest_base - interest_prog
    saved_inst_prog    = len(sched_base) - len(sched_prog)
    end_prog           = sched_prog["Data"].iloc[-1]
    total_op_prog      = sched_prog["Nadpłata"].sum()
    miesięczna_prog    = base_payment + monthly_op

    with col_res:
        m1, m2, m3 = st.columns(3)
        m1.metric(
            "Koniec kredytu",
            end_prog.strftime("%m.%Y"),
            delta=f"-{saved_inst_prog} rat vs. bez nadpłat",
            delta_color="inverse",
        )
        m2.metric(
            "Oszczędność na odsetkach",
            fmt_pln(saved_prog),
            delta=f"-{fmt_pln(saved_prog)}",
            delta_color="inverse",
        )
        m3.metric("Miesięczna wpłata łącznie", fmt_pln(miesięczna_prog))

    st.info(
        f"Przy nadpłacie **{fmt_pln(monthly_op)}/mies.** kredyt skończy się "
        f"**w {fmt_miesiac(end_prog)}** zamiast **{fmt_miesiac(end_base)}** "
        f"– czyli **{saved_inst_prog} rat wcześniej** "
        f"({saved_inst_prog // 12} lat {saved_inst_prog % 12} mies.). "
        f"Łączna oszczędność na odsetkach: **{fmt_pln(saved_prog)}**."
    )

    st.divider()

    # Wykres porównawczy saldo
    fig_prog = go.Figure()
    fig_prog.add_trace(go.Scatter(
        x=sched_base["Data"], y=sched_base["Saldo po"],
        name="Bez nadpłat",
        line=dict(color="#94a3b8", dash="dash"),
        fill="tozeroy", fillcolor="rgba(148,163,184,0.08)",
    ))
    fig_prog.add_trace(go.Scatter(
        x=sched_prog["Data"], y=sched_prog["Saldo po"],
        name=f"Nadpłata {fmt_pln(monthly_op)}/mies.",
        line=dict(color="#10b981", width=2),
        fill="tozeroy", fillcolor="rgba(16,185,129,0.12)",
    ))
    fig_prog.update_layout(
        title="Saldo w czasie – porównanie",
        xaxis_title="Data", yaxis_title="PLN",
        hovermode="x unified", height=380,
        legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99),
    )
    st.plotly_chart(fig_prog, use_container_width=True)

    # Wykres struktury płatności
    fig_prog_bar = go.Figure()
    fig_prog_bar.add_trace(go.Bar(
        x=sched_prog["Data"], y=sched_prog["Odsetki"],
        name="Odsetki", marker_color="#f43f5e",
    ))
    fig_prog_bar.add_trace(go.Bar(
        x=sched_prog["Data"], y=sched_prog["Kapitał"],
        name="Kapitał", marker_color="#6366f1",
    ))
    fig_prog_bar.add_trace(go.Bar(
        x=sched_prog["Data"], y=sched_prog["Nadpłata"],
        name="Nadpłata", marker_color="#10b981",
    ))
    fig_prog_bar.update_layout(
        barmode="stack",
        title="Struktura miesięcznych wpłat",
        xaxis_title="Data", yaxis_title="PLN",
        height=320,
    )
    st.plotly_chart(fig_prog_bar, use_container_width=True)

    # Tabela harmonogramu
    with st.expander("Pokaż pełny harmonogram prognozy"):
        disp_prog = sched_prog.copy()
        disp_prog["Data"] = disp_prog["Data"].apply(lambda d: d.strftime("%d.%m.%Y"))
        for col in ["Saldo przed","Odsetki","Kapitał","Nadpłata","Rata","Łączna wpłata","Saldo po","Suma odsetek"]:
            disp_prog[col] = disp_prog[col].apply(lambda x: f"{x:,.2f}")
        st.dataframe(disp_prog, use_container_width=True, hide_index=True, height=400)

        csv_prog = sched_prog.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Pobierz harmonogram prognozy CSV",
            csv_prog, "prognoza_kredytu.csv", "text/csv",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 – Historia mBank CSV
# ═══════════════════════════════════════════════════════════════════════════════

with tab5:
    st.subheader("Historia spłat z mBank")
    st.markdown(
        "Wgraj plik CSV eksportowany z mBank Hipoteczny "
        "(**Historia kredytu**). Aplikacja automatycznie rozpozna format banku."
    )

    uploaded = st.file_uploader("Wgraj plik CSV z historią kredytu", type=["csv"])

    if uploaded:
        raw = uploaded.read()
        df_raw = parse_mbank_csv(raw)

        if df_raw is None:
            st.error(
                "Nie rozpoznano formatu mBank. Upewnij się, że to eksport "
                "'Historia kredytu' z mBank Hipoteczny "
                "(nagłówek musi zaczynać się od '#Data;')."
            )
        else:
            st.success(f"Wczytano {len(df_raw)} wierszy z historii mBank.")
            stats = summarize_mbank(df_raw)

            # ── metryki ──────────────────────────────────────────────────────
            st.divider()
            st.subheader("Podsumowanie historii")

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric(
                "Kredyt otwarty",
                stats["otwarcie_data"].strftime("%m.%Y")
                if stats["otwarcie_data"] else "—",
            )
            m2.metric("Kwota początkowa", fmt_pln(stats["otwarcie_kwota"]))
            m3.metric("Zapłacone odsetki", fmt_pln(stats["total_odsetki"]))
            m4.metric("Suma nadpłat", fmt_pln(stats["total_nadplaty"]))
            m5.metric("Opłacone raty", str(stats["n_rat"]))

            kapital_splac = stats["otwarcie_kwota"] - balance
            st.info(
                f"Od otwarcia spłacono **{fmt_pln(kapital_splac)}** z kapitału "
                f"(w tym **{fmt_pln(stats['total_nadplaty'])}** nadpłat) "
                f"i **{fmt_pln(stats['total_odsetki'])}** odsetek. "
                f"Pozostało: **{fmt_pln(balance)}**."
            )

            # ── saldo w czasie ────────────────────────────────────────────────
            st.divider()
            st.subheader("Saldo kredytu – historia rzeczywista")

            saldo_hist = stats["saldo_hist"]
            if not saldo_hist.empty:
                fig_saldo = go.Figure()
                fig_saldo.add_trace(
                    go.Scatter(
                        x=saldo_hist["data"],
                        y=saldo_hist["Saldo"],
                        name="Saldo rzeczywiste",
                        line=dict(color="#6366f1", width=2),
                        fill="tozeroy",
                        fillcolor="rgba(99,102,241,0.12)",
                        mode="lines+markers",
                        marker=dict(size=5),
                    )
                )
                fig_saldo.update_layout(
                    xaxis_title="Data", yaxis_title="PLN",
                    hovermode="x unified", height=380,
                )
                st.plotly_chart(fig_saldo, use_container_width=True)

            # ── nadpłaty ──────────────────────────────────────────────────────
            st.divider()
            st.subheader("Nadpłaty w historii")

            nadplaty = stats["nadplaty_mies"]
            if nadplaty.empty:
                st.info("Brak nadpłat w historii.")
            else:
                fig_op = px.bar(
                    nadplaty, x="data", y="Nadpłata",
                    labels={"data": "Data", "Nadpłata": "Kwota nadpłaty (PLN)"},
                    color_discrete_sequence=["#10b981"],
                )
                fig_op.update_layout(
                    xaxis_title="Data", yaxis_title="PLN",
                    height=320, bargap=0.3,
                )
                st.plotly_chart(fig_op, use_container_width=True)

                nadp_display = nadplaty.copy()
                nadp_display["data"] = nadp_display["data"].apply(
                    lambda d: d.strftime("%d.%m.%Y")
                )
                nadp_display["Nadpłata"] = nadp_display["Nadpłata"].apply(
                    lambda x: f"{x:,.2f} PLN"
                )
                st.dataframe(nadp_display, use_container_width=True, hide_index=True)

            # ── raty miesięczne ───────────────────────────────────────────────
            st.divider()
            st.subheader("Miesięczne raty (kapitał + odsetki)")

            raty = stats["raty_mies"]
            if not raty.empty:
                fig_raty = px.bar(
                    raty, x="data", y="Rata",
                    labels={"data": "Data", "Rata": "Kwota raty (PLN)"},
                    color_discrete_sequence=["#6366f1"],
                )
                fig_raty.update_layout(
                    xaxis_title="Data", yaxis_title="PLN",
                    height=300, bargap=0.2,
                )
                st.plotly_chart(fig_raty, use_container_width=True)

            # ── surowe dane ───────────────────────────────────────────────────
            with st.expander("Pokaż surowe dane z pliku"):
                disp_raw = df_raw.copy()
                disp_raw["data"] = disp_raw["data"].apply(
                    lambda d: d.strftime("%d.%m.%Y") if pd.notna(d) else ""
                )
                st.dataframe(
                    disp_raw, use_container_width=True,
                    hide_index=True, height=400,
                )
