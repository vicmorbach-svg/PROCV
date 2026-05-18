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
    "carimbo de data/hora do resultado parcial": "carimbo_parcial",
}

PADRAO_AGENTE = re.compile(r"usu.{0,15}interagiram", re.IGNORECASE)

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

        cols_tempo = {
            "duracao_str":          "duracao_segundos",
            "total_ura_str":        "ura_segundos",
            "fila_total_str":       "fila_segundos",
            "total_conversas_str":  "conversas_segundos",
            "total_tpc_str":        "tpc_segundos",
            "tratamento_total_str": "tratamento_segundos",
            "tempo_abandono_str":   "abandono_segundos",
        }
        for col_str, col_seg in cols_tempo.items():
            if col_str in df.columns:
                df[col_seg] = df[col_str].apply(duracao_para_segundos)

        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)
        else:
            df["id_genesys_norm"] = np.nan

        if "ani" in df.columns:
            df["ani"] = df["ani"].astype(str).str.replace(r"^tel:\+", "", regex=True).str.strip()

        if "nome_agente" in df.columns:
            df["nome_agente"] = df["nome_agente"].astype(str).str.strip()
            df.loc[df["nome_agente"].str.lower().isin(["nan", "", "none"]), "nome_agente"] = np.nan

        st.info(f"Genesys: {len(df)} interacoes carregadas.")
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
            df["data_criacao_zen"] = pd.to_datetime(
                df["data_criacao_zen"].astype(str).str.strip(),
                errors="coerce", dayfirst=True
            )
        else:
            df["data_criacao_zen"] = pd.NaT

        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)
        else:
            df["id_genesys_norm"] = np.nan

        if "assunto" in df.columns:
            df["assunto"] = df["assunto"].astype(str).str.strip()
            df.loc[df["assunto"].str.lower().isin(["nan", "", "none"]), "assunto"] = np.nan

        st.info(f"Zendesk: {len(df)} tickets carregados.")
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()


def integrar_dados(df_zen, df_gen):
    if df_gen.empty:
        return pd.DataFrame()

    df_integrado = df_gen.copy()

    if not df_zen.empty:
        df_integrado = pd.merge(
            df_integrado,
            df_zen[["id_genesys_norm", "assunto", "data_criacao_zen"]],
            on="id_genesys_norm",
            how="left",
            suffixes=("_gen", "_zen")
        )
        # Prioriza data do Zendesk se disponivel
        df_integrado["data_atendimento"] = df_integrado["data_criacao_zen"].fillna(
            df_integrado["data_atendimento"]
        )
        df_integrado["mes"] = df_integrado["data_atendimento"].dt.to_period("M").astype(str)
    else:
        df_integrado["assunto"] = np.nan
        df_integrado["data_criacao_zen"] = pd.NaT
        df_integrado["mes"] = df_integrado["data_atendimento"].dt.to_period("M").astype(str)

    # Converte colunas de texto com poucos valores unicos para category para otimizar memoria
    for col in ["tipo_desconexao", "fila", "nome_agente", "assunto", "mes"]:
        if col in df_integrado.columns:
            if df_integrado[col].nunique() < 200: # Limite arbitrario para converter para category
                df_integrado[col] = df_integrado[col].astype("category")

    return df_integrado


@st.cache_data(show_spinner="Carregando historico...", ttl=60)
def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            df_hist = pd.read_parquet(HISTORICO_PATH)
            st.sidebar.success(f"Historico carregado: {len(df_hist)} registros.")
            return df_hist
        except Exception as e:
            st.error(f"Erro ao carregar historico: {e}")
            return pd.DataFrame()
    return pd.DataFrame()


def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo

    # Remove duplicatas baseadas no id_genesys_norm para evitar entradas repetidas
    df_acum = pd.concat([df_hist, df_novo]).drop_duplicates(subset=["id_genesys_norm"], keep="last")
    return df_acum


def salvar_historico(df):
    try:
        df.to_parquet(HISTORICO_PATH, index=False)
        return True
    except Exception as e:
        st.error(f"Erro ao salvar historico: {e}")
        return False


# -------------------- Filtros --------------------

