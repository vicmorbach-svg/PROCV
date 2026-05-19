import streamlit as st
import pandas as pd
import numpy as np
import os
import re
import unicodedata
import gc
import plotly.express as px
from io import BytesIO

# Adicionadas as importacoes necessarias para as funcoes do GitHub e Login
import requests
import base64
import json
import io
import datetime
import pytz

# Configura o fuso horário do Brasil
fuso_br = pytz.timezone('America/Sao_Paulo')
hora_atual = datetime.datetime.now(fuso_br).hour

# Define o funcionamento das 08h às 18h (por exemplo)
if hora_atual < 8 or hora_atual >= 18:
    st.cache_data.clear()
    st.title("🌙 Sistema em Repouso")
    st.info("O painel de análise funciona apenas das 08h às 18h para economia de recursos.")
    st.stop() # Interrompe a execução de todo o resto do código abaixo

# ══════════════════════════════════════════════════════════════
# SISTEMA DE LOGIN (Integrado do app_analise.py)
# ══════════════════════════════════════════════════════════════

#def get_users():
    users = {}
    try:
        secrets  = st.secrets["users"]
        prefixes = set()
        for key in secrets:
            if key.endswith("_user"):
                prefixes.add(key[:-5])
        for prefix in prefixes:
            username = secrets.get(f"{prefix}_user", "")
            password = secrets.get(f"{prefix}_password", "")
            role     = secrets.get(f"{prefix}_role", "user")
            if username:
                users[username] = {"password": password, "role": role}
    except Exception:
        pass
    return users

#def login_screen():
    st.title("🔐 Login")
    st.markdown("Faça login para acessar o sistema.")
    with st.form("login_form"):
        username  = st.text_input("Usuário")
        password  = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar")
    if submitted:
        users = get_users()
        if username in users and users[username]["password"] == password:
            st.session_state["logged_in"] = True
            st.session_state["username"]  = username
            st.session_state["role"]      = users[username]["role"]
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")

#def is_admin():
    return st.session_state.get("role") == "admin"

# ══════════════════════════════════════════════════════════════
# GITHUB — Integração (Integrado do app_analise.py)
# ══════════════════════════════════════════════════════════════

def get_github_config():
    try:
        token  = st.secrets["github"]["token"]
        repo   = st.secrets["github"]["repo"]
        branch = st.secrets["github"].get("branch", "main")
        return token, repo, branch
    except Exception:
        st.error("Erro ao carregar configuracao do GitHub. Verifique st.secrets.")
        return None, None, None

def get_github_headers():
    token, _, _ = get_github_config()
    if not token: return {}
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

def get_file_sha(path):
    token, repo, branch = get_github_config()
    if not token: return None
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    r   = requests.get(url, headers=get_github_headers())
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict): return data.get("sha")
    return None

def get_file_from_github(path):
    token, repo, branch = get_github_config()
    if not token: return None, None
    # A API de contents retorna o conteudo base64-encoded
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=get_github_headers())
    if r.status_code == 200:
        data = r.json()
        if "content" in data:
            content_bytes = base64.b64decode(data["content"])
            return content_bytes, data.get("sha")
    return None, None

def save_file_to_github(path, content_bytes, message):
    token, repo, branch = get_github_config()
    if not token: return False
    sha = get_file_sha(path) # Obtem o SHA atual para atualizacao
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch":  branch
    }
    if sha: payload["sha"] = sha
    r = requests.put(url, headers=get_github_headers(), data=json.dumps(payload))
    return r.status_code in [200, 201]

def delete_file_from_github(path, message):
    token, repo, branch = get_github_config()
    if not token: return False
    sha = get_file_sha(path)
    if not sha: return True # Se o arquivo nao existe, ja esta "apagado"
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {"message": message, "sha": sha, "branch": branch}
    r = requests.delete(url, headers=get_github_headers(), data=json.dumps(payload))
    return r.status_code == 200

def df_to_parquet_bytes(df):
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine='pyarrow')
    buf.seek(0)
    return buf.getvalue()

def parquet_bytes_to_df(content_bytes, columns=None):
    if not content_bytes: return None
    try:
        buf = io.BytesIO(content_bytes)
        buf.seek(0)
        return pd.read_parquet(buf, engine='pyarrow', columns=columns)
    except Exception as e:
        st.error(f"Erro ao ler arquivo Parquet do GitHub: {e}")
        return None

# ══════════════════════════════════════════════════════════════
# HISTORICO DE ATENDIMENTOS (Adaptado para GitHub)
# ══════════════════════════════════════════════════════════════

