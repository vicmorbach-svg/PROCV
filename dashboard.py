import streamlit as st
import pandas as pd
import numpy as np
import os
from datetime import datetime

HISTORICO_PATH = "historico_atendimentos.parquet"

st.set_page_config(
    page_title="Dashboard Call Center",
    layout="wide"
)

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
    return s if s and s != "nan" else np.nan

# -------------------- Carregamento arquivos --------------------

def carregar_zendesk(uploaded_file):
    try:
        df = pd.read_excel(uploaded_file, engine="openpyxl", dtype=str)
        df.columns = df.columns.str.strip()

        renomear = {
            "ID do ticket":                             "ticket_id",
            "Assuntos do Ticket":                       "assunto",
            "Criação do ticket - Carimbo de data/hora": "data_criacao_zen",
            "ID Genesys":                               "id_genesys",
            "Matricula":                                "matricula",
            "Tickets":                                  "tickets_zen",
            "Arquivo_Origem":                           "arquivo_origem_zen"
        }
        df = df.rename(columns={k: v for k, v in renomear.items() if k in df.columns})

        if "data_criacao_zen" in df.columns:
            df["data_criacao_zen"] = pd.to_datetime(df["data_criacao_zen"], errors="coerce")

        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)

        total = len(df)
        com_id = df["id_genesys_norm"].notna().sum() if "id_genesys_norm" in df.columns else 0
        st.info(f"Zendesk: {total} tickets carregados, {com_id} com ID Genesys preenchido.")

        return df
    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()


