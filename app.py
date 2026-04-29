import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
from io import StringIO
from pathlib import Path
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(
    page_title="Monitor Kredytu",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── stałe ─────────────────────────────────────────────────────────────────────

DATA_DIR  = Path(__file__).parent / "data"
CSV_CACHE = DATA_DIR / "historia_mbank.csv"

_MIESIACE = [
    "", "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
    "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
]

# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_miesiac(d: date) -> str:
    return f"{_MIESIACE[d.month]} {d.year}"

def fmt_pln(x: float) -> str:
    return f"{x:,.2f} PLN".replace(",", " ")

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
    balance: float, annual_rate_pct: float, n_inst: int, start: date,
    overpayments: dict, mode: str = "reduce_term",
) -> pd.DataFrame:
    mr      = annual_rate_pct / 100 / 12
    bal     = balance
    payment = calc_payment(bal, mr, n_inst)
    rows    = []
    total_interest = 0.0
    for nr in range(1, n_inst + 500):
        if bal < 0.01:
            break
        interest  = bal * mr
        principal = min(payment - interest, bal)
        if principal < 0:
            break
        op      = max(0.0, min(float(overpayments.get(nr, 0)), bal - principal))
        new_bal = max(0.0, bal - principal - op)
        total_interest += interest
        rows.append({
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
        })
        bal = new_bal
        remaining = n_inst - nr
        if op > 0 and bal > 0 and remaining > 0 and mode == "reduce_installment":
            payment = calc_payment(bal, mr, remaining)
    return pd.DataFrame(rows)


# ── mBank CSV parser ──────────────────────────────────────────────────────────

def parse_mbank_csv(content: bytes):
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
    header_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith("#Data")), None
    )
    if header_idx is None:
        return None

    df = pd.read_csv(StringIO("\n".join(lines[header_idx:])),
                     sep=";", dtype=str, keep_default_na=False)
    df.columns = [c.strip().lstrip("#") for c in df.columns]

    rename = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "data":             rename[c] = "data"
        elif "ksi" in cl:            rename[c] = "data_ks"
        elif "opis" in cl:           rename[c] = "opis"
        elif "pozosta" in cl:        rename[c] = "saldo"
        elif "kwota" in cl:          rename[c] = "kwota"
    df = df.rename(columns=rename)

    df["data"]  = pd.to_datetime(df["data"],  format="%d-%m-%Y", errors="coerce").dt.date
    df["kwota"] = pd.to_numeric(df["kwota"].str.replace(",", ".").str.strip(), errors="coerce").fillna(0.0)
    df["saldo"] = pd.to_numeric(df["saldo"].str.replace(",", ".").str.strip(), errors="coerce").fillna(0.0)
    return df.dropna(subset=["data"]).reset_index(drop=True)


def get_masks(df: pd.DataFrame):
    opis = df["opis"].str.lower()
    return {
        "odsetki_rata": opis.str.contains(r"sp.ata raty - odsetki",  regex=True, na=False),
        "kapital_rata": opis.str.contains(r"sp.ata raty - kapita",   regex=True, na=False),
        "kapital_nadp": opis.str.contains(r"cz.*sp.ata.*kapita",     regex=True, na=False),
        "odsetki_nadp": opis.str.contains(r"cz.*sp.ata.*odsetki",    regex=True, na=False),
        "otwarcie":     opis.str.contains("otwarcie",  regex=False,  na=False),
    }


# ── auto-load CSV z dysku ─────────────────────────────────────────────────────

if "mbank_df" not in st.session_state:
    if CSV_CACHE.exists():
        try:
            st.session_state.mbank_df = parse_mbank_csv(CSV_CACHE.read_bytes())
        except Exception:
            st.session_state.mbank_df = None
    else:
        st.session_state.mbank_df = None

if "overpayments" not in st.session_state:
    st.session_state.overpayments: dict = {}

# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Parametry kredytu")
    balance    = st.number_input("Saldo do spłaty (PLN)", value=236_600.0, step=100.0, format="%.2f", min_value=1.0)
    n_inst     = st.number_input("Pozostałe raty", value=72, min_value=1, max_value=480, step=1)
    rate_pct   = st.number_input("Oprocentowanie roczne (%)", value=5.74, step=0.01, format="%.4f", min_value=0.0)
    start_date = st.date_input("Data następnej raty", value=next_25th())
    mode       = st.radio(
        "Efekt nadpłat",
        ["reduce_term", "reduce_installment"],
        format_func=lambda x: "Skrócenie okresu (rata stała)" if x == "reduce_term" else "Zmniejszenie raty (okres stały)",
    )
    st.divider()
    st.caption("Zmiany parametrów aktualizują harmonogram na bieżąco.")

# ── harmonogramy ──────────────────────────────────────────────────────────────

mr             = rate_pct / 100 / 12
base_payment   = calc_payment(balance, mr, n_inst)
sched_base     = build_schedule(balance, rate_pct, n_inst, start_date, {})
sched_op       = build_schedule(balance, rate_pct, n_inst, start_date, st.session_state.overpayments, mode)
interest_base  = sched_base["Odsetki"].sum()
interest_op    = sched_op["Odsetki"].sum()
saved_interest = interest_base - interest_op
saved_inst     = len(sched_base) - len(sched_op)
end_base       = sched_base["Data"].iloc[-1]
end_op         = sched_op["Data"].iloc[-1]

# ── nagłówek ──────────────────────────────────────────────────────────────────

st.title("🏠 Monitor Kredytu Hipotecznego")
st.caption(
    f"Następna rata: **{start_date.strftime('%d.%m.%Y')}** · "
    f"Oprocentowanie: **{rate_pct}%** · Saldo: **{fmt_pln(balance)}**"
)

# ── zakładki ──────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Podsumowanie", "📅 Harmonogram", "💰 Nadpłaty",
    "🔮 Prognoza", "📂 Historia i analiza",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 – Podsumowanie
# ═══════════════════════════════════════════════════════════════════════════════

with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo do spłaty", fmt_pln(balance))
    c2.metric("Miesięczna rata", fmt_pln(base_payment))
    c3.metric("Pozostałe raty", str(len(sched_op)),
              delta=f"-{saved_inst} rat" if saved_inst > 0 else None, delta_color="inverse")
    c4.metric("Łączne odsetki", fmt_pln(interest_op),
              delta=f"-{fmt_pln(saved_interest)}" if saved_interest > 0 else None, delta_color="inverse")

    st.divider()

    fig_bal = go.Figure()
    fig_bal.add_trace(go.Scatter(
        x=sched_base["Data"], y=sched_base["Saldo po"], name="Bez nadpłat",
        line=dict(color="#94a3b8", dash="dash"),
        fill="tozeroy", fillcolor="rgba(148,163,184,0.08)",
    ))
    fig_bal.add_trace(go.Scatter(
        x=sched_op["Data"], y=sched_op["Saldo po"], name="Z nadpłatami",
        line=dict(color="#6366f1", width=2),
        fill="tozeroy", fillcolor="rgba(99,102,241,0.12)",
    ))
    fig_bal.update_layout(title="Saldo kredytu w czasie (prognoza)",
                          xaxis_title="Data", yaxis_title="PLN",
                          hovermode="x unified", height=380,
                          legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99))
    st.plotly_chart(fig_bal, use_container_width=True)

    cl, cr = st.columns(2)
    with cl:
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(x=sched_op["Data"], y=sched_op["Odsetki"], name="Odsetki", marker_color="#f43f5e"))
        fig_bar.add_trace(go.Bar(x=sched_op["Data"], y=sched_op["Kapitał"], name="Kapitał", marker_color="#6366f1"))
        if sum(st.session_state.overpayments.values()) > 0:
            fig_bar.add_trace(go.Bar(x=sched_op["Data"], y=sched_op["Nadpłata"], name="Nadpłata", marker_color="#10b981"))
        fig_bar.update_layout(barmode="stack", title="Struktura wpłat",
                              xaxis_title="Data", yaxis_title="PLN", height=340)
        st.plotly_chart(fig_bar, use_container_width=True)
    with cr:
        labels = ["Kapitał", "Odsetki"]
        values = [balance, interest_op]
        colors = ["#6366f1", "#f43f5e"]
        fig_pie = go.Figure(go.Pie(labels=labels, values=values, hole=0.45, marker_colors=colors))
        fig_pie.update_layout(title="Łączny koszt kredytu (prognoza)", height=340)
        st.plotly_chart(fig_pie, use_container_width=True)

    if saved_inst > 0:
        st.success(
            f"Dzięki nadpłatom oszczędzasz **{fmt_pln(saved_interest)}** na odsetkach "
            f"i kończysz kredyt **{saved_inst} rat wcześniej** "
            f"({end_op.strftime('%m.%Y')} zamiast {end_base.strftime('%m.%Y')})."
        )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 – Harmonogram
