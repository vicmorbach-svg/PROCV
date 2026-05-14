import streamlit as st
import pandas as pd
import numpy as np
import os
import io
from datetime import datetime

# ─────────────────────────────────────────
# CONFIGURAÇÕES GERAIS
# ─────────────────────────────────────────
HISTORICO_PATH = "historico_atendimentos.parquet"

st.set_page_config(
    layout="wide",
    page_title="Dashboard Call Center",
    page_icon="📞"
)

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
# UTILITÁRIOS
# ─────────────────────────────────────────

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
    if pd.isna(valor) or str(valor).strip() in ("", "-", "nan"):
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


def card_metrica(titulo, valor):
    st.markdown(f"""
    <div class="metric-card">
        <h2>{valor}</h2>
        <p>{titulo}</p>
    </div>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────
# CARREGAMENTO DO ZENDESK
# ─────────────────────────────────────────

def carregar_zendesk(arquivo):
    """
    Carrega o XLSX do Zendesk.
    Colunas relevantes:
      - 'ID do ticket'
      - 'Assuntos do Ticket'
      - 'Criação do ticket - Carimbo de data/hora'
      - 'ID Genesys'   ← chave de cruzamento
      - 'Matricula'
      - 'Tickets'
    """
    try:
        df = pd.read_excel(arquivo, engine="openpyxl")
        df.columns = df.columns.str.strip()

        # Garante que as colunas essenciais existem
        colunas_esperadas = {
            "ID do ticket":                              "ticket_id",
            "Assuntos do Ticket":                        "assunto",
            "Criação do ticket - Carimbo de data/hora":  "data_criacao_zen",
            "ID Genesys":                                "id_genesys",       # ← chave
            "Matricula":                                 "matricula",
            "Tickets":                                   "tickets_zen",
        }

        renomear = {k: v for k, v in colunas_esperadas.items() if k in df.columns}
        df = df.rename(columns=renomear)

        if "data_criacao_zen" in df.columns:
            df["data_criacao_zen"] = pd.to_datetime(
                df["data_criacao_zen"], errors="coerce", dayfirst=False
            )

        if "ticket_id" in df.columns:
            df["ticket_id"] = df["ticket_id"].astype(str).str.strip()

        # Normaliza a chave: string, strip, lowercase para facilitar o match
        if "id_genesys" in df.columns:
            df["id_genesys"] = (
                df["id_genesys"]
                .astype(str)
                .str.strip()
                .str.lower()
                .replace("nan", np.nan)
            )

        return df

    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# CARREGAMENTO DO GENESYS
# ─────────────────────────────────────────

def carregar_genesys(arquivo):
    """
    Carrega o CSV do Genesys (separador pipe |, metadados no topo).
    Colunas relevantes:
      - 'ID de conversa'            ← chave de cruzamento
      - 'Usuários – Interagiram'    → nome_agente
      - 'Data'                      → data_atendimento
      - 'Duração'                   → duracao_str / duracao_segundos
      - 'ANI'                       → número do chamador
      - 'Tipo de desconexão'
      - 'Filtros'                   → fila
    """
    try:
        conteudo_bytes = arquivo.read()

        # Tenta UTF-8, depois latin-1
        for enc in ("utf-8", "latin-1", "utf-8-sig"):
            try:
                conteudo = conteudo_bytes.decode(enc)
                break
            except Exception:
                continue

        linhas = conteudo.splitlines()

        # Localiza a linha de cabeçalho real (contém "Usuários" ou "ID de conversa")
        idx_header = None
        for i, linha in enumerate(linhas):
            linha_lower = linha.lower()
            if "id de conversa" in linha_lower or "usuários" in linha_lower or "usuari" in linha_lower:
                idx_header = i
                break

        # Fallback: procura qualquer linha com pelo menos 4 pipes
        if idx_header is None:
            for i, linha in enumerate(linhas):
                if linha.count("|") >= 4:
                    idx_header = i
                    break

        if idx_header is None:
            st.error("Não foi possível identificar o cabeçalho no arquivo Genesys.")
            return pd.DataFrame()

        csv_limpo = "\n".join(linhas[idx_header:])
        df = pd.read_csv(
            io.StringIO(csv_limpo),
            sep="|",
            engine="python",
            skipinitialspace=True,
            dtype=str
        )

        # Remove colunas e linhas completamente vazias
        df = df.dropna(axis=1, how="all")
        df = df.dropna(axis=0, how="all")
        df.columns = [str(c).strip() for c in df.columns]

        # ── Mapeamento dinâmico de colunas ──────────────────────────────────
        renomear = {}
        for col in df.columns:
            col_lower = col.lower().strip()
            if "id de conversa" in col_lower or "conversation" in col_lower:
                renomear[col] = "id_genesys"          # ← chave
            elif "usuário" in col_lower or "usuario" in col_lower or "interagi" in col_lower:
                renomear[col] = "nome_agente"
            elif col_lower == "data" or "data/hora" in col_lower:
                renomear[col] = "data_atendimento"
            elif "duração" in col_lower or "duracao" in col_lower:
                renomear[col] = "duracao_str"
            elif "filtro" in col_lower:
                renomear[col] = "filtros"
            elif "ani" in col_lower:
                renomear[col] = "ani"
            elif "desconexão" in col_lower or "desconexao" in col_lower:
                renomear[col] = "tipo_desconexao"
            elif "exportação" in col_lower or "exportacao" in col_lower:
                renomear[col] = "exportacao"

        df = df.rename(columns=renomear)

        # Se não encontrou coluna de duração pelo nome, tenta pelo padrão HH:MM:SS
        if "duracao_str" not in df.columns:
            for col in df.columns:
                amostra = df[col].dropna().astype(str).head(20)
                if amostra.str.match(r"^\s*\d{1,2}:\d{2}:\d{2}\s*$").sum() >= 3:
                    df = df.rename(columns={col: "duracao_str"})
                    break

        # Normaliza chave: string, strip, lowercase
        if "id_genesys" in df.columns:
            df["id_genesys"] = (
                df["id_genesys"]
                .astype(str)
                .str.strip()
                .str.lower()
                .replace("nan", np.nan)
            )
        else:
            # Se a coluna de ID ainda não foi encontrada, exibe aviso e lista colunas disponíveis
            st.warning(
                f"⚠️ Coluna 'ID de conversa' não encontrada no Genesys. "
                f"Colunas disponíveis: {list(df.columns)}"
            )

        # Extrai fila da coluna filtros
        if "filtros" in df.columns:
            df["fila"] = df["filtros"].str.extract(r"Fila:\s*(.+)", expand=False).str.strip()
        else:
            df["fila"] = "URA_CORSAN"

        # Converte duração
        if "duracao_str" in df.columns:
            df["duracao_str"] = df["duracao_str"].astype(str).str.strip()
            df["duracao_segundos"] = df["duracao_str"].apply(duracao_para_segundos)
        else:
            df["duracao_segundos"] = np.nan

        # Converte data
        if "data_atendimento" in df.columns:
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento"].astype(str).str.strip(),
                errors="coerce",
                dayfirst=True
            )

        # Limpa nome do agente
        if "nome_agente" in df.columns:
            df["nome_agente"] = df["nome_agente"].astype(str).str.strip()
            # Remove linhas de metadados (nome muito curto ou vazio)
            df = df[df["nome_agente"].str.len() > 2]

        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# INTEGRAÇÃO (MERGE) DOS DOIS ARQUIVOS
# ─────────────────────────────────────────

def integrar_dados(df_zen, df_gen):
    """
    Faz o cruzamento pelo ID de conversa do Genesys.
    Base: Genesys (tem a duração e dados de agente).
    Enriquece com: assunto, ticket_id, matricula do Zendesk.
    """
    if df_gen.empty:
        st.error("Arquivo Genesys vazio após processamento.")
        return pd.DataFrame()

    if df_zen.empty or "id_genesys" not in df_zen.columns:
        st.warning("Zendesk sem dados ou sem coluna 'ID Genesys'. Continuando só com Genesys.")
        return df_gen.copy()

    if "id_genesys" not in df_gen.columns:
        st.error("Coluna 'ID de conversa' não encontrada no Genesys após processamento.")
        return df_gen.copy()

    # Colunas do Zendesk para trazer no merge
    colunas_zen = ["id_genesys"]
    for col in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]:
        if col in df_zen.columns:
            colunas_zen.append(col)

    df_zen_slim = df_zen[colunas_zen].copy()

    # Merge: base Genesys + enriquecimento Zendesk (left join)
    df = pd.merge(
        df_gen,
        df_zen_slim,
        on="id_genesys",
        how="left",
        suffixes=("_gen", "_zen")
    )

    # Log de aproveitamento do merge
    total = len(df)
    com_assunto = df["assunto"].notna().sum() if "assunto" in df.columns else 0
    st.info(
        f"✅ Merge concluído: {total:,} registros Genesys | "
        f"{com_assunto:,} cruzados com Zendesk ({com_assunto/total*100:.1f}%)"
    )

    return df


# ─────────────────────────────────────────
# HISTÓRICO (PARQUET)
# ─────────────────────────────────────────

def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            df = pd.read_parquet(HISTORICO_PATH)
            if "data_atendimento" in df.columns:
                df["data_atendimento"] = pd.to_datetime(df["data_atendimento"], errors="coerce")
            return df
        except Exception as e:
            st.error(f"Erro ao carregar histórico: {e}")
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

    df_combinado = pd.concat([df_hist, df_novo], ignore_index=True)

    # Deduplicação: por id_genesys (se existir) ou por agente + data + duração
    if "id_genesys" in df_combinado.columns:
        # Mantém linhas sem id_genesys (podem ser legítimas)
        com_id = df_combinado[df_combinado["id_genesys"].notna()]
        sem_id = df_combinado[df_combinado["id_genesys"].isna()]
        com_id = com_id.drop_duplicates(subset=["id_genesys"], keep="last")
        df_combinado = pd.concat([com_id, sem_id], ignore_index=True)
    else:
        chaves = [c for c in ["nome_agente", "data_atendimento", "duracao_segundos"] if c in df_combinado.columns]
        if chaves:
            df_combinado = df_combinado.drop_duplicates(subset=chaves, keep="last")

    return df_combinado.reset_index(drop=True)


# ─────────────────────────────────────────
# FILTROS
# ─────────────────────────────────────────

def aplicar_filtros(df):
    st.sidebar.markdown("---")
    st.sidebar.header("🎛️ Filtros")

    df_f = df.copy()

    # Período
    if "data_atendimento" in df_f.columns and df_f["data_atendimento"].notna().any():
        min_data = df_f["data_atendimento"].min().date()
        max_data = df_f["data_atendimento"].max().date()
        periodo = st.sidebar.date_input(
            "Período",
            value=(min_data, max_data),
            min_value=min_data,
            max_value=max_data
        )
        if isinstance(periodo, (list, tuple)) and len(periodo) == 2:
            ini, fim = periodo
            df_f = df_f[
                (df_f["data_atendimento"].dt.date >= ini) &
                (df_f["data_atendimento"].dt.date <= fim)
            ]

    # Hora do dia
    hora_range = st.sidebar.slider("Horário do atendimento (hora)", 0, 23, (0, 23))
    if "data_atendimento" in df_f.columns:
        df_f = df_f[
            (df_f["data_atendimento"].dt.hour >= hora_range[0]) &
            (df_f["data_atendimento"].dt.hour <= hora_range[1])
        ]

    # Agente
    if "nome_agente" in df_f.columns:
        agentes = sorted(df_f["nome_agente"].dropna().unique())
        sel = st.sidebar.multiselect("Agente", options=agentes, default=agentes)
        if sel:
            df_f = df_f[df_f["nome_agente"].isin(sel)]

    # Assunto
    if "assunto" in df_f.columns and df_f["assunto"].notna().any():
        assuntos = sorted(df_f["assunto"].dropna().unique())
        sel_ass = st.sidebar.multiselect("Assunto", options=assuntos, default=assuntos)
        if sel_ass:
            df_f = df_f[df_f["assunto"].isin(sel_ass)]

    # Fila
    if "fila" in df_f.columns and df_f["fila"].notna().any():
        filas = sorted(df_f["fila"].dropna().unique())
        sel_fila = st.sidebar.multiselect("Fila", options=filas, default=filas)
        if sel_fila:
            df_f = df_f[df_f["fila"].isin(sel_fila)]

    st.sidebar.markdown(f"**📌 Registros filtrados:** `{len(df_f):,}`")
    return df_f


# ─────────────────────────────────────────
# SEÇÕES DO DASHBOARD
# ─────────────────────────────────────────

def secao_visao_geral(df):
    st.markdown('<h3 class="section-title">📊 Visão Geral</h3>', unsafe_allow_html=True)

    total         = len(df)
    tma           = df["duracao_segundos"].mean() if "duracao_segundos" in df.columns else None
    total_horas   = (df["duracao_segundos"].sum() / 3600) if "duracao_segundos" in df.columns else 0
    n_agentes     = df["nome_agente"].nunique() if "nome_agente" in df.columns else 0
    taxa_cruzamento = (
        df["assunto"].notna().sum() / total * 100
        if "assunto" in df.columns and total > 0 else 0
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: card_metrica("Total de Atendimentos", f"{total:,}")
    with col2: card_metrica("TMA Geral", formatar_tempo(tma))
    with col3: card_metrica("Horas em Atendimento", f"{total_horas:.1f}h")
    with col4: card_metrica("Agentes Ativos", str(n_agentes))
    with col5: card_metrica("Cruzados c/ Zendesk", f"{taxa_cruzamento:.0f}%")

    st.markdown("---")

    if "data_atendimento" in df.columns:
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**📈 Atendimentos por Dia**")
            df_dia = (
                df.set_index("data_atendimento")
                .resample("D")["nome_agente"]
                .count()
                .reset_index()
                .rename(columns={"nome_agente": "Atendimentos", "data_atendimento": "Data"})
            )
            st.line_chart(df_dia.set_index("Data"))

        with col_b:
            st.markdown("**⏱️ TMA Médio por Dia (segundos)**")
            df_tma_dia = (
                df.set_index("data_atendimento")
                .resample("D")["duracao_segundos"]
                .mean()
                .reset_index()
                .rename(columns={"duracao_segundos": "TMA (s)", "data_atendimento": "Data"})
            )
            st.line_chart(df_tma_dia.set_index("Data"))

        # Mapa de calor: hora × dia da semana
        st.markdown("**🔥 Mapa de Calor: Volume por Hora × Dia da Semana**")
        traduzir = {
            "Monday": "Seg", "Tuesday": "Ter", "Wednesday": "Qua",
            "Thursday": "Qui", "Friday": "Sex", "Saturday": "Sáb", "Sunday": "Dom"
        }
        ordem_pt = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        df_heat = df.copy()
        df_heat["hora"] = df_heat["data_atendimento"].dt.hour
        df_heat["dia_semana"] = df_heat["data_atendimento"].dt.day_name().map(traduzir)
        pivot = df_heat.pivot_table(
            index="hora", columns="dia_semana",
            values="duracao_segundos", aggfunc="count", fill_value=0
        )
        pivot = pivot.reindex(columns=[d for d in ordem_pt if d in pivot.columns])
        st.dataframe(
            pivot.style.background_gradient(cmap="YlOrRd"),
            use_container_width=True
        )


def secao_por_assunto(df):
    st.markdown('<h3 class="section-title">🗂️ Análise por Assunto</h3>', unsafe_allow_html=True)

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Dados de assunto não disponíveis — verifique se o cruzamento com o Zendesk funcionou.")
        return

    df_ass = (
        df.groupby("assunto", dropna=True)
        .agg(
            Atendimentos=("nome_agente", "count"),
            TMA_s=("duracao_segundos", "mean"),
            Tempo_Total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("Atendimentos", ascending=False)
    )
    df_ass["TMA"]          = df_ass["TMA_s"].apply(formatar_tempo)
    df_ass["Tempo Total"]  = df_ass["Tempo_Total_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por Assunto**")
        st.bar_chart(df_ass.set_index("assunto")["Atendimentos"])
    with col2:
        st.markdown("**TMA por Assunto (segundos)**")
        st.bar_chart(df_ass.set_index("assunto")["TMA_s"])

    # Pareto
    st.markdown("**📊 Pareto — Assuntos que representam 80% do volume**")
    df_pareto = df_ass.copy()
    df_pareto["% Acumulado"] = (
        df_pareto["Atendimentos"].cumsum() / df_pareto["Atendimentos"].sum() * 100
    ).round(1)
    df_pareto["Pareto 80%"] = df_pareto["% Acumulado"] <= 80
    st.dataframe(
        df_pareto[["assunto", "Atendimentos", "TMA", "Tempo Total", "% Acumulado"]],
        use_container_width=True
    )


def secao_por_agente(df):
    st.markdown('<h3 class="section-title">👥 Análise por Agente</h3>', unsafe_allow_html=True)

    if "nome_agente" not in df.columns:
        st.info("Coluna de agente não encontrada.")
        return

    tma_geral = df["duracao_segundos"].mean()

    df_ag = (
        df.groupby("nome_agente", dropna=True)
        .agg(
            Atendimentos=("duracao_segundos", "count"),
            TMA_s=("duracao_segundos", "mean"),
            Tempo_Total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("Atendimentos", ascending=False)
    )
    df_ag["TMA"]          = df_ag["TMA_s"].apply(formatar_tempo)
    df_ag["Tempo Total"]  = df_ag["Tempo_Total_s"].apply(formatar_tempo)
    df_ag["Δ vs Média"]   = (df_ag["TMA_s"] - tma_geral).round(0).astype(int)
    df_ag["Acima Média"]  = df_ag["Δ vs Média"] > 0

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🏆 Ranking por Volume de Atendimentos**")
        st.bar_chart(df_ag.set_index("nome_agente")["Atendimentos"])
    with col2:
        st.markdown("**⏱️ TMA por Agente (segundos)**")
        st.bar_chart(df_ag.set_index("nome_agente")["TMA_s"])

    st.markdown("**📋 Tabela Comparativa — todos os agentes**")
    st.dataframe(
        df_ag[["nome_agente", "Atendimentos", "TMA", "Tempo Total", "Δ vs Média"]],
        use_container_width=True
    )

    st.markdown("**🎯 Dispersão: Volume × TMA** — quadrantes de desempenho")
    st.scatter_chart(
        df_ag,
        x="Atendimentos",
        y="TMA_s",
        color="Acima Média",
        size="Atendimentos"
    )


def secao_detalhe_agente(df):
    st.markdown('<h3 class="section-title">🔍 Detalhamento Individual do Agente</h3>', unsafe_allow_html=True)

    if "nome_agente" not in df.columns:
        st.info("Coluna de agente não encontrada.")
        return

    agentes = sorted(df["nome_agente"].dropna().unique())
    agente_sel = st.selectbox(
        "Selecione o agente:",
        ["— Selecione —"] + list(agentes),
        key="sel_agente_detalhe"
    )

    if agente_sel == "— Selecione —":
        return

    df_ag = df[df["nome_agente"] == agente_sel].copy()

    total_ag       = len(df_ag)
    tma_ag         = df_ag["duracao_segundos"].mean()
    tma_geral      = df["duracao_segundos"].mean()
    total_horas_ag = df_ag["duracao_segundos"].sum() / 3600
    pct_volume     = total_ag / len(df) * 100

    col1, col2, col3, col4 = st.columns(4)
    with col1: card_metrica("Atendimentos", f"{total_ag:,}")
    with col2: card_metrica("TMA do Agente", formatar_tempo(tma_ag))
    with col3: card_metrica("TMA Geral (ref.)", formatar_tempo(tma_geral))
    with col4: card_metrica("% do Volume Total", f"{pct_volume:.1f}%")

    st.markdown(f"**⏰ Total em atendimento:** {total_horas_ag:.1f}h")

    if "data_atendimento" in df_ag.columns:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**📈 Evolução do TMA por Dia**")
            df_ag_tma = (
                df_ag.set_index("data_atendimento")
                .resample("D")["duracao_segundos"]
                .mean()
                .reset_index()
                .rename(columns={"duracao_segundos": "TMA (s)", "data_atendimento": "Data"})
            )
            st.line_chart(df_ag_tma.set_index("Data"))

        with col_b:
            st.markdown("**📊 Volume de Atendimentos por Dia**")
            df_ag_vol = (
                df_ag.set_index("data_atendimento")
                .resample("D")["duracao_segundos"]
                .count()
                .reset_index()
                .rename(columns={"duracao_segundos": "Atendimentos", "data_atendimento": "Data"})
            )
            st.bar_chart(df_ag_vol.set_index("Data"))

    if "assunto" in df_ag.columns and df_ag["assunto"].notna().any():
        st.markdown("**🗂️ Assuntos atendidos por este agente**")
        df_ag_ass = (
            df_ag.groupby("assunto", dropna=True)
            .agg(
                Atendimentos=("duracao_segundos", "count"),
                TMA_s=("duracao_segundos", "mean")
            )
            .reset_index()
            .sort_values("Atendimentos", ascending=False)
        )
        df_ag_ass["TMA"] = df_ag_ass["TMA_s"].apply(formatar_tempo)

        col_c, col_d = st.columns(2)
        with col_c:
            st.bar_chart(df_ag_ass.set_index("assunto")["Atendimentos"])
        with col_d:
            st.dataframe(df_ag_ass[["assunto", "Atendimentos", "TMA"]], use_container_width=True)

    # Comparação agente × time
    st.markdown("**⚖️ Comparação: este agente vs. média do time**")
    comparativo = pd.DataFrame({
        "Métrica": ["TMA (s)", "Atendimentos"],
        agente_sel: [tma_ag, total_ag],
        "Média do Time": [tma_geral, len(df) / df["nome_agente"].nunique()]
    })
    st.dataframe(comparativo, use_container_width=True)

    # Tabela completa de atendimentos do agente
    st.markdown("**📋 Todos os atendimentos no período selecionado**")
    colunas_tabela = [c for c in [
        "data_atendimento", "ticket_id", "assunto",
        "duracao_str", "duracao_segundos", "fila",
        "ani", "tipo_desconexao", "id_genesys"
    ] if c in df_ag.columns]
    st.dataframe(
        df_ag[colunas_tabela].sort_values("data_atendimento", ascending=False),
        use_container_width=True
    )


def secao_qualidade_dados(df):
    st.markdown('<h3 class="section-title">🔧 Qualidade dos Dados</h3>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    with col1:
        sem_agente   = df["nome_agente"].isna().sum() if "nome_agente" in df.columns else 0
        sem_duracao  = df["duracao_segundos"].isna().sum() if "duracao_segundos" in df.columns else 0
        dur_zero     = (df["duracao_segundos"] == 0).sum() if "duracao_segundos" in df.columns else 0
        st.metric("Sem nome de agente", sem_agente)
        st.metric("Sem duração", sem_duracao)
        st.metric("Duração zero", dur_zero)

    with col2:
        sem_assunto  = df["assunto"].isna().sum() if "assunto" in df.columns else "-"
        sem_id       = df["id_genesys"].isna().sum() if "id_genesys" in df.columns else "-"
        st.metric("Sem assunto (não cruzou Zendesk)", sem_assunto)
        st.metric("Sem ID Genesys", sem_id)

    with col3:
        if "duracao_segundos" in df.columns:
            q3  = df["duracao_segundos"].quantile(0.75)
            iqr = df["duracao_segundos"].quantile(0.75) - df["duracao_segundos"].quantile(0.25)
            lim = q3 + 3 * iqr
            outliers = df[df["duracao_segundos"] > lim]
            st.metric("Outliers de duração (> Q3+3×IQR)", len(outliers))
            st.caption(f"Limite: {formatar_tempo(lim)}")

    if "duracao_segundos" in df.columns:
        q3  = df["duracao_segundos"].quantile(0.75)
        iqr = df["duracao_segundos"].quantile(0.75) - df["duracao_segundos"].quantile(0.25)
        lim = q3 + 3 * iqr
        outliers = df[df["duracao_segundos"] > lim]
        if not outliers.empty:
            st.markdown("**Atendimentos com duração muito acima do normal:**")
            cols_out = [c for c in ["nome_agente", "data_atendimento", "duracao_str", "assunto"] if c in outliers.columns]
            st.dataframe(outliers[cols_out].sort_values("duracao_segundos" if "duracao_segundos" in outliers.columns else cols_out[0], ascending=False).head(20), use_container_width=True)


# ─────────────────────────────────────────
# UPLOAD + PROCESSAMENTO
# ─────────────────────────────────────────

def secao_upload():
    st.sidebar.header("📂 Upload Mensal")
    st.sidebar.markdown(
        "Suba os dois arquivos mensais. "
        "Os dados são acumulados automaticamente a cada upload."
    )

    arq_zen = st.sidebar.file_uploader("Zendesk (XLSX)", type=["xlsx", "xls"], key="up_zen")
    arq_gen = st.sidebar.file_uploader("Genesys (CSV)", type=["csv"], key="up_gen")

    if arq_zen and arq_gen:
        if st.sidebar.button("✅ Processar e Acumular", key="btn_proc"):
            with st.spinner("Processando..."):
                df_zen = carregar_zendesk(arq_zen)
                df_gen = carregar_genesys(arq_gen)

                if df_zen.empty and df_gen.empty:
                    st.sidebar.error("Ambos os arquivos vieram vazios. Verifique os formatos.")
                    return

                df_novo = integrar_dados(df_zen, df_gen)

                if df_novo.empty:
                    st.sidebar.error("Nenhum dado gerado após o merge. Veja os avisos acima.")
                    return

                df_hist = carregar_historico()
                df_acumulado = adicionar_ao_historico(df_novo, df_hist)

                if salvar_historico(df_acumulado):
                    st.sidebar.success(
                        f"✅ {len(df_novo):,} registros processados.\n\n"
                        f"📦 Histórico total: {len(df_acumulado):,} registros."
                    )
                    st.cache_data.clear()
                    st.rerun()

    with st.sidebar.expander("⚠️ Gerenciar Histórico"):
        if st.button("🗑️ Apagar todo o histórico", key="btn_apagar"):
            if os.path.exists(HISTORICO_PATH):
                os.remove(HISTORICO_PATH)
                st.success("Histórico apagado com sucesso.")
                st.cache_data.clear()
                st.rerun()

    # Download do histórico completo
    if os.path.exists(HISTORICO_PATH):
        df_dl = carregar_historico()
        if not df_dl.empty:
            csv_bytes = df_dl.to_csv(index=False).encode("utf-8")
            st.sidebar.download_button(
                label="⬇️ Baixar histórico completo (CSV)",
                data=csv_bytes,
                file_name="historico_atendimentos.csv",
                mime="text/csv",
                key="btn_dl_hist"
            )


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    st.title("📞 Dashboard de Atendimentos — Call Center")
    st.markdown(
        "Análise consolidada de atendimentos **Zendesk + Genesys** | "
        "Cruzamento por **ID de Conversa**"
    )

    secao_upload()

    df_hist = carregar_historico()

    if df_hist.empty:
        st.info(
            "👆 Nenhum dado encontrado. "
            "Faça o upload dos arquivos Zendesk e Genesys na barra lateral para começar."
        )
        return

    df_filtrado = aplicar_filtros(df_hist)

    if df_filtrado.empty:
        st.warning("⚠️ Nenhum registro encontrado para os filtros selecionados.")
        return

    aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "📊 Visão Geral",
        "🗂️ Por Assunto",
        "👥 Por Agente",
        "🔍 Detalhe do Agente",
        "🔧 Qualidade dos Dados"
    ])

    with aba1:
        secao_visao_geral(df_filtrado)
    with aba2:
        secao_por_assunto(df_filtrado)
    with aba3:
        secao_por_agente(df_filtrado)
    with aba4:
        secao_detalhe_agente(df_filtrado)
    with aba5:
        secao_qualidade_dados(df_filtrado)


if __name__ == "__main__":
    main()
