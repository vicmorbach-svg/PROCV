import streamlit as st
import pandas as pd
import io

# --- Funções de Carregamento e Cache ---
@st.cache_data
def load_excel_with_sheets(uploaded_file):
    """
    Carrega um arquivo Excel e retorna um dicionário de DataFrames,
    onde as chaves são os nomes das abas.
    Esta função é cacheada para melhorar o desempenho.
    """
    if uploaded_file is not None:
        try:
            # pd.read_excel com sheet_name=None lê todas as abas em um dicionário
            xls = pd.read_excel(uploaded_file, sheet_name=None, engine='openpyxl')
            return xls
        except Exception as e:
            st.error(f"Erro ao carregar o arquivo Excel: {e}")
            return None
    return None

@st.cache_data
def load_csv_data(uploaded_file):
    """
    Carrega um arquivo CSV em um DataFrame do pandas.
    Esta função é cacheada para melhorar o desempenho.
    """
    if uploaded_file is not None:
        try:
            df = pd.read_csv(uploaded_file)
            return df
        except Exception as e:
            st.error(f"Erro ao carregar o arquivo CSV: {e}")
            return None
    return None

# --- Função Principal da Aplicação ---
def app():
    st.set_page_config(layout="wide", page_title="Ferramenta de Busca e Merge de Planilhas")

    st.title("🔎 Ferramenta de Busca e Merge de Planilhas")
    st.markdown("""
        Esta ferramenta permite que você combine dados de planilhas (CSV ou Excel com múltiplas abas)
        de forma semelhante às funções PROCV/PROCX do Excel.
        Você pode definir as colunas de busca e quais colunas deseja incluir no resultado final.
    """)

    st.sidebar.header("Upload de Arquivos")

    # --- Opção de Upload: Um arquivo Excel com múltiplas abas OU dois arquivos ---
    upload_mode = st.sidebar.radio(
        "Como você deseja fazer o merge?",
        ("Dois arquivos (Lookup e Target)", "Um arquivo Excel com múltiplas abas"),
        key="upload_mode_radio"
    )

    # Inicialização de variáveis para garantir que existam no escopo
    lookup_df = None
    target_df = None
    lookup_key_column = None
    target_key_column = None
    selected_lookup_columns = []
    selected_target_columns = []

    if upload_mode == "Dois arquivos (Lookup e Target)":
        # --- Upload do Lookup File ---
        st.sidebar.subheader("1. Arquivo de Busca (Lookup File)")
        lookup_file_uploader = st.sidebar.file_uploader(
            "Faça o upload do seu arquivo de busca (CSV ou Excel)",
            type=["csv", "xlsx"],
            key="lookup_uploader_two_files"
        )

        if lookup_file_uploader:
            if lookup_file_uploader.name.endswith('.csv'):
                lookup_df = load_csv_data(lookup_file_uploader)
                if lookup_df is not None:
                    st.sidebar.success("Lookup CSV carregado com sucesso!")
                    st.sidebar.write(f"Primeiras 5 linhas do Lookup File ({lookup_df.shape[0]} linhas, {lookup_df.shape[1]} colunas):")
                    st.sidebar.dataframe(lookup_df.head())
                    st.sidebar.markdown(f"**Colunas disponíveis:** `{', '.join(lookup_df.columns.tolist())}`")
            else: # Excel
                lookup_xls_data = load_excel_with_sheets(lookup_file_uploader)
                if lookup_xls_data:
                    sheet_names = list(lookup_xls_data.keys())
                    selected_lookup_sheet = st.sidebar.selectbox(
                        "Selecione a aba do Lookup File:",
                        options=sheet_names,
                        key="lookup_sheet_selector"
                    )
                    lookup_df = lookup_xls_data[selected_lookup_sheet]
                    st.sidebar.success(f"Lookup Excel (aba '{selected_lookup_sheet}') carregado com sucesso!")
                    st.sidebar.write(f"Primeiras 5 linhas do Lookup File ({lookup_df.shape[0]} linhas, {lookup_df.shape[1]} colunas):")
                    st.sidebar.dataframe(lookup_df.head())
                    st.sidebar.markdown(f"**Colunas disponíveis:** `{', '.join(lookup_df.columns.tolist())}`")

        # --- Upload do Target File ---
        st.sidebar.subheader("2. Arquivo Alvo (Target File)")
        target_file_uploader = st.sidebar.file_uploader(
            "Faça o upload do seu arquivo alvo (CSV ou Excel)",
            type=["csv", "xlsx"],
            key="target_uploader_two_files"
        )

        if target_file_uploader:
            if target_file_uploader.name.endswith('.csv'):
                target_df = load_csv_data(target_file_uploader)
                if target_df is not None:
                    st.sidebar.success("Target CSV carregado com sucesso!")
                    st.sidebar.write(f"Primeiras 5 linhas do Target File ({target_df.shape[0]} linhas, {target_df.shape[1]} colunas):")
                    st.sidebar.dataframe(target_df.head())
                    st.sidebar.markdown(f"**Colunas disponíveis:** `{', '.join(target_df.columns.tolist())}`")
            else: # Excel
                target_xls_data = load_excel_with_sheets(target_file_uploader)
                if target_xls_data:
                    sheet_names = list(target_xls_data.keys())
                    selected_target_sheet = st.sidebar.selectbox(
                        "Selecione a aba do Target File:",
                        options=sheet_names,
                        key="target_sheet_selector"
                    )
                    target_df = target_xls_data[selected_target_sheet]
                    st.sidebar.success(f"Target Excel (aba '{selected_target_sheet}') carregado com sucesso!")
                    st.sidebar.write(f"Primeiras 5 linhas do Target File ({target_df.shape[0]} linhas, {target_df.shape[1]} colunas):")
                    st.sidebar.dataframe(target_df.head())
                    st.sidebar.markdown(f"**Colunas disponíveis:** `{', '.join(target_df.columns.tolist())}`")

    else: # upload_mode == "Um arquivo Excel com múltiplas abas"
        st.sidebar.subheader("1. Arquivo Excel Único")
        single_excel_uploader = st.sidebar.file_uploader(
            "Faça o upload do seu arquivo Excel (.xlsx)",
            type=["xlsx"],
            key="single_excel_uploader"
        )

        if single_excel_uploader:
            single_xls_data = load_excel_with_sheets(single_excel_uploader)
            if single_xls_data:
                sheet_names = list(single_xls_data.keys())
                if len(sheet_names) < 2:
                    st.sidebar.warning("O arquivo Excel precisa ter pelo menos duas abas para este modo de merge.")
                else:
                    st.sidebar.success("Arquivo Excel carregado com sucesso!")
                    st.sidebar.markdown(f"**Abas disponíveis:** `{', '.join(sheet_names)}`")

                    st.sidebar.subheader("Selecione as Abas para o Merge")
                    col_single_1, col_single_2 = st.sidebar.columns(2)
                    with col_single_1:
                        selected_lookup_sheet_single = st.selectbox(
                            "Aba para o Lookup File:",
                            options=sheet_names,
                            key="lookup_sheet_single_selector"
                        )
                    with col_single_2:
                        # Garante que a aba target não seja a mesma que a lookup por padrão
                        default_target_index = (sheet_names.index(selected_lookup_sheet_single) + 1) % len(sheet_names)
                        selected_target_sheet_single = st.selectbox(
                            "Aba para o Target File:",
                            options=sheet_names,
                            index=default_target_index,
                            key="target_sheet_single_selector"
                        )

                    if selected_lookup_sheet_single == selected_target_sheet_single:
                        st.sidebar.warning("As abas de Lookup e Target não podem ser a mesma. Por favor, selecione abas diferentes.")
                    else:
                        lookup_df = single_xls_data[selected_lookup_sheet_single]
                        target_df = single_xls_data[selected_target_sheet_single]
                        st.sidebar.info(f"Lookup File da aba: '{selected_lookup_sheet_single}'")
                        st.sidebar.info(f"Target File da aba: '{selected_target_sheet_single}'")
                        st.sidebar.write("Primeiras 5 linhas do Lookup DF:")
                        st.sidebar.dataframe(lookup_df.head())
                        st.sidebar.write("Primeiras 5 linhas do Target DF:")
                        st.sidebar.dataframe(target_df.head())


    # --- Configurações de Merge (exibidas apenas se os DFs estiverem prontos) ---
    if lookup_df is not None and target_df is not None:
        st.header("⚙️ Configurações de Merge")
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Coluna de Busca no Lookup File")
            lookup_key_column = st.selectbox(
                "Selecione a coluna do Lookup File que será usada como chave para a busca:",
                options=lookup_df.columns.tolist(),
                key="lookup_key_col"
            )

        with col2:
            st.subheader("Coluna de Busca no Target File")
            target_key_column = st.selectbox(
                "Selecione a coluna do Target File onde a busca será realizada:",
                options=target_df.columns.tolist(),
                key="target_key_col"
            )

        st.subheader("Colunas para Incluir no Resultado")
        st.markdown("Selecione as colunas de ambos os arquivos que você deseja ver no resultado final.")

        all_lookup_cols = lookup_df.columns.tolist()
        all_target_cols = target_df.columns.tolist()

        selected_lookup_columns = st.multiselect(
            "Selecione as colunas do **Lookup File** para incluir:",
            options=all_lookup_cols,
            default=[lookup_key_column] if lookup_key_column else []
        )

        selected_target_columns = st.multiselect(
            "Selecione as colunas do **Target File** para incluir:",
            options=all_target_cols,
            default=[target_key_column] if target_key_column else []
        )

        # --- Botão para Executar o Merge ---
        if st.button("Executar Busca e Merge", type="primary"):
            if lookup_key_column and target_key_column and selected_lookup_columns and selected_target_columns:
                try:
                    # --- Validação de Colunas antes do Merge ---
                    if lookup_key_column not in lookup_df.columns:
                        st.error(f"A coluna chave '{lookup_key_column}' não foi encontrada no Lookup File.")
                        st.info(f"Colunas disponíveis no Lookup File: `{', '.join(lookup_df.columns.tolist())}`")
                        return

                    if target_key_column not in target_df.columns:
                        st.error(f"A coluna chave '{target_key_column}' não foi encontrada no Target File.")
                        st.info(f"Colunas disponíveis no Target File: `{', '.join(target_df.columns.tolist())}`")
                        return

                    missing_lookup_cols = [col for col in selected_lookup_columns if col not in lookup_df.columns]
                    missing_target_cols = [col for col in selected_target_columns if col not in target_df.columns]

                    if missing_lookup_cols:
                        st.error(f"As seguintes colunas selecionadas para o Lookup File não foram encontradas: {', '.join(missing_lookup_cols)}")
                        st.info(f"Colunas disponíveis no Lookup File: `{', '.join(lookup_df.columns.tolist())}`")
                        return

                    if missing_target_cols:
                        st.error(f"As seguintes colunas selecionadas para o Target File não foram encontradas: {', '.join(missing_target_cols)}")
                        st.info(f"Colunas disponíveis no Target File: `{', '.join(target_df.columns.tolist())}`")
                        return

                    # --- Preparação para o Merge ---
                    lookup_df_copy = lookup_df.copy(deep=True)
                    target_df_copy = target_df.copy(deep=True)

                    # Renomeia a coluna chave do target_df_copy para coincidir com a do lookup_df_copy
                    if target_key_column != lookup_key_column:
                        target_df_copy = target_df_copy.rename(columns={target_key_column: lookup_key_column})

                    # Garante que as colunas chave sejam do tipo string para um merge consistente
                    lookup_df_copy[lookup_key_column] = lookup_df_copy[lookup_key_column].astype(str)
                    target_df_copy[lookup_key_column] = target_df_copy[lookup_key_column].astype(str)

                    # Filtra as colunas do lookup_df_copy para incluir apenas as selecionadas e a chave
                    cols_to_keep_from_lookup = list(dict.fromkeys(selected_lookup_columns + [lookup_key_column]))
                    lookup_df_filtered = lookup_df_copy[cols_to_keep_from_lookup]

                    # Filtra as colunas do target_df_copy para incluir apenas as selecionadas e a chave (já renomeada)
                    cols_to_keep_from_target = [lookup_key_column] # Começa com a chave renomeada
                    for col in selected_target_columns:
                        # Adiciona outras colunas selecionadas, evitando duplicatas e a chave original
                        if col != target_key_column and col not in cols_to_keep_from_target:
                            cols_to_keep_from_target.append(col)

                    target_df_filtered = target_df_copy[cols_to_keep_from_target]

                    # Realiza o merge (left join: mantém todas as linhas do lookup_df e adiciona correspondências do target_df)
                    # 'how="left"' simula o comportamento de PROCV/PROCX.
                    # 'suffixes' ajuda a diferenciar colunas com o mesmo nome (exceto a chave)
                    merged_df = pd.merge(
                        lookup_df_filtered,
                        target_df_filtered,
                        on=lookup_key_column, # A coluna chave agora tem o mesmo nome em ambos os DataFrames filtrados
                        how="left",
                        suffixes=('_lookup', '_target')
                    )

                    st.success("Merge realizado com sucesso!")
                    st.subheader("📊 Resultado da Busca e Merge")
                    # Otimização: Mostrar apenas as primeiras 100 linhas para visualização rápida
                    st.dataframe(merged_df.head(100))
                    if len(merged_df) > 100:
                        st.info(f"Exibindo as primeiras 100 linhas de um total de {len(merged_df)} linhas. Baixe o arquivo completo para ver todos os dados.")

                    # Armazena o DataFrame resultante na session_state para download
                    st.session_state['merged_df_for_download'] = merged_df

                    # Opções de download
                    download_format = st.radio(
                        "Selecione o formato para download:",
                        ["Nenhum", "CSV", "Excel"],
                        index=0, # 'Nenhum' como padrão
                        horizontal=True,
                        key="download_format_radio"
                    )

                except KeyError as ke:
                    st.error(f"Ocorreu um erro de coluna (KeyError) durante o merge: {ke}")
                    st.info("Isso geralmente significa que uma coluna selecionada não existe no DataFrame. Por favor, verifique os nomes das colunas.")
                    st.info(f"Colunas disponíveis no Lookup File: `{', '.join(lookup_df.columns.tolist())}`")
                    st.info(f"Colunas disponíveis no Target File: `{', '.join(target_df.columns.tolist())}`")
                except Exception as e:
                    st.error(f"Ocorreu um erro inesperado durante o merge: {e}")
                    st.info("Verifique se as colunas selecionadas para a busca possuem dados compatíveis e se os arquivos estão no formato correto.")
            else:
                st.warning("Por favor, selecione as colunas de busca e as colunas para incluir no resultado.")
    else:
        st.info("Por favor, faça o upload dos arquivos/abas necessários para começar a configurar o merge.")

    # --- Botões de Download (fora do if st.button("Executar Busca e Merge")) ---
    # Estes botões precisam ser renderizados em cada re-execução para funcionar.
    # Eles só aparecerão se 'merged_df_for_download' existir na session_state
    # e o formato de download for selecionado.
    if 'merged_df_for_download' in st.session_state and st.session_state['merged_df_for_download'] is not None:
        merged_df_to_download = st.session_state['merged_df_for_download']

        # Acessa o estado do radio button para saber qual formato foi selecionado
        download_format_current = st.session_state.get('download_format_radio', 'Nenhum')

        if download_format_current == "CSV":
            csv_output = merged_df_to_download.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Baixar Resultado como CSV",
                data=csv_output,
                file_name="resultado_merge.csv",
                mime="text/csv",
                key="download_csv_button" # Chave única para o botão
            )
        elif download_format_current == "Excel":
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                merged_df_to_download.to_excel(writer, index=False, sheet_name='Resultado')
            excel_buffer.seek(0) # Volta para o início do buffer

            st.download_button(
                label="Baixar Resultado como Excel",
                data=excel_buffer,
                file_name="resultado_merge.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_excel_button" # Chave única para o botão
            )

# Ponto de entrada da aplicação
if __name__ == "__main__":
    app()