def carregar_genesys(uploaded_file):
    """
    Lê o relatório Genesys exportado como XLSX.
    Suporta tanto .xlsx quanto .csv com separador pipe (legado).
    """
    try:
        nome = uploaded_file.name.lower()

        # ---------- XLSX ----------
        if nome.endswith(".xlsx") or nome.endswith(".xls"):
            df_raw = pd.read_excel(uploaded_file, engine="openpyxl", dtype=str)
            df_raw.columns = df_raw.columns.str.strip()

            # Mapeamento robusto: aceita variações de encoding/espaço nos nomes
            mapa_colunas = {
                # exportação
                "Exportação total concluída":           "exportacao",
                "Exporta\u00e7\u00e3o total conclu\u00edda": "exportacao",
                # filtros
                "Filtros":                              "filtros",
                # agente
                "Usuários – Interagiram":               "nome_agente",
                "Usu\u00e1rios \u2013 Interagiram":     "nome_agente",
                "Usuários - Interagiram":               "nome_agente",
                # data
                "Data":                                 "data_atendimento_raw",
                # duração total da chamada
                "Duração":                              "duracao_str",
                "Dura\u00e7\u00e3o":                    "duracao_str",
                # ANI (número do cliente)
                "ANI":                                  "ani",
                # tipo de desconexão
                "Tipo de desconexão":                   "tipo_desconexao",
                "Tipo de desconex\u00e3o":              "tipo_desconexao",
                # tempos detalhados
                "Total da URA":                         "total_ura_str",
                "Fila total":                           "fila_total_str",
                "Total de conversas":                   "total_conversas_str",
                "Total de TPC":                         "total_tpc_str",
                "Tratamento total":                     "tratamento_total_str",
                "Tempo para abandonar":                 "tempo_abandono_str",
                # ID de conversa (chave de cruzamento com Zendesk)
                "ID de conversa":                       "id_genesys",
            }

            # Renomeia apenas as colunas que existem no arquivo
            renomear = {k: v for k, v in mapa_colunas.items() if k in df_raw.columns}
            df = df_raw.rename(columns=renomear)

            # Remove linhas que não sejam registros reais
            # (ex.: linha de cabeçalho repetida, linhas de totais, etc.)
            if "exportacao" in df.columns:
                df = df[df["exportacao"].str.strip().str.lower().isin(["sim", "yes"])].copy()

            df = df.reset_index(drop=True)

        # ---------- CSV legado (pipe) ----------
        else:
            conteudo = uploaded_file.read().decode("utf-8", errors="replace")
            linhas = conteudo.splitlines()
            registros = []
            for linha in linhas:
                linha = linha.strip()
                if not linha or linha == "|":
                    continue
                if "|" not in linha:
                    continue
                partes = [p.strip() for p in linha.split("|")]
                while partes and partes[0] == "":
                    partes.pop(0)
                while partes and partes[-1] == "":
                    partes.pop()
                if len(partes) < 5:
                    continue
                registros.append({
                    "exportacao":          partes[0],
                    "filtros":             partes[2] if len(partes) > 2 else "",
                    "nome_agente":         partes[3] if len(partes) > 3 else "",
                    "data_atendimento_raw": partes[4] if len(partes) > 4 else "",
                    "duracao_str":         partes[5] if len(partes) > 5 else "",
                })
            df = pd.DataFrame(registros)

        # ---------- Pós-processamento comum ----------

        # Fila
        if "filtros" in df.columns:
            df["fila"] = (
                df["filtros"]
                .str.extract(r"Fila:\s*(.+)", expand=False)
                .str.strip()
            )
            df.loc[df["fila"].isna(), "fila"] = "URA_CORSAN"
        else:
            df["fila"] = "URA_CORSAN"

        # Data/hora
        if "data_atendimento_raw" in df.columns:
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento_raw"].astype(str).str.strip(),
                errors="coerce",
                dayfirst=True
            )
        else:
            df["data_atendimento"] = pd.NaT

        # Duração total em segundos
        if "duracao_str" in df.columns:
            df["duracao_segundos"] = df["duracao_str"].apply(duracao_para_segundos)
        else:
            df["duracao_segundos"] = np.nan

        # Tempos detalhados em segundos
        for col_str, col_s in [
            ("total_ura_str",        "ura_segundos"),
            ("fila_total_str",       "fila_segundos"),
            ("total_conversas_str",  "conversas_segundos"),
            ("total_tpc_str",        "tpc_segundos"),
            ("tratamento_total_str", "tratamento_segundos"),
            ("tempo_abandono_str",   "abandono_segundos"),
        ]:
            if col_str in df.columns:
                df[col_s] = df[col_str].apply(duracao_para_segundos)

        # ID de conversa (chave para cruzar com Zendesk)
        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)
        else:
            df["id_genesys_norm"] = np.nan

        # ANI sem prefixo "tel:+"
        if "ani" in df.columns:
            df["ani"] = df["ani"].str.replace(r"^tel:\+?", "", regex=True)

        if df.empty:
            st.error("Nenhum registro válido encontrado no arquivo do Genesys.")
            return pd.DataFrame()

        st.info(f"Genesys: {len(df)} interações carregadas.")
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
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
        colunas_zen = ["id_genesys_norm"]
        for col in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]:
            if col in df_zen.columns:
                colunas_zen.append(col)

        df_zen_slim = df_zen[colunas_zen].drop_duplicates(subset=["id_genesys_norm"])

        df = pd.merge(df, df_zen_slim, on="id_genesys_norm", how="left", suffixes=("", "_zen"))

        total = len(df)
        com_assunto = df["assunto"].notna().sum() if "assunto" in df.columns else 0
        st.success(
            f"Merge concluído: {total} registros Genesys | "
            f"{com_assunto} cruzados com Zendesk ({com_assunto/total*100:.1f}%)"
        )
    else:
        if df_zen.empty:
            st.warning("Zendesk não carregado; exibindo só dados do Genesys.")
        else:
            ids_disponiveis = df["id_genesys_norm"].notna().any() if "id_genesys_norm" in df.columns else False
            if not ids_disponiveis:
                st.warning(
                    "ID de conversa não encontrado no arquivo Genesys. "
                    "O app funciona, mas sem cruzamento com o Zendesk."
                )
        df["ticket_id"] = np.nan
        df["assunto"]   = np.nan
        df["matricula"] = np.nan

    df["data_base"] = df["data_atendimento"].copy()
    return df

