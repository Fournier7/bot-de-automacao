# Automações de IA para Windows 11

Dois scripts completos e comentados, sem controle de teclado/mouse:

- `email_classifier.py`: IMAP com TLS, não lidos recebidos nas últimas 24 horas, classificação com **IA local** e Telegram **somente para urgência Alta**.
- `job_finder.py`: consulta **Nerdin, Gupy, RioVagas e Indeed** em cada rodada, com Chromium **headless**, filtro de TI júnior, análise por **IA local** e boletim com até 10 vagas únicas e links.

Ambos usam `requests` para a API do Telegram e a API local do Ollama. `common.py` é uma dependência compartilhada obrigatória. Esta versão segue a exigência de **nenhuma API paga**, substituindo o pedido original de gpt-4o. Não precisa criar conta, chave ou faturamento na OpenAI.

Os agendamentos só são criados após configuração e execução do instalador. O padrão é e-mails a cada 15 minutos e vagas diariamente às 09:00.

Em aplicativos Windows que virtualizam LocalAppData, um arquivo local `runtime_location.json` pode definir o diretório físico de dados. Ele é específico da instalação e não deve ser versionado. Consulte o caminho efetivo:

```powershell
.\.venv\Scripts\python.exe -c "from runtime_paths import DATA_DIR, CONFIG_FILE; print(DATA_DIR); print(CONFIG_FILE)"
```

## 1. Instalação