# CORRIGIDO: O caminho do historico agora aponta para a pasta 'data' no GitHub
HISTORICO_PATH = "data/historico_atendimentos.parquet"

@st.cache_data(ttl=3600) # Cache para evitar multiplas chamadas a API do GitHub
def carregar_historico():
    content, _ = get_file_from_github(HISTORICO_PATH)
    if content:
        df = parquet_bytes_to_df(content)
        if df is not None:
            # Garante que as colunas de data estao no formato correto
            for col in ['data_atendimento', 'data_fim_atendimento']:
                if col in df.columns:
                    # CORRIGIDO: Adicionado format para evitar UserWarning
                    df[col] = pd.to_datetime(df[col], errors='coerce', format="%Y-%m-%d %H:%M:%S")
            return df
    return pd.DataFrame()

def salvar_historico(df):
    if df.empty:
        # Se o DataFrame estiver vazio, tenta apagar o arquivo do GitHub
        return delete_file_from_github(HISTORICO_PATH, "Apaga historico vazio")

    parquet_data = df_to_parquet_bytes(df)
    if parquet_data:
        return save_file_to_github(HISTORICO_PATH, parquet_data, "Atualiza historico de atendimentos")
    return False

# ══════════════════════════════════════════════════════════════
# FUNCOES DE PROCESSAMENTO DE DADOS (DO SEU DASHBOARD ORIGINAL)
# ══════════════════════════════════════════════════════════════

def formatar_tempo(segundos):
    if pd.isna(segundos): return "00:00:00"
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    seg = int(segundos % 60)
    return f"{horas:02}:{minutos:02}:{seg:02}"

def _col_tma(df):
    if "tma_segundos" in df.columns: return "tma_segundos"
    if "duracao_segundos" in df.columns: return "duracao_segundos"
    return None

def carregar_zendesk(uploaded_file_content, file_name):
    try:
        df = pd.read_excel(uploaded_file_content)
        df = df.rename(columns={
            "Ticket ID": "ticket_id",
            "Created at": "data_atendimento",
            "Group": "grupo",
            "Assignee": "agente",
            "Subject": "assunto",
            "Status": "status_zendesk",
            "Tags": "tags"
        })
        df["data_atendimento"] = pd.to_datetime(df["data_atendimento"], errors='coerce')
        df = df.dropna(subset=["ticket_id", "data_atendimento"])
        df["ticket_id"] = df["ticket_id"].astype(str)
        return df
    except Exception as e:
        st.error(f"Erro ao carregar Zendesk: {e}")
        return pd.DataFrame()

