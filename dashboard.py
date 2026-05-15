import streamlit as st
import pandas as pd
import numpy as np
import os
import re
import unicodedata
import plotly.express as px
import plotly.graph_objects as go

HISTORICO_PATH = "historico_atendimentos.parquet"

st.set_page_config(page_title="Dashboard Call Center", layout="wide")

# -------------------- Utils --------------------

def formatar_tempo(segundos):
    if pd.isna(segundos) or segundos is None:
        return "-"
    segundos = int(segundos)
    h = segundos // 3600
    m = (segundos % 3600) // 60
    s = segundos % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def duracao_para_segundos(valor):
    if pd.isna(valor):
        return np.nan
    s = str(valor).strip()
    if not s or s.lower() == "nan":
        return np.nan
    s = s.split(".")[0]
    partes = s.split(":")
    try:
        if len(partes) == 3:
            return int(partes[0]) * 3600 + int(partes[1]) * 60 + int(partes[2])
        elif len(partes) == 2:
            return int(partes[0]) * 60 + int(partes[1])
        else:
            return float(s)
    except Exception:
        return np.nan

def normalizar_id(valor):
    if pd.isna(valor):
        return np.nan
    s = str(valor).strip().lower()
    if not s or s == "nan":
        return np.nan
    match = re.search(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', s
    )
    return match.group(0) if match else np.nan

def normalizar_col(nome):
    try:
        nome = nome.encode("latin-1").decode("utf-8")
    except Exception:
        pass
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return nome.strip().lower()

MAPA_GENESYS = {
    "exportacao total concluida":  "exportacao",
    "filtros":                     "filtros",
    "data":                        "data_atendimento_raw",
    "duracao":                     "duracao_str",
    "ani":                         "ani",
    "tipo de desconexao":          "tipo_desconexao",
    "total da ura":                "total_ura_str",
    "fila total":                  "fila_total_str",
    "total de conversas":          "total_conversas_str",
    "total de tpc":                "total_tpc_str",
    "tratamento total":            "tratamento_total_str",
    "tempo para abandonar":        "tempo_abandono_str",
    "id de conversa":              "id_genesys",
}

PADRAO_AGENTE = re.compile(r"usu.{0,10}interagiram", re.IGNORECASE)

def detectar_coluna_agente(colunas):
    for col in colunas:
        if PADRAO_AGENTE.search(normalizar_col(col)):
            return col
    return None

# -------------------- Carregamento Genesys --------------------

def carregar_genesys(uploaded_file):
    try:
        df_raw = pd.read_excel(uploaded_file, engine="openpyxl", dtype=str)

        renomear = {}
        for col in df_raw.columns:
            chave = normalizar_col(col)
            if chave in MAPA_GENESYS:
                renomear[col] = MAPA_GENESYS[chave]

        col_agente = detectar_coluna_agente(df_raw.columns)
        if col_agente:
            renomear[col_agente] = "nome_agente"
        else:
            st.warning(f"Coluna de agente não encontrada. Colunas: {list(df_raw.columns)}")

        df = df_raw.rename(columns=renomear)

        if "exportacao" in df.columns:
            mask = df["exportacao"].astype(str).str.strip().str.lower().isin(["sim", "yes"])
            df = df[mask].copy()

        df = df.reset_index(drop=True)

        # Fila
        if "filtros" in df.columns:
            df["fila"] = (
                df["filtros"].astype(str)
                .str.extract(r"Fila:\s*(.+)", expand=False)
                .str.strip()
            )
        if "fila" not in df.columns:
            df["fila"] = "URA_CORSAN"
        df.loc[df["fila"].isna(), "fila"] = "URA_CORSAN"

        # Data
        if "data_atendimento_raw" in df.columns:
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento_raw"].astype(str).str.strip(),
                errors="coerce",
                dayfirst=True
            )
        else:
            df["data_atendimento"] = pd.NaT

        # Durações em segundos
        for col_str, col_s in [
            ("duracao_str",          "duracao_segundos"),
            ("total_ura_str",        "ura_segundos"),
            ("fila_total_str",       "fila_segundos"),
            ("total_conversas_str",  "conversas_segundos"),   # <- TMA real
            ("total_tpc_str",        "tpc_segundos"),
            ("tratamento_total_str", "tratamento_segundos"),
            ("tempo_abandono_str",   "abandono_segundos"),
        ]:
            if col_str in df.columns:
                df[col_s] = df[col_str].apply(duracao_para_segundos)

        # ID conversa
        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)
        else:
            df["id_genesys_norm"] = np.nan

        # ANI
        if "ani" in df.columns:
            df["ani"] = (
                df["ani"].astype(str)
                .str.replace(r"^tel:\+?", "", regex=True)
                .str.strip()
            )
            df.loc[df["ani"].str.lower() == "nan", "ani"] = np.nan

        # Nome agente
        if "nome_agente" in df.columns:
            df["nome_agente"] = df["nome_agente"].astype(str).str.strip()
            df.loc[df["nome_agente"].str.lower().isin(["nan", ""]), "nome_agente"] = np.nan
        else:
            df["nome_agente"] = np.nan

        agentes_ok = df["nome_agente"].notna().sum()
        st.info(
            f"Genesys: {len(df)} interações | "
            f"{agentes_ok} com agente | "
            f"{df['id_genesys_norm'].notna().sum()} com ID de conversa"
        )
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        import traceback; st.code(traceback.format_exc())
        return pd.DataFrame()

