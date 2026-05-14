import streamlit as st
import pandas as pd
import numpy as np
import os
import io
import csv

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
    """Converte HH:MM:SS, HH:MM:SS.mmm ou MM:SS em segundos inteiros."""
    if pd.isna(valor) or str(valor).strip() in ("", "-", "nan"):
        return np.nan
    valor = str(valor).strip()
    # Remove milissegundos se existir (ex: 00:14:03.923)
    valor = valor.split(".")[0]
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


def normalizar_id(valor):
    """Normaliza IDs para comparação: strip, lowercase, remove hífens extras."""
    if pd.isna(valor):
        return np.nan
    s = str(valor).strip().lower()
    if s in ("nan", "", "none"):
        return np.nan
    return s


# ─────────────────────────────────────────
# CARREGAMENTO DO ZENDESK
# ─────────────────────────────────────────

def carregar_zendesk(arquivo):
    """
    Carrega o XLSX do Zendesk.
    Estrutura real confirmada:
      ID do ticket | Assuntos do Ticket | Criação do ticket - Carimbo de data/hora
      | ID Genesys | Matricula | Tickets | Arquivo_Origem
    """
    try:
        df = pd.read_excel(arquivo, engine="openpyxl", dtype=str)
        df.columns = df.columns.str.strip()

        renomear = {
            "ID do ticket":                             "ticket_id",
            "Assuntos do Ticket":                       "assunto",
            "Criação do ticket - Carimbo de data/hora": "data_criacao_zen",
            "ID Genesys":                               "id_genesys",
            "Matricula":                                "matricula",
            "Tickets":                                  "tickets_zen",
            "Arquivo_Origem":                           "arquivo_origem_zen",
        }
        df = df.rename(columns={k: v for k, v in renomear.items() if k in df.columns})

        if "data_criacao_zen" in df.columns:
            df["data_criacao_zen"] = pd.to_datetime(
                df["data_criacao_zen"], errors="coerce", dayfirst=False
            )

        # Normaliza chave
        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)

        # Remove linhas sem ID Genesys (não vão cruzar de qualquer forma)
        total_antes = len(df)
        df_com_id = df[df.get("id_genesys_norm", pd.Series(dtype=str)).notna()]

        st.info(
            f"📋 Zendesk: {total_antes} tickets carregados, "
            f"{len(df_com_id)} com ID Genesys preenchido."
        )
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────
# CARREGAMENTO DO GENESYS (PARSER ROBUSTO)
# ─────────────────────────────────────────