# -------------------- Histórico --------------------

def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            df = pd.read_parquet(HISTORICO_PATH)
            if "data_base" in df.columns:
                df["data_base"] = pd.to_datetime(df["data_base"], errors="coerce")
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def salvar_historico(df):
    try:
        df.to_parquet(HISTORICO_PATH, index=False)
        return True
    except Exception as e:
        st.error(f"Erro ao salvar histórico: {e}")
        return False

def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo.reset_index(drop=True)

    df_comb = pd.concat([df_hist, df_novo], ignore_index=True)

    if "id_genesys_norm" in df_comb.columns and df_comb["id_genesys_norm"].notna().any():
        com_id = df_comb[df_comb["id_genesys_norm"].notna()]
        sem_id = df_comb[df_comb["id_genesys_norm"].isna()]
        com_id = com_id.drop_duplicates(subset=["id_genesys_norm"], keep="last")
        df_comb = pd.concat([com_id, sem_id], ignore_index=True)
    else:
        chaves = [c for c in ["nome_agente", "data_atendimento", "duracao_segundos"] if c in df_comb.columns]
        if chaves:
            df_comb = df_comb.drop_duplicates(subset=chaves, keep="last")

    return df_comb.reset_index(drop=True)

# -------------------- Filtros --------------------

def aplicar_filtros(df):
    st.sidebar.header("Filtros")
    df_f = df.copy()

    if "data_base" in df_f.columns and df_f["data_base"].notna().any():
        min_data = df_f["data_base"].min().date()
        max_data = df_f["data_base"].max().date()
        periodo = st.sidebar.date_input(
            "Período",
            value=(min_data, max_data),
            min_value=min_data,
            max_value=max_data
        )
        if isinstance(periodo, (list, tuple)) and len(periodo) == 2:
            ini, fim = periodo
            df_f = df_f[
                (df_f["data_base"].dt.date >= ini) &
                (df_f["data_base"].dt.date <= fim)
            ]

    if "data_atendimento" in df_f.columns:
        h_ini, h_fim = st.sidebar.slider("Hora do dia", 0, 23, (0, 23))
        df_f = df_f[
            (df_f["data_atendimento"].dt.hour >= h_ini) &
            (df_f["data_atendimento"].dt.hour <= h_fim)
        ]

    if "nome_agente" in df_f.columns:
        agentes = sorted(df_f["nome_agente"].dropna().unique())
        sel_agente = st.sidebar.multiselect("Agente", options=agentes, default=agentes)
        if sel_agente:
            df_f = df_f[df_f["nome_agente"].isin(sel_agente)]

    if "tipo_desconexao" in df_f.columns and df_f["tipo_desconexao"].notna().any():
        tipos = sorted(df_f["tipo_desconexao"].dropna().unique())
        sel_tipo = st.sidebar.multiselect("Tipo de desconexão", options=tipos, default=tipos)
        if sel_tipo:
            df_f = df_f[df_f["tipo_desconexao"].isin(sel_tipo)]

    if "assunto" in df_f.columns and df_f["assunto"].notna().any():
        assuntos = sorted(df_f["assunto"].dropna().unique())
        sel_ass = st.sidebar.multiselect("Assunto", options=assuntos, default=assuntos)
        if sel_ass:
            df_f = df_f[df_f["assunto"].isin(sel_ass)]

    st.sidebar.markdown(f"Registros no filtro: **{len(df_f)}**")
    return df_f

# -------------------- Seções de visualização --------------------