# -------------------- Carregamento Zendesk --------------------

def carregar_zendesk(uploaded_file):
    try:
        df = pd.read_excel(uploaded_file, engine="openpyxl", dtype=str)
        df.columns = df.columns.str.strip()

        renomear = {
            "ID do ticket":                              "ticket_id",
            "Assuntos do Ticket":                        "assunto",
            "Criação do ticket - Carimbo de data/hora":  "data_criacao_zen",
            "ID Genesys":                                "id_genesys",
            "Matricula":                                 "matricula",
            "Tickets":                                   "tickets_zen",
            "Arquivo_Origem":                            "arquivo_origem_zen",
        }
        df = df.rename(columns={k: v for k, v in renomear.items() if k in df.columns})

        if "data_criacao_zen" in df.columns:
            df["data_criacao_zen"] = pd.to_datetime(df["data_criacao_zen"], errors="coerce")

        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)

        total  = len(df)
        com_id = df["id_genesys_norm"].notna().sum() if "id_genesys_norm" in df.columns else 0
        st.info(f"Zendesk: {total} tickets | {com_id} com ID Genesys.")
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()

# -------------------- Integração --------------------

def integrar_dados(df_zen, df_gen):
    if df_gen.empty:
        st.error("Arquivo Genesys vazio após processamento.")
        return pd.DataFrame()

    df = df_gen.copy()

    if (
        not df_zen.empty
        and "id_genesys_norm" in df_zen.columns
        and "id_genesys_norm" in df.columns
        and df["id_genesys_norm"].notna().any()
    ):
        cols_zen = ["id_genesys_norm"] + [
            c for c in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]
            if c in df_zen.columns
        ]
        df_zen_slim = df_zen[cols_zen].drop_duplicates(subset=["id_genesys_norm"])
        df = pd.merge(df, df_zen_slim, on="id_genesys_norm", how="left", suffixes=("", "_zen"))

        com_assunto = df["assunto"].notna().sum() if "assunto" in df.columns else 0
        st.success(
            f"Merge: {len(df)} registros | "
            f"{com_assunto} cruzados com Zendesk ({com_assunto/len(df)*100:.1f}%)"
        )
    else:
        st.warning("Zendesk não carregado ou sem ID para cruzar.")
        df["ticket_id"] = np.nan
        df["assunto"]   = np.nan
        df["matricula"] = np.nan

    df["data_base"] = pd.to_datetime(
        df["data_atendimento"].dt.date if "data_atendimento" in df.columns else pd.NaT,
        errors="coerce"
    )
    df["mes"] = df["data_base"].dt.to_period("M").astype(str)

    return df