def aplicar_filtros(df):
    st.sidebar.header("Filtros")

    # Filtro por data
    if "data_atendimento" in df.columns and df["data_atendimento"].notna().any():
        min_data = df["data_atendimento"].min().date()
        max_data = df["data_atendimento"].max().date()
        data_inicio, data_fim = st.sidebar.date_input(
            "Periodo por datas",
            value=(min_data, max_data),
            min_value=min_data,
            max_value=max_data,
            key="filtro_data"
        )
        df = df[(df["data_atendimento"].dt.date >= data_inicio) & (df["data_atendimento"].dt.date <= data_fim)]

    # Filtro por tipo de desconexao
    if "tipo_desconexao" in df.columns and df["tipo_desconexao"].notna().any():
        tipos_desconexao = sorted(df["tipo_desconexao"].dropna().unique().tolist())
        tipos_selecionados = st.sidebar.multiselect(
            "Tipo de desconexao",
            tipos_desconexao,
            default=tipos_desconexao,
            key="filtro_desconexao"
        )
        df = df[df["tipo_desconexao"].isin(tipos_selecionados)]

    # Filtro por agente
    if "nome_agente" in df.columns and df["nome_agente"].notna().any():
        agentes = sorted(df["nome_agente"].dropna().unique().tolist())
        agentes_selecionados = st.sidebar.multiselect(
            "Agente",
            agentes,
            default=agentes,
            key="filtro_agente"
        )
        df = df[df["nome_agente"].isin(agentes_selecionados)]

    # Filtro por fila
    if "fila" in df.columns and df["fila"].notna().any():
        filas = sorted(df["fila"].dropna().unique().tolist())
        filas_selecionadas = st.sidebar.multiselect(
            "Fila",
            filas,
            default=filas,
            key="filtro_fila"
        )
        df = df[df["fila"].isin(filas_selecionadas)]

    # Filtro por assunto (Zendesk)
    if "assunto" in df.columns and df["assunto"].notna().any():
        assuntos = sorted(df["assunto"].dropna().unique().tolist())
        assuntos_selecionados = st.sidebar.multiselect(
            "Assunto (Zendesk)",
            assuntos,
            default=assuntos,
            key="filtro_assunto"
        )
        df = df[df["assunto"].isin(assuntos_selecionados)]

    return df


# -------------------- Secao Visao Geral --------------------

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

    # Linha 2 — tempos por componente
    m5, m6, m7, m8 = st.columns(4)
    m5.metric("TMA URA", formatar_tempo(ura_medio))
    m6.metric("TMA TPC", formatar_tempo(tpc_medio))
    m7.metric("TMA Tratamento", formatar_tempo(trat_medio))
    m8.metric("TMA Abandono", formatar_tempo(aband_medio))

    st.markdown("---")

    # Atendimentos por dia
    if "data_atendimento" in df.columns and df["data_atendimento"].notna().any():
        df_diario = df.set_index("data_atendimento").resample("D").size().reset_index(name="atendimentos")
        fig_diario = px.line(
            df_diario, x="data_atendimento", y="atendimentos",
            title="Atendimentos por dia",
            labels={"data_atendimento": "Data", "atendimentos": "Atendimentos"}
        )
        st.plotly_chart(fig_diario, use_container_width=True, key="vg_atend_diario")

    st.markdown("---")

    c1, c2 = st.columns(2)

    with c1:
        # Tipo de desconexao geral
        if "tipo_desconexao" in df.columns and df["tipo_desconexao"].notna().any():
            df_desc = df["tipo_desconexao"].dropna().value_counts().reset_index()
            df_desc.columns = ["tipo", "quantidade"]
            fig_desc = px.pie(
                df_desc, values="quantidade", names="tipo",
                title="Tipos de desconexao", hole=0.4
            )
            st.plotly_chart(fig_desc, use_container_width=True, key="vg_tipo_desconexao")
        else:
            st.info("Sem dados de tipo de desconexao.")

    with c2:
        # Atendimentos e TMA por agente (top 10)
        if "nome_agente" in df.columns and df["nome_agente"].notna().any():
            df_ag = (
                df.groupby("nome_agente")
                .agg(
                    atendimentos=(col_tma, "count"),
                    tma_s=(col_tma, "mean"),
                    tempo_total_s=("duracao_segundos", "sum")
                )
                .reset_index()
                .sort_values("atendimentos", ascending=False)
                .head(10)
            )
            df_ag["TMA"] = df_ag["tma_s"].apply(formatar_tempo)
            df_ag["Tempo Total"] = df_ag["tempo_total_s"].apply(formatar_tempo)

            fig_ag = px.bar(
                df_ag, x="nome_agente", y="atendimentos", text="atendimentos",
                title="Top 10 agentes por atendimentos",
                labels={"nome_agente": "Agente", "atendimentos": "Atendimentos"}
            )
            fig_ag.update_traces(textposition="outside")
            fig_ag.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_ag, use_container_width=True, key="vg_agente_volume")
        else:
            st.info("Sem dados de agente.")

    st.markdown("---")

    # Componentes de tempo medio geral
    componentes = {
        "URA":          "ura_segundos",
        "Fila":         "fila_segundos",
        "Conversa":     "conversas_segundos",
        "TPC":          "tpc_segundos",
        "Tratamento":   "tratamento_segundos",
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
            df_comp, x="componente", y="media_s", text="Tempo medio",
            title="Tempo medio por componente (geral)",
            labels={"componente": "Componente", "media_s": "Segundos"}
        )
        fig_comp.update_traces(textposition="outside")
        st.plotly_chart(fig_comp, use_container_width=True, key="vg_comp_tempo")
    else:
        st.info("Sem dados de componentes de tempo.")

    st.markdown("---")

    # Atendimentos e TMA por assunto (Zendesk)
    if "assunto" in df.columns and df["assunto"].notna().any():
        df_ass = (
            df.groupby("assunto")
            .agg(
                atendimentos=(col_tma, "count"),
                tma_s=(col_tma, "mean"),
                tempo_total_s=("duracao_segundos", "sum")
            )
            .reset_index()
            .sort_values("atendimentos", ascending=False)
            .head(10)
        )
        df_ass["TMA"] = df_ass["tma_s"].apply(formatar_tempo)
        df_ass["Tempo Total"] = df_ass["tempo_total_s"].apply(formatar_tempo)

        c1, c2 = st.columns(2)
        with c1:
            fig_ass = px.bar(
                df_ass, x="assunto", y="atendimentos", text="atendimentos",
                title="Volume por assunto",
                labels={"assunto": "Assunto", "atendimentos": "Atendimentos"}
            )
            fig_ass.update_traces(textposition="outside")
            fig_ass.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_ass, use_container_width=True, key="vg_ass_volume")
        with c2:
            fig_ass2 = px.bar(
                df_ass, x="assunto", y="tma_s", text=df_ass["TMA"],
                title="TMA por assunto",
                labels={"assunto": "Assunto", "tma_s": "TMA (s)"}
            )
            fig_ass2.update_traces(textposition="outside")
            fig_ass2.update_layout(xaxis_tickangle=-30)
            st.plotly_chart(fig_ass2, use_container_width=True, key="vg_ass_tma")

        st.dataframe(
            df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]],
            use_container_width=True
        )
    else:
        st.info("Sem dados de assunto (Zendesk nao carregado ou sem dados).")