# ═══════════════════════════════════════════════════════════════════════════════

with tab2:
    st.subheader("Harmonogram spłat")
    disp = sched_op.copy()
    disp["Data"] = disp["Data"].apply(lambda d: d.strftime("%d.%m.%Y"))
    for col in ["Saldo przed","Odsetki","Kapitał","Nadpłata","Rata","Łączna wpłata","Saldo po","Suma odsetek"]:
        disp[col] = disp[col].apply(lambda x: f"{x:,.2f}")
    st.dataframe(disp, use_container_width=True, hide_index=True, height=520)
    st.download_button("⬇️ Pobierz harmonogram CSV",
                       sched_op.to_csv(index=False).encode("utf-8"),
                       "harmonogram_kredytu.csv", "text/csv")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 – Nadpłaty
# ═══════════════════════════════════════════════════════════════════════════════

with tab3:
    st.subheader("Planuj nadpłaty")
    col_form, col_list = st.columns([1, 2])

    with col_form:
        with st.form("add_op", clear_on_submit=True):
            st.markdown("**Dodaj nadpłatę**")
            inst_nr   = st.number_input("Numer raty", min_value=1, max_value=n_inst + 100, value=1, step=1)
            op_amount = st.number_input("Kwota nadpłaty (PLN)", min_value=0.0, step=100.0, format="%.2f")
            st.caption(f"Data raty nr {inst_nr}: {add_months(start_date, int(inst_nr)-1).strftime('%d.%m.%Y')}")
            if st.form_submit_button("➕ Dodaj"):
                if op_amount > 0:
                    st.session_state.overpayments[int(inst_nr)] = float(op_amount)
                    st.rerun()
                else:
                    st.warning("Kwota musi być > 0.")

        if st.session_state.overpayments:
            with st.form("del_op"):
                st.markdown("**Usuń nadpłatę**")
                del_nr = st.selectbox("Rata nr", sorted(st.session_state.overpayments))
                if st.form_submit_button("🗑️ Usuń"):
                    del st.session_state.overpayments[del_nr]
                    st.rerun()

    with col_list:
        if st.session_state.overpayments:
            rows = [{"Rata nr": k,
                     "Data": add_months(start_date, k-1).strftime("%d.%m.%Y"),
                     "Nadpłata (PLN)": f"{v:,.2f}"}
                    for k, v in sorted(st.session_state.overpayments.items())]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.metric("Suma nadpłat", fmt_pln(sum(st.session_state.overpayments.values())))
            if st.button("🗑️ Wyczyść wszystkie"):
                st.session_state.overpayments = {}
                st.rerun()
        else:
            st.info("Brak zaplanowanych nadpłat.")

    if st.session_state.overpayments:
        st.divider()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Oszczędność – odsetki", fmt_pln(saved_interest),
                  delta=f"-{fmt_pln(saved_interest)}", delta_color="inverse")
        c2.metric("Skrócenie okresu", f"{saved_inst} rat",
                  delta=f"-{saved_inst}", delta_color="inverse")
        c3.metric("Koniec kredytu", end_op.strftime("%m.%Y"))
        c4.metric("Bez nadpłat",    end_base.strftime("%m.%Y"))

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 – Prognoza
# ═══════════════════════════════════════════════════════════════════════════════

