import streamlit as st
import pandas as pd
import numpy as np
import os
import re
import unicodedata
import gc
import plotly.express as px
from io import BytesIO

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

def _col_tma(df):
    return "conversas_segundos" if "conversas_segundos" in df.columns else "duracao_segundos"

# -------------------- Mapa Genesys --------------------

MAPA_GENESYS = {
    "exportacao total concluida": "exportacao",
    "filtros":                    "filtros",
    "data":                       "data_atendimento_raw",
    "duracao":                    "duracao_str",
    "ani":                        "ani",
    "tipo de desconexao":         "tipo_desconexao",
    "total da ura":               "total_ura_str",
    "fila total":                 "fila_total_str",
    "total de conversas":         "total_conversas_str",
    "total de tpc":               "total_tpc_str",
    "tratamento total":           "tratamento_total_str",
    "tempo para abandonar":       "tempo_abandono_str",
    "id de conversa":             "id_genesys",
}

PADRAO_AGENTE = re.compile(r"usu.{0,10}interagiram", re.IGNORECASE)

def detectar_coluna_agente(colunas):
    for col in colunas:
        if PADRAO_AGENTE.search(normalizar_col(col)):
            return col
    return None

# -------------------- Carregamento --------------------

@st.cache_data(show_spinner="Carregando Genesys...", max_entries=3)
def carregar_genesys(file_bytes: bytes, file_name: str):
    try:
        df_raw = pd.read_excel(BytesIO(file_bytes), engine="openpyxl", dtype=str)

        renomear = {}
        for col in df_raw.columns:
            chave = normalizar_col(col)
            if chave in MAPA_GENESYS:
                renomear[col] = MAPA_GENESYS[chave]

        col_agente = detectar_coluna_agente(df_raw.columns)
        if col_agente:
            renomear[col_agente] = "nome_agente"
        else:
            st.warning(f"Coluna de agente nao encontrada. Colunas: {list(df_raw.columns)}")

        df = df_raw.rename(columns=renomear)
        del df_raw
        gc.collect()

        if "exportacao" in df.columns:
            mask = df["exportacao"].astype(str).str.strip().str.lower().isin(["sim", "yes"])
            df = df[mask].reset_index(drop=True)

        if "filtros" in df.columns:
            df["fila"] = (
                df["filtros"].astype(str)
                .str.extract(r"Fila:\s*(.+)", expand=False)
                .str.strip()
            )
        if "fila" not in df.columns:
            df["fila"] = "URA_CORSAN"
        df["fila"] = df["fila"].fillna("URA_CORSAN")

        if "data_atendimento_raw" in df.columns:
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento_raw"].astype(str).str.strip(),
                errors="coerce", dayfirst=True
            )
        else:
            df["data_atendimento"] = pd.NaT

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

        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)
        else:
            df["id_genesys_norm"] = np.nan

        if "ani" in df.columns:
            df["ani"] = (
                df["ani"].astype(str)
                .str.replace(r"^tel:\+?", "", regex=True)
                .str.strip()
            )

        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        return pd.DataFrame()


@st.cache_data(show_spinner="Carregando Zendesk...", max_entries=3)
def carregar_zendesk(file_bytes: bytes, file_name: str):
    try:
        df = pd.read_excel(BytesIO(file_bytes), engine="openpyxl", dtype=str)
        df.columns = df.columns.str.strip()

        renomear = {
            "ID do ticket":                              "ticket_id",
            "Assuntos do Ticket":                        "assunto",
            "Criacao do ticket - Carimbo de data/hora":  "data_criacao_zen",
            "Criação do ticket - Carimbo de data/hora":  "data_criacao_zen",
            "ID Genesys":                                "id_genesys",
            "Matricula":                                 "matricula",
            "Tickets":                                   "tickets_zen",
        }
        df = df.rename(columns={k: v for k, v in renomear.items() if k in df.columns})

        if "data_criacao_zen" in df.columns:
            df["data_criacao_zen"] = pd.to_datetime(df["data_criacao_zen"], errors="coerce")

        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)

        total = len(df)
        com_id = df["id_genesys_norm"].notna().sum() if "id_genesys_norm" in df.columns else 0
        st.info(f"Zendesk: {total} tickets, {com_id} com ID Genesys.")
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()


