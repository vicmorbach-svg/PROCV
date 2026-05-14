import streamlit as st
import pandas as pd
import numpy as np
import os
import io
import re
from datetime import datetime, timedelta

# ─────────────────────────────────────────
# CONFIGURAÇÕES GERAIS
# ─────────────────────────────────────────
HISTORICO_PATH = "historico_atendimentos.parquet"

st.set_page_config(
    layout="wide",
    page_title="Dashboard Call Center",
    page_icon="📞"
)

# ─────────────────────────────────────────
# CSS CUSTOMIZADO
# ─────────────────────────────────────────
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 15px 20px;
        margin: 5px;
        text-align: center;
    }
    .metric-card h2 { font-size: 2rem; margin: 0; color: #1f77b4; }
    .metric-card p  { font-size: 0.9rem; margin: 0; color: #555; }
    .section-title  { border-bottom: 2px solid #1f77b4; padding-bottom: 6px; margin-top: 20px; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────
# FUNÇÕES UTILITÁRIAS
# ─────────────────────────────────────────

def formatar_tempo(segundos):
    """Converte segundos em string mm:ss ou hh:mm:ss."""
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
    """
    Converte string de duração (HH:MM:SS ou MM:SS) em segundos.
    Trata valores nulos e variações de formato.
    """
    if pd.isna(valor) or str(valor).strip() == "":
        return np.nan
    valor = str(valor).strip()
    partes = valor.split(":")
    try:
        if len(partes) == 3:
            return int(partes[0]) * 3600 + int(partes[1]) * 60 + int(partes[2])
        elif len(partes) == 2:
            return int(partes[0]) * 60 + int(partes[1])
        else:
            return float(valor)
    except Exception:
        return np.nan


# ─────────────────────────────────────────
# CARREGAMENTO E LIMPEZA DOS ARQUIVOS
# ─────────────────────────────────────────

@st.cache_data(show_spinner=False)
def carregar_zendesk(arquivo):
    """
    Carrega o arquivo Zendesk (XLSX).
    Colunas esperadas:
      - 'ID do ticket'
      - 'Assuntos do Ticket'
      - 'Criação do ticket - Carimbo de data/hora'
      - Coluna de matrícula do agente
    """
    try:
        df = pd.read_excel(arquivo, engine="openpyxl")
        df.columns = df.columns.str.strip()

        # Mapeamento de colunas — ajuste aqui se os nomes mudarem
        renomear = {}
        for col in df.columns:
            col_lower = col.lower()
            if "id do ticket" in col_lower or col_lower == "id":
                renomear[col] = "ticket_id"
            elif "assunto" in col_lower:
                renomear[col] = "assunto"
            elif "carimbo" in col_lower or "criação" in col_lower or "data" in col_lower:
                renomear[col] = "data_criacao_zen"
            elif "matric" in col_lower or "matricula" in col_lower:
                renomear[col] = "matricula_agente"
            elif "genesys" in col_lower or "id genesys" in col_lower:
                renomear[col] = "id_genesys"

        df = df.rename(columns=renomear)

        if "data_criacao_zen" in df.columns:
            df["data_criacao_zen"] = pd.to_datetime(
                df["data_criacao_zen"], errors="coerce", dayfirst=True
            )

        if "ticket_id" in df.columns:
            df["ticket_id"] = df["ticket_id"].astype(str).str.strip()

        return df

    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def carregar_genesys(arquivo):
    """
    Carrega o arquivo Genesys (CSV com separador pipe |).
    O arquivo tem metadados no cabeçalho que precisam ser ignorados.
    Colunas esperadas após parse:
      - Usuários (nome do agente)
      - Carimbo de data/hora do resultado parcial (data/hora do atendimento)
      - Duração no formato HH:MM:SS
      - Filtros (contém nome da fila: 'Fila: URA_CORSAN')
    """
    try:
        # Lê o conteúdo bruto
        conteudo = arquivo.read().decode("utf-8", errors="replace")
        linhas = conteudo.splitlines()

        # Localiza a linha de cabeçalho real (contém "Usuários" ou "Carimbo")
        idx_header = None
        for i, linha in enumerate(linhas):
            if "usuários" in linha.lower() or "carimbo" in linha.lower() or "filtros" in linha.lower():
                idx_header = i
                break

        if idx_header is None:
            st.error("Não foi possível identificar o cabeçalho no arquivo Genesys.")
            return pd.DataFrame()

        # Reconstrói o CSV a partir do cabeçalho
        csv_limpo = "\n".join(linhas[idx_header:])
        df = pd.read_csv(
            io.StringIO(csv_limpo),
            sep="|",
            engine="python",
            skipinitialspace=True
        )

        # Remove colunas totalmente vazias (artefato do separador pipe)
        df = df.dropna(axis=1, how="all")
        df.columns = [str(c).strip() for c in df.columns]

        # Mapeamento dinâmico de colunas
        renomear = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if "usuário" in col_lower or "usuario" in col_lower or "agente" in col_lower:
                renomear[col] = "nome_agente"
            elif "carimbo" in col_lower or "data/hora" in col_lower or "timestamp" in col_lower:
                renomear[col] = "data_atendimento"
            elif "filtro" in col_lower:
                renomear[col] = "filtros"
            elif "exportação" in col_lower or "exportacao" in col_lower:
                renomear[col] = "exportacao_concluida"
            elif "duração" in col_lower or "duracao" in col_lower or "tma" in col_lower:
                renomear[col] = "duracao_str"

        df = df.rename(columns=renomear)

        # Extrai nome da fila a partir da coluna filtros
        if "filtros" in df.columns:
            df["fila"] = df["filtros"].str.extract(r"Fila:\s*(.+)", expand=False).str.strip()
        else:
            df["fila"] = "URA_CORSAN"

        # Converte duração para segundos
        # Tenta identificar coluna de duração se não foi mapeada
        if "duracao_str" not in df.columns:
            for col in df.columns:
                amostra = df[col].dropna().astype(str).head(10)
                if amostra.str.match(r"\d{1,2}:\d{2}:\d{2}").any():
                    df = df.rename(columns={col: "duracao_str"})
                    break

        if "duracao_str" in df.columns:
            df["duracao_str"] = df["duracao_str"].astype(str).str.strip()
            df["duracao_segundos"] = df["duracao_str"].apply(duracao_para_segundos)
        else:
            df["duracao_segundos"] = np.nan

        # Converte data/hora do atendimento
        if "data_atendimento" in df.columns:
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento"].astype(str).str.strip(),
                errors="coerce",
                dayfirst=True
            )

        # Limpa nome do agente
        if "nome_agente" in df.columns:
            df["nome_agente"] = df["nome_agente"].astype(str).str.strip()
            df = df[df["nome_agente"].str.len() > 1]  # Remove linhas de metadados

        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        return pd.DataFrame()


def integrar_dados(df_zen, df_gen):
    """
    Integra os dados do Zendesk e do Genesys.
    Como não há chave direta confiável entre os dois arquivos,
    o merge é feito por nome do agente (quando disponível) e data.
    O resultado principal é o dataframe do Genesys enriquecido com assunto do Zendesk.
    """
    # Garante que ambos têm dados
    if df_gen.empty:
        return pd.DataFrame()

    df = df_gen.copy()

    # Se existir matrícula no Zendesk e no Genesys, usar como chave de enriquecimento
    if not df_zen.empty and "assunto" in df_zen.columns:
        # Tentativa de join por id_genesys se existir nos dois
        if "id_genesys" in df_zen.columns and "id_genesys" in df_gen.columns:
            df = pd.merge(df, df_zen[["id_genesys", "assunto", "ticket_id"]], on="id_genesys", how="left")
        else:
            # Sem chave direta: apenas marca que veio do Zendesk via agente + data (aproximado)
            # Neste caso, o assunto fica nulo e o usuário pode enriquecer manualmente
            df["assunto"] = np.nan
            df["ticket_id"] = np.nan
    else:
        df["assunto"] = np.nan
        df["ticket_id"] = np.nan

    return df


# ─────────────────────────────────────────
# HISTÓRICO (PARQUET)
# ─────────────────────────────────────────

def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            return pd.read_parquet(HISTORICO_PATH)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def salvar_historico(df):
    """Salva o dataframe acumulado em Parquet."""
    try:
        df.to_parquet(HISTORICO_PATH, index=False)
        return True
    except Exception as e:
        st.error(f"Erro ao salvar histórico: {e}")
        return False


def adicionar_ao_historico(df_novo, df_hist):
    """
    Concatena novos dados ao histórico.
    Deduplicação por nome_agente + data_atendimento + duracao_segundos.
    """
    if df_hist.empty:
        return df_novo

    df_combinado = pd.concat([df_hist, df_novo], ignore_index=True)

    chaves_dedup = [c for c in ["nome_agente", "data_atendimento", "duracao_segundos"] if c in df_combinado.columns]
    if chaves_dedup:
        df_combinado = df_combinado.drop_duplicates(subset=chaves_dedup, keep="last")

    return df_combinado.reset_index(drop=True)


# ─────────────────────────────────────────
# COMPONENTES DE VISUALIZAÇÃO
# ─────────────────────────────────────────

def card_metrica(titulo, valor):
    st.markdown(f"""
    <div class="metric-card">
        <h2>{valor}</h2>
        <p>{titulo}</p>
    </div>
    """, unsafe_allow_html=True)


def secao_visao_geral(df):
    st.markdown('<h3 class="section-title">📊 Visão Geral</h3>', unsafe_allow_html=True)

    total = len(df)
    tma = df["duracao_segundos"].mean() if "duracao_segundos" in df.columns else None
    total_horas = df["duracao_segundos"].sum() / 3600 if "duracao_segundos" in df.columns else 0
    n_agentes = df["nome_agente"].nunique() if "nome_agente" in df.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        card_metrica("Total de Atendimentos", f"{total:,}")
    with col2:
        card_metrica("TMA Geral", formatar_tempo(tma))
    with col3:
        card_metrica("Horas em Atendimento", f"{total_horas:.1f}h")
    with col4:
        card_metrica("Agentes Ativos", str(n_agentes))

    st.markdown("---")

    # Atendimentos por dia
    if "data_atendimento" in df.columns:
        df_dia = (
            df.set_index("data_atendimento")
            .resample("D")["nome_agente"]
            .count()
            .reset_index()
            .rename(columns={"nome_agente": "Atendimentos", "data_atendimento": "Data"})
        )
        st.markdown("**Atendimentos por Dia**")
        st.line_chart(df_dia.set_index("Data"))

        # Heatmap de hora x dia da semana
        st.markdown("**Mapa de Calor: Hora do Dia × Dia da Semana**")
        df["hora"] = df["data_atendimento"].dt.hour
        df["dia_semana"] = df["data_atendimento"].dt.day_name()
        ordem_dias = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        traduzir_dias = {
            "Monday": "Seg", "Tuesday": "Ter", "Wednesday": "Qua",
            "Thursday": "Qui", "Friday": "Sex", "Saturday": "Sáb", "Sunday": "Dom"
        }
        df["dia_semana_pt"] = df["dia_semana"].map(traduzir_dias)
        ordem_pt = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

        pivot_heat = df.pivot_table(
            index="hora", columns="dia_semana_pt", values="duracao_segundos",
            aggfunc="count", fill_value=0
        )
        pivot_heat = pivot_heat.reindex(columns=[d for d in ordem_pt if d in pivot_heat.columns])
        st.dataframe(pivot_heat.style.background_gradient(cmap="Blues"), use_container_width=True)


def secao_por_assunto(df):
    st.markdown('<h3 class="section-title">🗂️ Análise por Assunto</h3>', unsafe_allow_html=True)

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Dados de assunto não disponíveis. Verifique se o arquivo Zendesk foi carregado corretamente.")
        return

    df_ass = (
        df.groupby("assunto")
        .agg(
            Atendimentos=("nome_agente", "count"),
            TMA_s=("duracao_segundos", "mean"),
            Tempo_Total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("Atendimentos", ascending=False)
    )
    df_ass["TMA"] = df_ass["TMA_s"].apply(formatar_tempo)
    df_ass["Tempo Total"] = df_ass["Tempo_Total_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por Assunto**")
        st.bar_chart(df_ass.set_index("assunto")["Atendimentos"])
    with col2:
        st.markdown("**TMA por Assunto (segundos)**")
        st.bar_chart(df_ass.set_index("assunto")["TMA_s"])

    # Pareto
    st.markdown("**Análise de Pareto dos Assuntos**")
    df_ass_sorted = df_ass.sort_values("Atendimentos", ascending=False).copy()
    df_ass_sorted["% Acumulado"] = (
        df_ass_sorted["Atendimentos"].cumsum() / df_ass_sorted["Atendimentos"].sum() * 100
    ).round(1)
    st.dataframe(
        df_ass_sorted[["assunto", "Atendimentos", "TMA", "Tempo Total", "% Acumulado"]],
        use_container_width=True
    )


def secao_por_agente(df):
    st.markdown('<h3 class="section-title">👥 Análise por Agente</h3>', unsafe_allow_html=True)

    if "nome_agente" not in df.columns:
        st.info("Coluna de agente não encontrada.")
        return

    tma_geral = df["duracao_segundos"].mean()

    df_ag = (
        df.groupby("nome_agente")
        .agg(
            Atendimentos=("duracao_segundos", "count"),
            TMA_s=("duracao_segundos", "mean"),
            Tempo_Total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("Atendimentos", ascending=False)
    )
    df_ag["TMA"] = df_ag["TMA_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["Tempo_Total_s"].apply(formatar_tempo)
    df_ag["vs. Média (s)"] = (df_ag["TMA_s"] - tma_geral).round(0)
    df_ag["Acima da Média"] = df_ag["vs. Média (s)"] > 0

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Ranking: Volume de Atendimentos**")
        st.bar_chart(df_ag.set_index("nome_agente")["Atendimentos"])
    with col2:
        st.markdown("**Ranking: TMA por Agente (segundos)**")
        st.bar_chart(df_ag.set_index("nome_agente")["TMA_s"])

    st.markdown("**Tabela Comparativa de Agentes**")
    st.dataframe(
        df_ag[["nome_agente", "Atendimentos", "TMA", "Tempo Total", "vs. Média (s)"]],
        use_container_width=True
    )

    # Dispersão: Volume × TMA
    st.markdown("**Dispersão: Volume × TMA** — identifique quadrantes de desempenho")
    st.scatter_chart(
        df_ag,
        x="Atendimentos",
        y="TMA_s",
        color="Acima da Média",
        size="Atendimentos"
    )


def secao_detalhe_agente(df):
    st.markdown('<h3 class="section-title">🔍 Detalhamento Individual do Agente</h3>', unsafe_allow_html=True)

    if "nome_agente" not in df.columns:
        st.info("Coluna de agente não encontrada.")
        return

    agentes = sorted(df["nome_agente"].dropna().unique())
    agente_sel = st.selectbox("Selecione o agente:", ["— Selecione —"] + list(agentes), key="sel_agente_detalhe")

    if agente_sel == "— Selecione —":
        return

    df_ag = df[df["nome_agente"] == agente_sel].copy()

    # KPIs do agente
    total_ag = len(df_ag)
    tma_ag = df_ag["duracao_segundos"].mean()
    tma_geral = df["duracao_segundos"].mean()
    total_horas_ag = df_ag["duracao_segundos"].sum() / 3600
    pct_volume = total_ag / len(df) * 100

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        card_metrica("Atendimentos", f"{total_ag:,}")
    with col2:
        card_metrica("TMA do Agente", formatar_tempo(tma_ag))
    with col3:
        card_metrica("TMA Geral (referência)", formatar_tempo(tma_geral))
    with col4:
        card_metrica("% do Volume Total", f"{pct_volume:.1f}%")

    st.markdown(f"**Horas em Atendimento:** {total_horas_ag:.1f}h")

    # Evolução do TMA ao longo do tempo
    if "data_atendimento" in df_ag.columns:
        st.markdown("**Evolução do TMA ao longo do tempo (por dia)**")
        df_ag_dia = (
            df_ag.set_index("data_atendimento")
            .resample("D")["duracao_segundos"]
            .mean()
            .reset_index()
            .rename(columns={"duracao_segundos": "TMA (s)", "data_atendimento": "Data"})
        )
        st.line_chart(df_ag_dia.set_index("Data"))

        st.markdown("**Volume de Atendimentos por Dia**")
        df_ag_vol = (
            df_ag.set_index("data_atendimento")
            .resample("D")["duracao_segundos"]
            .count()
            .reset_index()
            .rename(columns={"duracao_segundos": "Atendimentos", "data_atendimento": "Data"})
        )
        st.bar_chart(df_ag_vol.set_index("Data"))

    # Assuntos do agente
    if "assunto" in df_ag.columns and not df_ag["assunto"].isna().all():
        st.markdown("**Assuntos atendidos por este agente**")
        df_ag_ass = (
            df_ag.groupby("assunto")
            .agg(Atendimentos=("duracao_segundos", "count"), TMA_s=("duracao_segundos", "mean"))
            .reset_index()
            .sort_values("Atendimentos", ascending=False)
        )
        df_ag_ass["TMA"] = df_ag_ass["TMA_s"].apply(formatar_tempo)
        st.dataframe(df_ag_ass[["assunto", "Atendimentos", "TMA"]], use_container_width=True)

    # Distribuição de duração
    st.markdown("**Distribuição das durações de atendimento (segundos)**")
    st.bar_chart(
        df_ag["duracao_segundos"].dropna().value_counts(bins=10).sort_index()
    )

    # Tabela completa dos atendimentos
    st.markdown("**Todos os atendimentos deste agente no período**")
    colunas_tabela = [c for c in ["data_atendimento", "fila", "assunto", "duracao_str", "duracao_segundos"] if c in df_ag.columns]
    st.dataframe(
        df_ag[colunas_tabela].sort_values("data_atendimento", ascending=False),
        use_container_width=True
    )


def secao_qualidade_dados(df, df_zen, df_gen):
    st.markdown('<h3 class="section-title">🔧 Qualidade dos Dados</h3>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Genesys**")
        sem_agente = df_gen["nome_agente"].isna().sum() if "nome_agente" in df_gen.columns else 0
        sem_duracao = df_gen["duracao_segundos"].isna().sum() if "duracao_segundos" in df_gen.columns else 0
        duracao_zero = (df_gen["duracao_segundos"] == 0).sum() if "duracao_segundos" in df_gen.columns else 0
        st.metric("Registros sem agente", sem_agente)
        st.metric("Registros sem duração", sem_duracao)
        st.metric("Atendimentos com duração zero", duracao_zero)

    with col2:
        st.markdown("**Zendesk**")
        if not df_zen.empty:
            sem_assunto = df_zen["assunto"].isna().sum() if "assunto" in df_zen.columns else 0
            sem_genesys = df_zen["id_genesys"].isna().sum() if "id_genesys" in df_zen.columns else 0
            st.metric("Tickets sem assunto", sem_assunto)
            st.metric("Tickets sem ID Genesys", sem_genesys)
        else:
            st.info("Zendesk não carregado.")

    # Outliers de duração
    if "duracao_segundos" in df.columns:
        q1 = df["duracao_segundos"].quantile(0.25)
        q3 = df["duracao_segundos"].quantile(0.75)
        iqr = q3 - q1
        outliers = df[df["duracao_segundos"] > q3 + 3 * iqr]
        st.markdown(f"**Atendimentos com duração muito alta (outliers acima de Q3 + 3×IQR = {formatar_tempo(q3 + 3*iqr)}):** {len(outliers)} registros")
        if not outliers.empty:
            colunas_out = [c for c in ["nome_agente", "data_atendimento", "duracao_str", "duracao_segundos"] if c in outliers.columns]
            st.dataframe(outliers[colunas_out].head(20), use_container_width=True)


# ─────────────────────────────────────────
# FILTROS DA SIDEBAR
# ─────────────────────────────────────────

def aplicar_filtros(df):
    """Renderiza filtros na sidebar e retorna o DataFrame filtrado."""
    st.sidebar.markdown("---")
    st.sidebar.header("🎛️ Filtros")

    df_filtrado = df.copy()

    # Filtro de período
    if "data_atendimento" in df.columns and not df["data_atendimento"].isna().all():
        min_data = df["data_atendimento"].min().date()
        max_data = df["data_atendimento"].max().date()

        periodo = st.sidebar.date_input(
            "Período de análise",
            value=(min_data, max_data),
            min_value=min_data,
            max_value=max_data,
            key="filtro_periodo"
        )
        if isinstance(periodo, (list, tuple)) and len(periodo) == 2:
            inicio, fim = periodo
            df_filtrado = df_filtrado[
                (df_filtrado["data_atendimento"].dt.date >= inicio) &
                (df_filtrado["data_atendimento"].dt.date <= fim)
            ]

    # Filtro por agente
    if "nome_agente" in df.columns:
        agentes = sorted(df_filtrado["nome_agente"].dropna().unique())
        sel_agentes = st.sidebar.multiselect("Agentes", options=agentes, default=agentes, key="filtro_agentes")
        if sel_agentes:
            df_filtrado = df_filtrado[df_filtrado["nome_agente"].isin(sel_agentes)]

    # Filtro por assunto
    if "assunto" in df.columns and not df["assunto"].isna().all():
        assuntos = sorted(df_filtrado["assunto"].dropna().unique())
        sel_assuntos = st.sidebar.multiselect("Assuntos", options=assuntos, default=assuntos, key="filtro_assuntos")
        if sel_assuntos:
            df_filtrado = df_filtrado[df_filtrado["assunto"].isin(sel_assuntos)]

    # Filtro por fila
    if "fila" in df.columns and not df["fila"].isna().all():
        filas = sorted(df_filtrado["fila"].dropna().unique())
        sel_filas = st.sidebar.multiselect("Fila", options=filas, default=filas, key="filtro_filas")
        if sel_filas:
            df_filtrado = df_filtrado[df_filtrado["fila"].isin(sel_filas)]

    # Filtro por hora do dia
    hora_range = st.sidebar.slider("Horário do atendimento", 0, 23, (0, 23), key="filtro_hora")
    if "data_atendimento" in df_filtrado.columns:
        df_filtrado = df_filtrado[
            (df_filtrado["data_atendimento"].dt.hour >= hora_range[0]) &
            (df_filtrado["data_atendimento"].dt.hour <= hora_range[1])
        ]

    st.sidebar.markdown(f"**Registros no filtro:** {len(df_filtrado):,}")
    return df_filtrado


# ─────────────────────────────────────────
# UPLOAD E PROCESSAMENTO
# ─────────────────────────────────────────

def secao_upload():
    st.sidebar.header("📂 Upload de Arquivos")
    st.sidebar.markdown("Faça o upload dos dois arquivos mensais. Os dados serão acumulados automaticamente.")

    arquivo_zen = st.sidebar.file_uploader(
        "Zendesk (XLSX)",
        type=["xlsx", "xls"],
        key="upload_zen"
    )
    arquivo_gen = st.sidebar.file_uploader(
        "Genesys (CSV)",
        type=["csv"],
        key="upload_gen"
    )

    if arquivo_zen is not None and arquivo_gen is not None:
        if st.sidebar.button("✅ Processar e Acumular", key="btn_processar"):
            with st.spinner("Processando arquivos..."):
                df_zen = carregar_zendesk(arquivo_zen)
                df_gen = carregar_genesys(arquivo_gen)
                df_novo = integrar_dados(df_zen, df_gen)

                if df_novo.empty:
                    st.sidebar.error("Nenhum dado foi gerado. Verifique os arquivos.")
                    return

                df_hist = carregar_historico()
                df_acumulado = adicionar_ao_historico(df_novo, df_hist)

                if salvar_historico(df_acumulado):
                    st.sidebar.success(
                        f"✅ {len(df_novo):,} registros processados.\n"
                        f"📦 Histórico total: {len(df_acumulado):,} registros."
                    )
                    # Limpa cache para forçar releitura
                    st.cache_data.clear()

    # Botão para limpar histórico
    with st.sidebar.expander("⚠️ Gerenciar Histórico"):
        if st.button("🗑️ Apagar todo o histórico", key="btn_apagar"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                st.success("Histórico apagado.")
                st.cache_data.clear()
                st.rerun()


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    st.title("📞 Dashboard de Atendimentos — Call Center")
    st.markdown("Análise consolidada de atendimentos Zendesk + Genesys | Fila URA_CORSAN")

    # Upload na sidebar
    secao_upload()

    # Carrega histórico
    df_hist = carregar_historico()

    if df_hist.empty:
        st.info("👆 Nenhum dado histórico encontrado. Faça o upload dos arquivos na barra lateral para começar.")
        return

    # Converte datas caso venham como string do parquet
    if "data_atendimento" in df_hist.columns:
        df_hist["data_atendimento"] = pd.to_datetime(df_hist["data_atendimento"], errors="coerce")

    # Aplica filtros
    df_filtrado = aplicar_filtros(df_hist)

    if df_filtrado.empty:
        st.warning("Nenhum dado encontrado para os filtros selecionados.")
        return

    # Navegação por abas
    aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "📊 Visão Geral",
        "🗂️ Por Assunto",
        "👥 Por Agente",
        "🔍 Detalhe do Agente",
        "🔧 Qualidade dos Dados"
    ])

    # Carrega os DFs brutos para a aba de qualidade (sem filtro de período)
    df_zen_raw = pd.DataFrame()
    df_gen_raw = pd.DataFrame()

    with aba1:
        secao_visao_geral(df_filtrado)

    with aba2:
        secao_por_assunto(df_filtrado)

    with aba3:
        secao_por_agente(df_filtrado)

    with aba4:
        secao_detalhe_agente(df_filtrado)

    with aba5:
        secao_qualidade_dados(df_filtrado, df_zen_raw, df_gen_raw)


if __name__ == "__main__":
    main()
