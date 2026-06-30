import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# CONFIGURAÇÃO DA PÁGINA E FUNÇÕES
# ==========================================
st.set_page_config(page_title="Automação Razão Contabilístico", page_icon="📊", layout="wide")

# Função para formatar números no padrão brasileiro (1.234,56)
def formatar_br(valor):
    try:
        # Coloca vírgula nos milhares (formato americano temporário)
        texto = f"{float(valor):,.2f}"
        # Inverte: vírgula vira X, ponto vira vírgula, X vira ponto
        return texto.replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return valor

st.title("📊 Carregamento do Razão para o Google Sheets")
st.write("Faça o carregamento (upload) do seu ficheiro. Valide os dados e edite informações em falta antes de enviar.")

# 1. Componente para upload do ficheiro
arquivo_upload = st.file_uploader("Selecione o ficheiro do Razão (CSV ou Excel)", type=["csv", "xlsx"])

if arquivo_upload is not None:
    try:
        # ==========================================
        # LEITURA E LIMPEZA IMEDIATA
        # ==========================================
        if arquivo_upload.name.endswith('.csv'):
            df = pd.read_csv(arquivo_upload)
        else:
            df = pd.read_excel(arquivo_upload)
            
        df = df.dropna(subset=['Conta'])
        df['Descrição da Conta'] = df['Descrição da Conta'].astype(str).str.upper().str.strip()
        df['Descrição Centro de Resultado'] = df['Descrição Centro de Resultado'].astype(str).str.upper().str.strip()
        df['Saldo'] = pd.to_numeric(df['Saldo'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)
        
        for col in ['Debito', 'Crédito']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)

        # Tratar a data para o resumo
        if 'Data' in df.columns:
            df['Data_Real'] = pd.to_datetime(df['Data'], format='%d/%m/%Y', errors='coerce')
            df['Mês_Ano'] = df['Data_Real'].dt.strftime('%m/%Y')
            df['Data'] = df['Data_Real'].dt.strftime('%d/%m/%Y')

        # ==========================================
        # REGRAS DE FILTRAGEM
        # ==========================================
        contas_alvo = ['LICENCAS DE SOFTWARE', 'SERVICOS DE INFORMATICA']

        # Passo 1: Contas de licenças/serviços (Usamos .copy() para evitar avisos do Pandas na edição)
        parte_1 = df[df['Descrição da Conta'].isin(contas_alvo)].copy()

        # Passo 2: Outras contas (CR de TI)
        parte_2 = df[
            (~df['Descrição da Conta'].isin(contas_alvo)) & 
            (df['Descrição Centro de Resultado'].str.startswith('TI -'))
        ].copy()

        # ==========================================
        # VERIFICAÇÃO E EDIÇÃO DE FORNECEDORES EM BRANCO (SÓ NA PARTE 1)
        # ==========================================
        if 'Fornecedor' in parte_1.columns:
            # Encontrar quem é NaN, Vazio ou apenas a palavra "Fornecedor:"
            condicao_vazio = (
                parte_1['Fornecedor'].isna() | 
                parte_1['Fornecedor'].astype(str).str.strip().isin(['', 'nan', 'Fornecedor:', 'Fornecedor'])
            )
            
            if condicao_vazio.any():
                st.warning("⚠️ Encontrámos lançamentos de Licenças/Serviços sem o nome do Fornecedor!")
                st.write("Verifique a coluna 'Historico' e digite o nome correto na coluna 'Fornecedor' abaixo:")
                
                colunas_mostrar = ['Data', 'Descrição da Conta', 'Historico', 'Fornecedor']
                
                # O data_editor permite digitar no ecrã. Bloqueamos a edição nas outras colunas.
                df_editado = st.data_editor(
                    parte_1.loc[condicao_vazio, colunas_mostrar],
                    disabled=['Data', 'Descrição da Conta', 'Historico'],
                    hide_index=True,
                    use_container_width=True
                )
                
                # Atualiza a base real (parte_1) com os dados que digitou no ecrã
                parte_1.update(df_editado['Fornecedor'])

        # Junta as duas partes agora com os fornecedores corrigidos
        base_final = pd.concat([parte_1, parte_2])

        # ==========================================
        # ECRÃ DE VALIDAÇÃO (ANTES DE ENVIAR)
        # ==========================================
        if base_final.empty:
            st.error("Nenhum dado encontrado com os critérios estipulados.")
        else:
            st.markdown("---")
            st.subheader("🔎 Validação dos Totais (Por Mês)")
            
            # Resumos
            resumo_p1 = parte_1.groupby('Mês_Ano')['Saldo'].sum().reset_index()
            resumo_p1.rename(columns={'Saldo': 'Licenças e Serv. Informática (R$)'}, inplace=True)
            
            resumo_p2 = parte_2.groupby('Mês_Ano')['Saldo'].sum().reset_index()
            resumo_p2.rename(columns={'Saldo': 'CR de TI - Outras Contas (R$)'}, inplace=True)
            
            # Juntar e formatar os números para o padrão Brasil
            tabela_validacao = pd.merge(resumo_p1, resumo_p2, on='Mês_Ano', how='outer').fillna(0)
            tabela_validacao['Licenças e Serv. Informática (R$)'] = tabela_validacao['Licenças e Serv. Informática (R$)'].apply(formatar_br)
            tabela_validacao['CR de TI - Outras Contas (R$)'] = tabela_validacao['CR de TI - Outras Contas (R$)'].apply(formatar_br)
            
            # hide_index=True remove a primeira coluna indicativa de 0, 1, 2...
            st.dataframe(tabela_validacao, hide_index=True, use_container_width=True)
            
            st.info("👆 Se os valores e fornecedores estiverem corretos, clique no botão abaixo para concluir.")

            # ==========================================
            # BOTÃO DE ENVIO DEFINITIVO
            # ==========================================
            if st.button("Tudo certo! Validar e Enviar para o Sheets", type="primary"):
                with st.spinner('A enviar os dados para o Google Sheets...'):
                    
                    # Limpeza final antes de enviar
                    base_final = base_final.drop(columns=['Data_Real', 'Mês_Ano'], errors='ignore')
                    base_final = base_final.fillna("")
                    
                    dados_para_subir = base_final.values.tolist()

                    scopes = [
                        "https://www.googleapis.com/auth/spreadsheets",
                        "https://www.googleapis.com/auth/drive"
                    ]
                    
                    # Lê as credenciais do cofre seguro do Streamlit (Secrets)
                    credenciais_dict = dict(st.secrets["gcp_service_account"])
                    credentials = Credentials.from_service_account_info(credenciais_dict, scopes=scopes)
                    client = gspread.authorize(credentials)

                    # --- CONEXÃO COM A SUA PLANILHA OFICIAL ---
                    planilha = client.open("[Opex LATAM] - Base Realizado")
                    aba = planilha.worksheet("Razao")

                    aba.append_rows(dados_para_subir, value_input_option='USER_ENTERED')
                    
                    st.success("✅ Dados processados e anexados ao Google Sheets com sucesso!")

    except Exception as e:
        st.error(f"Ocorreu um erro durante a execução: {e}")