def carregar_genesys(arquivo):
    """
    Parser robusto para o CSV do Genesys.

    Características conhecidas do arquivo:
    - Separador: pipe |
    - Metadados nas primeiras linhas (ex: "Exportação total concluída")
    - Aspas dentro dos dados causam erro com parser padrão
    - Colunas esperadas (ordem pode variar):
        Exportação total concluída | Carimbo de data/hora do resultado parcial
        | Filtros | Usuários – Interagiram | Data | Duração | ANI
        | Tipo de desconexão | (possivelmente) ID de conversa

    Solução: lê linha a linha, ignora quoting, split manual por pipe.
    """
    try:
        conteudo_bytes = arquivo.read()

        # Detecta encoding
        conteudo = None
        for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                conteudo = conteudo_bytes.decode(enc)
                break
            except Exception:
                continue

        if conteudo is None:
            st.error("Não foi possível decodificar o arquivo Genesys.")
            return pd.DataFrame()

        linhas = conteudo.splitlines()

        # ── Localiza a linha de cabeçalho ─────────────────────────────────
        # Estratégia: primeira linha com pelo menos 3 pipes
        idx_header = None
        for i, linha in enumerate(linhas):
            if linha.count("|") >= 3:
                idx_header = i
                break

        if idx_header is None:
            st.error("Não foi possível identificar o cabeçalho no arquivo Genesys.")
            return pd.DataFrame()

        # ── Parse manual linha a linha (evita problemas com quoting) ──────
        # Divide cada linha pelo pipe e ignora completamente as aspas como delimitadores
        cabecalho_raw = linhas[idx_header].split("|")
        colunas = [c.strip() for c in cabecalho_raw]
        # Remove colunas vazias das extremidades (artefato do pipe no início/fim da linha)
        while colunas and colunas[0] == "":
            colunas.pop(0)
        while colunas and colunas[-1] == "":
            colunas.pop()

        n_colunas = len(colunas)

        registros = []
        for linha in linhas[idx_header + 1:]:
            if not linha.strip() or linha.strip() == "|":
                continue
            partes = linha.split("|")
            partes = [p.strip() for p in partes]
            # Remove extremidades vazias (pipe inicial/final)
            while partes and partes[0] == "":
                partes.pop(0)
            while partes and partes[-1] == "":
                partes.pop()

            # Pula linhas claramente de metadados/rodapé
            if len(partes) < 2:
                continue

            # Ajusta tamanho para bater com o cabeçalho
            if len(partes) < n_colunas:
                partes += [""] * (n_colunas - len(partes))
            elif len(partes) > n_colunas:
                partes = partes[:n_colunas]

            registros.append(partes)

        if not registros:
            st.error("Nenhum registro encontrado no arquivo Genesys após o cabeçalho.")
            return pd.DataFrame()

        df = pd.DataFrame(registros, columns=colunas)

        # ── Diagnóstico: mostra colunas encontradas ────────────────────────
        with st.expander("🔍 Diagnóstico: colunas encontradas no arquivo Genesys"):
            st.write(list(df.columns))
            st.dataframe(df.head(3))

        # ── Mapeamento dinâmico de colunas ────────────────────────────────
        renomear = {}
        for col in df.columns:
            col_norm = col.lower().strip()
            # Chave de cruzamento — tenta variações do nome
            if any(x in col_norm for x in ["id de conversa", "conversation id", "id conversa", "conversationid"]):
                renomear[col] = "id_genesys"
            elif any(x in col_norm for x in ["usuário", "usuario", "interagi", "agente"]):
                renomear[col] = "nome_agente"
            elif col_norm == "data" or "data/hora" in col_norm:
                renomear[col] = "data_atendimento"
            elif "carimbo" in col_norm:
                renomear[col] = "carimbo_parcial"
            elif "duração" in col_norm or "duracao" in col_norm:
                renomear[col] = "duracao_str"
            elif "filtro" in col_norm:
                renomear[col] = "filtros"
            elif col_norm == "ani":
                renomear[col] = "ani"
            elif "desconex" in col_norm:
                renomear[col] = "tipo_desconexao"
            elif "exporta" in col_norm:
                renomear[col] = "exportacao"

        df = df.rename(columns=renomear)

        # ── Se não encontrou id_genesys, tenta coluna por posição ─────────
        # (o ID de conversa costuma ser uma das primeiras colunas numéricas/GUID)
        if "id_genesys" not in df.columns:
            st.warning(
                "⚠️ Coluna 'ID de conversa' não identificada pelo nome. "
                "Tentando identificar pelo conteúdo (padrão GUID)..."
            )
            for col in df.columns:
                amostra = df[col].dropna().astype(str).head(20)
                # GUID pattern: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
                guid_matches = amostra.str.match(
                    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    case=False
                ).sum()
                if guid_matches >= 3:
                    st.info(f"✅ Coluna '{col}' identificada como ID de conversa pelo padrão GUID.")
                    df = df.rename(columns={col: "id_genesys"})
                    break

        # ── Extrai fila ───────────────────────────────────────────────────
        if "filtros" in df.columns:
            df["fila"] = df["filtros"].str.extract(r"Fila:\s*(.+)", expand=False).str.strip()
        else:
            df["fila"] = "URA_CORSAN"

        # ── Converte duração ──────────────────────────────────────────────
        # Tenta coluna mapeada; se não, busca pelo padrão HH:MM:SS
        if "duracao_str" not in df.columns:
            for col in df.columns:
                if col in ("id_genesys", "nome_agente", "data_atendimento", "filtros", "fila", "ani", "tipo_desconexao"):
                    continue
                amostra = df[col].dropna().astype(str).head(30)
                matches = amostra.str.match(r"^\d{1,2}:\d{2}:\d{2}").sum()
                if matches >= 3:
                    df = df.rename(columns={col: "duracao_str"})
                    break

        if "duracao_str" in df.columns:
            df["duracao_str"] = df["duracao_str"].astype(str).str.strip()
            df["duracao_segundos"] = df["duracao_str"].apply(duracao_para_segundos)
        else:
            df["duracao_segundos"] = np.nan
            st.warning("⚠️ Coluna de duração não encontrada. TMA não será calculado.")

        # ── Converte data ─────────────────────────────────────────────────
        col_data = "data_atendimento" if "data_atendimento" in df.columns else \
                   "carimbo_parcial" if "carimbo_parcial" in df.columns else None

        if col_data:
            if col_data == "carimbo_parcial":
                df = df.rename(columns={"carimbo_parcial": "data_atendimento"})
                col_data = "data_atendimento"
            df["data_atendimento"] = pd.to_datetime(
                df["data_atendimento"].astype(str).str.strip(),
                errors="coerce",
                dayfirst=True
            )
        else:
            df["data_atendimento"] = pd.NaT
            st.warning("⚠️ Coluna de data não encontrada.")

        # ── Limpa nome do agente ──────────────────────────────────────────
        if "nome_agente" in df.columns:
            df["nome_agente"] = df["nome_agente"].astype(str).str.strip()
            df = df[df["nome_agente"].str.len() > 2]
            df = df[~df["nome_agente"].isin(["nan", "None", ""])]

        # ── Normaliza chave ───────────────────────────────────────────────
        if "id_genesys" in df.columns:
            df["id_genesys_norm"] = df["id_genesys"].apply(normalizar_id)
        else:
            df["id_genesys_norm"] = np.nan
            st.warning(
                "⚠️ ID de conversa não encontrado no Genesys. "
                "O cruzamento com o Zendesk não será possível. "
                "Os dados do Genesys serão carregados sem o assunto do ticket."
            )

        df = df.reset_index(drop=True)
        st.success(f"✅ Genesys: {len(df)} registros carregados.")
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        import traceback
        st.code(traceback.format_exc())
        return pd.DataFrame()