def carregar_genesys(uploaded_file_content, file_name):
    try:
        df = pd.read_excel(uploaded_file_content)
        df = df.rename(columns={
            "Conversation Id": "conversation_id",
            "Queue Name": "fila",
            "Agent Name": "nome_agente",
            "Start Time": "data_atendimento",
            "End Time": "data_fim_atendimento",
            "Duration (s)": "duracao_segundos",
            "ACD Duration (s)": "acd_segundos",
            "Handle Time (s)": "handle_segundos",
            "Talk Time (s)": "conversas_segundos",
            "Hold Time (s)": "espera_segundos",
            "Wrap-up Time (s)": "tpc_segundos",
            "IVR Duration (s)": "ura_segundos",
            "Flow Out Time (s)": "fila_segundos",
            "Abandon Time (s)": "abandono_segundos",
            "Abandon": "abandonado",
            "Customer Sentiment Score": "sentimento_cliente",
            "Customer Sentiment Trend": "tendencia_sentimento",
            "Customer Sentiment Type": "tipo_sentimento",
            "Customer Sentiment Score (0-100)": "sentimento_score_0_100",
            "Customer Sentiment Trend (0-100)": "tendencia_sentimento_0_100",
            "Customer Sentiment Type (0-100)": "tipo_sentimento_0_100",
            "Interaction Type": "tipo_interacao",
            "Interaction Media Type": "tipo_midia",
            "Interaction Direction": "direcao_interacao",
            "Customer Participant Id": "customer_participant_id",
            "Customer Participant Name": "customer_participant_name",
            "Customer Participant Address": "customer_participant_address",
            "Customer Participant Email": "customer_participant_email",
            "Customer Participant Phone": "customer_participant_phone",
            "Customer Participant Type": "customer_participant_type",
            "Customer Participant Role": "customer_participant_role",
            "Customer Participant Status": "customer_participant_status",
            "Customer Participant State": "customer_participant_state",
            "Customer Participant Start Time": "customer_participant_start_time",
            "Customer Participant End Time": "customer_participant_end_time",
            "Customer Participant Duration (s)": "customer_participant_duration_s",
            "Customer Participant ACD Duration (s)": "customer_participant_acd_duration_s",
            "Customer Participant Handle Time (s)": "customer_participant_handle_time_s",
            "Customer Participant Talk Time (s)": "customer_participant_talk_time_s",
            "Customer Participant Hold Time (s)": "customer_participant_hold_time_s",
            "Customer Participant Wrap-up Time (s)": "customer_participant_wrap_up_time_s",
            "Customer Participant IVR Duration (s)": "customer_participant_ivr_duration_s",
            "Customer Participant Flow Out Time (s)": "customer_participant_flow_out_time_s",
            "Customer Participant Abandon Time (s)": "customer_participant_abandon_time_s",
            "Customer Participant Abandon": "customer_participant_abandon",
            "Customer Participant Customer Sentiment Score": "customer_participant_sentiment_score",
            "Customer Participant Customer Sentiment Trend": "customer_participant_sentiment_trend",
            "Customer Participant Customer Sentiment Type": "customer_participant_sentiment_type",
            "Customer Participant Customer Sentiment Score (0-100)": "customer_participant_sentiment_score_0_100",
            "Customer Participant Customer Sentiment Trend (0-100)": "customer_participant_sentiment_trend_0_100",
            "Customer Participant Customer Sentiment Type (0-100)": "customer_participant_sentiment_type_0_100",
            "Customer Participant Interaction Type": "customer_participant_interaction_type",
            "Customer Participant Interaction Media Type": "customer_participant_interaction_media_type",
            "Customer Participant Interaction Direction": "customer_participant_interaction_direction",
            # Removido o restante das colunas com nomes muito longos para evitar SyntaxError
            # e focar nas colunas essenciais para a análise do Call Center.
            # Se precisar de mais colunas, adicione-as manualmente e verifique os nomes.
        })
        # CORRIGIDO: Adicionado format para evitar UserWarning
        df["data_atendimento"] = pd.to_datetime(df["data_atendimento"], errors='coerce', format="%Y-%m-%d %H:%M:%S")
        df["data_fim_atendimento"] = pd.to_datetime(df["data_fim_atendimento"], errors='coerce', format="%Y-%m-%d %H:%M:%S")

        # Calcula TMA se nao existir
        if "tma_segundos" not in df.columns and "duracao_segundos" in df.columns:
            df["tma_segundos"] = df["duracao_segundos"]

        df = df.dropna(subset=["conversation_id", "data_atendimento"])
        df["conversation_id"] = df["conversation_id"].astype(str)
        return df
    except Exception as e:
        st.error(f"Erro ao carregar Genesys: {e}")
        return pd.DataFrame()

def normalizar_texto(texto):
    if pd.isna(texto): return ""
    texto = str(texto).lower()
    texto = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode('utf-8')
    texto = re.sub(r'[^a-z0-9\s]', '', texto)
    return texto

# ══════════════════════════════════════════════════════════════
# INTERFACE STREAMLIT
# ══════════════════════════════════════════════════════════════

st.set_page_config(page_title="Dashboard Call Center", layout="wide")

if not st.session_state.get("logged_in"):
    login_screen()
    st.stop()

st.title("Dashboard de Atendimentos Call Center")

st.sidebar.markdown(f"👤 **{st.session_state['username']}**")
if st.sidebar.button("Sair"):
    st.session_state.clear()
    st.rerun()

st.sidebar.markdown("---")

# --- FILTROS ---
st.sidebar.header("Filtros")
df_historico = carregar_historico()

if df_historico.empty:
    st.warning("Nenhum dado de histórico encontrado. Por favor, faça o upload de um arquivo.")
    df_filtrado = pd.DataFrame() # Garante que df_filtrado existe mesmo vazio