def secao_visao_geral(df):
    st.subheader("Visão Geral")

    total   = len(df)
    tma     = df["duracao_segundos"].mean() if "duracao_segundos" in df.columns else None
    horas   = df["duracao_segundos"].sum() / 3600 if "duracao_segundos" in df.columns else 0
    agentes = df["nome_agente"].nunique() if "nome_agente" in df.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total de atendimentos", total)
    col2.metric("TMA geral", formatar_tempo(tma))
    col3.metric("Horas em atendimento", f"{horas:.1f} h")
    col4.metric("Agentes ativos", agentes)

    # Métricas de tempo detalhadas (se disponíveis)
    cols_tempo = {
        "ura_segundos":        "Média URA",
        "fila_segundos":       "Média Fila",
        "tratamento_segundos": "Média Tratamento",
        "abandono_segundos":   "Média Abandono",
    }
    disponiveis = {label: col for col, label in cols_tempo.items() if col in df.columns}
    if disponiveis:
        st.markdown("**Tempos médios detalhados**")
        colunas_m = st.columns(len(disponiveis))
        for i, (label, col) in enumerate(disponiveis.items()):
            colunas_m[i].metric(label, formatar_tempo(df[col].mean()))

    # Distribuição por tipo de desconexão
    if "tipo_desconexao" in df.columns and df["tipo_desconexao"].notna().any():
        st.markdown("**Distribuição por tipo de desconexão**")
        dist = df["tipo_desconexao"].value_counts().reset_index()
        dist.columns = ["Tipo", "Qtd"]
        st.bar_chart(dist.set_index("Tipo"))

    if "data_base" in df.columns:
        df_dia = (
            df.set_index("data_base")
            .resample("D")["nome_agente"]
            .count()
            .reset_index()
            .rename(columns={"nome_agente": "Atendimentos"})
        )
        st.markdown("**Atendimentos por dia**")
        st.line_chart(df_dia.set_index("data_base"))

def secao_por_agente(df):
    st.subheader("Análise por agente")

    if "nome_agente" not in df.columns:
        st.info("Não há coluna de agente nos dados.")
        return

    agg_dict = dict(atendimentos=("duracao_segundos", "count"),
                    tma_s=("duracao_segundos", "mean"),
                    tempo_total_s=("duracao_segundos", "sum"))

    for col, alias in [("tratamento_segundos", "trat_s"), ("fila_segundos", "fila_s")]:
        if col in df.columns:
            agg_dict[alias] = (col, "mean")

    df_ag = (
        df.groupby("nome_agente")
        .agg(**agg_dict)
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )

    df_ag["TMA"] = df_ag["tma_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["tempo_total_s"].apply(formatar_tempo)

    if "trat_s" in df_ag.columns:
        df_ag["Trat. Médio"] = df_ag["trat_s"].apply(formatar_tempo)
    if "fila_s" in df_ag.columns:
        df_ag["Fila Média"] = df_ag["fila_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por agente**")
        st.bar_chart(df_ag.set_index("nome_agente")["atendimentos"])
    with col2:
        st.markdown("**TMA por agente (s)**")
        st.bar_chart(df_ag.set_index("nome_agente")["tma_s"])

    colunas_tabela = ["nome_agente", "atendimentos", "TMA", "Tempo Total"]
    for c in ["Trat. Médio", "Fila Média"]:
        if c in df_ag.columns:
            colunas_tabela.append(c)

    st.dataframe(df_ag[colunas_tabela])

def secao_detalhe_agente(df):
    st.subheader("Detalhe por agente")

    if "nome_agente" not in df.columns:
        st.info("Não há coluna de agente nos dados.")
        return

    agentes = sorted(df["nome_agente"].dropna().unique())
    agente_sel = st.selectbox("Selecione o agente", ["(Selecione)"] + list(agentes))
    if agente_sel == "(Selecione)":
        return

    df_ag = df[df["nome_agente"] == agente_sel].copy()
    if df_ag.empty:
        st.info("Nenhum atendimento para este agente no filtro atual.")
        return

    total = len(df_ag)
    tma   = df_ag["duracao_segundos"].mean()
    horas = df_ag["duracao_segundos"].sum() / 3600

    col1, col2, col3 = st.columns(3)
    col1.metric("Atendimentos", total)
    col2.metric("TMA do agente", formatar_tempo(tma))
    col3.metric("Horas em atendimento", f"{horas:.1f} h")

    if "data_base" in df_ag.columns:
        df_dia = (
            df_ag.set_index("data_base")
            .resample("D")["duracao_segundos"]
            .count()
            .reset_index()
            .rename(columns={"duracao_segundos": "Atendimentos"})
        )
        st.markdown("**Atendimentos por dia (agente)**")
        st.line_chart(df_dia.set_index("data_base"))

    st.markdown("**Atendimentos detalhados**")
    cols_det = [
        "data_atendimento", "fila", "ani", "tipo_desconexao",
        "duracao_str", "total_ura_str", "fila_total_str",
        "tratamento_total_str", "tempo_abandono_str",
        "assunto", "ticket_id", "id_genesys"
    ]
    cols_det = [c for c in cols_det if c in df_ag.columns]
    st.dataframe(df_ag[cols_det].sort_values("data_atendimento", ascending=False))

def secao_por_assunto(df):
    st.subheader("Análise por assunto")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda não há assuntos cruzados com o Zendesk (faça o upload do arquivo Zendesk também).")
        return

    df_val = df[df["assunto"].notna()]
    df_ass = (
        df_val.groupby("assunto")
        .agg(
            atendimentos=("duracao_segundos", "count"),
            tma_s=("duracao_segundos", "mean"),
            tempo_total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )

    df_ass["TMA"] = df_ass["tma_s"].apply(formatar_tempo)
    df_ass["Tempo Total"] = df_ass["tempo_total_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por assunto**")
        st.bar_chart(df_ass.set_index("assunto")["atendimentos"])
    with col2:
        st.markdown("**TMA por assunto (s)**")
        st.bar_chart(df_ass.set_index("assunto")["tma_s"])

    st.dataframe(df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]])

