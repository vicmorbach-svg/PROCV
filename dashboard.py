import streamlit as st
import pandas as pd
import numpy as np
import os
import re
import unicodedata
import gc
import io
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
    """
    Normaliza nome de coluna para comparação:
    - corrige encoding latin-1/utf-8 quebrado
    - remove acentos via NFKD
    - minúsculo e strip
    """
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
    "exportacao total concluida":  "exportacao",
    "carimbo de data/hora do resultado parcial": "carimbo_parcial",
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

# Regex flexível para coluna do agente
PADRAO_AGENTE = re.compile(r"usu.{0,15}interagiram", re.IGNORECASE)

def detectar_coluna_agente(colunas):
    for col in colunas:
        if PADRAO_AGENTE.search(normalizar_col(col)):
            return col
    return None

# -------------------- Carregamento Genesys --------------------

def carregar_genesys(uploaded_file):
    try:
        file_bytes = uploaded_file.read()
        df_raw = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl", dtype=str)

        # Debug: mostra colunas originais encontradas
        st.write("Colunas encontradas no XLSX:", list(df_raw.columns))

        renomear = {}
        for col in df_raw.columns:
            chave = normalizar_col(col)
            if chave in MAPA_GENESYS:
                renomear[col] = MAPA_GENESYS[chave]

        col_agente = detectar_coluna_agente(df_raw.columns)
        if col_agente:
            renomear[col_agente] = "nome_agente"
            st.write(f"Coluna de agente detectada: '{col_agente}'")
        else:
            st.warning(
                f"Coluna de agente não encontrada.\n"
                f"Colunas normalizadas: {[normalizar_col(c) for c in df_raw.columns]}"
            )

        df = df_raw.rename(columns=renomear)
        del df_raw
        gc.collect()

        # Filtra exportações concluídas
        if "exportacao" in df.columns:
            mask = df["exportacao"].astype(str).str.strip().str.lower().isin(["sim", "yes"])
            df = df[mask].reset_index(drop=True)
        else:
            st.warning("Coluna 'Exportação total concluída' não encontrada; usando todos os registros.")

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

        # ANI — remove prefixo tel:+
        if "ani" in df.columns:
            df["ani"] = (
                df["ani"].astype(str)
                .str.replace(r"^tel:\+?", "", regex=True)
                .str.strip()
            )
            df.loc[df["ani"].str.lower().isin(["nan", ""]), "ani"] = np.nan

        # Nome agente
        if "nome_agente" in df.columns:
            df["nome_agente"] = df["nome_agente"].astype(str).str.strip()
            df.loc[df["nome_agente"].str.lower().isin(["nan", ""]), "nome_agente"] = np.nan
        else:
            df["nome_agente"] = np.nan

        # Tipo desconexão
        if "tipo_desconexao" in df.columns:
            df["tipo_desconexao"] = df["tipo_desconexao"].astype(str).str.strip().str.title()
            df.loc[df["tipo_desconexao"].str.lower().isin(["nan", ""]), "tipo_desconexao"] = np.nan

        st.info(
            f"Genesys: {len(df)} interações | "
            f"agentes únicos: {df['nome_agente'].nunique()} | "
            f"com ID conversa: {df['id_genesys_norm'].notna().sum()}"
        )
        return df

    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        import traceback
        st.code(traceback.format_exc())
        return pd.DataFrame()

# -------------------- Carregamento Zendesk --------------------

def carregar_zendesk(uploaded_file):
    try:
        file_bytes = uploaded_file.read()
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

        total    = len(df)
        com_id   = df["id_genesys_norm"].notna().sum() if "id_genesys_norm" in df.columns else 0
        st.info(f"Zendesk: {total} tickets | {com_id} com ID Genesys preenchido.")
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
        colunas_zen = ["id_genesys_norm"]
        for col in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]:
            if col in df_zen.columns:
                colunas_zen.append(col)

        df_zen_slim = df_zen[colunas_zen].drop_duplicates(subset=["id_genesys_norm"])
        df = pd.merge(df, df_zen_slim, on="id_genesys_norm", how="left", suffixes=("", "_zen"))

        total       = len(df)
        com_assunto = df["assunto"].notna().sum() if "assunto" in df.<searching_in_web>