else:
    # Filtro de data
    min_date = df_historico["data_atendimento"].min().date() if not df_historico["data_atendimento"].empty else datetime.date.today()
    max_date = df_historico["data_atendimento"].max().date() if not df_historico["data_atendimento"].empty else datetime.date.today()

    data_inicio = st.sidebar.date_input("Data Início", value=min_date, min_value=min_date, max_value=max_date)
    data_fim = st.sidebar.date_input("Data Fim", value=max_date, min_value=min_date, max_value=max_date)

    df_filtrado = df_historico[
        (df_historico["data_atendimento"].dt.date >= data_inicio) &
        (df_historico["data_atendimento"].dt.date <= data_fim)
    ].copy()

    # Filtro de Agente
    if "nome_agente" in df_filtrado.columns and not df_filtrado["nome_agente"].empty:
        agentes = ["Todos"] + sorted(df_filtrado["nome_agente"].dropna().unique().tolist())
        agente_selecionado = st.sidebar.selectbox("Agente", agentes)
        if agente_selecionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado["nome_agente"] == agente_selecionado]
    else:
        agente_selecionado = "Todos" # Define para evitar erro se a coluna nao existir

    # Filtro de Fila
    if "fila" in df_filtrado.columns and not df_filtrado["fila"].empty:
        filas = ["Todas"] + sorted(df_filtrado["fila"].dropna().unique().tolist())
        fila_selecionada = st.sidebar.selectbox("Fila", filas)
        if fila_selecionada != "Todas":
            df_filtrado = df_filtrado[df_filtrado["fila"] == fila_selecionada]
    else:
        fila_selecionada = "Todas" # Define para evitar erro se a coluna nao existir

st.sidebar.markdown("---")

# ══════════════════════════════════════════════════════════════
# SECAO DE UPLOAD E GERENCIAMENTO DE DADOS
# ══════════════════════════════════════════════════════════════

def secao_upload():
    st.sidebar.header("Upload de Dados")
    uploaded_file = st.sidebar.file_uploader("Escolha um arquivo Excel (Genesys ou Zendesk)", type=["xlsx"])

    if uploaded_file is not None:
        file_name = uploaded_file.name
        file_content = uploaded_file.read()

        if "genesys" in file_name.lower():
            df_novo = carregar_genesys(BytesIO(file_content), file_name)
        elif "zendesk" in file_name.lower():
            df_novo = carregar_zendesk(BytesIO(file_content), file_name)
        else:
            st.sidebar.error("Nome do arquivo não reconhecido (Genesys ou Zendesk).")
            df_novo = pd.DataFrame()

        if not df_novo.empty:
            st.sidebar.success(f"Arquivo '{file_name}' carregado com sucesso. {len(df_novo)} registros.")

            if st.sidebar.button("Adicionar ao Histórico"):
                df_atual = carregar_historico()
                df_combinado = pd.concat([df_atual, df_novo], ignore_index=True)
                # Remove duplicatas com base em um ID único (conversation_id ou ticket_id)
                if "conversation_id" in df_combinado.columns:
                    df_combinado.drop_duplicates(subset=["conversation_id"], keep="last", inplace=True)
                elif "ticket_id" in df_combinado.columns:
                    df_combinado.drop_duplicates(subset=["ticket_id"], keep="last", inplace=True)
                else:
                    st.sidebar.warning("Não foi possível identificar uma coluna de ID única para remover duplicatas.")

                if salvar_historico(df_combinado):
                    st.sidebar.success(f"Dados adicionados ao histórico. Total: {len(df_combinado)} registros.")
                    st.cache_data.clear() # Limpa o cache para recarregar o historico
                    st.rerun()
                else:
                    st.sidebar.error("Erro ao salvar histórico no GitHub.")
        else:
            st.sidebar.error("Nenhum dado válido processado do arquivo.")

    if is_admin():
        if st.sidebar.button("Apagar Histórico Completo", type="secondary"):
            if delete_file_from_github(HISTORICO_PATH, "Apaga historico completo via app"):
                st.sidebar.success("Histórico apagado com sucesso do GitHub.")
                st.cache_data.clear() # Limpa o cache para recarregar o historico
                st.rerun()
            else:
                st.sidebar.error("Erro ao apagar histórico do GitHub.")

secao_upload()

# ══════════════════════════════════════════════════════════════
# SECOES DE ANALISE (DO SEU DASHBOARD ORIGINAL)
# ══════════════════════════════════════════════════════════════

