# 🏠 Alerta de casas — Covilhã

Script que pesquisa **apartamentos e moradias à venda** no **Imovirtual** e no
**Casa Sapo**, filtra pelos teus critérios e envia um **e-mail de alerta** com os
anúncios novos (cada anúncio só é enviado uma vez).

## Critérios (definidos em `config.json`)

| Critério | Valor |
|---|---|
| Preço | 130.000 € – 210.000 € |
| Tipologia | T3 ou superior |
| Casas de banho | pelo menos 2 |
| Idade da construção | máximo 20 anos (obras novas contam como recentes) |
| Elevador | obrigatório para apartamentos |
| Zonas | Covilhã e Canhoso, Tortosendo, Boidobra, Refúgio |

Nota: nem todos os anúncios indicam o nº de casas de banho, o ano ou o elevador.
Os que **violam** um critério são excluídos; os que **não indicam** o dado são
incluídos com um aviso ⚠ no e-mail (para não perderes boas oportunidades).
Se preferires excluí-los também, muda `"incluir_dados_em_falta"` para `false`.

## 1. Configurar o Gmail (obrigatório, só uma vez)

O script envia o e-mail através da tua própria conta Gmail. Por segurança, o
Google exige uma **App Password** (não uses a tua password normal):

1. Vai a <https://myaccount.google.com/apppasswords>
   (é preciso ter a verificação em 2 passos ativa na conta)
2. Cria uma nova app password com o nome `alerta casas`
3. Copia o código de 16 letras gerado
4. Abre o `config.json` e substitui `COLOCA_AQUI_A_APP_PASSWORD` por esse código

Testa com:

```
python alerta_casas.py --test-email
```

Deves receber um e-mail de teste em segundos.

## 2. Usar

```
python alerta_casas.py            # pesquisa e envia e-mail com os anúncios novos
python alerta_casas.py --dry-run  # só mostra os resultados, não envia nada
```

Na **primeira execução** recebes um e-mail com todos os imóveis que cumprem os
critérios neste momento. Nas execuções seguintes só recebes os **novos**.

## 3. Executar automaticamente todos os dias

Há duas formas: localmente no teu PC (só corre se o PC estiver ligado) ou na
cloud via GitHub Actions (corre sempre, PC ligado ou não). Recomenda-se a opção
da cloud.

### Opção A — tarefa agendada do Windows (PC tem de estar ligado)

```powershell
$py = (Get-Command python).Source
$script = "C:\Users\tiago\OneDrive\Ambiente de Trabalho\script casas\alerta_casas.py"
$action = New-ScheduledTaskAction -Execute $py -Argument ('"' + $script + '"')
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "AlertaCasasCovilha" -Action $action -Trigger $trigger -Settings $settings
```

`-StartWhenAvailable` faz a tarefa correr assim que ligares o PC, caso tenha
perdido a hora das 09:00. Para remover: `Unregister-ScheduledTask -TaskName "AlertaCasasCovilha"`

### Opção B — GitHub Actions (corre na cloud, mesmo com o PC desligado)

1. Cria um repositório **privado** no GitHub (ex.: `alerta-casas-covilha`)
2. Faz push desta pasta para esse repositório (o `config.json` real com a
   password **não** vai — está no `.gitignore`; só vai o `config.example.json`
   sem a password)
3. No repositório, vai a **Settings → Secrets and variables → Actions** e cria
   3 "New repository secret":
   - `GMAIL_REMETENTE` = `tiagofonseca200319@gmail.com`
   - `GMAIL_APP_PASSWORD` = a app password de 16 letras (a mesma da secção 1)
   - `GMAIL_DESTINATARIO` = `tiagofonseca200319@gmail.com`
4. Pronto. O workflow em `.github/workflows/alerta.yml` corre todos os dias às
   09:00 (hora de Portugal) automaticamente. Também podes correr manualmente em
   **Actions → Alerta de casas Covilhã → Run workflow**.

O `estado.json` (anúncios já alertados) é atualizado e guardado de volta no
repositório a cada execução, para nunca repetires um alerta.

## Ficheiros

- `alerta_casas.py` — o script
- `config.json` — critérios e credenciais de e-mail (local, nunca vai para o Git)
- `config.example.json` — igual, mas sem a password real; é o que vai para o Git
- `estado.json` — criado automaticamente; memoriza os anúncios já alertados
  (apaga-o se quiseres receber tudo de novo)
- `.github/workflows/alerta.yml` — agendamento na cloud (GitHub Actions)

## Notas

- Os portais bloqueiam pedidos muito rápidos; o script espera automaticamente
  entre pedidos (uma execução completa demora 2–5 minutos, é normal).
- O Idealista não está incluído porque bloqueia programas automáticos.
- Se um portal mudar o site, o script avisa ("estrutura inesperada") — nesse
  caso é preciso atualizar o código.