Instale Python 3.11 ou posterior, com suporte a `venv`, pelo [site oficial do Python](https://www.python.org/downloads/windows/). Abra o PowerShell na pasta destes arquivos:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Instala o navegador invisível dentro da própria pasta do projeto.
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD '.venv\browsers'
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
```

Não precisa ativar o ambiente virtual. As dependências estão consolidadas em `requirements.txt`. Cada versão do Playwright precisa de seu binário correspondente: repita a instalação do Chromium depois de atualizar o Playwright. [Documentação Playwright](https://playwright.dev/python/docs/browsers).

## 2. Arquivo de credenciais

Como a pasta do projeto pode estar no OneDrive, o arquivo com segredos fica **fora do projeto**, em `%LOCALAPPDATA%\IAAutomacoes\.env`:

```powershell
$configDir = Join-Path $env:LOCALAPPDATA 'IAAutomacoes'
if (Test-Path -LiteralPath '.\runtime_location.json') {
    $configDir = (Get-Content -LiteralPath '.\runtime_location.json' -Raw | ConvertFrom-Json).data_directory
}
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
# Execute a cópia apenas na primeira configuração, preservando credenciais existentes.
if (-not (Test-Path -LiteralPath (Join-Path $configDir 'credentials.env'))) {
    Copy-Item -LiteralPath '.env.example' -Destination (Join-Path $configDir 'credentials.env')
}
notepad (Join-Path $configDir 'credentials.env')
```

Preencha:

```dotenv
TELEGRAM_BOT_TOKEN=seu_token_do_bot
TELEGRAM_CHAT_ID=seu_chat_id
GMAIL_EMAIL=seu_email@gmail.com
GMAIL_APP_PASSWORD=sua_senha_de_app
GMAIL_MAILBOX=INBOX
EMAIL_MAX_PER_RUN=100
EMAIL_BODY_MAX_CHARS=6000
```

Não envie esse arquivo para terceiros. Ele é texto simples protegido pelas permissões da sua conta Windows; não é um cofre criptografado. Variáveis de ambiente já definidas têm prioridade sobre `.env`. Opcionalmente, `AUTOMACOES_ENV_FILE` aponta para outro arquivo; tarefas agendadas precisam enxergar essa variável no seu ambiente.

## 3. Gmail: senha de app e IMAP

Esta implementação usa **IMAP com senha de app**, uma das alternativas solicitadas; não implementa OAuth2.

1. Entre na [Conta Google — Segurança](https://myaccount.google.com/security).
2. Ative a **Verificação em duas etapas**.
3. Abra [Senhas de app](https://myaccount.google.com/apppasswords).
4. Crie uma senha para um app chamado `Automacoes IA Windows`.
5. Coloque a senha de app de 16 caracteres em `GMAIL_APP_PASSWORD`. Espaços de apresentação são removidos pelo script. Não use a senha normal da conta.
6. Coloque seu endereço completo em `GMAIL_EMAIL`.

Para informar a senha sem exibi-la no terminal e testar a conexão antes de salvar:

```powershell
.\.venv\Scripts\python.exe configure_gmail.py
```

O utilitário solicita a senha com entrada oculta, autentica por IMAP/TLS e seleciona INBOX em modo somente leitura. Não busca mensagens. A senha é gravada apenas depois de confirmar a conexão.

O Google exige verificação em duas etapas para senhas de app. A opção pode estar indisponível em contas corporativas/escolares, contas com Proteção Avançada ou certas configurações de autenticação. Se sua conta não permitir senha de app, esta versão não autenticará: será necessário adaptar para OAuth2 com autorização inicial e renovação de token. Trocar a senha da conta pode revogar senhas de app. [Ajuda oficial do Google](https://support.google.com/accounts/answer/185833?hl=pt-BR).

Em contas pessoais, IMAP permanece habilitado; em Workspace, a política do administrador pode restringi-lo. Endpoint usado: `imap.gmail.com`, porta `993`, TLS com certificado verificado. [Configuração de clientes Gmail](https://support.google.com/mail/answer/7126229?hl=pt-BR).

Por padrão, a pasta é **INBOX**. E-mails arquivados e que pularam a caixa de entrada não são incluídos. Para incluir outra pasta, ajuste `GMAIL_MAILBOX` com o nome IMAP exato apresentado pela conta. O script não move, exclui nem marca mensagens como lidas.

## 4. IA local gratuita — Ollama

Esta implementação usa somente IA local, sem chave da OpenAI ou faturamento por chamada.

Usamos **qwen3:4b-instruct** no Ollama, localmente, sem chave nem cobrança por chamada. O modelo ocupa cerca de 2,5 GB, além do espaço do programa, e usa RAM e CPU do seu computador. Internet e energia continuam sendo recursos do seu equipamento. A qualidade e velocidade podem diferir do gpt-4o. [Modelo e licença](https://ollama.com/library/qwen3:4b-instruct).

O executável portátil fica em `%LOCALAPPDATA%\IAAutomacoes\ollama\ollama.exe` e o modelo em `%LOCALAPPDATA%\IAAutomacoes\ollama_models`. O servidor inicia sob demanda sem janela, escuta apenas em `127.0.0.1:11434` e é iniciado com `OLLAMA_NO_CLOUD=1`. Não há fallback para serviços pagos. As chamadas são serializadas, usam duas threads de CPU e desativam GPU para limitar o impacto no uso do computador. O modelo é descarregado ao final de cada resposta (`keep_alive: 0`), em vez de permanecer residente.

### Instalar o Ollama portátil e o modelo

Baixe o arquivo **ollama-windows-amd64.zip** das [releases oficiais do Ollama](https://github.com/ollama/ollama/releases), compatível com seu Windows. Extraia o conteúdo para a subpasta `ollama` do diretório de dados: deve existir `ollama\ollama.exe` com as bibliotecas distribuídas no ZIP. Não envie esses binários ao GitHub.

No PowerShell, após extrair:

```powershell
$dataDir = (.\.venv\Scripts\python.exe -c "from runtime_paths import DATA_DIR; print(DATA_DIR)").Trim()
$ollamaExe = Join-Path $dataDir 'ollama\ollama.exe'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_MODELS = Join-Path $dataDir 'ollama_models'
if (-not (Test-Path -LiteralPath $ollamaExe)) { throw 'Extraia o Ollama portátil primeiro.' }
# Inicia o servidor da instalação em segundo plano.
Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
# Quando o servidor estiver pronto, baixa o modelo uma única vez.
& $ollamaExe pull qwen3:4b-instruct
```

Se já houver outro servidor Ollama na porta 11434, encerre-o antes de iniciar esta instalação, para não baixar o modelo em outro diretório. Na operação normal, o projeto inicia seu servidor sob demanda. O download do modelo pode levar alguns minutos.

**Proteção dos jogos:** Steam, Riot Client, Valorant, League of Legends, Roblox e outros launchers impedem a execução, mesmo se estiverem em segundo plano. Jogos em bibliotecas Steam/Epic, pastas padrão de jogos, pastas adicionais de `GAME_EXTRA_DIRS` (separadas por `;`) e executáveis registrados pelo Windows como jogos também são detectados. Nenhum detector cobre todo jogo portátil ou futuro automaticamente.

Antes de executar, cada automação verifica esse bloqueio. Durante a execução, uma verificação a cada dois segundos detecta a abertura do jogo/launcher e interrompe somente subprocessos do projeto e o Ollama portátil instalado por ele. Não altera o jogo, Vanguard, Riot Client ou navegador pessoal. Não garante ausência de qualquer oscilação nos primeiros segundos da detecção. O próximo agendamento tenta novamente quando o jogo e os launchers estiverem fechados; mensagens pendentes confirmadas permanecem no banco. Se a busca diária de vagas for adiada, ela será retomada no próximo gatilho de vagas.

Para jogos ainda não detectados, pause manualmente todas as automações antes de abri-los:

```powershell
$dataDir = (.\.venv\Scripts\python.exe -c "from runtime_paths import DATA_DIR; print(DATA_DIR)").Trim()
New-Item -ItemType File -Path (Join-Path $dataDir 'PAUSED') -Force
```

Para permitir as próximas execuções após jogar:

```powershell
Remove-Item -LiteralPath (Join-Path $dataDir 'PAUSED') -ErrorAction SilentlyContinue
```

**Regra permanente do projeto:** não configurar assinatura, cartão, API paga ou serviço com período de teste. Se uma dependência externa passar a exigir pagamento, a automação falha e exige revisão; não migra automaticamente para um serviço pago. Não é possível prometer que serviços de terceiros continuarão gratuitos ou disponíveis para sempre. Energia e internet do próprio computador continuam sendo utilizadas.

A IA retorna JSON conforme o esquema enviado, validado também por `jsonschema`. Respostas incompletas ou inválidas não são encaminhadas. [API local](https://docs.ollama.com/api/chat), [modo exclusivamente local](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

Assunto, remetente e corpo dos e-mails são processados localmente. Anexos não são enviados. Links de rastreamento e sequências técnicas longas são compactados. O corpo é limitado a 6.000 caracteres úteis: informações após esse limite não participam da classificação. O classificador usa janela de 4.096 tokens, e as outras tarefas usam até 8.192, para reduzir RAM e tempo de processamento. O resumo dos alertas Alta será transmitido ao Telegram.

## 5. Telegram: bot, token e chat_id

1. No Telegram, abra o bot oficial [@BotFather](https://t.me/BotFather).
2. Envie `/newbot` e escolha nome e username.
3. Copie o token recebido para `TELEGRAM_BOT_TOKEN`.
4. No celular, abra **o bot que você criou** e envie `/start`.
5. Para validar e salvar o token sem exibi-lo e identificar um único chat privado:

```powershell
.\.venv\Scripts\python.exe configure_telegram.py --test
```

Se o token já estiver salvo e faltar apenas enviar `/start`, reutilize-o assim:

```powershell
.\.venv\Scripts\python.exe configure_telegram.py --existing-token --test
```

O utilitário não escolhe automaticamente entre vários chats privados. Com um único destino identificado, salva `chat_id` e envia uma mensagem de teste quando chamado com `--test`.

Alternativamente, para consultar os IDs sem configuração automática:
5. Na pasta do projeto, execute:

```powershell
.\.venv\Scripts\python.exe telegram_setup.py
```

O utilitário usa `requests.get(.../getUpdates)` e mostra `chat_id` de chats encontrados, sem mostrar o token. Escolha seu chat privado e coloque o número em `TELEGRAM_CHAT_ID`. Se nada aparecer, envie outra mensagem ao bot e repita. Use um bot dedicado: webhooks ou outro consumidor de `getUpdates` podem impedir essa descoberta.

Teste explicitamente o envio ao celular:

```powershell
.\.venv\Scripts\python.exe telegram_setup.py --test
```

O bot precisa ter permissão para conversar com o destino e não pode estar bloqueado. [Tutorial oficial do Telegram](https://core.telegram.org/bots/tutorial#obtain-your-bot-token), [Bot API / sendMessage](https://core.telegram.org/bots/api#sendmessage).

## 6. Execução e comportamento

Na configuração manual, execute uma vez cada script:

```powershell
.\.venv\Scripts\python.exe email_classifier.py
.\.venv\Scripts\python.exe job_finder.py
```

Essas chamadas retornam código `0` no sucesso e `1` na falha. O diagnóstico fica em arquivo, mesmo nas chamadas manuais:

```powershell
Get-Content (Join-Path $configDir 'email_classifier.log') -Tail 30
Get-Content (Join-Path $configDir 'job_finder.log') -Tail 30
```

### E-mails

- Busca `UNSEEN` com filtro de tempo do Gmail e confirma a janela móvel de 24h com `INTERNALDATE`, a data de chegada ao servidor. [Extensões IMAP Gmail](https://developers.google.com/workspace/gmail/imap/imap-extensions).
- Consulta somente a pasta configurada, com `readonly=True` e `BODY.PEEK[]`.
- Alta significa ação pessoal em até 24h com consequência concreta, incidente crítico ou risco imediato. Média e Baixa são classificadas e não geram Telegram. Altere os critérios no prompt de `email_classifier.py` se necessário.
- Cache persistente evita reclassificar a mesma mensagem em cada execução. O padrão é 100 mensagens por execução, ajustável em `EMAIL_MAX_PER_RUN`. Um valor menor, como 5, limita o trabalho contínuo por lote. Falhas são tentadas de novo enquanto a mensagem continuar elegível na janela.
- Alertas Alta já classificados ficam pendentes até o envio confirmado, mesmo que o e-mail seja lido depois ou deixe a janela de 24h.
- A classificação é probabilística; pode errar, especialmente se o e-mail depender de anexos ou contexto não incluído.

### Vagas — quatro sites em cada rodada

**Filtro de localidade obrigatório:** vaga de TI júnior **100% home office**, em qualquer localidade, **OU** localizada no município do Rio de Janeiro ou na Baixada Fluminense. Híbridas e presenciais são aceitas apenas nessa região. Não basta o anúncio dizer somente “RJ” ou “Estado do Rio de Janeiro”. Localidade/modalidade sem evidência é rejeitada; a IA não pode completar esses dados por suposição. Cidade e modalidade aparecem no boletim.

A região configurada inclui Rio capital, Belford Roxo, Duque de Caxias, Guapimirim, Itaguaí, Japeri, Magé, Mesquita, Nilópolis, Nova Iguaçu, Paracambi, Queimados, São João de Meriti e Seropédica. Niterói, São Gonçalo, Região Serrana e outras cidades do estado não entram nas vagas presenciais/híbridas. A lista está em `job_preferences.py`.

Modalidade remota deve constar em campo específico do site, em um campo identificado do anúncio ou no próprio título; menções genéricas a benefício de home office não bastam. Anúncios híbridos, com remoto parcial/ocasional ou exigência explícita de encontros presenciais fora da região são excluídos. Isso usa as informações disponíveis no anúncio, sem prometer condições que a empresa não declarou.

- Consulta **Nerdin, Gupy, RioVagas e Indeed**, sequencialmente, em todas as rodadas. Uma fonte bloqueada não interrompe as demais. A coleta continua pelos outros sites mesmo quando os primeiros já encontraram vagas.
- Pesquisa desenvolvimento, dados, IA, Tecnologia da Informação, suporte de TI, testes de software, redes e segurança da informação, sempre em nível júnior. Aplica o filtro remoto integral OU Rio capital/Baixada em todas as fontes. Vagas.com e Glassdoor não fazem parte das fontes ativas.
- Nerdin: página pública de estágio/júnior, com estágio e títulos fora do filtro excluídos. RioVagas e Indeed: até oito buscas por cargo. Gupy: quatro buscas públicas com termos de júnior e TI. Cada fonte tem um limite de candidatos para conter uso de memória e tempo de coleta.
- Valida os links e deduplica por URL e, quando a empresa é conhecida, por cargo, empresa, localidade e modalidade normalizados. Os formatos de link da mesma vaga Gupy são reconhecidos. Vagas com empresa desconhecida são deduplicadas somente por URL para não juntar empresas diferentes.
- Intercala os candidatos dos sites e seleciona no máximo 30 únicos para a IA, evitando que a primeira fonte ocupe o lote inteiro. Envia à IA apenas id, título, empresa e trechos limitados do texto. Os links originais permanecem fora da geração de texto.
- Envia **até 10 vagas únicas**. “Melhores” significa maior aderência de cargo e nível; não há avaliação de salário ou compatibilidade com seu currículo. Havendo menos de 10 compatíveis, não inventa vagas. Se nenhuma for encontrada em buscas bem-sucedidas, envia um aviso de lista vazia.
- Exige cargo compatível e nível júnior explicitamente no título antes de chamar a IA. Depois da IA, descarta pontuação abaixo de 60 e preserva o título extraído do site. Isso pode excluir vagas cujo nível aparece apenas na descrição. O nome do próprio portal não é tratado como empresa contratante.
- Links são recuperados do DOM e validados por domínio/caminho. A IA não gera URLs nem substitui títulos ou empresas por informações inventadas. Cada item do boletim informa o site de origem. Quando a mesma vaga aparece em mais de um portal, esses sites são associados ao mesmo item.
- Um boletim por dia e destino. A primeira execução do dia congela o resultado; novas execuções naquele dia só concluem partes pendentes. Vagas podem reaparecer no boletim do dia seguinte.
- O JSON limpo `vagas_ultimo_resultado.json`, no diretório efetivo de dados, inclui as vagas e o resultado de consulta de cada site.
- A coleta é exclusivamente headless. CAPTCHA, login obrigatório, bloqueio HTTP e mudança de layout são falhas; não se tenta contornar essas condições. O Indeed pode impedir a coleta automatizada. Por isso, **não é possível garantir disponibilidade contínua da raspagem**. Os seletores estão nas funções coletoras de `job_finder.py`; consulte também as condições de uso do site antes da operação.
- O boletim informa fontes consultadas e sites indisponíveis naquela rodada. Se todos os sites falharem, envia no máximo um aviso por dia, sem apresentar a falha como busca vazia. Pausa por jogos interrompe a rodada inteira, sem acionar outros sites nem avisos de falha de coleta.

### Confiabilidade e estado

Logs com rotação, banco SQLite e travas de execução ficam em `%LOCALAPPDATA%\IAAutomacoes`. Não salvamos corpos de e-mails nem chaves em logs; o banco contém resumos dos e-mails e boletins. Não exclua o banco para uma “limpeza” sem considerar que isso permite novos alertas de mensagens já processadas.

O envio usa `requests.post` com timeouts e tratamento de limites. O estado de entrega só é gravado após confirmação do Telegram. Há uma pequena janela inevitável de duplicação se a API aceitar a mensagem, mas a conexão cair antes da resposta, ou se o processo for interrompido entre envio e gravação. A API `sendMessage` não oferece uma chave de idempotência para eliminar essa ambiguidade. Boletins extensos são divididos e cada parte possui registro de entrega separado.

## 7. Agendador de Tarefas: sem janelas e sem roubar foco

Os scripts ajustam o caminho do Chromium para `.venv\browsers`; não dependem de uma variável temporária do PowerShell. Deixe o projeto em uma pasta fixa e mantenha os arquivos disponíveis localmente se estiver no OneDrive.

Após instalar dependências, configurar credenciais e testar, rode uma vez:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_tasks.ps1
```

`Bypass` se aplica somente a essa execução do instalador. Em máquinas gerenciadas, políticas da organização podem impedir o registro. Se houver “Acesso negado”, execute o registro em PowerShell elevado sob a mesma conta.

O instalador registra:

| Tarefa  | Frequência padrão |
|---|---|
| IA - Emails urgentes | A cada 15 minutos, início em cerca de um minuto |
| IA - Vagas Junior | Todos os dias às 09:00, hora local do Windows |

Personalização:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_tasks.ps1 -EmailIntervalMinutes 10 -JobsAt '08:30'
```

Se já registrou as tarefas, altere-as no Agendador ou remova as duas antes de registrar novamente. O instalador não sobrescreve tarefas existentes silenciosamente.

As ações agendadas executam diretamente **`.venv\Scripts\pythonw.exe`**, sem `cmd.exe`, sem terminal e sem PowerShell intermediário. O Chromium permanece `headless=True`, sem reutilizar seu navegador pessoal. Assim não há janela para receber foco ou tomar teclado/mouse.

O padrão exige **usuário logado**, inclusive com a tela bloqueada. Para executar também após logoff, abra `taskschd.msc`, Propriedades de cada tarefa → Geral → **Executar estando o usuário conectado ou não** e forneça a senha da conta Windows quando solicitado. Use a mesma conta das credenciais. Não escolha a opção “Não armazenar senha… acesso somente a recursos locais”, pois as tarefas precisam da rede. Essa configuração adicional é feita pelo Windows, não pelo script.

O computador precisa estar ligado, acordado e com acesso à internet. O script não liga o PC nem desperta de suspensão. Configure o Windows conforme sua rotina. Se ficar mais de 24h sem executar, e-mails mais antigos não serão recuperados por esta política de janela móvel.

As tarefas impedem instâncias simultâneas, têm limite de duas horas e até três novas tentativas com intervalo de cinco minutos na falha. O script também usa trava de arquivo para execuções manuais concorrentes. [Agendador: principal de execução](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal), [gatilhos e repetição](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger).

Verificar ou executar sem abrir interface:

```powershell
Get-ScheduledTask -TaskName 'IA - Emails urgentes','IA - Vagas Junior'
Get-ScheduledTaskInfo -TaskName 'IA - Emails urgentes'
Start-ScheduledTask -TaskName 'IA - Vagas Junior'
```

Para desativar:

```powershell
Disable-ScheduledTask -TaskName 'IA - Emails urgentes'
Disable-ScheduledTask -TaskName 'IA - Vagas Junior'
```

## 8. Verificação automatizada

```powershell
.\.venv\Scripts\python.exe -m unittest test_automations -v
```

Os testes não usam credenciais nem enviam mensagens. Verificam MIME/HTML e datas, filtros de entrega Alta/Baixa, retentativa de alerta pendente após leitura, deduplicação, URLs, IDs inválidos da IA, divisão de mensagens por tamanho e extração headless em uma página local simulada do Indeed.

Os testes locais não certificam o layout ou a disponibilidade do Indeed em produção, nem autenticam Gmail/Telegram. A validação final das contas depende de preencher suas credenciais e executar os testes manuais acima.

O teste sintético `python verify_local_ai.py` respeita o bloqueio de jogos, não acessa Gmail/Telegram e mede a memória aproximada dos processos locais da IA. Requer o Ollama e o modelo instalados. Tempo de resposta e memória variam com o equipamento, tamanho do texto e contexto. O modelo é descarregado após cada resposta.