def integrar_dados(df_zen, df_gen):
    if df_gen.empty:
        st.error("Genesys sem dados.")
        return pd.DataFrame()

    if not df_zen.empty and "id_genesys_norm" in df_zen.columns and "id_genesys_norm" in df_gen.columns:
        colunas_zen = ["id_genesys_norm", "assunto", "ticket_id", "data_criacao_zen", "matricula"]
        colunas_zen = [c for c in colunas_zen if c in df_zen.columns]
        df_zen_slim = df_zen[colunas_zen].drop_duplicates(subset=["id_genesys_norm"])
        df = df_gen.merge(df_zen_slim, on="id_genesys_norm", how="left")
        del df_zen_slim
        gc.collect()
        casados = df["assunto"].notna().sum() if "assunto" in df.columns else 0
        st.info(f"Cruzamento: {casados} de {len(df)} registros com assunto do Zendesk.")
    else:
        df = df_gen.copy()
        if "assunto" not in df.columns:
            df["assunto"] = np.nan

    if "data_criacao_zen" in df.columns and "data_atendimento" in df.columns:
        df["mes"] = df["data_criacao_zen"].fillna(df["data_atendimento"]).dt.to_period("M").astype(str)
    elif "data_atendimento" in df.columns:
        df["mes"] = df["data_atendimento"].dt.to_period("M").astype(str)
    else:
        df["mes"] = "Desconhecido"

    return df


@st.cache_data(show_spinner="Carregando historico...", ttl=60)
def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            return pd.read_parquet(HISTORICO_PATH)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo
    if "id_genesys_norm" in df_novo.columns and "id_genesys_norm" in df_hist.columns:
        ids_existentes = set(df_hist["id_genesys_norm"].dropna().unique())
        df_filtrado = df_novo[~df_novo["id_genesys_norm"].isin(ids_existentes)]
        novos = len(df_filtrado)
        st.info(f"Novos registros adicionados: {novos} (duplicatas ignoradas: {len(df_novo) - novos})")
        return pd.concat([df_hist, df_filtrado], ignore_index=True)
    return pd.concat([df_hist, df_novo], ignore_index=True)


def salvar_historico(df):
    try:
        df.to_parquet(HISTORICO_PATH, index=False)
        carregar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar historico: {e}")
        return False


# -------------------- Filtros --------------------

def aplicar_filtros(df):
    st.sidebar.header("Filtros")

    if "data_atendimento" in df.columns:
        datas_validas = df["data_atendimento"].dropna()
        if not datas_validas.empty:
            data_min = datas_validas.min().date()
            data_max = datas_validas.max().date()
            col1, col2 = st.sidebar.columns(2)
            with col1:
                data_ini = st.date_input("De", value=data_min, min_value=data_min, max_value=data_max)
            with col2:
                data_fim = st.date_input("Ate", value=data_max, min_value=data_min, max_value=data_max)
            df = df[
                (df["data_atendimento"].dt.date >= data_ini) &
                (df["data_atendimento"].dt.date <= data_fim)
            ]

    if "nome_agente" in df.columns:
        agentes = sorted(df["nome_agente"].dropna().unique().tolist())
        sel = st.sidebar.multiselect("Agente", agentes)
        if sel:
            df = df[df["nome_agente"].isin(sel)]

    if "fila" in df.columns:
        filas = sorted(df["fila"].dropna().unique().tolist())
        sel_fila = st.sidebar.multiselect("Fila", filas)
        if sel_fila:
            df = df[df["fila"].isin(sel_fila)]

    if "tipo_desconexao" in df.columns:
        tipos = sorted(df["tipo_desconexao"].dropna().unique().tolist())
        sel_tipo = st.sidebar.multiselect("Tipo de desconexao", tipos)
        if sel_tipo:
            df = df[df["tipo_desconexao"].isin(sel_tipo)]

    return df


# -------------------- Visao Geral --------------------