if not df_filtrado.empty:
    st.markdown("---")
    st.subheader("Visão Geral dos Atendimentos")

    # Métricas principais
    col1, col2, col3, col4 = st.columns(4)
    total_atendimentos = len(df_filtrado)
    col1.metric("Total de Atendimentos", total_atendimentos)

    if _col_tma(df_filtrado):
        tma_medio = df_filtrado[_col_tma(df_filtrado)].mean()
        col2.metric("TMA Médio", formatar_tempo(tma_medio))

    if "conversas_segundos" in df_filtrado.columns:
        tme_medio = df_filtrado["conversas_segundos"].mean()
        col3.metric("TME Médio", formatar_tempo(tme_medio))

    if "tpc_segundos" in df_filtrado.columns:
        tpc_medio = df_filtrado["tpc_segundos"].mean()
        col4.metric("TPC Médio", formatar_tempo(tpc_medio))

    st.markdown("---")

    aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "Visão Geral", "Por Agente", "Detalhe Agente", "Por Assunto", "Top TMA por Assunto"
    ])

    # ══════════════════════════════════════════════════════════
    # ABA 1 — VISÃO GERAL
    # ══════════════════════════════════════════════════════════
    with aba1:
        st.subheader("Atendimentos por Dia")
        df_dia = df_filtrado.groupby(df_filtrado["data_atendimento"].dt.date).size().reset_index(name="Atendimentos")
        df_dia.columns = ["Data", "Atendimentos"]
        fig_dia = px.bar(
            df_dia, x="Data", y="Atendimentos",
            title="Atendimentos Diários",
            labels={"Data": "Data", "Atendimentos": "Atendimentos"}
        )
        fig_dia.update_traces(textposition="outside")
        fig_dia.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig_dia, use_container_width=True, key="vg_dia")

        st.markdown("---")

        # Componentes de tempo (geral)
        componentes = {
            "URA":        "ura_segundos",
            "Fila":       "fila_segundos",
            "Conversa":   "conversas_segundos",
            "TPC":        "tpc_segundos",
            "Tratamento": "tratamento_segundos", # Assumindo que 'tratamento_segundos' é uma coluna relevante
        }
        dados_comp = [
            {"componente": k, "media_s": df_filtrado[v].mean()}
            for k, v in componentes.items()
            if v in df_filtrado.columns and df_filtrado[v].notna().any()
        ]
        if dados_comp:
            df_comp = pd.DataFrame(dados_comp)
            df_comp["Tempo medio"] = df_comp["media_s"].apply(formatar_tempo)
            fig = px.bar(
                df_comp, x="componente", y="media_s", text="Tempo medio",
                title="Tempo medio por componente",
                labels={"componente": "Componente", "media_s": "Segundos"}
            )
            fig.update_traces(textposition="outside")
            st.plotly_chart(fig, use_container_width=True, key="vg_comp")
        else:
            st.info("Dados de componentes de tempo não disponíveis ou vazios.")

    # ══════════════════════════════════════════════════════════
    # ABA 2 — POR AGENTE
    # ══════════════════════════════════════════════════════════
    with aba2:
        if "nome_agente" in df_filtrado.columns and not df_filtrado["nome_agente"].empty:
            st.subheader("Atendimentos por Agente")
            df_agente = df_filtrado.groupby("nome_agente").agg(
                Atendimentos=("nome_agente", "size"),
                TMA_Medio=(_col_tma(df_filtrado), "mean") if _col_tma(df_filtrado) else (None, None),
                TME_Medio=("conversas_segundos", "mean") if "conversas_segundos" in df_filtrado.columns else (None, None),
                TPC_Medio=("tpc_segundos", "mean") if "tpc_segundos" in df_filtrado.columns else (None, None),
            ).reset_index()

            if "TMA_Medio" in df_agente.columns: df_agente["TMA_Medio"] = df_agente["TMA_Medio"].apply(formatar_tempo)
            if "TME_Medio" in df_agente.columns: df_agente["TME_Medio"] = df_agente["TME_Medio"].apply(formatar_tempo)
            if "TPC_Medio" in df_agente.columns: df_agente["TPC_Medio"] = df_agente["TPC_Medio"].apply(formatar_tempo)

            st.dataframe(df_agente.sort_values("Atendimentos", ascending=False), use_container_width=True, hide_index=True)

            fig_agente_atend = px.bar(
                df_agente, x="nome_agente", y="Atendimentos",
                title="Total de Atendimentos por Agente",
                labels={"nome_agente": "Agente", "Atendimentos": "Atendimentos"}
            )
            fig_agente_atend.update_traces(textposition="outside")
            st.plotly_chart(fig_agente_atend, use_container_width=True, key="ag_atend")
        else:
            st.info("Dados de agente não disponíveis ou vazios.")

    # ══════════════════════════════════════════════════════════
    # ABA 3 — DETALHE AGENTE
    # ══════════════════════════════════════════════════════════
    with aba3:
        if "nome_agente" in df_filtrado.columns and not df_filtrado["nome_agente"].empty:
            agentes_detalhe = sorted(df_filtrado["nome_agente"].dropna().unique().tolist())
            agente_sel = st.selectbox("Selecione um Agente para Detalhes", agentes_detalhe)

            if agente_sel:
                df_ag = df_filtrado[df_filtrado["nome_agente"] == agente_sel].copy()

                st.subheader(f"Métricas Detalhadas para {agente_sel}")
                col_da1, col_da2, col_da3, col_da4 = st.columns(4)
                col_da1.metric("Total Atendimentos", len(df_ag))
                if _col_tma(df_ag): col_da2.metric("TMA Médio", formatar_tempo(df_ag[_col_tma(df_ag)].mean()))
                if "conversas_segundos" in df_ag.columns: col_da3.metric("TME Médio", formatar_tempo(df_ag["conversas_segundos"].mean()))
                if "tpc_segundos" in df_ag.columns: col_da4.metric("TPC Médio", formatar_tempo(df_ag["tpc_segundos"].mean()))

                st.markdown("---")
                st.subheader(f"Tempo médio por componente - {agente_sel}")
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
                        title=f"Tempo medio por componente - {agente_sel}",
                        labels={"componente": "Componente", "media_s": "Segundos"}
                    )
                    fig.update_traces(textposition="outside")
                    st.plotly_chart(fig, use_container_width=True, key="da_comp")
                else:
                    st.info("Dados de componentes de tempo para este agente não disponíveis ou vazios.")
        else:
            st.info("Dados de agente não disponíveis ou vazios para detalhamento.")

    # ══════════════════════════════════════════════════════════
    # ABA 4 — POR ASSUNTO
    # ══════════════════════════════════════════════════════════
    with aba4:
        if "assunto" in df_filtrado.columns and not df_filtrado["assunto"].empty:
            st.subheader("Atendimentos por Assunto")
            df_assunto = df_filtrado.groupby("assunto").agg(
                Atendimentos=("assunto", "size"),
                TMA_Medio=(_col_tma(df_filtrado), "mean") if _col_tma(df_filtrado) else (None, None),
            ).reset_index()

            if "TMA_Medio" in df_assunto.columns: df_assunto["TMA_Medio"] = df_assunto["TMA_Medio"].apply(formatar_tempo)

            st.dataframe(df_assunto.sort_values("Atendimentos", ascending=False), use_container_width=True, hide_index=True)

            fig_assunto_atend = px.bar(
                df_assunto.head(10), x="assunto", y="Atendimentos",
                title="Top 10 Assuntos por Atendimentos",
                labels={"assunto": "Assunto", "Atendimentos": "Atendimentos"}
            )
            fig_assunto_atend.update_traces(textposition="outside")
            st.plotly_chart(fig_assunto_atend, use_container_width=True, key="as_atend")
        else:
            st.info("Dados de assunto não disponíveis ou vazios.")

    # ══════════════════════════════════════════════════════════
    # ABA 5 — TOP TMA POR ASSUNTO
    # ══════════════════════════════════════════════════════════
    with aba5:
        if "assunto" in df_filtrado.columns and _col_tma(df_filtrado) and not df_filtrado["assunto"].empty:
            st.subheader("Top Assuntos com Maior TMA Médio")
            df_tma_assunto = df_filtrado.groupby("assunto")[_col_tma(df_filtrado)].mean().reset_index(name="TMA_Medio_Segundos")
            df_tma_assunto = df_tma_assunto.sort_values("TMA_Medio_Segundos", ascending=False)
            df_tma_assunto["TMA_Medio"] = df_tma_assunto["TMA_Medio_Segundos"].apply(formatar_tempo)

            st.dataframe(df_tma_assunto.head(10), use_container_width=True, hide_index=True)

            fig_tma_assunto = px.bar(
                df_tma_assunto.head(10), x="assunto", y="TMA_Medio_Segundos", text="TMA_Medio",
                title="Top 10 Assuntos com Maior TMA Médio",
                labels={"assunto": "Assunto", "TMA_Medio_Segundos": "TMA Médio (Segundos)"}
            )
            fig_tma_assunto.update_traces(textposition="outside")
            st.plotly_chart(fig_tma_assunto, use_container_width=True, key="tma_assunto")
        else:
            st.info("Dados de assunto ou TMA não disponíveis ou vazios para esta análise.")

else:
    st.info("Carregue dados para visualizar o dashboard.")

if __name__ == "__main__":
    # A função main() não é mais necessária aqui, pois o código é executado sequencialmente
    # após as verificações de login e horário.
    pass
