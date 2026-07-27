import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# CONFIGURAÇÃO DA PÁGINA E FUNÇÕES
# ==========================================
st.set_page_config(page_title="Automação Razão Contabilístico", page_icon="📊", layout="wide")

def formatar_br(valor):
    try:
        texto = f"{float(valor):,.2f}"
        return texto.replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return valor

@st.cache_data
def processar_arquivo(arquivo):
    """Lê e aplica a limpeza inicial no arquivo. O cache evita reprocessamento a cada clique."""
    if arquivo.name.endswith('.csv'):
        df = pd.read_csv(arquivo)
    else:
        df = pd.read_excel(arquivo)
        
    df = df.dropna(subset=['Conta'])
    df['Descrição da Conta'] = df['Descrição da Conta'].astype(str).str.upper().str.strip()
    df['Descrição Centro de Resultado'] = df['Descrição Centro de Resultado'].astype(str).str.upper().str.strip()
    df['Saldo'] = pd.to_numeric(df['Saldo'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)
    
    for col in ['Debito', 'Crédito']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)

    if 'Data' in df.columns:
        df['Data_Real'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
        df['Mês_Ano'] = df['Data_Real'].dt.strftime('%m/%Y')
        df['Data'] = df['Data_Real'].dt.strftime('%d/%m/%Y')
        
    return df

# ==========================================
# INTERFACE PRINCIPAL
# ==========================================
st.title("📊 Carregamento do Razão para o Google Sheets")
st.write("Faça o carregamento (upload) do seu ficheiro. Valide os dados e edite informações em falta antes de enviar.")

arquivo_upload = st.file_uploader("Selecione o ficheiro do Razão (CSV ou Excel)", type=["csv", "xlsx"])

if arquivo_upload is not None:
    try:
        # Carrega os dados de forma otimizada usando a função com cache
        df = processar_arquivo(arquivo_upload)

        # ==========================================
        # REGRAS DE FILTRAGEM
        # ==========================================
        contas_alvo = ['LICENCAS DE SOFTWARE', 'SERVICOS DE INFORMATICA']

        # Passo 1: Contas de licenças/serviços
        parte_1 = df[df['Descrição da Conta'].isin(contas_alvo)].copy()

        # Passo 2: Outras contas (CR de TI) - REMOVENDO DEPRECIAÇÃO E AMORTIZAÇÃO
        filtro_cr_ti = df['Descrição Centro de Resultado'].str.startswith('TI -')
        filtro_nao_licencas = ~df['Descrição da Conta'].isin(contas_alvo)
        filtro_nao_depreciacao = ~df['Descrição da Conta'].str.startswith('DEPRECIAÇÃO DE IMOBILIZADO')
        filtro_nao_amortizacao = ~df['Descrição da Conta'].str.startswith('AMORTIZAÇÃO')

        parte_2 = df[filtro_cr_ti & filtro_nao_licencas & filtro_nao_depreciacao & filtro_nao_amortizacao].copy()

        # ==========================================
        # VERIFICAÇÃO E EDIÇÃO DE FORNECEDORES
        # ==========================================
        if 'Fornecedor' in parte_1.columns:
            condicao_vazio = (
                parte_1['Fornecedor'].isna() | 
                parte_1['Fornecedor'].astype(str).str.strip().isin(['', 'nan', 'Fornecedor:', 'Fornecedor'])
            )
            
            if condicao_vazio.any():
                st.warning("⚠️ Encontrámos lançamentos de Licenças/Serviços sem o nome do Fornecedor!")
                st.write("Verifique a coluna 'Historico' e digite o nome correto na coluna 'Fornecedor' abaixo:")
                
                colunas_mostrar = ['Data', 'Descrição da Conta', 'Historico', 'Fornecedor']
                
                # Armazena a edição do usuário na variável
                df_editado = st.data_editor(
                    parte_1.loc[condicao_vazio, colunas_mostrar],
                    disabled=['Data', 'Descrição da Conta', 'Historico'],
                    hide_index=True,
                    use_container_width=True
                )
                
                # Atualiza parte_1 com as edições feitas na tela
                parte_1.update(df_editado['Fornecedor'])

        # Junta as duas partes 
        base_final = pd.concat([parte_1, parte_2])

        # ==========================================
        # ECRÃ DE VALIDAÇÃO
        # ==========================================
        if base_final.empty:
            st.error("Nenhum dado encontrado com os critérios estipulados.")
        else:
            st.markdown("---")
            st.subheader("🔎 Validação dos Totais (Por Mês)")
            
            resumo_p1 = parte_1.groupby('Mês_Ano')['Saldo'].sum().reset_index()
            resumo_p1.rename(columns={'Saldo': 'Licenças e Serv. Informática (R$)'}, inplace=True)
            
            resumo_p2 = parte_2.groupby('Mês_Ano')['Saldo'].sum().reset_index()
            resumo_p2.rename(columns={'Saldo': 'CR de TI - Outras Contas (R$)'}, inplace=True)
            
            tabela_validacao = pd.merge(resumo_p1, resumo_p2, on='Mês_Ano', how='outer').fillna(0)
            tabela_validacao['Licenças e Serv. Informática (R$)'] = tabela_validacao['Licenças e Serv. Informática (R$)'].apply(formatar_br)
            tabela_validacao['CR de TI - Outras Contas (R$)'] = tabela_validacao['CR de TI - Outras Contas (R$)'].apply(formatar_br)
            
            st.dataframe(tabela_validacao, hide_index=True, use_container_width=True)
            
            st.info("👆 Se os valores e fornecedores estiverem corretos, clique no botão abaixo para concluir.")

            # ==========================================
            # BOTÃO DE ENVIO DEFINITIVO PARA O SHEETS
            # ==========================================
            if st.button("Tudo certo! Validar e Enviar para o Sheets", type="primary"):
                with st.spinner('A calcular a linha correta e enviar os dados para o Google Sheets...'):
                    
                    # Limpeza final antes de enviar
                    base_final = base_final.drop(columns=['Data_Real', 'Mês_Ano'], errors='ignore')
                    
                    if 'Fornecedor' in base_final.columns:
                        base_final['Fornecedor'] = base_final['Fornecedor'].astype(str).str.strip()
                    
                    base_final = base_final.replace("nan", "")
                    base_final = base_final.fillna("")
                    
                    # Converter o DataFrame para lista de listas
                    dados_para_subir = base_final.values.tolist()

                    # Autenticação na API do Google
                    scopes = [
                        "https://www.googleapis.com/auth/spreadsheets",
                        "https://www.googleapis.com/auth/drive"
                    ]
                    credenciais_dict = dict(st.secrets["gcp_service_account"])
                    credentials = Credentials.from_service_account_info(credenciais_dict, scopes=scopes)
                    client = gspread.authorize(credentials)

                    # Abertura da planilha
                    planilha = client.open("[Opex LATAM] - Base Realizado")
                    aba = planilha.worksheet("Razao")

                    # ----------------------------------------------------
                    # NOVA LÓGICA DE INSERÇÃO À PROVA DE FILTROS
                    # ----------------------------------------------------
                    # 1. Pega todas as linhas para descobrir o tamanho real
                    todas_as_linhas = aba.get_all_values()
                    proxima_linha_vazia = len(todas_as_linhas) + 1

                    # 2. Garante que existem linhas suficientes na planilha
                    linhas_necessarias = proxima_linha_vazia + len(dados_para_subir) - 1
                    if linhas_necessarias > aba.row_count:
                        aba.add_rows(linhas_necessarias - aba.row_count)

                    # 3. Define a célula inicial exata (ex: "A150") e cola os dados
                    intervalo = f"A{proxima_linha_vazia}"
                    aba.update(
                        range_name=intervalo, 
                        values=dados_para_subir, 
                        value_input_option='USER_ENTERED'
                    )
                    
                    st.success(f"✅ Dados processados e anexados com sucesso a partir da linha {proxima_linha_vazia}!")

    except Exception as e:
        st.error(f"Ocorreu um erro durante a execução: {e}")