def secao_visao_geral(df):
    st.subheader("Visao geral")

    col_tma = _col_tma(df)

    total        = len(df)
    tma_medio    = df[col_tma].mean() if col_tma in df.columns else np.nan
    dur_total    = df["duracao_segundos"].sum() if "duracao_segundos" in df.columns else 0
    ura_medio    = df["ura_segundos"].mean() if "ura_segundos" in df.columns else np.nan
    fila_medio   = df["fila_segundos"].mean() if "fila_segundos" in df.columns else np.nan
    tpc_medio    = df["tpc_segundos"].mean() if "tpc_segundos" in df.columns else np.nan
    trat_medio   = df["tratamento_segundos"].mean() if "tratamento_segundos" in df.columns else np.nan
    aband_medio  = df["abandono_segundos"].mean() if "abandono_segundos" in df.columns else np.nan

    # Linha 1 — volumes e tempos principais
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total de atendimentos", total)
    m2.metric("TMA medio", formatar_tempo(tma_medio))
    m3.metric("Tempo total em linha", formatar_tempo(dur_total))
    m4.metric("Tempo medio em fila", formatar_tempo(fila_medio))

    # Linha 2 — detalhamento de tempos
    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Tempo medio URA", formatar_tempo(ura_medio))
    m6.metric("Tempo medio TPC", formatar_tempo(tpc_medio))
    m7.metric("Tempo medio tratamento", formatar_tempo(trat_medio))
    m8.metric("Tempo medio abandono", formatar_tempo(aband_medio))

    st.markdown("---")

    # Atendimentos por dia
    if "data_atendimento" in df.columns and df["data_atendimento"].notna().any():
        df_dia = (
            df.set_index("data_atendimento")
            .resample("D")
            .size()
            .reset_index(name="atendimentos")
        )
        df_dia["data_atendimento"] = df_dia["data_atendimento"].dt.strftime("%d/%m/%Y")
        fig_dia = px.bar(
            df_dia, x="data_atendimento", y="atendimentos",
            text="atendimentos",
            title="Atendimentos por dia",
            labels={"data_atendimento": "Data", "atendimentos": "Atendimentos"}
        )
        fig_dia.update_traces(textposition="outside")
        fig_dia.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig_dia, use_container_width=True)

    st.markdown("---")

    # Tipo de desconexao
    if "tipo_desconexao" in df.columns and df["tipo_desconexao"].notna().any():
        df_desc = df["tipo_desconexao"].dropna().value_counts().reset_index()
        df_desc.columns = ["tipo", "quantidade"]
        df_desc["percentual"] = (df_desc["quantidade"] / df_desc["quantidade"].sum() * 100).round(1)
        df_desc["label"] = df_desc.apply(lambda r: f"{r['quantidade']} ({r['percentual']}%)", axis=1)

        c1, c2 = st.columns(2)
        with c1:
            fig_desc = px.pie(
                df_desc, names="tipo", values="quantidade",
                hole=0.4,
                title="Distribuicao por tipo de desconexao"
            )
            fig_desc.update_traces(textinfo="percent+label")
            st.plotly_chart(fig_desc, use_container_width=True)
        with c2:
            st.markdown("**Detalhamento**")
            st.dataframe(df_desc[["tipo", "quantidade", "percentual"]], use_container_width=True)

    st.markdown("---")

    # Atendimentos por agente
    if "nome_agente" in df.columns and df["nome_agente"].notna().any():
        df_ag = (
            df[df["nome_agente"].notna()]
            .groupby("nome_agente")
            .agg(
                atendimentos=(col_tma, "count"),
                tma_s=(col_tma, "mean"),
            )
            .reset_index()
            .sort_values("atendimentos", ascending=False)
        )
        df_ag["TMA"] = df_ag["tma_s"].apply(formatar_tempo)

        c1, c2 = st.columns(2)
        with c1:
            fig_ag = px.bar(
                df_ag, x="nome_agente", y="atendimentos",
                text="atendimentos",
                title="Atendimentos por agente",
                labels={"nome_agente": "Agente", "atendimentos": "Atendimentos"}
            )
            fig_ag.update_traces(textposition="outside")
            fig_ag.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_ag, use_container_width=True)
        with c2:
            fig_tma = px.bar(
                df_ag, x="nome_agente", y="tma_s",
                text=df_ag["TMA"],
                title="TMA por agente",
                labels={"nome_agente": "Agente", "tma_s": "TMA (s)"}
            )
            fig_tma.update_traces(textposition="outside")
            fig_tma.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_tma, use_container_width=True)

    st.markdown("---")

    # Componentes de tempo medios gerais
    componentes = {
        "URA":        "ura_segundos",
        "Fila":       "fila_segundos",
        "Conversa":   "conversas_segundos",
        "TPC":        "tpc_segundos",
        "Tratamento": "tratamento_segundos",
        "Abandono":   "abandono_segundos",
    }
    dados_comp = [
        {"componente": k, "media_s": df[v].mean()}
        for k, v in componentes.items()
        if v in df.columns and df[v].notna().any()
    ]
    if dados_comp:
        df_comp = pd.DataFrame(dados_comp)
        df_comp["Tempo medio"] = df_comp["media_s"].apply(formatar_tempo)
        fig_comp = px.bar(
            df_comp, x="componente", y="media_s",
            text="Tempo medio",
            title="Tempo medio por componente (geral)",
            labels={"componente": "Componente", "media_s": "Segundos"}
        )
        fig_comp.update_traces(textposition="outside")
        st.plotly_chart(fig_comp, use_container_width=True)

    st.markdown("---")

    # Assuntos (se houver cruzamento com Zendesk)
    if "assunto" in df.columns and df["assunto"].notna().any():
        df_ass = (
            df[df["assunto"].notna()]
            .groupby("assunto")
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

        c1, c2 = st.columns(2)
        with c1:
            fig_ass = px.bar(
                df_ass, x="assunto", y="atendimentos",
                text="atendimentos",
                title="Atendimentos por assunto",
                labels={"assunto": "Assunto", "atendimentos": "Atendimentos"}
            )
            fig_ass.update_traces(textposition="outside")
            fig_ass.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_ass, use_container_width=True)
        with c2:
            fig_ass2 = px.bar(
                df_ass, x="assunto", y="tma_s",
                text=df_ass["TMA"],
                title="TMA por assunto",
                labels={"assunto": "Assunto", "tma_s": "TMA (s)"}
            )
            fig_ass2.update_traces(textposition="outside")
            fig_ass2.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_ass2, use_container_width=True)

        st.dataframe(
            df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]],
            use_container_width=True
        )