with tab4:
    st.subheader("Prognoza ze stałą miesięczną nadpłatą")

    col_inp, col_res = st.columns([1, 2])
    with col_inp:
        monthly_op = st.number_input("Stała nadpłata miesięczna (PLN)",
                                     min_value=0.0, value=9_850.0, step=100.0, format="%.2f")
        prog_mode  = st.radio("Efekt nadpłaty",
                              ["reduce_term", "reduce_installment"],
                              format_func=lambda x: "Skrócenie okresu" if x == "reduce_term" else "Zmniejszenie raty",
                              key="prog_mode")

    prog_op    = {nr: monthly_op for nr in range(1, n_inst + 1)}
    sched_prog = build_schedule(balance, rate_pct, n_inst, start_date, prog_op, prog_mode)

    interest_prog   = sched_prog["Odsetki"].sum()
    saved_prog      = interest_base - interest_prog
    saved_inst_prog = len(sched_base) - len(sched_prog)
    end_prog        = sched_prog["Data"].iloc[-1]

    with col_res:
        m1, m2, m3 = st.columns(3)
        m1.metric("Koniec kredytu", end_prog.strftime("%m.%Y"),
                  delta=f"-{saved_inst_prog} rat", delta_color="inverse")
        m2.metric("Oszczędność na odsetkach", fmt_pln(saved_prog),
                  delta=f"-{fmt_pln(saved_prog)}", delta_color="inverse")
        m3.metric("Miesięczna wpłata łącznie", fmt_pln(base_payment + monthly_op))

    st.info(
        f"Przy nadpłacie **{fmt_pln(monthly_op)}/mies.** kredyt skończy się "
        f"**w {fmt_miesiac(end_prog)}** zamiast **{fmt_miesiac(end_base)}** "
        f"– czyli **{saved_inst_prog} rat wcześniej** "
        f"({saved_inst_prog // 12} lat {saved_inst_prog % 12} mies.). "
        f"Łączna oszczędność na odsetkach: **{fmt_pln(saved_prog)}**."
    )

    st.divider()

    fig_prog = go.Figure()
    fig_prog.add_trace(go.Scatter(
        x=sched_base["Data"], y=sched_base["Saldo po"], name="Bez nadpłat",
        line=dict(color="#94a3b8", dash="dash"),
        fill="tozeroy", fillcolor="rgba(148,163,184,0.08)",
    ))
    fig_prog.add_trace(go.Scatter(
        x=sched_prog["Data"], y=sched_prog["Saldo po"],
        name=f"Nadpłata {fmt_pln(monthly_op)}/mies.",
        line=dict(color="#10b981", width=2),
        fill="tozeroy", fillcolor="rgba(16,185,129,0.12)",
    ))
    fig_prog.update_layout(title="Saldo w czasie – porównanie",
                           xaxis_title="Data", yaxis_title="PLN",
                           hovermode="x unified", height=380,
                           legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99))
    st.plotly_chart(fig_prog, use_container_width=True)

    fig_pb = go.Figure()
    fig_pb.add_trace(go.Bar(x=sched_prog["Data"], y=sched_prog["Odsetki"], name="Odsetki", marker_color="#f43f5e"))
    fig_pb.add_trace(go.Bar(x=sched_prog["Data"], y=sched_prog["Kapitał"], name="Kapitał",  marker_color="#6366f1"))
    fig_pb.add_trace(go.Bar(x=sched_prog["Data"], y=sched_prog["Nadpłata"],name="Nadpłata", marker_color="#10b981"))
    fig_pb.update_layout(barmode="stack", title="Struktura miesięcznych wpłat",
                         xaxis_title="Data", yaxis_title="PLN", height=320)
    st.plotly_chart(fig_pb, use_container_width=True)

    with st.expander("Pokaż pełny harmonogram prognozy"):
        disp_prog = sched_prog.copy()
        disp_prog["Data"] = disp_prog["Data"].apply(lambda d: d.strftime("%d.%m.%Y"))
        for col in ["Saldo przed","Odsetki","Kapitał","Nadpłata","Rata","Łączna wpłata","Saldo po","Suma odsetek"]:
            disp_prog[col] = disp_prog[col].apply(lambda x: f"{x:,.2f}")
        st.dataframe(disp_prog, use_container_width=True, hide_index=True, height=400)
        st.download_button("⬇️ Pobierz harmonogram prognozy CSV",
                           sched_prog.to_csv(index=False).encode("utf-8"),
                           "prognoza_kredytu.csv", "text/csv")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 – Historia i analiza
