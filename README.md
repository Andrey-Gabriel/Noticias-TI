# Portal de notícias de TI

Portal estilo G1/CNN com notícias de tecnologia e programação do Brasil e do mundo.
Tudo que estiver em outro idioma é traduzido para português do Brasil. O mesmo script
também pode mandar um resumo por e-mail.

- Funciona em celular, tablet e computador (layout responsivo, modo claro/escuro)
- Destaques, categorias (Programação, Tecnologia, Segurança, IA, Startups), busca e filtro por fonte
- Pode ser instalado na tela inicial do celular (Chrome: menu → "Adicionar à tela inicial")
- O site atualiza 4 vezes ao dia (02h, 08h, 14h e 20h, horário de Brasília) via GitHub Actions
- O e-mail é um resumo diário, enviado às 08h com tudo que saiu desde o último envio

## 1. Ver o portal no seu computador

```bash
pip install -r requirements.txt
python noticias_ti.py     # busca as notícias e gera docs/index.html
```

Depois é só abrir `docs/index.html` no navegador. Para ver só o que seria coletado, sem gravar
nada: `python noticias_ti.py --teste`.

## 2. Publicar na internet (GitHub Pages)

1. Suba esta pasta para um repositório no GitHub.
2. Em *Settings → Pages*, escolha *Deploy from a branch*, branch `main`, pasta `/docs`.
3. Em *Actions*, rode o workflow uma vez (*Run workflow*; isso também envia o e-mail, se configurado). Em seguida o site fica em
   `https://SEU-USUARIO.github.io/NOME-DO-REPOSITORIO/`.
4. (Opcional) Em *Settings → Secrets and variables → Actions → Variables*, crie `SITE_URL`
   com esse endereço; o e-mail passa a trazer o link "Abrir o portal completo".

**Atenção:** no plano gratuito do GitHub, o Pages só funciona com repositório **público**.
Não tem problema para os segredos (senha de e-mail fica em *Secrets*, que nunca aparecem), mas o
código e as notícias coletadas ficam visíveis. Se preferir repositório privado, Cloudflare Pages
ou Netlify publicam a pasta `docs/` de repositórios privados de graça.

## 3. Tradução com DeepL (recomendado)

O DeepL traduz melhor que o Google e é estável. Sem chave, o script usa o Google Tradutor não
oficial, que bloqueia por excesso de requisições.

1. Crie uma conta em https://www.deepl.com/pro-api e escolha **DeepL API Free** (500 mil
   caracteres por mês grátis; pede um cartão só para verificar a conta e não cobra no plano Free).
2. Copie a chave em *Conta → Chaves de API*. A chave do plano gratuito termina em `:fx`.
3. No GitHub: *Settings → Secrets and variables → Actions → New repository secret*, nome
   `DEEPL_API_KEY`, valor = a chave.
4. No seu computador (fish): `set -x DEEPL_API_KEY "sua-chave:fx"` (vale só para esse terminal).
   No bash/zsh: `export DEEPL_API_KEY="sua-chave:fx"`.

Nunca coloque a chave dentro de arquivos do projeto nem em prints/conversas.

**Cota:** a cada execução o script mostra quanto da cota do mês já foi usada. Ele envia só o título
e os primeiros 200 caracteres do resumo, e ignora o resumo inútil do Hacker News. Se a cota acabar
(ou a chave falhar), ele tenta o Google Tradutor naquela execução e, se este também falhar, deixa as
notícias em outro idioma para a próxima execução. Para gastar menos, reduza `MAX_POR_FEED` ou
remova fontes em inglês de `FEEDS`.

## 4. E-mail (opcional)

1. Ative a verificação em duas etapas na conta Google.
2. Crie uma **senha de app** em https://myaccount.google.com/apppasswords.
3. No GitHub, crie os *Secrets*: `EMAIL_REMETENTE`, `EMAIL_SENHA_APP` e (opcional) `EMAIL_DESTINO`
   (vários e-mails separados por vírgula).

Sem esses segredos, o script só atualiza o site.

**Como o e-mail funciona:** a cada execução (4 por dia) as notícias novas entram numa fila
(`fila_email.json`). Só a execução das 08h (ou o botão *Run workflow*, ou `--email` no seu
computador) envia o e-mail com tudo que está na fila e a esvazia. Se o envio falhar, a fila é
mantida e as notícias vão no próximo e-mail. A fila só começa a acumular depois que os segredos
do e-mail forem cadastrados.

Para testar o e-mail no seu computador:

```bash
# Linux/macOS
export EMAIL_REMETENTE="seu@gmail.com"
export EMAIL_SENHA_APP="xxxxxxxxxxxxxxxx"
python noticias_ti.py --email

# Windows (PowerShell)
$env:EMAIL_REMETENTE="seu@gmail.com"
$env:EMAIL_SENHA_APP="xxxxxxxxxxxxxxxx"
python noticias_ti.py --email
```

## Personalização

- `noticias_ti.py`, topo do arquivo: `FEEDS` (fontes e categorias), `MAX_POR_FEED`,
  `MAX_IDADE_HORAS`, `DIAS_NO_SITE`.
- `template.html`: visual do portal (cores das categorias no objeto `CORES`).
- `.github/workflows/noticias.yml`: horários (`cron`, em UTC). O horário do e-mail é o da primeira linha; se mudar, atualize também o texto `'0 11 * * *'` na linha `ENVIAR_EMAIL`.

## Observações

- Tradução automática (DeepL com chave; sem chave, Google Tradutor não oficial). Pode ter
  imprecisões; todo card leva ao link original.
- Se uma fonte sair do ar ou mudar o feed, o script pula e segue com as demais.
- O portal mostra título, resumo curto e imagem de cada notícia, sempre com link para a fonte.
- `vistas.json` evita reprocessar notícias; `docs/noticias.json` guarda os últimos 7 dias;
  `fila_email.json` guarda o que ainda vai no próximo e-mail.