# -------------------- Por Agente --------------------

def secao_por_agente(df):
    st.subheader("Desempenho por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.info("Sem dados de agente.")
        return

    col_tma = _col_tma(df)

    df_ag = (
        df[df["nome_agente"].notna()]
        .groupby("nome_agente")
        .agg(
            atendimentos=(col_tma, "count"),
            tma_s=(col_tma, "mean"),
            tempo_total_s=("duracao_segundos", "sum"),
        )
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )
    df_ag["TMA"]         = df_ag["tma_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["tempo_total_s"].apply(formatar_tempo)

    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(
            df_ag, x="nome_agente", y="atendimentos", text="atendimentos",
            title="Atendimentos por agente",
            labels={"nome_agente": "Agente", "atendimentos": "Atendimentos"}
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(
            df_ag, x="nome_agente", y="tma_s", text=df_ag["TMA"],
            title="TMA por agente",
            labels={"nome_agente": "Agente", "tma_s": "TMA (s)"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(
        df_ag[["nome_agente", "atendimentos", "TMA", "Tempo Total"]],
        use_container_width=True
    )


# -------------------- Detalhe Agente --------------------

def secao_detalhe_agente(df):
    st.subheader("Detalhe por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.info("Sem dados de agente.")
        return

    agentes = sorted(df["nome_agente"].dropna().unique().tolist())
    agente_sel = st.selectbox("Selecione o agente", agentes)

    df_ag = df[df["nome_agente"] == agente_sel].copy()
    if df_ag.empty:
        st.info("Sem dados para este agente.")
        return

    col_tma = _col_tma(df_ag)

    total     = len(df_ag)
    tma_med   = df_ag[col_tma].mean() if col_tma in df_ag.columns else np.nan
    dur_total = df_ag["duracao_segundos"].sum() if "duracao_segundos" in df_ag.columns else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Atendimentos", total)
    m2.metric("TMA medio", formatar_tempo(tma_med))
    m3.metric("Tempo total", formatar_tempo(dur_total))

    st.markdown("---")

    # Tempos medios por componente
    componentes = {
        "URA":        "ura_segundos",
        "Fila":       "fila_segundos",
        "Conversa":   "conversas_segundos",
        "TPC":        "tpc_segundos",
        "Tratamento": "tratamento_segundos",
    }
    dados_comp = [
        {"componente": k, "media_s": df_ag[v].mean()}
        for k, v in componentes.items()
        if v in df_ag.columns and df_ag[v].notna().any()
    ]
    if dados_comp:
        df_comp = pd.DataFrame(dados_comp)
        df_comp["Tempo medio"] = df_comp["media_s"].apply(formatar_tempo)
        fig = px.bar(
            df_comp, x="componente", y="media_s", text="Tempo medio",
            title=f"Tempo medio por componente — {agente_sel}",
            labels={"componente": "Componente", "media_s": "Segundos"}
        )
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Tipo de desconexao do agente
    if "tipo_desconexao" in df_ag.columns and df_ag["tipo_desconexao"].notna().any():
        df_desc = df_ag["tipo_desconexao"].dropna().value_counts().reset_index()
        df_desc.columns = ["tipo", "quantidade"]
        df_desc["pct"] = (df_desc["quantidade"] / df_desc["quantidade"].sum() * 100).round(1)

        c1, c2 = st.columns(2)
        with c1:
            fig_d = px.pie(
                df_desc, names="tipo", values="quantidade",
                title="Tipos de desconexao",
                hole=0.4
            )
            fig_d.update_traces(textinfo="label+percent")
            st.plotly_chart(fig_d, use_container_width=True)
        with c2:
            st.dataframe(
                df_desc.rename(columns={"tipo": "Tipo", "quantidade": "Qtd", "pct": "%"}),
                use_container_width=True
            )

    st.markdown("---")

    # Volume diario do agente
    if "data_atendimento" in df_ag.columns:
        df_dia = (
            df_ag.set_index("data_atendimento")
            .resample("D")
            .size()
            .reset_index(name="atendimentos")
        )
        df_dia["data_str"] = df_dia["data_atendimento"].dt.strftime("%d/%m/%Y")
        fig2 = px.bar(
            df_dia, x="data_str", y="atendimentos", text="atendimentos",
            title=f"Volume diario — {agente_sel}",
            labels={"data_str": "Data", "atendimentos": "Atendimentos"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True)


# -------------------- Por Assunto --------------------

def secao_por_assunto(df):
    st.subheader("Atendimentos por assunto")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda nao ha assuntos cruzados com o Zendesk.")
        return

    col_tma = _col_tma(df)

    df_ass = (
        df[df["assunto"].notna()]
        .groupby("assunto")
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

    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(
            df_ass, x="assunto", y="atendimentos", text="atendimentos",
            title="Volume por assunto",
            labels={"assunto": "Assunto", "atendimentos": "Atendimentos"}
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(
            df_ass, x="assunto", y="tma_s", text=df_ass["TMA"],
            title="TMA por assunto",
            labels={"assunto": "Assunto", "tma_s": "TMA (s)"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(
        df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]],
        use_container_width=True
    )


# -------------------- Top TMA por mes --------------------

def secao_top_assuntos_tma(df):
    st.subheader("Top 10 assuntos por TMA — por mes")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda nao ha assuntos cruzados com o Zendesk.")
        return

    if "mes" not in df.columns or df["mes"].isna().all():
        st.info("Coluna de mes nao disponivel.")
        return

    col_tma = _col_tma(df)
    meses   = sorted(df["mes"].dropna().astype(str).unique().tolist())
    mes_sel = st.selectbox("Selecione o mes", meses)

    df_mes = df[(df["mes"].astype(str) == mes_sel) & df["assunto"].notna()].copy()
    if df_mes.empty:
        st.info("Sem dados para este mes.")
        return

    df_top = (
        df_mes.groupby("assunto")
        .agg(atendimentos=(col_tma, "count"), tma_s=(col_tma, "mean"))
        .reset_index()
        .sort_values("tma_s", ascending=False)
        .head(10)
    )
    df_top["TMA"] = df_top["tma_s"].apply(formatar_tempo)

    fig = px.bar(
        df_top.sort_values("tma_s", ascending=True),
        x="tma_s", y="assunto", orientation="h",
        text="TMA",
        labels={"tma_s": "TMA (s)", "assunto": "Assunto"},
        color="tma_s", color_continuous_scale="Reds",
        title=f"Top 10 assuntos com maior TMA — {mes_sel}"
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(coloraxis_showscale=False, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        df_top[["assunto", "atendimentos", "TMA"]].reset_index(drop=True),
        use_container_width=True
    )

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
            labels={"tma_s": "TMA (s)", "assunto": "Assunto", "mes": "Mes"},
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

    with st.sidebar.expander("Gerenciar historico"):
        if st.button("Apagar historico"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                carregar_historico.clear()
                st.success("Historico apagado.")
                st.rerun()


def main():
    st.title("Dashboard de Atendimentos — Call Center")

    secao_upload()
    df_hist = carregar_historico()

    if df_hist.empty:
        st.info("Faca o upload do arquivo Genesys (XLSX) para comecar.")
        return

    df_filtrado = aplicar_filtros(df_hist)
    if df_filtrado.empty:
        st.warning("Nenhum registro para os filtros atuais.")
        return

    aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "Visao geral",
        "Por agente",
        "Detalhe do agente",
        "Por assunto",
        "Top TMA por mes",
    ])
    with aba1: secao_visao_geral(df_filtrado)
    with aba2: secao_por_agente(df_filtrado)
    with aba3: secao_detalhe_agente(df_filtrado)
    with aba4: secao_por_assunto(df_filtrado)
    with aba5: secao_top_assuntos_tma(df_filtrado)


if __name__ == "__main__":
    main()