# ─────────────────────────────────────────
# INTEGRAÇÃO (MERGE)
# ─────────────────────────────────────────

def integrar_dados(df_zen, df_gen):
    """
    Cruzamento principal:
      Zendesk.id_genesys_norm  ←→  Genesys.id_genesys_norm

    Base: Genesys (sempre tem dados de duração e agente).
    Enriquece com: assunto, ticket_id, matricula do Zendesk.
    """
    if df_gen.empty:
        st.error("Arquivo Genesys vazio após processamento.")
        return pd.DataFrame()

    df = df_gen.copy()

    if df_zen.empty or "id_genesys_norm" not in df_zen.columns:
        st.warning("Zendesk não disponível. Continuando apenas com dados do Genesys.")
        df["assunto"]    = np.nan
        df["ticket_id"]  = np.nan
        df["matricula"]  = np.nan
        return df

    if "id_genesys_norm" not in df.columns:
        st.warning("ID de conversa ausente no Genesys. Sem cruzamento possível.")
        df["assunto"]    = np.nan
        df["ticket_id"]  = np.nan
        df["matricula"]  = np.nan
        return df

    # Colunas do Zendesk para trazer
    colunas_zen = ["id_genesys_norm"]
    for col in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]:
        if col in df_zen.columns:
            colunas_zen.append(col)

    df_zen_slim = df_zen[colunas_zen].drop_duplicates(subset=["id_genesys_norm"])

    # Left join: todos os registros do Genesys + dados do Zendesk quando existir
    df_merged = pd.merge(
        df,
        df_zen_slim,
        on="id_genesys_norm",
        how="left",
        suffixes=("", "_zen")
    )

    total = len(df_merged)
    com_assunto = df_merged["assunto"].notna().sum() if "assunto" in df_merged.columns else 0

    st.success(
        f"✅ Merge concluído: {total:,} registros do Genesys | "
        f"{com_assunto:,} cruzados com Zendesk ({com_assunto/total*100:.1f}% de aproveitamento)"
    )

    if com_assunto == 0:
        st.warning(
            "⚠️ Nenhum registro cruzou com o Zendesk. "
            "Verifique se a coluna 'ID de conversa' do Genesys contém os mesmos valores "
            "que a coluna 'ID Genesys' do Zendesk."
        )

    return df_merged


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
        # Garante que colunas com tipos mistos sejam convertidas para string antes de salvar
        df_salvar = df.copy()
        for col in df_salvar.columns:
            if df_salvar[col].dtype == object:
                df_salvar[col] = df_salvar[col].astype(str).replace("nan", np.nan)
        df_salvar.to_parquet(HISTORICO_PATH, index=False)
        return True
    except Exception as e:
        st.error(f"Erro ao salvar histórico: {e}")
        return False