# -------------------- Secao Por Agente --------------------

def secao_por_agente(df):
    st.subheader("Por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.info("Sem dados de agente.")
        return

    col_tma = _col_tma(df)

    df_ag = (
        df.groupby("nome_agente")
        .agg(
            atendimentos=(col_tma, "count"),
            tma_s=(col_tma, "mean"),
            tempo_total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )
    df_ag["TMA"] = df_ag["tma_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["tempo_total_s"].apply(formatar_tempo)

    c1, c2 = st.columns(2)
    with c1:
        fig1 = px.bar(
            df_ag, x="nome_agente", y="atendimentos", text="atendimentos",
            title="Atendimentos por agente",
            labels={"nome_agente": "Agente", "atendimentos": "Atendimentos"}
        )
        fig1.update_traces(textposition="outside")
        fig1.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig1, use_container_width=True, key="pa_agente_volume")
    with c2:
        fig2 = px.bar(
            df_ag, x="nome_agente", y="tma_s", text="TMA",
            title="TMA por agente",
            labels={"nome_agente": "Agente", "tma_s": "TMA (s)"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True, key="pa_agente_tma")

    st.dataframe(
        df_ag[["nome_agente", "atendimentos", "TMA", "Tempo Total"]],
        use_container_width=True
    )


# -------------------- Secao Detalhe Agente --------------------

def secao_detalhe_agente(df):
    st.subheader("Detalhe por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.info("Sem dados de agente.")
        return

    agentes = sorted(df["nome_agente"].dropna().unique().tolist())
    agente_sel = st.selectbox("Selecione o agente", agentes, key="sel_agente_detalhe")

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

    # Tempos medios detalhados
    componentes = {
        "URA":          "ura_segundos",
        "Fila":         "fila_segundos",
        "Conversa":     "conversas_segundos",
        "TPC":          "tpc_segundos",
        "Tratamento":   "tratamento_segundos",
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
            title=f"Tempo medio por componente - {agente_sel}",
            labels={"componente": "Componente", "media_s": "Segundos"}
        )
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True, key="da_comp_tempo")

    st.markdown("---")

    # Tipo de desconexao do agente
    if "tipo_desconexao" in df_ag.columns and df_ag["tipo_desconexao"].notna().any():
        df_desc = df_ag["tipo_desconexao"].dropna().value_counts().reset_index()
        df_desc.columns = ["tipo", "quantidade"]
        df_desc["percentual"] = (df_desc["quantidade"] / df_desc["quantidade"].sum()) * 100
        df_desc["percentual_str"] = df_desc["percentual"].map("{:.1f}%".format)

        c1, c2 = st.columns(2)
        with c1:
            fig_desc = px.pie(
                df_desc, values="quantidade", names="tipo",
                title=f"Tipos de desconexao - {agente_sel}", hole=0.4
            )
            st.plotly_chart(fig_desc, use_container_width=True, key="da_tipo_desconexao")
        with c2:
            st.markdown(f"**Detalhe de desconexao para {agente_sel}**")
            st.dataframe(
                df_desc[["tipo", "quantidade", "percentual_str"]],
                use_container_width=True,
                hide_index=True
            )
    else:
        st.info("Sem dados de tipo de desconexao para este agente.")

    st.markdown("---")

    # Atendimentos por dia do agente
    if "data_atendimento" in df_ag.columns and df_ag["data_atendimento"].notna().any():
        df_diario = df_ag.set_index("data_atendimento").resample("D").size().reset_index(name="atendimentos")
        fig_diario = px.line(
            df_diario, x="data_atendimento", y="atendimentos",
            title=f"Atendimentos por dia - {agente_sel}",
            labels={"data_atendimento": "Data", "atendimentos": "Atendimentos"}
        )
        st.plotly_chart(fig_diario, use_container_width=True, key="da_atend_diario")


# -------------------- Secao Por Assunto --------------------

def secao_por_assunto(df):
    st.subheader("Por assunto (Zendesk)")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda nao ha assuntos cruzados com o Zendesk.")
        return

    col_tma = _col_tma(df)

    df_ass = (
        df.groupby("assunto")
        .agg(
            atendimentos=(col_tma, "count"),
            tma_s=(col_tma, "mean"),
            tempo_total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )
    df_ass["TMA"] = df_ass["tma_s"].apply(formatar_tempo)
    df_ass["Tempo Total"] = df_ass["tempo_total_s"].apply(formatar_tempo)

    c1, c2 = st.columns(2)
    with c1:
        fig1 = px.bar(
            df_ass, x="assunto", y="atendimentos", text="atendimentos",
            title="Atendimentos por assunto",
            labels={"assunto": "Assunto", "atendimentos": "Atendimentos"}
        )
        fig1.update_traces(textposition="outside")
        fig1.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig1, use_container_width=True, key="ass_assunto_volume")
    with c2:
        fig2 = px.bar(
            df_ass, x="assunto", y="tma_s", text="TMA",
            title="TMA por assunto",
            labels={"assunto": "Assunto", "tma_s": "TMA (s)"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True, key="ass_assunto_tma")

    st.dataframe(
        df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]],
        use_container_width=True
    )


# -------------------- Top TMA por mes --------------------

def secao_top_assuntos_tma(df):
    st.subheader("Top 10 assuntos por TMA - por mes")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda nao ha assuntos cruzados com o Zendesk.")
        return

    if "mes" not in df.columns or df["mes"].isna().all():
        st.info("Coluna de mes nao disponivel.")
        return

    col_tma = _col_tma(df)
    meses   = sorted(df["mes"].dropna().astype(str).unique().tolist())
    mes_sel = st.selectbox("Selecione o mes", meses, key="sel_mes_top_tma")

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
        text="TMA", color="tma_s", color_continuous_scale="Reds",
        title=f"Top 10 assuntos com maior TMA - {mes_sel}",
        labels={"tma_s": "TMA (s)", "assunto": "Assunto"}
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(coloraxis_showscale=False, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True, key="top_tma_bar")

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
            title="TMA por assunto - comparativo entre meses",
            labels={"tma_s": "TMA (s)", "assunto": "Assunto", "mes": "Mes"}
        )
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True, key="top_tma_comp")


# -------------------- Upload & main --------------------

def secao_upload():
    st.sidebar.header("Upload mensal")

    arq_zen = st.sidebar.file_uploader("Zendesk (XLSX)", type=["xlsx", "xls"], key="upload_zen")
    arq_gen = st.sidebar.file_uploader("Genesys (XLSX)", type=["xlsx", "xls"], key="upload_gen")

    # O botão de processar só deve estar ativo se houver um arquivo Genesys
    processar_disabled = arq_gen is None

    if st.sidebar.button("Processar e acumular", key="btn_processar", disabled=processar_disabled):
        if arq_gen is None:
            st.sidebar.warning("Por favor, carregue o arquivo Genesys para processar.")
            return

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
        if st.button("Apagar historico", key="btn_apagar_hist"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                carregar_historico.clear()
                st.success("Historico apagado.")
                st.rerun()


def main():
    st.title("Dashboard de Atendimentos - Call Center")

    # Força a revalidação do cache do histórico na inicialização do app
    # Isso garante que o arquivo mais recente seja lido do disco
    carregar_historico.clear() 
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
