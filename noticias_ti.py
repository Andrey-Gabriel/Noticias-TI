#!/usr/bin/env python3
"""
Portal de notícias de TI e Programação (site + e-mail).

- Busca notícias em feeds RSS (PT e EN)
- Traduz para português do Brasil o que estiver em outro idioma
  (DeepL se DEEPL_API_KEY estiver definida; senão, Google Tradutor não oficial)
- Gera um site responsivo em docs/ (publicável no GitHub Pages)
- Opcionalmente envia um resumo por e-mail
- Evita repetir notícias já processadas (vistas.json)

Uso:
    python noticias_ti.py           # busca e atualiza o site (e guarda na fila do e-mail)
    python noticias_ti.py --email   # idem, e envia agora o e-mail com tudo que está na fila
    python noticias_ti.py --teste   # só mostra no terminal (não grava nada)

O e-mail é um resumo diário: cada execução acumula as notícias novas numa fila
(fila_email.json) e o envio acontece só quando ENVIAR_EMAIL=true ou --email.
"""

import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests
from deep_translator import GoogleTranslator
from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0  # deixa a detecção de idioma determinística

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# (nome exibido, URL do feed, categoria). Adicione ou remova à vontade.
# Categorias usadas no site: Programação, Tecnologia, Segurança, IA, Startups
FEEDS = [
    # Português
    ("TabNews", "https://www.tabnews.com.br/recentes/rss", "Programação"),
    ("Tecnoblog", "https://tecnoblog.net/feed/", "Tecnologia"),
    ("Canaltech", "https://canaltech.com.br/rss/", "Tecnologia"),
    ("Olhar Digital", "https://olhardigital.com.br/feed/", "Tecnologia"),
    ("Tecmundo", "https://rss.tecmundo.com.br/feed", "Tecnologia"),
    # Inglês
    ("Hacker News", "https://hnrss.org/newest?points=50", "Programação"),
    ("InfoQ", "https://feed.infoq.com/", "Programação"),
    ("DEV Community", "https://dev.to/feed", "Programação"),
    ("GitHub Blog", "https://github.blog/feed/", "Programação"),
    ("Stack Overflow Blog", "https://stackoverflow.blog/feed/", "Programação"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab", "Tecnologia"),
    ("The Verge", "https://www.theverge.com/rss/tech/index.xml", "Tecnologia"),
    ("The Register", "https://www.theregister.com/headlines.atom", "Tecnologia"),
    ("TechCrunch", "https://techcrunch.com/feed/", "Startups"),
    ("Wired Segurança", "https://www.wired.com/feed/category/security/latest/rss", "Segurança"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/", "Segurança"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "Segurança"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", "IA"),
]

MAX_POR_FEED = 12                 # no máximo N notícias por fonte em cada execução
MAX_IDADE_HORAS = 30              # ignora notícias mais antigas que isso
JANELA_PRIMEIRA_EXECUCAO = 72     # na 1ª vez (site vazio) olha mais para trás
TAMANHO_RESUMO = 280              # caracteres do resumo
GUARDAR_VISTAS_DIAS = 30          # por quanto tempo lembrar o que já foi processado
DIAS_NO_SITE = 7                  # por quantos dias a notícia fica no site
MAX_NO_SITE = 600                 # teto de notícias no site

RAIZ = Path(__file__).parent
ARQUIVO_VISTAS = RAIZ / "vistas.json"
PASTA_SITE = RAIZ / "docs"
TEMPLATE_SITE = RAIZ / "template.html"
ARQUIVO_FILA = RAIZ / "fila_email.json"   # notícias aguardando o próximo e-mail diário
MAX_FILA = 300                            # teto da fila (evita crescer sem limite)
FUSO_BR = timezone(timedelta(hours=-3))

# Notícias cujo título fala de IA vão para a categoria "IA", seja qual for a fonte.
RE_IA_SENSIVEL = re.compile(r"\b(IA|AI|LLMs?|GPT(-\w+)?)\b")
RE_RESUMO_INUTIL = re.compile(r"^(Article URL|Comments URL)", re.IGNORECASE)
RE_IA_LIVRE = re.compile(
    r"intelig[êe]ncia artificial|machine learning|aprendizado de m[áa]quina|openai|chatgpt"
    r"|gemini|anthropic|\bclaude\b|copilot|deepseek|\bllama\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def limpar_html(texto: str) -> str:
    """Remove tags HTML e espaços sobrando."""
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def cortar(texto: str, limite: int) -> str:
    if len(texto) <= limite:
        return texto
    return texto[:limite].rsplit(" ", 1)[0] + "…"


def id_noticia(entrada) -> str:
    base = entrada.get("id") or entrada.get("link") or entrada.get("title", "")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def data_publicacao(entrada):
    for campo in ("published_parsed", "updated_parsed"):
        t = entrada.get(campo)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def extrair_imagem(entrada) -> str:
    """Tenta achar uma imagem de capa no item do feed. Retorna '' se não achar."""
    candidatas = []
    for campo in ("media_thumbnail", "media_content"):
        for m in entrada.get(campo, []) or []:
            candidatas.append(m.get("url", ""))
    for enc in entrada.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image"):
            candidatas.append(enc.get("href", ""))
    blocos = [c.get("value", "") for c in entrada.get("content", []) or []]
    blocos.append(entrada.get("summary", ""))
    for bloco in blocos:
        m = re.search(r"<img[^>]+src=[\"']([^\"']+)", bloco or "", re.IGNORECASE)
        if m:
            candidatas.append(html.unescape(m.group(1)))
    for url in candidatas:
        if url.startswith(("http://", "https://")):
            return url
    return ""


def categoria_da_noticia(titulo: str, padrao: str) -> str:
    if RE_IA_SENSIVEL.search(titulo) or RE_IA_LIVRE.search(titulo):
        return "IA"
    return padrao


def carregar_vistas() -> dict:
    if ARQUIVO_VISTAS.exists():
        try:
            return json.loads(ARQUIVO_VISTAS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def salvar_vistas(vistas: dict) -> None:
    limite = (datetime.now(timezone.utc) - timedelta(days=GUARDAR_VISTAS_DIAS)).timestamp()
    vistas = {k: v for k, v in vistas.items() if v >= limite}
    ARQUIVO_VISTAS.write_text(json.dumps(vistas), encoding="utf-8")


def carregar_fila() -> list:
    if ARQUIVO_FILA.exists():
        try:
            return json.loads(ARQUIVO_FILA.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def salvar_fila(fila: list) -> None:
    ARQUIVO_FILA.write_text(json.dumps(fila[-MAX_FILA:], ensure_ascii=False), encoding="utf-8")


def agrupar_por_fonte(noticias: list) -> dict:
    grupos = {}
    for n in noticias:
        grupos.setdefault(n["fonte"], []).append(n)
    return grupos


# ---------------------------------------------------------------------------
# Tradução
# ---------------------------------------------------------------------------


def eh_portugues(texto: str) -> bool:
    try:
        return detect(texto) == "pt"
    except LangDetectException:
        return True  # na dúvida, não traduz


INTERVALO_TRADUCAO = {"deepl": 0.2, "google": 0.5}  # segundos entre chamadas, por serviço
ESPERAS_RETRY = (3, 10)         # esperas (s) entre tentativas quando uma chamada falha
MAX_FALHAS_SEGUIDAS = 3         # depois disso, o serviço é abandonado nesta execução
RESUMO_PARA_TRADUZIR = 200      # só estes caracteres do resumo são enviados (poupa a cota do DeepL)
_traducao = {
    "falhas": {"deepl": 0, "google": 0},
    "ultima": {"deepl": 0.0, "google": 0.0},
}


class ErroDefinitivo(Exception):
    """Erro que não adianta repetir (chave inválida, cota mensal esgotada)."""


def chave_deepl() -> str:
    return os.environ.get("DEEPL_API_KEY", "").strip()


def _url_deepl(caminho: str) -> str:
    # Chaves do plano gratuito terminam em ":fx" e usam outro endereço.
    host = "api-free.deepl.com" if chave_deepl().endswith(":fx") else "api.deepl.com"
    return f"https://{host}/v2/{caminho}"


def _cabecalho_deepl() -> dict:
    return {"Authorization": f"DeepL-Auth-Key {chave_deepl()}"}


def traduzir_com_deepl(textos: list) -> list:
    resp = requests.post(
        _url_deepl("translate"),
        headers=_cabecalho_deepl(),
        json={"text": textos, "target_lang": "PT-BR"},
        timeout=30,
    )
    if resp.status_code == 456:
        raise ErroDefinitivo("cota mensal do DeepL esgotada")
    if resp.status_code == 403:
        raise ErroDefinitivo("chave do DeepL inválida (confira DEEPL_API_KEY)")
    resp.raise_for_status()
    saidas = [t["text"] for t in resp.json()["translations"]]
    if len(saidas) != len(textos):
        raise RuntimeError("resposta do DeepL com número inesperado de textos")
    return saidas


def traduzir_com_google(textos: list) -> list:
    saidas = []
    for i, texto in enumerate(textos):
        if i:
            time.sleep(INTERVALO_TRADUCAO["google"])
        saidas.append(GoogleTranslator(source="auto", target="pt").translate(texto) or texto)
    return saidas


def mostrar_uso_deepl() -> None:
    """Mostra quanto da cota mensal do DeepL já foi usada."""
    if not chave_deepl():
        return
    try:
        resp = requests.get(_url_deepl("usage"), headers=_cabecalho_deepl(), timeout=15)
        resp.raise_for_status()
        dados = resp.json()
        usados, limite = dados["character_count"], dados["character_limit"]
        texto = f"{usados:,} de {limite:,}".replace(",", ".")
        print(f"DeepL: {texto} caracteres usados neste mês ({usados / limite:.0%})")
    except Exception as erro:
        print(f"  aviso: não consegui consultar o uso do DeepL: {str(erro)[:80]}", file=sys.stderr)


def _traduzir_lista(textos: list):
    """Traduz uma lista de textos. Usa o DeepL (se houver chave) e, na falta dele, o Google.

    Devolve a lista traduzida, ou None se nenhum serviço estiver disponível."""
    provedores = (["deepl"] if chave_deepl() else []) + ["google"]
    funcoes = {"deepl": traduzir_com_deepl, "google": traduzir_com_google}

    for prov in provedores:
        if _traducao["falhas"][prov] >= MAX_FALHAS_SEGUIDAS:
            continue
        for espera in (0, *ESPERAS_RETRY):
            if espera:
                time.sleep(espera)
            folga = INTERVALO_TRADUCAO[prov] - (time.monotonic() - _traducao["ultima"][prov])
            if folga > 0:
                time.sleep(folga)
            _traducao["ultima"][prov] = time.monotonic()
            try:
                saidas = funcoes[prov](textos)
                _traducao["falhas"][prov] = 0
                return saidas
            except ErroDefinitivo as erro:
                print(f"  aviso ({prov}): {erro}; usando outro serviço nesta execução", file=sys.stderr)
                _traducao["falhas"][prov] = MAX_FALHAS_SEGUIDAS
                break
            except Exception as erro:  # rede, limite de uso etc.
                print(f"  aviso: falha ao traduzir ({prov}): {str(erro)[:90]}", file=sys.stderr)
        else:  # esgotou as tentativas sem sucesso
            _traducao["falhas"][prov] += 1
    return None


def traduzir_par(titulo: str, resumo: str):
    """Traduz título e resumo juntos. Devolve (titulo_pt, resumo_pt) ou None se indisponível."""
    entradas = [t for t in (titulo, resumo) if t]
    saidas = _traduzir_lista(entradas)
    if saidas is None:
        return None
    it = iter(saidas)
    return (next(it) if titulo else titulo, next(it) if resumo else resumo)


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------


def coletar(vistas: dict, horas: int) -> dict:
    """Retorna {nome_da_fonte: [noticias]} apenas com notícias novas."""
    corte = datetime.now(timezone.utc) - timedelta(hours=horas)
    resultado = {}

    for nome, url, categoria in FEEDS:
        print(f"Lendo {nome}...")
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0 (noticias-ti-bot)")
        except Exception as erro:
            print(f"  erro ao ler {nome}: {erro}", file=sys.stderr)
            continue

        if feed.bozo and not feed.entries:
            print(f"  feed indisponível ou inválido: {nome}", file=sys.stderr)
            continue

        noticias = []
        pulados = 0
        for e in feed.entries:
            if len(noticias) >= MAX_POR_FEED:
                break

            nid = id_noticia(e)
            if nid in vistas:
                continue

            pub = data_publicacao(e)
            if pub and pub < corte:
                continue

            titulo = limpar_html(e.get("title", "")) or "(sem título)"
            resumo = cortar(limpar_html(e.get("summary", "")), TAMANHO_RESUMO)
            if RE_RESUMO_INUTIL.match(resumo):
                resumo = ""  # evita gastar tradução (e espaço no card) com texto sem informação

            traduzida = False
            if not eh_portugues(f"{titulo}. {resumo}"):
                par = traduzir_par(titulo, cortar(resumo, RESUMO_PARA_TRADUZIR))
                if par is None:
                    pulados += 1  # não entra no site nem em vistas.json: tenta de novo depois
                    continue
                titulo, resumo = par
                traduzida = True

            noticias.append(
                {
                    "id": nid,
                    "titulo": titulo,
                    "resumo": resumo,
                    "link": e.get("link", ""),
                    "data": pub,
                    "traduzida": traduzida,
                    "imagem": extrair_imagem(e),
                    "fonte": nome,
                    "categoria": categoria_da_noticia(titulo, categoria),
                }
            )

        if pulados:
            print(f"  {pulados} notícia(s) de {nome} ficam para a próxima execução (tradução indisponível)")
        if noticias:
            resultado[nome] = noticias

    return resultado


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------


def carregar_arquivo_site() -> list:
    caminho = PASTA_SITE / "noticias.json"
    if caminho.exists():
        try:
            return json.loads(caminho.read_text(encoding="utf-8")).get("noticias", [])
        except (json.JSONDecodeError, AttributeError):
            return []
    return []


def serializar(n: dict, agora: datetime) -> dict:
    return {
        "id": n["id"],
        "titulo": n["titulo"],
        "resumo": n["resumo"],
        "link": n["link"],
        "data": (n["data"] or agora).isoformat(),
        "traduzida": n["traduzida"],
        "imagem": n["imagem"],
        "fonte": n["fonte"],
        "categoria": n["categoria"],
    }


def atualizar_site(por_fonte: dict) -> int:
    """Junta as notícias novas ao histórico e regenera docs/index.html."""
    agora = datetime.now(timezone.utc)
    por_id = {n["id"]: n for n in carregar_arquivo_site()}
    for noticias in por_fonte.values():  # já serializadas
        for n in noticias:
            por_id[n["id"]] = n

    corte = (agora - timedelta(days=DIAS_NO_SITE)).isoformat()
    lista = [n for n in por_id.values() if n["data"] >= corte]
    lista.sort(key=lambda n: n["data"], reverse=True)
    lista = lista[:MAX_NO_SITE]

    PASTA_SITE.mkdir(exist_ok=True)
    (PASTA_SITE / "noticias.json").write_text(
        json.dumps({"atualizado": agora.isoformat(), "noticias": lista}, ensure_ascii=False),
        encoding="utf-8",
    )

    # Os dados vão embutidos no HTML para o site funcionar até aberto direto do arquivo.
    embutido = json.dumps(lista, ensure_ascii=False).replace("</", "<\\/")
    pagina = (
        TEMPLATE_SITE.read_text(encoding="utf-8")
        .replace("__NOTICIAS_JSON__", embutido)
        .replace("__ATUALIZADO__", agora.isoformat())
    )
    (PASTA_SITE / "index.html").write_text(pagina, encoding="utf-8")
    return len(lista)


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------


def montar_html(por_fonte: dict) -> str:
    hoje = datetime.now(FUSO_BR).strftime("%d/%m/%Y")
    total = sum(len(v) for v in por_fonte.values())
    site = os.environ.get("SITE_URL", "").strip()
    link_site = (
        f'<p><a href="{html.escape(site)}" style="color:#0b57d0;font-weight:600;">'
        f"Abrir o portal completo →</a></p>"
        if site.startswith(("http://", "https://"))
        else ""
    )

    blocos = []
    for fonte, noticias in por_fonte.items():
        itens = []
        for n in noticias:
            hora = datetime.fromisoformat(n["data"]).astimezone(FUSO_BR).strftime("%d/%m %H:%M")
            etiqueta = " · 🌐 traduzida" if n["traduzida"] else ""
            resumo = (
                f'<p style="margin:4px 0 0;color:#444;font-size:14px;">{html.escape(n["resumo"])}</p>'
                if n["resumo"]
                else ""
            )
            itens.append(
                f"""
                <li style="margin-bottom:14px;">
                  <a href="{html.escape(n['link'])}" style="color:#0b57d0;font-size:16px;
                     font-weight:600;text-decoration:none;">{html.escape(n['titulo'])}</a>
                  <div style="color:#888;font-size:12px;">{hora}{etiqueta}</div>
                  {resumo}
                </li>"""
            )
        blocos.append(
            f"""
            <h2 style="font-size:18px;border-bottom:2px solid #eee;padding-bottom:4px;
                margin-top:28px;">{html.escape(fonte)}</h2>
            <ul style="padding-left:18px;">{''.join(itens)}</ul>"""
        )

    return f"""<!doctype html>
<html lang="pt-BR"><body style="font-family:Arial,Helvetica,sans-serif;max-width:680px;
margin:auto;padding:16px;color:#111;">
  <h1 style="font-size:22px;">📰 Notícias de TI e Programação — {hoje}</h1>
  <p style="color:#666;">{total} notícias novas de {len(por_fonte)} fontes.</p>
  {link_site}
  {''.join(blocos)}
  <p style="color:#999;font-size:12px;margin-top:32px;">
    Tradução automática; confira o link original em caso de dúvida.</p>
</body></html>"""


def montar_texto(por_fonte: dict) -> str:
    linhas = []
    for fonte, noticias in por_fonte.items():
        linhas.append(f"\n== {fonte} ==")
        for n in noticias:
            linhas.append(f"- {n['titulo']}\n  {n['link']}")
    return "\n".join(linhas)


def email_configurado() -> bool:
    return bool(os.environ.get("EMAIL_REMETENTE") and os.environ.get("EMAIL_SENHA_APP"))


def enviar_email(assunto: str, corpo_html: str, corpo_texto: str) -> None:
    remetente = os.environ["EMAIL_REMETENTE"]
    senha = os.environ["EMAIL_SENHA_APP"]
    destino = os.environ.get("EMAIL_DESTINO") or remetente
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    porta = int(os.environ.get("SMTP_PORTA", "465"))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = destino
    msg.attach(MIMEText(corpo_texto, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    with smtplib.SMTP_SSL(host, porta, context=ssl.create_default_context()) as servidor:
        servidor.login(remetente, senha)
        servidor.sendmail(remetente, [d.strip() for d in destino.split(",")], msg.as_string())


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teste", action="store_true", help="só mostra no terminal; não grava nada")
    parser.add_argument(
        "--email",
        action="store_true",
        help="envia agora o e-mail com tudo que está na fila (também ativado por ENVIAR_EMAIL=true)",
    )
    args = parser.parse_args()

    vistas = carregar_vistas()
    primeira_vez = not carregar_arquivo_site()
    horas = JANELA_PRIMEIRA_EXECUCAO if primeira_vez else MAX_IDADE_HORAS
    por_fonte = coletar(vistas, horas)
    mostrar_uso_deepl()

    agora = datetime.now(timezone.utc)
    novas = {f: [serializar(n, agora) for n in ns] for f, ns in por_fonte.items()}
    total_novas = sum(len(v) for v in novas.values())

    if args.teste:
        print(montar_texto(novas) if novas else "Nenhuma notícia nova.")
        return 0

    # 1) Site: sempre atualiza.
    if novas or primeira_vez:
        no_site = atualizar_site(novas)
        print(f"Site atualizado: {no_site} notícias em docs/index.html ({total_novas} novas)")
    else:
        print("Nenhuma notícia nova.")

    # 2) Fila do e-mail: acumula as novas (só se o e-mail estiver configurado).
    fila = carregar_fila()
    if email_configurado():
        ids_na_fila = {n["id"] for n in fila}
        fila += [n for ns in novas.values() for n in ns if n["id"] not in ids_na_fila]

    # 3) E-mail: só nas execuções marcadas (diária, 8h) ou com --email.
    enviar = args.email or os.environ.get("ENVIAR_EMAIL", "").lower() == "true"
    email_ok = True
    if enviar:
        if not email_configurado():
            print("E-mail não configurado (EMAIL_REMETENTE / EMAIL_SENHA_APP); pulando.")
        elif not fila:
            print("Fila de e-mail vazia; nada a enviar.")
        else:
            grupos = agrupar_por_fonte(fila)
            assunto = f"📰 {len(fila)} notícias de TI — {datetime.now(FUSO_BR).strftime('%d/%m/%Y')}"
            try:
                enviar_email(assunto, montar_html(grupos), montar_texto(grupos))
                print(f"E-mail enviado com {len(fila)} notícias.")
                fila = []
            except Exception as erro:
                email_ok = False  # a fila é mantida e vai no próximo envio
                print(f"ERRO ao enviar e-mail: {erro}", file=sys.stderr)

    salvar_fila(fila)

    # 4) Histórico: tudo que foi coletado já está no site e/ou na fila.
    for ns in novas.values():
        for n in ns:
            vistas[n["id"]] = agora.timestamp()
    salvar_vistas(vistas)

    return 0 if email_ok else 1


if __name__ == "__main__":
    sys.exit(main())