def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo.reset_index(drop=True)

    df_combinado = pd.concat([df_hist, df_novo], ignore_index=True)

    # Deduplicação por id_genesys_norm (mais confiável)
    if "id_genesys_norm" in df_combinado.columns:
        com_id = df_combinado[df_combinado["id_genesys_norm"].notna() & (df_combinado["id_genesys_norm"] != "nan")]
        sem_id = df_combinado[df_combinado["id_genesys_norm"].isna() | (df_combinado["id_genesys_norm"] == "nan")]
        com_id = com_id.drop_duplicates(subset=["id_genesys_norm"], keep="last")
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

    # Horário
    hora_range = st.sidebar.slider("Horário (hora do dia)", 0, 23, (0, 23))
    if "data_atendimento" in df_f.columns:
        df_f = df_f[
            (df_f["data_atendimento"].dt.hour >= hora_range[0]) &
            (df_f["data_atendimento"].dt.hour <= hora_range[1])
        ]

    # Agente
    if "nome_agente" in df_f.columns:
        agentes = sorted(df_f["nome_agente"].dropna().unique())
        agentes = [a for a in agentes if a not in ("nan", "", "None")]
        sel = st.sidebar.multiselect("Agente", options=agentes, default=agentes)
        if sel:
            df_f = df_f[df_f["nome_agente"].isin(sel)]

    # Assunto
    if "assunto" in df_f.columns and df_f["assunto"].notna().any():
        assuntos = sorted(df_f["assunto"].dropna().unique())
        assuntos = [a for a in assuntos if a not in ("nan", "", "None")]
        if assuntos:
            sel_ass = st.sidebar.multiselect("Assunto", options=assuntos, default=assuntos)
            if sel_ass:
                df_f = df_f[df_f["assunto"].isin(sel_ass)]

    # Fila
    if "fila" in df_f.columns and df_f["fila"].notna().any():
        filas = sorted(df_f["fila"].dropna().unique())
        filas = [f for f in filas if f not in ("nan", "", "None")]
        if filas:
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

    total       = len(df)
    tma         = df["duracao_segundos"].mean() if "duracao_segundos" in df.columns else None
    total_horas = df["duracao_segundos"].sum() / 3600 if "duracao_segundos" in df.columns else 0
    n_agentes   = df["nome_agente"].nunique() if "nome_agente" in df.columns else 0
    taxa_cruz   = (
        df["assunto"].notna().sum() / total * 100
        if "assunto" in df.columns and total > 0 else 0
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: card_metrica("Total Atendimentos", f"{total:,}")
    with col2: card_metrica("TMA Geral", formatar_tempo(tma))
    with col3: card_metrica("Horas em Atendimento", f"{total_horas:.1f}h")
    with col4: card_metrica("Agentes Ativos", str(n_agentes))
    with col5: card_metrica("Cruzados c/ Zendesk", f"{taxa_cruz:.0f}%")

    st.markdown("---")

    if "data_atendimento" in df.columns and df["data_atendimento"].notna().any():
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

        # Mapa de calor hora × dia da semana
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
        st.info("Dados de assunto indisponíveis — o cruzamento com o Zendesk pode não ter funcionado.")
        return

    df_valido = df[df["assunto"].notna() & (df["assunto"].astype(str) != "nan")]

    df_ass = (
        df_valido.groupby("assunto", dropna=True)
        .agg(
            Atendimentos=("nome_agente", "count"),
            TMA_s=("duracao_segundos", "mean"),
            Tempo_Total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("Atendimentos", ascending=False)
    )
    df_ass["TMA"]         = df_ass["TMA_s"].apply(formatar_tempo)
    df_ass["Tempo Total"] = df_ass["Tempo_Total_s"].apply(formatar_tempo)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Volume por Assunto**")
        st.bar_chart(df_ass.set_index("assunto")["Atendimentos"])
    with col2:
        st.markdown("**TMA por Assunto (segundos)**")
        st.bar_chart(df_ass.set_index("assunto")["TMA_s"])

    st.markdown("**📊 Pareto — Assuntos que representam 80% do volume**")
    df_pareto = df_ass.copy()
    df_pareto["% Acumulado"] = (
        df_pareto["Atendimentos"].cumsum() / df_pareto["Atendimentos"].sum() * 100
    ).round(1)
    st.dataframe(
        df_pareto[["assunto", "Atendimentos", "TMA", "Tempo Total", "% Acumulado"]],
        use_container_width=True
    )


def secao_por_agente(df):
    st.markdown('<h3 class="section-title">👥 Análise por Agente</h3>', unsafe_allow_html=True)

    if "nome_agente" not in df.columns:
        st.info("Coluna de agente não encontrada.")
        return

    df_valido = df[df["nome_agente"].notna() & (df["nome_agente"].astype(str) != "nan")]
    tma_geral = df_valido["duracao_segundos"].mean()

    df_ag = (
        df_valido.groupby("nome_agente", dropna=True)
        .agg(
            Atendimentos=("duracao_segundos", "count"),
            TMA_s=("duracao_segundos", "mean"),
            Tempo_Total_s=("duracao_segundos", "sum")
        )
        .reset_index()
        .sort_values("Atendimentos", ascending=False)
    )
    df_ag["TMA"]         = df_ag["TMA_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["Tempo_Total_s"].apply(formatar_tempo)
    df_ag["Δ vs Média"]  = (df_ag["TMA_s"] - tma_geral).round(0).astype(int)
    df_ag["Acima Média"] = df_ag["Δ vs Média"] > 0

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🏆 Ranking por Volume**")
        st.bar_chart(df_ag.set_index("nome_agente")["Atendimentos"])
    with col2:
        st.markdown("**⏱️ TMA por Agente (segundos)**")
        st.bar_chart(df_ag.set_index("nome_agente")["TMA_s"])

    st.markdown("**📋 Tabela Comparativa**")
    st.dataframe(
        df_ag[["nome_agente", "Atendimentos", "TMA", "Tempo Total", "Δ vs Média"]],
        use_container_width=True
    )

    st.markdown("**🎯 Dispersão: Volume × TMA**")
    st.scatter_chart(
        df_ag,
        x="Atendimentos",
        y="TMA_s",
        color="Acima Média",
        size="Atendimentos"
    )


def secao_detalhe_agente(df):
    st.markdown('<h3 class="section-title">🔍 Detalhamento Individual</h3>', unsafe_allow_html=True)

    if "nome_agente" not in df.columns:
        st.info("Coluna de agente não encontrada.")
        return

    agentes = sorted([
        a for a in df["nome_agente"].dropna().unique()
        if str(a) not in ("nan", "", "None")
    ])
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
    pct_volume     = total_ag / len(df) * 100 if len(df) > 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    with col1: card_metrica("Atendimentos", f"{total_ag:,}")
    with col2: card_metrica("TMA do Agente", formatar_tempo(tma_ag))
    with col3: card_metrica("TMA Geral (ref.)", formatar_tempo(tma_geral))
    with col4: card_metrica("% do Volume Total", f"{pct_volume:.1f}%")

    st.markdown(f"**⏰ Total em atendimento:** {total_horas_ag:.1f}h")

    if "data_atendimento" in df_ag.columns and df_ag["data_atendimento"].notna().any():
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**📈 Evolução do TMA por Dia**")
            df_tma = (
                df_ag.set_index("data_atendimento")
                .resample("D")["duracao_segundos"]
                .mean()
                .reset_index()
                .rename(columns={"duracao_segundos": "TMA (s)", "data_atendimento": "Data"})
            )
            st.line_chart(df_tma.set_index("Data"))

        with col_b:
            st.markdown("**📊 Volume por Dia**")
            df_vol = (
                df_ag.set_index("data_atendimento")
                .resample("D")["duracao_segundos"]
                .count()
                .reset_index()
                .rename(columns={"duracao_segundos": "Atendimentos", "data_atendimento": "Data"})
            )
            st.bar_chart(df_vol.set_index("Data"))

    # Assuntos do agente (só se cruzamento com Zendesk funcionou)
    if "assunto" in df_ag.columns and df_ag["assunto"].notna().any():
        df_valido_ass = df_ag[df_ag["assunto"].astype(str) != "nan"]
        if not df_valido_ass.empty:
            st.markdown("**🗂️ Assuntos atendidos**")
            df_ag_ass = (
                df_valido_ass.groupby("assunto")
                .agg(Atendimentos=("duracao_segundos", "count"), TMA_s=("duracao_segundos", "mean"))
                .reset_index()
                .sort_values("Atendimentos", ascending=False)
            )
            df_ag_ass["TMA"] = df_ag_ass["TMA_s"].apply(formatar_tempo)
            col_c, col_d = st.columns(2)
            with col_c:
                st.bar_chart(df_ag_ass.set_index("assunto")["Atendimentos"])
            with col_d:
                st.dataframe(df_ag_ass[["assunto", "Atendimentos", "TMA"]], use_container_width=True)

    # Comparativo agente × time
    st.markdown("**⚖️ Comparação: Agente vs. Média do Time**")
    n_agentes = df["nome_agente"].nunique()
    comp = pd.DataFrame({
        "Métrica": ["TMA (segundos)", "Atendimentos", "% do Volume"],
        agente_sel: [round(tma_ag, 0), total_ag, f"{pct_volume:.1f}%"],
        "Média do Time": [
            round(tma_geral, 0),
            round(len(df) / n_agentes, 1) if n_agentes > 0 else "-",
            f"{100/n_agentes:.1f}%" if n_agentes > 0 else "-"
        ]
    })
    st.dataframe(comp, use_container_width=True)

    # Tabela completa dos atendimentos
    st.markdown("**📋 Todos os atendimentos no período**")
    colunas_tabela = [c for c in [
        "data_atendimento", "ticket_id", "assunto",
        "duracao_str", "duracao_segundos",
        "fila", "ani", "tipo_desconexao", "id_genesys"
    ] if c in df_ag.columns]
    st.dataframe(
        df_ag[colunas_tabela].sort_values("data_atendimento", ascending=False),
        use_container_width=True
    )


def secao_qualidade_dados(df):
    st.markdown('<h3 class="section-title">🔧 Qualidade dos Dados</h3>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**Genesys**")
        sem_agente  = df["nome_agente"].isna().sum() if "nome_agente" in df.columns else 0
        sem_duracao = df["duracao_segundos"].isna().sum() if "duracao_segundos" in df.columns else 0
        dur_zero    = (df["duracao_segundos"] == 0).sum() if "duracao_segundos" in df.columns else 0
        st.metric("Sem nome de agente", int(sem_agente))
        st.metric("Sem duração", int(sem_duracao))
        st.metric("Duração zero", int(dur_zero))

    with col2:
        st.markdown("**Zendesk (cruzamento)**")
        sem_assunto = (
            df["assunto"].isna().sum() + (df["assunto"].astype(str) == "nan").sum()
            if "assunto" in df.columns else "-"
        )
        sem_id = (
            df["id_genesys_norm"].isna().sum()
            if "id_genesys_norm" in df.columns else "-"
        )
        st.metric("Sem assunto (não cruzou)", int(sem_assunto) if isinstance(sem_assunto, (int, float)) else sem_assunto)
        st.metric("Sem ID Genesys", int(sem_id) if isinstance(sem_id, (int, float)) else sem_id)

    with col3:
        st.markdown("**Outliers de Duração**")
        if "duracao_segundos" in df.columns and df["duracao_segundos"].notna().any():
            q1  = df["duracao_segundos"].quantile(0.25)
            q3  = df["duracao_segundos"].quantile(0.75)
            iqr = q3 - q1
            lim = q3 + 3 * iqr
            outliers = df[df["duracao_segundos"] > lim]
            st.metric("Outliers (> Q3 + 3×IQR)", len(outliers))
            st.caption(f"Limite: {formatar_tempo(lim)}")

    # Tabela de outliers
    if "duracao_segundos" in df.columns and df["duracao_segundos"].notna().any():
        q3  = df["duracao_segundos"].quantile(0.75)
        iqr = q3 - q1
        lim = q3 + 3 * iqr
        outliers = df[df["duracao_segundos"] > lim]
        if not outliers.empty:
            st.markdown("**Atendimentos com duração muito acima do normal:**")
            cols_out = [c for c in ["nome_agente", "data_atendimento", "duracao_str", "assunto", "id_genesys"] if c in outliers.columns]
            st.dataframe(
                outliers[cols_out].sort_values("duracao_segundos" if "duracao_segundos" in outliers.columns else cols_out[0], ascending=False).head(20),
                use_container_width=True
            )


# ─────────────────────────────────────────
# UPLOAD + PROCESSAMENTO
# ─────────────────────────────────────────

def secao_upload():
    st.sidebar.header("📂 Upload Mensal")
    st.sidebar.markdown(
        "Suba os dois arquivos mensais. "
        "Os dados são **acumulados automaticamente** a cada upload."
    )

    arq_zen = st.sidebar.file_uploader("Zendesk (XLSX)", type=["xlsx", "xls"], key="up_zen")
    arq_gen = st.sidebar.file_uploader("Genesys (CSV)",  type=["csv"],         key="up_gen")

    if arq_zen and arq_gen:
        if st.sidebar.button("✅ Processar e Acumular", key="btn_proc"):
            with st.spinner("Processando arquivos..."):
                df_zen = carregar_zendesk(arq_zen)
                df_gen = carregar_genesys(arq_gen)
                df_novo = integrar_dados(df_zen, df_gen)

                if df_novo.empty:
                    st.sidebar.error("Nenhum dado gerado. Veja os avisos acima.")
                    return

                df_hist     = carregar_historico()
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
                st.success("Histórico apagado.")
                st.cache_data.clear()
                st.rerun()

    if os.path.exists(HISTORICO_PATH):
        df_dl = carregar_historico()
        if not df_dl.empty:
            csv_bytes = df_dl.to_csv(index=False).encode("utf-8")
            st.sidebar.download_button(
                label="⬇️ Baixar histórico (CSV)",
                data=csv_bytes,
                file_name="historico_atendimentos.csv",
                mime="text/csv",
                key="btn_dl"
            )


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    st.title("📞 Dashboard de Atendimentos — Call Center")
    st.markdown(
        "Análise consolidada **Zendesk + Genesys** | "
        "Cruzamento por `ID Genesys` ↔ `ID de conversa`"
    )

    secao_upload()

    df_hist = carregar_historico()

    if df_hist.empty:
        st.info(
            "👆 Nenhum dado encontrado. "
            "Faça o upload dos dois arquivos na barra lateral para começar."
        )
        return

    df_filtrado = aplicar_filtros(df_hist)

    if df_filtrado.empty:
        st.warning("⚠️ Nenhum registro para os filtros selecionados.")
        return

    aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "📊 Visão Geral",
        "🗂️ Por Assunto",
        "👥 Por Agente",
        "🔍 Detalhe do Agente",
        "🔧 Qualidade dos Dados"
    ])

    with aba1: secao_visao_geral(df_filtrado)
    with aba2: secao_por_assunto(df_filtrado)
    with aba3: secao_por_agente(df_filtrado)
    with aba4: secao_detalhe_agente(df_filtrado)
    with aba5: secao_qualidade_dados(df_filtrado)


if __name__ == "__main__":
    main()