# -------------------- Histórico --------------------

def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            df = pd.read_parquet(HISTORICO_PATH)
            if "data_base" in df.columns:
                df["data_base"] = pd.to_datetime(df["data_base"], errors="coerce")
            return df
        except Exception as e:
            st.warning(f"Erro ao carregar histórico: {e}")
    return pd.DataFrame()

def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo
    chave = "id_genesys_norm"
    if chave in df_novo.columns and chave in df_hist.columns:
        ids_existentes = set(df_hist[chave].dropna().unique())
        df_filtrado = df_novo[~df_novo[chave].isin(ids_existentes)]
        duplicatas = len(df_novo) - len(df_filtrado)
        if duplicatas:
            st.info(f"{duplicatas} registro(s) duplicado(s) ignorado(s).")
        return pd.concat([df_hist, df_filtrado], ignore_index=True)
    return pd.concat([df_hist, df_novo], ignore_index=True)

def salvar_historico(df):
    try:
        df.to_parquet(HISTORICO_PATH, index=False)
        return True
    except Exception as e:
        st.error(f"Erro ao salvar histórico: {e}")
        return False

# -------------------- Filtros --------------------

def aplicar_filtros(df):
    st.sidebar.header("Filtros")

    df_f = df.copy()

    # Período
    if "data_base" in df_f.columns and df_f["data_base"].notna().any():
        datas_validas = df_f["data_base"].dropna()
        d_min = datas_validas.min().date()
        d_max = datas_validas.max().date()
        intervalo = st.sidebar.date_input("Período", value=(d_min, d_max))
        if isinstance(intervalo, (list, tuple)) and len(intervalo) == 2:
            d_ini, d_fim = intervalo
            df_f = df_f[
                (df_f["data_base"].dt.date >= d_ini) &
                (df_f["data_base"].dt.date <= d_fim)
            ]

    # Agente
    if "nome_agente" in df_f.columns:
        agentes = sorted(df_f["nome_agente"].dropna().unique())
        sel_ag = st.sidebar.multiselect("Agente(s)", agentes)
        if sel_ag:
            df_f = df_f[df_f["nome_agente"].isin(sel_ag)]

    # Fila
    if "fila" in df_f.columns:
        filas = sorted(df_f["fila"].dropna().unique())
        sel_fila = st.sidebar.multiselect("Fila(s)", filas)
        if sel_fila:
            df_f = df_f[df_f["fila"].isin(sel_fila)]

    # Tipo de desconexão
    if "tipo_desconexao" in df_f.columns:
        tipos = sorted(df_f["tipo_desconexao"].dropna().unique())
        sel_tipo = st.sidebar.multiselect("Tipo de desconexão", tipos)
        if sel_tipo:
            df_f = df_f[df_f["tipo_desconexao"].isin(sel_tipo)]

    # Assunto
    if "assunto" in df_f.columns and df_f["assunto"].notna().any():
        assuntos = sorted(df_f["assunto"].dropna().unique())
        sel_ass = st.sidebar.multiselect("Assunto(s)", assuntos)
        if sel_ass:
            df_f = df_f[df_f["assunto"].isin(sel_ass)]

    st.sidebar.markdown(f"Registros no filtro: **{len(df_f)}**")
    return df_f

# -------------------- Seções --------------------

