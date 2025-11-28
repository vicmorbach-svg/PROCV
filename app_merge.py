import streamlit as st
import pandas as pd
import io # Importar io para o buffer do Excel

# --- Funções de Carregamento e Cache ---
@st.cache_data
def load_data(uploaded_file):
    """
    Carrega um arquivo CSV ou Excel em um DataFrame do pandas.
    Esta função é cacheada para melhorar o desempenho.
    """
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file, engine='openpyxl')
            return df
        except Exception as e:
            st.error(f"Erro ao carregar o arquivo: {e}")
            return None
    return None

# --- Função Principal da Aplicação Streamlit ---
def app():
    st.set_page_config(layout="wide", page_title="Ferramenta de Busca e Merge de Planilhas")

    st.title("🔎 Ferramenta de Busca e Merge de Planilhas")
    st.markdown("""
        Esta ferramenta permite que você combine dados de duas planilhas (arquivos CSV ou Excel)
        de forma semelhante à função PROCV do Excel.
        Você pode definir as colunas de busca e quais colunas deseja incluir no resultado final.
    """)

    st.sidebar.header("Upload de Arquivos")

    # --- Upload do Lookup File ---
    st.sidebar.subheader("1. Arquivo de Busca (Lookup File)")
    lookup_file_uploader = st.sidebar.file_uploader(
        "Faça o upload do seu arquivo de busca (CSV ou Excel)",
        type=["csv", "xlsx"],
        key="lookup_uploader"
    )
    lookup_df = load_data(lookup_file_uploader)
    if lookup_df is not None:
        st.sidebar.success("Lookup File carregado com sucesso!")
       

    # --- Upload do Target File ---
    st.sidebar.subheader("2. Arquivo Alvo (Target File)")
    target_file_uploader = st.sidebar.file_uploader(
        "Faça o upload do seu arquivo alvo (CSV ou Excel)",
        type=["csv", "xlsx"],
        key="target_uploader"
    )
    target_df = load_data(target_file_uploader)
    if target_df is not None:
        st.sidebar.success("Target File carregado com sucesso!")
        

    # --- Configurações de Merge ---
    st.header("⚙️ Configurações de Merge")

    if lookup_df is not None and target_df is not None:
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

                    if target_key_column != lookup_key_column:
                        target_df_copy = target_df_copy.rename(columns={target_key_column: lookup_key_column})

                    lookup_df_copy[lookup_key_column] = lookup_df_copy[lookup_key_column].astype(str)
                    target_df_copy[lookup_key_column] = target_df_copy[lookup_key_column].astype(str)

                    cols_to_keep_from_lookup = list(dict.fromkeys(selected_lookup_columns + [lookup_key_column]))
                    lookup_df_filtered = lookup_df_copy[cols_to_keep_from_lookup]

                    cols_to_keep_from_target = [lookup_key_column]
                    for col in selected_target_columns:
                        if col != target_key_column and col not in cols_to_keep_from_target:
                            cols_to_keep_from_target.append(col)

                    target_df_filtered = target_df_copy[cols_to_keep_from_target]

                    merged_df = pd.merge(
                        lookup_df_filtered,
                        target_df_filtered,
                        on=lookup_key_column,
                        how="left",
                        suffixes=('_lookup', '_target')
                    )

                    st.success("Merge realizado com sucesso!")
                    st.subheader("📊 Resultado da Busca e Merge")
                    st.dataframe(merged_df.head(100))
                    if len(merged_df) > 100:
                        st.info(f"Exibindo as primeiras 100 linhas de um total de {len(merged_df)} linhas. Baixe o arquivo completo para ver todos os dados.")

                    # --- Download do Resultado ---
                    st.subheader("📥 Download do Resultado")

                    # Armazenar o merged_df na session_state para que ele persista
                    # entre as re-execuções e possa ser acessado pelos botões de download.
                    st.session_state['merged_df_for_download'] = merged_df

                    download_format = st.radio(
                        "Selecione o formato para download:",
                        ["Nenhum", "CSV", "Excel"],
                        index=0,
                        horizontal=True,
                        key="download_format_radio" # Adicione uma chave para o radio button
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
        st.info("Por favor, faça o upload de ambos os arquivos para começar a configurar o merge.")

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
            excel_buffer.seek(0)

            st.download_button(
                label="Baixar Resultado como Excel",
                data=excel_buffer,
                file_name="resultado_merge.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_excel_button" # Chave única para o botão
            )

if __name__ == "__main__":
    app()
