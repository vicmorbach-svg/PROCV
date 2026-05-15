import streamlit as st
import pandas as pd
import numpy as np
import os
import re
import unicodedata
import gc
import plotly.express as px

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

# -------------------- Mapa Genesys --------------------

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

# -------------------- Carregamento com cache --------------------

@st.cache_data(show_spinner="Carregando Genesys...", max_entries=3)
def carregar_genesys(file_bytes: bytes, file_name: str):
    try:
        import io
        df_raw = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl", dtype=str)

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
        del df_raw
        gc.collect()

        # Filtra exportações concluídas
        if "exportacao" in df.columns:
            mask = df["exportacao"].astype(str).str.strip().str.lower().isin(["sim", "yes"])
            df = df[mask].reset_index(drop=True)

        # Fila
        if "filtros" in df.columns:
            df["fila"] = (
                df["filtros"].astype(str)
                .str.extract(r"Fila:\s*(.+)", expand=False)
                .str.strip()
            )
        if "fila" not in df.columns:
            df["fila"] = "URA_CORSAN"
        df["fila"] = df["fila"].fillna("URA_CORSAN")

        # Data
        if "data_atendimento_raw" in df.columns:
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento_raw"].astype(str).str.strip(),
                errors="coerce", dayfirst=True
            )
        else:
            df["data_atendimento"] = pd.NaT

        # Durações
        for col_str, col_s in [
            ("duracao_str",          "duracao_segundos"),
            ("total_ura_str",        "ura_segundos"),
            ("fila_total_str",       "fila_segundos"),
            ("total_conversas_str",  "conversas_segundos"),
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

        # Otimiza tipos
        for col in df.select_dtypes(include="object").columns:
            if df[col].nunique() < 200:
                df[col] = df[col].astype("category")

        st.info(
            f"Genesys: {len(df)} interações | "
            f"{df['nome_agente'].notna().sum()} com agente | "
            f"{df['id_genesys_norm'].notna().sum()} com ID de conversa"
        )
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        import traceback; st.code(traceback.format_exc())
        return pd.DataFrame()


@st.cache_data(show_spinner="Carregando Zendesk...", max_entries=3)
def carregar_zendesk(file_bytes: bytes, file_name: str):
    try:
        import io
        df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl", dtype=str)
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

        for col in df.select_dtypes(include="object").columns:
            if df[col].nunique() < 200:
                df[col] = df[col].astype("category")

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

    tem_zen = (
        not df_zen.empty
        and "id_genesys_norm" in df_zen.columns
        and "id_genesys_norm" in df.columns
        and df["id_genesys_norm"].notna().any()
    )

    if tem_zen:
        cols_zen = ["id_genesys_norm"] + [
            c for c in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]
            if c in df_zen.columns
        ]
        df_zen_slim = (
            df_zen[cols_zen]
            .drop_duplicates(subset=["id_genesys_norm"])
            .copy()
        )
        # Garante que categoricals não quebrem o merge
        for col in df_zen_slim.select_dtypes(include="category").columns:
            df_zen_slim[col] = df_zen_slim[col].astype(str)
        for col in df.select_dtypes(include="category").columns:
            df[col] = df[col].astype(str)

        df = pd.merge(df, df_zen_slim, on="id_genesys_norm", how="left", suffixes=("", "_zen"))
        del df_zen_slim
        gc.collect()

        com_assunto = df["assunto"].notna().sum() if "assunto" in df.columns else 0
        st.success(
            f"Merge: {len(df)} registros | "
            f"{com_assunto} cruzados com Zendesk ({com_assunto/len(df)*100:.1f}%)"
        )
    else:
        st.warning("Zendesk não carregado ou sem ID para cruzar.")
        df["ticket_id"]      = np.nan
        df["assunto"]        = np.nan
        df["matricula"]      = np.nan
        df["data_criacao_zen"] = pd.NaT

    # data_base vem da criação do ticket no Zendesk quando disponível,
    # caso contrário usa a data do atendimento no Genesys
    if "data_criacao_zen" in df.columns and df["data_criacao_zen"].notna().any():
        df["data_base"] = pd.to_datetime(df["data_criacao_zen"], errors="coerce").dt.normalize()
    else:
        df["data_base"] = pd.to_datetime(
            df["data_atendimento"].dt.date if "data_atendimento" in df.columns else pd.NaT,
            errors="coerce"
        )

    # Mês vem da data_criacao_zen (ticket Zendesk) para a aba de top TMA
    if "data_criacao_zen" in df.columns and df["data_criacao_zen"].notna().any():
        df["mes"] = pd.to_datetime(df["data_criacao_zen"], errors="coerce").dt.to_period("M").astype(str)
    else:
        df["mes"] = df["data_base"].dt.to_period("M").astype(str)

    return df

# -------------------- Histórico --------------------