def secao_visao_geral(df):
    st.subheader("Visão Geral")

    # Métricas principais
    # TMA = média de conversas_segundos (tempo real de conversa com o cliente)
    col_tma = "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"

    total    = len(df)
    tma      = df[col_tma].mean() if col_tma in df.columns else None
    horas    = df["duracao_segundos"].sum() / 3600 if "duracao_segundos" in df.columns else 0
    n_agentes = df["nome_agente"].nunique() if "nome_agente" in df.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total de atendimentos", total)
    col2.metric("TMA (tempo de conversa)", formatar_tempo(tma))
    col3.metric("Horas em atendimento", f"{horas:.1f} h")
    col4.metric("Agentes ativos", n_agentes)

    # Tempos médios detalhados
    cols_tempo = {
        "ura_segundos":        "Média URA",
        "fila_segundos":       "Média Fila",
        "conversas_segundos":  "Média Conversa (TMA)",
        "tratamento_segundos": "Média Tratamento",
        "abandono_segundos":   "Média Abandono",
    }
    disponiveis = [(label, col) for col, label in cols_tempo.items() if col in df.columns]
    if disponiveis:
        st.markdown("**Tempos médios detalhados**")
        cols_m = st.columns(len(disponiveis))
        for i, (label, col) in enumerate(disponiveis):
            cols_m[i].metric(label, formatar_tempo(df[col].mean()))

    # Tipo de desconexão
    if "tipo_desconexao" in df.columns and df["tipo_desconexao"].notna().any():
        st.markdown("**Distribuição por tipo de desconexão**")
        dist = df["tipo_desconexao"].value_counts().reset_index()
        dist.columns = ["Tipo", "Qtd"]
        fig = px.bar(
            dist, x="Tipo", y="Qtd",
            text="Qtd",
            labels={"Qtd": "Atendimentos"}
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(uniformtext_minsize=10, uniformtext_mode="hide")
        st.plotly_chart(fig, use_container_width=True)

    # Atendimentos por dia com valores nas barras
    if "data_base" in df.columns and df["data_base"].notna().any():
        df_dia = (
            df.set_index("data_base")
            .resample("D")
            .size()
            .reset_index(name="Atendimentos")
        )
        st.markdown("**Atendimentos por dia**")
        fig2 = px.bar(
            df_dia,
            x="data_base",
            y="Atendimentos",
            text="Atendimentos",
            labels={"data_base": "Data"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(uniformtext_minsize=9, uniformtext_mode="hide")
        st.plotly_chart(fig2, use_container_width=True)


def secao_por_agente(df):
    st.subheader("Análise por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.warning("Nenhum agente identificado nos dados.")
        return

    col_tma = "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"

    agg_dict = dict(
        atendimentos=(col_tma, "count"),
        tma_s=(col_tma, "mean"),
        tempo_total_s=("duracao_segundos", "sum"),
    )
    for col, alias in [("tratamento_segundos", "trat_s"), ("fila_segundos", "fila_s")]:
        if col in df.columns:
            agg_dict[alias] = (col, "mean")

    df_ag = (
        df[df["nome_agente"].notna()]
        .groupby("nome_agente")
        .agg(**agg_dict)
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )

    df_ag["TMA"]         = df_ag["tma_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["tempo_total_s"].apply(formatar_tempo)
    if "trat_s" in df_ag.columns:
        df_ag["Trat. Médio"] = df_ag["trat_s"].apply(formatar_tempo)
    if "fila_s" in df_ag.columns:
        df_ag["Fila Média"]  = df_ag["fila_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por agente**")
        fig = px.bar(
            df_ag, x="nome_agente", y="atendimentos",
            text="atendimentos",
            labels={"nome_agente": "Agente", "atendimentos": "Atendimentos"}
        )
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**TMA por agente**")
        fig2 = px.bar(
            df_ag, x="nome_agente", y="tma_s",
            text=df_ag["TMA"],
            labels={"nome_agente": "Agente", "tma_s": "TMA (s)"}
        )
        fig2.update_traces(textposition="outside")
        st.plotly_chart(fig2, use_container_width=True)

    colunas_tabela = ["nome_agente", "atendimentos", "TMA", "Tempo Total"]
    for c in ["Trat. Médio", "Fila Média"]:
        if c in df_ag.columns:
            colunas_tabela.append(c)
    st.dataframe(df_ag[colunas_tabela], use_container_width=True)


def secao_detalhe_agente(df):
    st.subheader("Detalhe por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.warning("Nenhum agente identificado nos dados.")
        return

    col_tma = "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"

    agentes    = sorted(df["nome_agente"].dropna().unique().tolist())
    agente_sel = st.selectbox("Selecione o agente", ["(Selecione)"] + agentes)

    if agente_sel == "(Selecione)":
        st.info(f"{len(agentes)} agente(s) disponíveis: {', '.join(agentes)}")
        return

    df_ag = df[df["nome_agente"] == agente_sel].copy()
    if df_ag.empty:
        st.info("Nenhum atendimento para este agente no filtro atual.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Atendimentos", len(df_ag))
    col2.metric("TMA (conversa)", formatar_tempo(df_ag[col_tma].mean()))
    col3.metric("Horas em atendimento", f"{df_ag['duracao_segundos'].sum() / 3600:.1f} h")

    cols_tempo = {
        "ura_segundos":        "Média URA",
        "fila_segundos":       "Média Fila",
        "conversas_segundos":  "TMA (conversa)",
        "tratamento_segundos": "Média Tratamento",
        "abandono_segundos":   "Média Abandono",
    }
    disponiveis = [(label, col) for col, label in cols_tempo.items() if col in df_ag.columns]
    if disponiveis:
        cols_m = st.columns(len(disponiveis))
        for i, (label, col) in enumerate(disponiveis):
            cols_m[i].metric(label, formatar_tempo(df_ag[col].mean()))

    if "data_base" in df_ag.columns and df_ag["data_base"].notna().any():
        df_dia = (
            df_ag.set_index("data_base")
            .resample("D")
            .size()
            .reset_index(name="Atendimentos")
        )
        st.markdown("**Atendimentos por dia**")
        fig = px.bar(
            df_dia, x="data_base", y="Atendimentos",
            text="Atendimentos",
            labels={"data_base": "Data"}
        )
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Atendimentos detalhados**")
    cols_det = [
        "data_atendimento", "fila", "ani", "tipo_desconexao",
        "duracao_str", "total_ura_str", "fila_total_str",
        "total_conversas_str", "tratamento_total_str", "tempo_abandono_str",
        "assunto", "ticket_id", "id_genesys",
    ]
    cols_det = [c for c in cols_det if c in df_ag.columns]
    st.dataframe(
        df_ag[cols_det].sort_values("data_atendimento", ascending=False),
        use_container_width=True
    )


def secao_por_assunto(df):
    st.subheader("Análise por assunto")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda não há assuntos cruzados com o Zendesk.")
        return

    col_tma = "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"

    df_val = df[df["assunto"].notna()]
    df_ass = (
        df_val.groupby("assunto")
        .agg(
            atendimentos=(col_tma, "count"),
            tma_s=(col_tma, "mean"),
            tempo_total_s=("duracao_segundos", "sum"),
        )
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )
    df_ass["TMA"]         = df_ass["tma_s"].apply(formatar_tempo)
    df_ass["Tempo Total"] = df_ass["tempo_total_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por assunto**")
        fig = px.bar(df_ass, x="assunto", y="atendimentos", text="atendimentos")
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.markdown("**TMA por assunto**")
        fig2 = px.bar(df_ass, x="assunto", y="tma_s", text=df_ass["TMA"])
        fig2.update_traces(textposition="outside")
        st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]], use_container_width=True)


def secao_top_assuntos_tma(df):
    st.subheader("Top 10 assuntos por TMA — por mês")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda não há assuntos cruzados com o Zendesk.")
        return

    if "mes" not in df.columns or df["mes"].isna().all():
        st.info("Coluna de mês não disponível.")
        return

    col_tma = "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"

    meses_disponiveis = sorted(df["mes"].dropna().unique().tolist())
    mes_sel = st.selectbox("Selecione o mês", meses_disponiveis)

    df_mes = df[(df["mes"] == mes_sel) & df["assunto"].notna()]

    if df_mes.empty:
        st.info("Sem dados para este mês.")
        return

    df_top = (
        df_mes.groupby("assunto")
        .agg(
            atendimentos=(col_tma, "count"),
            tma_s=(col_tma, "mean"),
        )
        .reset_index()
        .sort_values("tma_s", ascending=False)
        .head(10)
    )
    df_top["TMA"] = df_top["tma_s"].apply(formatar_tempo)

    st.markdown(f"**Top 10 assuntos com maior TMA em {mes_sel}**")

    fig = px.bar(
        df_top.sort_values("tma_s", ascending=True),
        x="tma_s",
        y="assunto",
        orientation="h",
        text="TMA",
        labels={"tma_s": "TMA (s)", "assunto": "Assunto"},
        color="tma_s",
        color_continuous_scale="Reds",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        coloraxis_showscale=False,
        yaxis={"categoryorder": "total ascending"}
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        df_top[["assunto", "atendimentos", "TMA"]].reset_index(drop=True),
        use_container_width=True
    )

    # Visão comparativa: todos os meses lado a lado
    if len(meses_disponiveis) > 1:
        st.markdown("**Comparativo entre meses — top assuntos por TMA**")

        df_todos = (
            df[df["assunto"].notna()]
            .groupby(["mes", "assunto"])
            .agg(tma_s=(col_tma, "mean"))
            .reset_index()
        )

        # Para cada mês pega o top 10 e une
        tops = []
        for m in meses_disponiveis:
            bloco = (
                df_todos[df_todos["mes"] == m]
                .sort_values("tma_s", ascending=False)
                .head(10)
            )
            tops.append(bloco)
        df_comp = pd.concat(tops, ignore_index=True)
        df_comp["TMA"] = df_comp["tma_s"].apply(formatar_tempo)

        fig2 = px.bar(
            df_comp,
            x="assunto",
            y="tma_s",
            color="mes",
            barmode="group",
            text="TMA",
            labels={"tma_s": "TMA (s)", "assunto": "Assunto", "mes": "Mês"},
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True)


# -------------------- Upload & main --------------------

def secao_upload():
    st.sidebar.header("Upload mensal")

    arq_zen = st.sidebar.file_uploader("Zendesk (XLSX)", type=["xlsx", "xls"])
    arq_gen = st.sidebar.file_uploader("Genesys (XLSX)", type=["xlsx", "xls"])

    if arq_gen is not None:
        if st.sidebar.button("Processar e acumular"):
            df_zen = carregar_zendesk(arq_zen) if arq_zen else pd.DataFrame()
            df_gen = carregar_genesys(arq_gen)
            df_novo = integrar_dados(df_zen, df_gen)

            if df_novo.empty:
                st.sidebar.error("Nenhum dado gerado.")
                return

            df_hist = carregar_historico()
            df_acum = adicionar_ao_historico(df_novo, df_hist)
            if salvar_historico(df_acum):
                st.sidebar.success(
                    f"Dados acumulados. Total: {len(df_acum)} registros."
                )
                st.rerun()

    with st.sidebar.expander("Gerenciar histórico"):
        if st.button("Apagar histórico"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                st.success("Histórico apagado.")
                st.rerun()


def main():
    st.title("Dashboard de Atendimentos – Call Center")

    secao_upload()
    df_hist = carregar_historico()

    if df_hist.empty:
        st.info("Faça o upload do arquivo Genesys (XLSX) para começar.")
        return

    df_filtrado = aplicar_filtros(df_hist)
    if df_filtrado.empty:
        st.warning("Nenhum registro para os filtros atuais.")
        return

    aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "Visão geral",
        "Por agente",
        "Detalhe do agente",
        "Por assunto",
        "Top TMA por mês",
    ])
    with aba1: secao_visao_geral(df_filtrado)
    with aba2: secao_por_agente(df_filtrado)
    with aba3: secao_detalhe_agente(df_filtrado)
    with aba4: secao_por_assunto(df_filtrado)
    with aba5: secao_top_assuntos_tma(df_filtrado)


if __name__ == "__main__":
    main()