# ═══════════════════════════════════════════════════════════════════════════════

with tab5:
    st.subheader("Historia spłat i analiza wsteczna")

    # ── upload / cache ────────────────────────────────────────────────────────
    col_up, col_status = st.columns([2, 1])
    with col_up:
        uploaded = st.file_uploader("Wgraj plik CSV z historią mBank Hipoteczny", type=["csv"])
    with col_status:
        if st.session_state.mbank_df is not None:
            st.success("✅ Plik wczytany – dane dostępne poniżej.")
            if CSV_CACHE.exists():
                st.caption(f"Ostatni zapis: {CSV_CACHE.stat().st_mtime and pd.Timestamp(CSV_CACHE.stat().st_mtime, unit='s').strftime('%d.%m.%Y %H:%M')}")

    if uploaded:
        raw    = uploaded.read()
        df_new = parse_mbank_csv(raw)
        if df_new is None:
            st.error("Nie rozpoznano formatu mBank. Oczekiwany nagłówek '#Data;...'")
        else:
            st.session_state.mbank_df = df_new
            DATA_DIR.mkdir(exist_ok=True)
            CSV_CACHE.write_bytes(raw)          # zapis na dysk
            st.success(f"Wczytano {len(df_new)} wierszy. Plik zapisany – nie trzeba wgrywać go przy kolejnych sesjach.")

    # ── treść analizy ─────────────────────────────────────────────────────────
    df = st.session_state.mbank_df

    if df is None:
        st.info("Wgraj plik CSV z historią kredytu z mBank powyżej, aby zobaczyć analizę.")
        st.stop()

    masks         = get_masks(df)
    m_odsetki     = masks["odsetki_rata"] | masks["odsetki_nadp"]
    m_kapital_reg = masks["kapital_rata"]
    m_kapital_all = masks["kapital_rata"] | masks["kapital_nadp"]
    m_nadplaty    = masks["kapital_nadp"]
    m_otwarcie    = masks["otwarcie"]

    total_odsetki  = df.loc[m_odsetki,  "kwota"].sum()
    total_nadplaty = df.loc[m_nadplaty, "kwota"].sum()
    kapital_splac  = df.loc[m_kapital_all, "kwota"].sum()
    otwarcie_kwota = df.loc[m_otwarcie, "kwota"].max() if m_otwarcie.any() else 0.0
    otwarcie_data  = df.loc[m_otwarcie, "data"].min()  if m_otwarcie.any() else None
    n_rat          = int(df.loc[m_kapital_reg, "data"].nunique())

    # ── metryki ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Podsumowanie historii")

    lata = (date.today() - otwarcie_data).days / 365.25 if otwarcie_data else 0
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Kredyt otwarty",       otwarcie_data.strftime("%m.%Y") if otwarcie_data else "—")
    m2.metric("Kwota początkowa",     fmt_pln(otwarcie_kwota))
    m3.metric("Zapłacone odsetki",    fmt_pln(total_odsetki))
    m4.metric("Suma nadpłat",         fmt_pln(total_nadplaty))
    m5.metric("Opłacone raty",        str(n_rat))

    if total_odsetki > 0:
        st.info(
            f"Trwa **{int(lata)} lat {int((lata % 1)*12)} mies.** · "
            f"Spłacono **{fmt_pln(kapital_splac)}** kapitału "
            f"(nadpłaty: **{total_nadplaty/kapital_splac*100:.1f}%**) · "
            f"Odsetki: **{fmt_pln(total_odsetki)}**"
        )

    # ── saldo w czasie ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Saldo kredytu – historia rzeczywista")

    saldo_hist = (
        df.loc[m_kapital_all & (df["saldo"] > 0)]
        .groupby("data")["saldo"].min()
        .reset_index().rename(columns={"saldo": "Saldo"})
        .sort_values("data")
    )

    if not saldo_hist.empty:
        fig_saldo = go.Figure()
        fig_saldo.add_trace(go.Scatter(
            x=saldo_hist["data"], y=saldo_hist["Saldo"],
            name="Saldo rzeczywiste",
            line=dict(color="#6366f1", width=2),
            fill="tozeroy", fillcolor="rgba(99,102,241,0.12)",
            mode="lines+markers", marker=dict(size=5),
        ))
        fig_saldo.update_layout(xaxis_title="Data", yaxis_title="PLN",
                                hovermode="x unified", height=360)
        st.plotly_chart(fig_saldo, use_container_width=True)

    # ── zestawienie roczne ────────────────────────────────────────────────────
    st.divider()
    st.subheader("Zestawienie roczne")

    df["_rok"] = df["data"].apply(lambda d: d.year)
    lata_unik  = sorted(df["_rok"].unique())

    def rsum(mask, rok):
        return df.loc[mask & (df["_rok"] == rok), "kwota"].sum()

    roczne = pd.DataFrame({
        "Rok":      lata_unik,
        "Odsetki":  [rsum(m_odsetki,  r) for r in lata_unik],
        "Kapitał":  [rsum(m_kapital_reg, r) for r in lata_unik],
        "Nadpłaty": [rsum(m_nadplaty,  r) for r in lata_unik],
    })
    roczne["Łącznie"] = roczne["Odsetki"] + roczne["Kapitał"] + roczne["Nadpłaty"]

    saldo_po_roku = (
        df.loc[m_kapital_all & (df["saldo"] > 0)]
        .groupby("_rok")["saldo"].min()
    )
    roczne["Saldo końcowe"] = roczne["Rok"].map(saldo_po_roku).fillna(0)

    roczne_disp = roczne.copy()
    for col in ["Odsetki","Kapitał","Nadpłaty","Łącznie","Saldo końcowe"]:
        roczne_disp[col] = roczne_disp[col].apply(lambda x: f"{x:,.2f}")
    st.dataframe(roczne_disp, use_container_width=True, hide_index=True)

    # ── roczna struktura wpłat ────────────────────────────────────────────────
    fig_rok = go.Figure()
    fig_rok.add_trace(go.Bar(x=roczne["Rok"], y=roczne["Odsetki"],  name="Odsetki",  marker_color="#f43f5e"))
    fig_rok.add_trace(go.Bar(x=roczne["Rok"], y=roczne["Kapitał"],  name="Kapitał",  marker_color="#6366f1"))
    fig_rok.add_trace(go.Bar(x=roczne["Rok"], y=roczne["Nadpłaty"], name="Nadpłaty", marker_color="#10b981"))
    fig_rok.update_layout(barmode="stack", xaxis_title="Rok", yaxis_title="PLN",
                          height=360, xaxis=dict(tickmode="linear", dtick=1))
    st.plotly_chart(fig_rok, use_container_width=True)

    # ── skumulowane wpłaty ────────────────────────────────────────────────────
    st.divider()
    st.subheader("Skumulowane wpłaty w czasie")

    df_c = df.sort_values("data").copy()
    df_c["_odsetki"] = df.loc[m_odsetki,    "kwota"].reindex(df_c.index, fill_value=0)
    df_c["_kapital"] = df.loc[m_kapital_all, "kwota"].reindex(df_c.index, fill_value=0)
    df_c_agg = df_c.groupby("data")[["_odsetki","_kapital"]].sum().cumsum().reset_index()

    fig_cum = go.Figure()
    fig_cum.add_trace(go.Scatter(x=df_c_agg["data"], y=df_c_agg["_kapital"],
                                 name="Spłacony kapitał", fill="tozeroy",
                                 line=dict(color="#6366f1"), fillcolor="rgba(99,102,241,0.15)"))
    fig_cum.add_trace(go.Scatter(x=df_c_agg["data"], y=df_c_agg["_odsetki"],
                                 name="Zapłacone odsetki", fill="tozeroy",
                                 line=dict(color="#f43f5e"), fillcolor="rgba(244,63,94,0.12)"))
    fig_cum.update_layout(xaxis_title="Data", yaxis_title="PLN",
                          hovermode="x unified", height=360)
    st.plotly_chart(fig_cum, use_container_width=True)

    # ── nadpłaty per miesiąc ──────────────────────────────────────────────────
    st.divider()
    st.subheader("Nadpłaty w historii")

    nadplaty_mies = (
        df.loc[m_nadplaty].groupby("data")["kwota"].sum()
        .reset_index().rename(columns={"kwota": "Nadpłata"}).sort_values("data")
    )

    if nadplaty_mies.empty:
        st.info("Brak nadpłat w historii.")
    else:
        fig_op = px.bar(nadplaty_mies, x="data", y="Nadpłata",
                        labels={"data": "Data", "Nadpłata": "Kwota (PLN)"},
                        color_discrete_sequence=["#10b981"])
        fig_op.update_layout(xaxis_title="Data", yaxis_title="PLN", height=320, bargap=0.3)
        st.plotly_chart(fig_op, use_container_width=True)

        ndp_disp = nadplaty_mies.copy()
        ndp_disp["data"]     = ndp_disp["data"].apply(lambda d: d.strftime("%d.%m.%Y"))
        ndp_disp["Nadpłata"] = ndp_disp["Nadpłata"].apply(lambda x: f"{x:,.2f} PLN")
        st.dataframe(ndp_disp, use_container_width=True, hide_index=True)

    # ── efektywna stopa % ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("Efektywna stopa procentowa w czasie")

    df_odsetki_r = df.loc[masks["odsetki_rata"]].sort_values("data")
    saldo_po_dniu = (
        df.loc[m_kapital_all & (df["saldo"] > 0)]
        .groupby("data")["saldo"].min()
    )

    stopa_rows, prev_saldo = [], None
    for _, row in df_odsetki_r.iterrows():
        if prev_saldo and prev_saldo > 0:
            s = row["kwota"] / prev_saldo * 12 * 100
            if 0 < s < 15:
                stopa_rows.append({"data": row["data"], "stopa": round(s, 2)})
        if row["data"] in saldo_po_dniu.index:
            prev_saldo = saldo_po_dniu[row["data"]]

    if stopa_rows:
        df_stopa = pd.DataFrame(stopa_rows)
        fig_stopa = go.Figure()
        fig_stopa.add_trace(go.Scatter(
            x=df_stopa["data"], y=df_stopa["stopa"],
            mode="lines+markers", marker=dict(size=5),
            line=dict(color="#f59e0b", width=2),
        ))
        fig_stopa.update_layout(xaxis_title="Data", yaxis_title="%",
                                hovermode="x unified", height=300,
                                yaxis=dict(ticksuffix="%"))
        st.plotly_chart(fig_stopa, use_container_width=True)
        st.caption("Stopa obliczona ze wzoru: odsetki_raty / saldo_przed × 12. Widoczne zmiany WIBOR w czasie.")

    # ── surowe dane ───────────────────────────────────────────────────────────
    with st.expander("Pokaż surowe dane z pliku"):
        disp_raw = df.drop(columns=["_rok"], errors="ignore").copy()
        disp_raw["data"] = disp_raw["data"].apply(lambda d: d.strftime("%d.%m.%Y") if pd.notna(d) else "")
        st.dataframe(disp_raw, use_container_width=True, hide_index=True, height=400)