@st.cache_data(show_spinner="Lendo histórico...", ttl=60)
def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            df = pd.read_parquet(HISTORICO_PATH)
            for col in ["data_base", "data_atendimento", "data_criacao_zen"]:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
            return df
        except Exception as e:
            st.error(f"Erro ao ler histórico: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo.copy()
    chave = "id_genesys_norm"
    if chave in df_novo.columns and chave in df_hist.columns:
        ids_existentes = set(df_hist[chave].dropna().unique())
        df_filtrado = df_novo[~df_novo[chave].isin(ids_existentes)].copy()
        resultado = pd.concat([df_hist, df_filtrado], ignore_index=True)
        del df_filtrado
        gc.collect()
        return resultado
    return pd.concat([df_hist, df_novo], ignore_index=True)

def salvar_historico(df):
    try:
        # Converte categoricals antes de salvar
        df_save = df.copy()
        for col in df_save.select_dtypes(include="category").columns:
            df_save[col] = df_save[col].astype(str)
        df_save.to_parquet(HISTORICO_PATH, index=False)
        del df_save
        gc.collect()
        # Invalida cache do histórico
        carregar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar histórico: {e}")
        return False

# -------------------- Filtros --------------------

def aplicar_filtros(df):
    st.sidebar.markdown("---")
    st.sidebar.header("Filtros")

    # Período
    if "data_base" in df.columns and df["data_base"].notna().any():
        datas_validas = df["data_base"].dropna()
        data_min = datas_validas.min().date()
        data_max = datas_validas.max().date()
        col1, col2 = st.sidebar.columns(2)
        d_ini = col1.date_input("De", value=data_min, min_value=data_min, max_value=data_max)
        d_fim = col2.date_input("Até", value=data_max, min_value=data_min, max_value=data_max)
        df = df[
            (df["data_base"] >= pd.Timestamp(d_ini)) &
            (df["data_base"] <= pd.Timestamp(d_fim))
        ]

    # Fila
    if "fila" in df.columns:
        filas = sorted(df["fila"].astype(str).dropna().unique())
        sel_filas = st.sidebar.multiselect("Fila", filas, default=filas)
        if sel_filas:
            df = df[df["fila"].astype(str).isin(sel_filas)]

    # Agente
    if "nome_agente" in df.columns:
        agentes = sorted(df["nome_agente"].astype(str).dropna().unique())
        sel_agentes = st.sidebar.multiselect("Agente", agentes)
        if sel_agentes:
            df = df[df["nome_agente"].astype(str).isin(sel_agentes)]

    # Tipo de desconexão
    if "tipo_desconexao" in df.columns:
        tipos = sorted(df["tipo_desconexao"].astype(str).dropna().unique())
        sel_tipos = st.sidebar.multiselect("Tipo de desconexão", tipos)
        if sel_tipos:
            df = df[df["tipo_desconexao"].astype(str).isin(sel_tipos)]

    return df

# -------------------- Seções do dashboard --------------------

def _col_tma(df):
    """Retorna a coluna preferida para TMA."""
    return "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"


def secao_visao_geral(df):
    st.subheader("Visão geral")

    col_tma = _col_tma(df)
    total   = len(df)
    tma     = df[col_tma].mean()
    horas   = df["duracao_segundos"].sum() / 3600 if "duracao_segundos" in df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de atendimentos", total)
    c2.metric("TMA geral (conversa)", formatar_tempo(tma))
    c3.metric("Horas totais", f"{horas:.1f} h")
    c4.metric("Agentes únicos", int(df["nome_agente"].nunique()) if "nome_agente" in df.columns else "-")

    # Atendimentos por dia com valores nas barras
    if "data_base" in df.columns and df["data_base"].notna().any():
        df_dia = (
            df.set_index("data_base")
            .resample("D")
            .size()
            .reset_index(name="Atendimentos")
        )
        fig = px.bar(
            df_dia, x="data_base", y="Atendimentos",
            text="Atendimentos",
            labels={"data_base": "Data"},
            title="Atendimentos por dia"
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(uniformtext_minsize=8, uniformtext_mode="hide")
        st.plotly_chart(fig, use_container_width=True)

    # TMA por dia com valores
    if "data_base" in df.columns and df[col_tma].notna().any():
        df_tma_dia = (
            df.groupby("data_base")[col_tma]
            .mean()
            .reset_index(name="tma_s")
        )
        df_tma_dia["TMA"] = df_tma_dia["tma_s"].apply(formatar_tempo)
        fig2 = px.line(
            df_tma_dia, x="data_base", y="tma_s",
            text="TMA",
            labels={"data_base": "Data", "tma_s": "TMA (s)"},
            title="TMA por dia (tempo de conversa)"
        )
        fig2.update_traces(textposition="top center", mode="lines+markers+text")
        st.plotly_chart(fig2, use_container_width=True)


def secao_por_agente(df):
    st.subheader("Análise por agente")

    if "nome_agente" not in df.columns:
        st.info("Não há coluna de agente nos dados.")
        return

    col_tma = _col_tma(df)

    agg_dict = {
        "atendimentos": (col_tma, "count"),
        "tma_s":        (col_tma, "mean"),
        "tempo_total_s":("duracao_segundos", "sum"),
    }
    if "tratamento_segundos" in df.columns:
        agg_dict["trat_s"] = ("tratamento_segundos", "mean")
    if "fila_segundos" in df.columns:
        agg_dict["fila_s"] = ("fila_segundos", "mean")

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
        fig = px.bar(
            df_ag, x="nome_agente", y="atendimentos",
            text="atendimentos", title="Volume por agente"
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.bar(
            df_ag, x="nome_agente", y="tma_s",
            text="TMA", title="TMA por agente (conversa)"
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
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

    col_tma = _col_tma(df)
    agentes = sorted(df["nome_agente"].dropna().astype(str).unique().tolist())
    agente_sel = st.selectbox("Selecione o agente", ["(Selecione)"] + agentes)

    if agente_sel == "(Selecione)":
        st.info(f"{len(agentes)} agente(s): {', '.join(agentes)}")
        return

    df_ag = df[df["nome_agente"].astype(str) == agente_sel].copy()
    if df_ag.empty:
        st.info("Nenhum atendimento para este agente no filtro atual.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Atendimentos", len(df_ag))
    c2.metric("TMA (conversa)", formatar_tempo(df_ag[col_tma].mean()))
    c3.metric("Horas em atendimento", f"{df_ag['duracao_segundos'].sum() / 3600:.1f} h")

    cols_tempo = [
        ("ura_segundos",        "Média URA"),
        ("fila_segundos",       "Média Fila"),
        ("conversas_segundos",  "TMA Conversa"),
        ("tratamento_segundos", "Média Tratamento"),
        ("abandono_segundos",   "Média Abandono"),
    ]
    disponiveis = [(label, col) for col, label in cols_tempo if col in df_ag.columns]
    if disponiveis:
        cols_m = st.columns(len(disponiveis))
        for i, (label, col) in enumerate(disponiveis):
            cols_m[i].metric(label, formatar_tempo(df_ag[col].mean()))

    if "data_base" in df_ag.columns and df_ag["data_base"].notna().any():
        df_dia = (
            df_ag.set_index("data_base")
            .resample("D").size()
            .reset_index(name="Atendimentos")
        )
        fig = px.bar(
            df_dia, x="data_base", y="Atendimentos",
            text="Atendimentos", title="Atendimentos por dia"
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

    col_tma = _col_tma(df)
    df_val  = df[df["assunto"].notna()]
    df_ass  = (
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
        fig = px.bar(df_ass, x="assunto", y="atendimentos", text="atendimentos", title="Volume por assunto")
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.bar(df_ass, x="assunto", y="tma_s", text="TMA", title="TMA por assunto")
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
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

    col_tma = _col_tma(df)

    # Mês derivado de data_criacao_zen (ticket Zendesk)
    meses = sorted(df["mes"].dropna().astype(str).unique().tolist())
    mes_sel = st.selectbox("Selecione o mês", meses)

    df_mes = df[(df["mes"].astype(str) == mes_sel) & df["assunto"].notna()].copy()
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

    fig = px.bar(
        df_top.sort_values("tma_s", ascending=True),
        x="tma_s", y="assunto",
        orientation="h",
        text="TMA",
        labels={"tma_s": "TMA (s)", "assunto": "Assunto"},
        color="tma_s",
        color_continuous_scale="Reds",
        title=f"Top 10 assuntos com maior TMA — {mes_sel}"
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(coloraxis_showscale=False, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(df_top[["assunto", "atendimentos", "TMA"]].reset_index(drop=True), use_container_width=True)

    # Comparativo entre meses (aparece só quando há mais de um mês)
    if len(meses) > 1:
        st.markdown("**Comparativo entre meses**")
        df_todos = (
            df[df["assunto"].notna()]
            .groupby(["mes", "assunto"])
            .agg(tma_s=(col_tma, "mean"))
            .reset_index()
        )
        tops = []
        for m in meses:
            bloco = (
                df_todos[df_todos["mes"].astype(str) == m]
                .sort_values("tma_s", ascending=False)
                .head(10)
            )
            tops.append(bloco)
        df_comp = pd.concat(tops, ignore_index=True)
        df_comp["TMA"] = df_comp["tma_s"].apply(formatar_tempo)

        fig2 = px.bar(
            df_comp, x="assunto", y="tma_s", color="mes",
            barmode="group", text="TMA",
            labels={"tma_s": "TMA (s)", "assunto": "Assunto", "mes": "Mês"},
            title="TMA por assunto — comparativo entre meses"
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
            # Passa bytes para aproveitar o cache por conteúdo
            df_zen = carregar_zendesk(arq_zen.read(), arq_zen.name) if arq_zen else pd.DataFrame()
            df_gen = carregar_genesys(arq_gen.read(), arq_gen.name)
            df_novo = integrar_dados(df_zen, df_gen)

            if df_novo.empty:
                st.sidebar.error("Nenhum dado gerado.")
                return

            df_hist = carregar_historico()
            df_acum = adicionar_ao_historico(df_novo, df_hist)
            if salvar_historico(df_acum):
                st.sidebar.success(f"Dados acumulados. Total: {len(df_acum)} registros.")
                st.rerun()

    with st.sidebar.expander("Gerenciar histórico"):
        if st.button("Apagar histórico"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                carregar_historico.clear()
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