# -------------------- Upload & main --------------------

def secao_upload():
    st.sidebar.header("Upload mensal")

    arq_zen = st.sidebar.file_uploader("Zendesk (XLSX)", type=["xlsx", "xls"])
    arq_gen = st.sidebar.file_uploader("Genesys (XLSX ou CSV)", type=["xlsx", "xls", "csv"])

    if arq_gen is not None:
        if st.sidebar.button("Processar e acumular"):
            df_zen = carregar_zendesk(arq_zen) if arq_zen else pd.DataFrame()
            df_gen = carregar_genesys(arq_gen)
            df_novo = integrar_dados(df_zen, df_gen)

            if df_novo.empty:
                st.sidebar.error("Nenhum dado gerado. Veja as mensagens acima.")
                return

            df_hist = carregar_historico()
            df_acum = adicionar_ao_historico(df_novo, df_hist)
            if salvar_historico(df_acum):
                st.sidebar.success(f"Dados acumulados. Total histórico: {len(df_acum)} registros.")
                st.rerun()

    with st.sidebar.expander("Gerenciar histórico"):
        if st.button("Apagar histórico"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                st.success("Histórico apagado.")
                st.rerun()

def main():
    st.title("Dashboard de Atendimentos – Call Center (Genesys + Zendesk)")

    secao_upload()
    df_hist = carregar_historico()

    if df_hist.empty:
        st.info("Faça o upload dos arquivos para começar.")
        return

    df_filtrado = aplicar_filtros(df_hist)
    if df_filtrado.empty:
        st.warning("Nenhum registro para os filtros atuais.")
        return

    aba1, aba2, aba3, aba4 = st.tabs([
        "Visão geral",
        "Por agente",
        "Detalhe do agente",
        "Por assunto"
    ])

    with aba1:
        secao_visao_geral(df_filtrado)
    with aba2:
        secao_por_agente(df_filtrado)
    with aba3:
        secao_detalhe_agente(df_filtrado)
    with aba4:
        secao_por_assunto(df_filtrado)

if __name__ == "__main__":
    main()