</searching_in_web>

Agora tenho os dois arquivos completos. O `dashboard.py` ainda usa `uploaded_file.read().decode("utf-8")` para o Genesys — nunca chegou a usar `openpyxl`. Segue o arquivo definitivo e completo:

```python
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
    """Normaliza nome de coluna: corrige encoding, remove acentos, minúsculo."""
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

        # Monta dicionário de renomeação pelo nome normalizado
        renomear = {}
        for col in df_raw.columns:
            chave = normalizar_col(col)
            if chave in MAPA_GENESYS:
                renomear[col] = MAPA_GENESYS[chave]

        col_agente = detectar_coluna_agente(df_raw.columns)
        if col_agente:
            renomear[col_agente] = "nome_agente"
        else:
            st.warning(
                f"Coluna de agente não encontrada.\n"
                f"Colunas detectadas: {list(df_raw.columns)}"
            )

        df = df_raw.rename(columns=renomear)
        del df_raw
        gc.collect()

        # Filtra apenas exportações concluídas
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

        # Durações em segundos
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

        # ANI — remove prefixo tel:+
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

        # Tipo desconexão
        if "tipo_desconexao" in df.columns:
            df["tipo_desconexao"] = (
                df["tipo_desconexao"].astype(str).str.strip().str.title()
            )
            df.loc[df["tipo_desconexao"].str.lower().isin(["nan", ""]), "tipo_desconexao"] = np.nan

        # Colunas categóricas para economizar memória
        for col in ["fila", "nome_agente", "tipo_desconexao", "ani"]:
            if col in df.columns:
                df[col] = df[col].astype("category")

        st.info(
            f"Genesys: {len(df)} interações | "
            f"agentes: {df['nome_agente'].nunique() if 'nome_agente' in df.columns else '?'} | "
            f"IDs: {df['id_genesys_norm'].notna().sum()}"
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

        total   = len(df)
        com_id  = df["id_genesys_norm"].notna().sum() if "id_genesys_norm" in df.columns else 0
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

    tem_chave = (
        not df_zen.empty
        and "id_genesys_norm" in df_zen.columns
        and "id_genesys_norm" in df.columns
        and df["id_genesys_norm"].notna().any()
        and df_zen["id_genesys_norm"].notna().any()
    )

    if tem_chave:
        cols_zen = ["id_genesys_norm"]
        for c in ["ticket_id", "assunto", "matricula", "data_criacao_zen", "tickets_zen"]:
            if c in df_zen.columns:
                cols_zen.append(c)

        df_zen_slim = (
            df_zen[cols_zen]
            .drop_duplicates(subset=["id_genesys_norm"])
        )
        df = pd.merge(df, df_zen_slim, on="id_genesys_norm", how="left", suffixes=("", "_zen"))

        total      = len(df)
        cruzados   = df["assunto"].notna().sum() if "assunto" in df.columns else 0
        st.success(
            f"Merge OK: {total} registros | {cruzados} cruzados com Zendesk "
            f"({cruzados / total * 100:.1f}%)"
        )
        del df_zen_slim
        gc.collect()
    else:
        if df_zen.empty:
            st.warning("Zendesk não enviado — exibindo só dados do Genesys.")
        else:
            st.warning("IDs não encontrados — cruzamento indisponível.")
        for c in ["ticket_id", "assunto", "matricula"]:
            df[c] = np.nan

    # Coluna de mês: usa data do Zendesk quando disponível, senão Genesys
    if "data_criacao_zen" in df.columns and df["data_criacao_zen"].notna().any():
        df["mes"] = df["data_criacao_zen"].dt.to_period("M").astype(str)
    elif "data_atendimento" in df.columns and df["data_atendimento"].notna().any():
        df["mes"] = df["data_atendimento"].dt.to_period("M").astype(str)
    else:
        df["mes"] = np.nan

    df["data_base"] = df["data_atendimento"].copy()
    return df

# -------------------- Histórico --------------------

@st.cache_data(ttl=60, show_spinner=False)
def carregar_historico():
    if os.path.exists(HISTORICO_PATH):
        try:
            df = pd.read_parquet(HISTORICO_PATH)
            for c in ["data_base", "data_atendimento", "data_criacao_zen"]:
                if c in df.columns:
                    df[c] = pd.to_datetime(df[c], errors="coerce")
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def salvar_historico(df):
    try:
        df.to_parquet(HISTORICO_PATH, index=False)
        carregar_historico.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar histórico: {e}")
        return False

def adicionar_ao_historico(df_novo, df_hist):
    if df_hist.empty:
        return df_novo.reset_index(drop=True)

    df_comb = pd.concat([df_hist, df_novo], ignore_index=True)

    if "id_genesys_norm" in df_comb.columns and df_comb["id_genesys_norm"].notna().any():
        com_id  = df_comb[df_comb["id_genesys_norm"].notna()].drop_duplicates(
            subset=["id_genesys_norm"], keep="last"
        )
        sem_id  = df_comb[df_comb["id_genesys_norm"].isna()]
        df_comb = pd.concat([com_id, sem_id], ignore_index=True)
    else:
        chaves = [c for c in ["nome_agente", "data_atendimento", "duracao_segundos"] if c in df_comb.columns]
        if chaves:
            df_comb = df_comb.drop_duplicates(subset=chaves, keep="last")

    gc.collect()
    return df_comb.reset_index(drop=True)

# -------------------- Filtros --------------------

def aplicar_filtros(df):
    st.sidebar.header("Filtros")
    df_f = df.copy()

    # Período por data
    if "data_base" in df_f.columns and df_f["data_base"].notna().any():
        min_d = df_f["data_base"].min().date()
        max_d = df_f["data_base"].max().date()
        periodo = st.sidebar.date_input("Período", value=(min_d, max_d),
                                        min_value=min_d, max_value=max_d)
        if isinstance(periodo, (list, tuple)) and len(periodo) == 2:
            ini, fim = periodo
            df_f = df_f[
                (df_f["data_base"].dt.date >= ini) &
                (df_f["data_base"].dt.date <= fim)
            ]

    # Agente
    if "nome_agente" in df_f.columns and df_f["nome_agente"].notna().any():
        agentes = sorted(df_f["nome_agente"].dropna().astype(str).unique())
        sel = st.sidebar.multiselect("Agente(s)", agentes)
        if sel:
            df_f = df_f[df_f["nome_agente"].astype(str).isin(sel)]

    # Tipo de desconexão
    if "tipo_desconexao" in df_f.columns and df_f["tipo_desconexao"].notna().any():
        tipos = sorted(df_f["tipo_desconexao"].dropna().astype(str).unique())
        sel_tipo = st.sidebar.multiselect("Tipo de desconexão", tipos)
        if sel_tipo:
            df_f = df_f[df_f["tipo_desconexao"].astype(str).isin(sel_tipo)]

    # Assunto
    if "assunto" in df_f.columns and df_f["assunto"].notna().any():
        assuntos = sorted(df_f["assunto"].dropna().astype(str).unique())
        sel_ass = st.sidebar.multiselect("Assunto(s)", assuntos)
        if sel_ass:
            df_f = df_f[df_f["assunto"].astype(str).isin(sel_ass)]

    st.sidebar.markdown(f"Registros no filtro: **{len(df_f)}**")
    return df_f

# -------------------- Seções --------------------

def secao_visao_geral(df):
    st.subheader("Visão Geral")

    col_tma  = _col_tma(df)
    total    = len(df)
    tma      = df[col_tma].mean() if col_tma in df.columns else None
    horas    = df["duracao_segundos"].sum() / 3600 if "duracao_segundos" in df.columns else 0
    n_agentes = df["nome_agente"].nunique() if "nome_agente" in df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de atendimentos", total)
    c2.metric("TMA (tempo de conversa)", formatar_tempo(tma))
    c3.metric("Horas em atendimento", f"{horas:.1f} h")
    c4.metric("Agentes ativos", n_agentes)

    # Tempos médios detalhados
    cols_tempo = [
        ("ura_segundos",        "Média URA"),
        ("fila_segundos",       "Média Fila"),
        ("conversas_segundos",  "Média Conversa"),
        ("tratamento_segundos", "Média Tratamento"),
        ("abandono_segundos",   "Média Abandono"),
    ]
    disp = [(label, c) for c, label in cols_tempo if c in df.columns]
    if disp:
        st.markdown("**Tempos médios detalhados**")
        cols_m = st.columns(len(disp))
        for i, (label, c) in enumerate(disp):
            cols_m[i].metric(label, formatar_tempo(df[c].mean()))

    # Distribuição por tipo de desconexão
    if "tipo_desconexao" in df.columns and df["tipo_desconexao"].notna().any():
        st.markdown("**Distribuição por tipo de desconexão**")
        dist = df["tipo_desconexao"].astype(str).value_counts().reset_index()
        dist.columns = ["Tipo", "Qtd"]
        fig = px.bar(dist, x="Tipo", y="Qtd", text="Qtd",
                     labels={"Qtd": "Atendimentos"})
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

    # Atendimentos por dia
    if "data_base" in df.columns and df["data_base"].notna().any():
        df_dia = (
            df.set_index("data_base")
            .resample("D").size()
            .reset_index(name="Atendimentos")
        )
        st.markdown("**Atendimentos por dia**")
        fig2 = px.bar(df_dia, x="data_base", y="Atendimentos",
                      text="Atendimentos", labels={"data_base": "Data"})
        fig2.update_traces(textposition="outside")
        st.plotly_chart(fig2, use_container_width=True)


def secao_por_agente(df):
    st.subheader("Análise por agente")

    if "nome_agente" not in df.columns or df["nome_agente"].isna().all():
        st.warning("Nenhum agente identificado nos dados.")
        return

    col_tma = _col_tma(df)

    agg = dict(
        atendimentos=(col_tma, "count"),
        tma_s=(col_tma, "mean"),
        tempo_total_s=("duracao_segundos", "sum"),
    )
    for col, alias in [("tratamento_segundos", "trat_s"), ("fila_segundos", "fila_s")]:
        if col in df.columns:
            agg[alias] = (col, "mean")

    df_ag = (
        df[df["nome_agente"].notna()]
        .groupby(df["nome_agente"].astype(str))
        .agg(**agg)
        .reset_index()
        .sort_values("atendimentos", ascending=False)
    )

    df_ag["TMA"]         = df_ag["tma_s"].apply(formatar_tempo)
    df_ag["Tempo Total"] = df_ag["tempo_total_s"].apply(formatar_tempo)
    if "trat_s" in df_ag.columns:
        df_ag["Trat. Médio"] = df_ag["trat_s"].apply(formatar_tempo)
    if "fila_s" in df_ag.columns:
        df_ag["Fila Média"]  = df_ag["fila_s"].apply(formatar_tempo)

    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(df_ag, x="nome_agente", y="atendimentos",
                     text="atendimentos",
                     labels={"nome_agente": "Agente", "atendimentos": "Atendimentos"})
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(df_ag, x="nome_agente", y="tma_s",
                      text=df_ag["TMA"],
                      labels={"nome_agente": "Agente", "tma_s": "TMA (s)"})
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True)

    cols_tab = ["nome_agente", "atendimentos", "TMA", "Tempo Total"]
    for c in ["Trat. Médio", "Fila Média"]:
        if c in df_ag.columns:
            cols_tab.append(c)
    st.dataframe(df_ag[cols_tab], use_container_width=True)


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

    # Métricas principais
    c1, c2, c3 = st.columns(3)
    c1.metric("Atendimentos", len(df_ag))
    c2.metric("TMA (conversa)", formatar_tempo(df_ag[col_tma].mean()))
    c3.metric("Horas em atendimento", f"{df_ag['duracao_segundos'].sum() / 3600:.1f} h")

    # Tempos médios detalhados
    cols_tempo = [
        ("ura_segundos",        "Média URA"),
        ("fila_segundos",       "Média Fila"),
        ("conversas_segundos",  "TMA (conversa)"),
        ("tratamento_segundos", "Média Tratamento"),
        ("abandono_segundos",   "Média Abandono"),
    ]
    disp = [(label, c) for c, label in cols_tempo if c in df_ag.columns]
    if disp:
        cols_m = st.columns(len(disp))
        for i, (label, c) in enumerate(disp):
            cols_m[i].metric(label, formatar_tempo(df_ag[c].mean()))

    # Tipo de desconexão — rosca + tabela
    if "tipo_desconexao" in df_ag.columns and df_ag["tipo_desconexao"].notna().any():
        st.markdown("**Tipos de desconexão**")
        dist = df_ag["tipo_desconexao"].astype(str).value_counts().reset_index()
        dist.columns = ["Tipo", "Qtd"]
        dist["Pct"] = (dist["Qtd"] / dist["Qtd"].sum() * 100).round(1).astype(str) + "%"

        col_g, col_t = st.columns([2, 1])
        with col_g:
            fig_rosca = px.pie(
                dist, names="Tipo", values="Qtd",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig_rosca.update_traces(textinfo="label+percent", textposition="outside")
            fig_rosca.update_layout(showlegend=False, margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig_rosca, use_container_width=True)
        with col_t:
            st.dataframe(dist.rename(columns={"Qtd": "Qtd", "Pct": "%"}),
                         use_container_width=True, hide_index=True)

    # Atendimentos por dia
    if "data_base" in df_ag.columns and df_ag["data_base"].notna().any():
        df_dia = (
            df_ag.set_index("data_base")
            .resample("D").size()
            .reset_index(name="Atendimentos")
        )
        fig_dia = px.bar(df_dia, x="data_base", y="Atendimentos",
                         text="Atendimentos", labels={"data_base": "Data"})
        fig_dia.update_traces(textposition="outside")
        st.plotly_chart(fig_dia, use_container_width=True)

    # Tabela detalhada
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

    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(df_ass, x="assunto", y="atendimentos", text="atendimentos",
                     title="Volume por assunto")
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(df_ass, x="assunto", y="tma_s", text=df_ass["TMA"],
                      title="TMA por assunto")
        fig2.update_traces(textposition="outside")
        fig2.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig2, use_container_width=True)

    st.dataframe(df_ass[["assunto", "atendimentos", "TMA", "Tempo Total"]],
                 use_container_width=True)


def secao_top_assuntos_tma(df):
    st.subheader("Top 10 assuntos por TMA — por mês")

    if "assunto" not in df.columns or df["assunto"].isna().all():
        st.info("Ainda não há assuntos cruzados com o Zendesk.")
        return

    if "mes" not in df.columns or df["mes"].isna().all():
        st.info("Coluna de mês não disponível.")
        return

    col_tma = _col_tma(df)
    meses   = sorted(df["mes"].dropna().astype(str).unique().tolist())
    mes_sel = st.selectbox("Selecione o mês", meses)

    df_mes = df[(df["mes"].astype(str) == mes_sel) & df["assunto"].notna()].copy()
    if df_mes.empty:
        st.info("Sem dados para este mês.")
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
    st.dataframe(df_top[["assunto", "atendimentos", "TMA"]].reset_index(drop=True),
                 use_container_width=True)

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
                .sort_values("tma_s", ascending=False).head(10)
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
        "Visão geral", "Por agente", "Detalhe do agente", "Por assunto", "Top TMA por mês"
    ])
    with aba1: secao_visao_geral(df_filtrado)
    with aba2: secao_por_agente(df_filtrado)
    with aba3: secao_detalhe_agente(df_filtrado)
    with aba4: secao_por_assunto(df_filtrado)
    with aba5: secao_top_assuntos_tma(df_filtrado)


if __name__ == "__main__":
    main()
```

Desta vez o arquivo está completo e sem cortes. As mudanças em relação ao que estava no repositório foram: `carregar_genesys` reescrita para ler XLSX via `openpyxl` usando `BytesIO`, ambas as funções de carregamento decoradas com `@st.cache_data` recebendo `bytes` em vez do `UploadedFile`, gráfico de rosca de tipo de desconexão na aba de detalhe do agente, slider de hora removido dos filtros, e `gc.collect()` nos pontos de maior alocação de memória.

