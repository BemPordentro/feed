# -*- coding: utf-8 -*-
"""
Gerador de página inicial de notícias (RSS -> HTML).
Sem algoritmo: ordem cronológica pura, fontes escolhidas por você.
Requer apenas Python 3 (biblioteca padrão).

No PC:      python noticias.py            (gera noticias.html na pasta do script)
No GitHub:  python noticias.py --saida site   (gera site/index.html para o app)
"""
import json, re, html, sys, os
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO — edite à vontade
# (nome, seção, url do feed)
# ---------------------------------------------------------------------------
FONTES = [
    # Brasil — espectro variado
    ("Poder360",          "Brasil",   "https://www.poder360.com.br/feed/"),
    ("Agência Brasil",    "Brasil",   "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml"),
    ("Folha",             "Brasil",   "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml"),
    # G1 por editoria. Só Política tem feed próprio confiável; as demais vêm da busca
    # do Google Notícias filtrada pelo caminho da editoria (o feed geral traz regional)
    ("G1 Política",       "Brasil",   "https://g1.globo.com/rss/g1/politica/"),
    ("G1 Economia",       "Economia", "https://news.google.com/rss/search?q=when:2d+site:g1.globo.com/economia&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    ("G1 Tecnologia",     "Tecnologia", "https://news.google.com/rss/search?q=when:2d+site:g1.globo.com/tecnologia&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    ("G1 Mundo",          "Mundo",    "https://news.google.com/rss/search?q=when:2d+site:g1.globo.com/mundo&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    ("CartaCapital",      "Brasil",   "https://www.cartacapital.com.br/feed/"),
    # Brasil — direita / centro-direita
    ("Gazeta do Povo",    "Brasil",   "https://www.gazetadopovo.com.br/feed/rss/republica.xml"),
    ("Revista Oeste",     "Brasil",   "https://revistaoeste.com/feed/"),
    ("Jovem Pan",         "Brasil",   "https://jovempan.com.br/feed"),
    # Rio Grande do Sul
    ("O Sul",             "RS",       "https://www.osul.com.br/feed/"),
    ("Correio do Povo",   "RS",       "https://www.correiodopovo.com.br/cmlink/ultimas-1.1100282"),
    ("GZH",               "RS",       "https://news.google.com/rss/search?q=when:2d+site:gauchazh.clicrbs.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    # Agências internacionais (sem RSS oficial: via busca do Google Notícias,
    # filtrada pelo site da agência, ordem cronológica, sem ranqueamento)
    ("Reuters (EN)",      "Agências", "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
    ("AP (EN)",           "Agências", "https://news.google.com/rss/search?q=when:24h+site:apnews.com&hl=en-US&gl=US&ceid=US:en"),
    ("AFP (FR/EN)",       "Agências", "https://news.google.com/rss/search?q=when:24h+site:afp.com&hl=en-US&gl=US&ceid=US:en"),
    ("AFP Checamos",      "Agências", "https://news.google.com/rss/search?q=when:7d+site:checamos.afp.com&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    # Mundo em português
    ("BBC Brasil",        "Mundo",    "https://feeds.bbci.co.uk/portuguese/rss.xml"),
    ("DW Brasil",         "Mundo",    "https://news.google.com/rss/search?q=when:3d+site:dw.com/pt-br&hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    # Economia
    ("InfoMoney",         "Economia", "https://www.infomoney.com.br/feed/"),
]

HORAS_JANELA = 36          # mostra notícias das últimas N horas
MAX_POR_FONTE = 40         # limite por fonte (evita uma fonte dominar)
# Itens cujo título/categoria contenham estes termos são descartados
EXCLUIR = ["horóscopo", "horoscopo", "astrologia", "tarot", "signos"]

PASTA = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.path.join(PASTA, "noticias.html")
if "--saida" in sys.argv:                    # modo servidor: gera <pasta>/index.html
    _pasta = os.path.abspath(sys.argv[sys.argv.index("--saida") + 1])
    os.makedirs(_pasta, exist_ok=True)
    SAIDA = os.path.join(_pasta, "index.html")
ONLINE = bool(os.environ.get("GITHUB_ACTIONS"))   # rodando no GitHub?
TZ_LOCAL = timezone(timedelta(hours=-3))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FeedPessoal/1.0"

# ---------------------------------------------------------------------------

def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""

def filho(el, *nomes):
    for c in el:
        if local(c.tag) in nomes:
            return c
    return None

def texto(el, *nomes):
    c = filho(el, *nomes)
    return (c.text or "").strip() if c is not None and c.text else ""

def parse_data(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d

TAG_RE = re.compile(r"<[^>]+>")
def limpar(s, n=240):
    s = html.unescape(TAG_RE.sub(" ", html.unescape(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:n].rsplit(" ", 1)[0] + "…") if len(s) > n else s

def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        dados = r.read()
    if dados[:2] == b"\x1f\x8b":          # resposta comprimida (gzip)
        import gzip
        dados = gzip.decompress(dados)
    return dados

ENT_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)(\w+);")
def xml_tolerante(dados):
    """Conserta erros comuns de feeds mal formados (prefixos sem declaração,
    entidades HTML soltas, lixo antes do XML)."""
    s = dados.decode("utf-8", errors="replace")
    s = s[s.find("<"):] if "<" in s else s
    s = re.sub(r"<(/?)[A-Za-z_][\w.-]*:(?=[A-Za-z_])", r"<\1", s)          # dc:creator -> creator
    s = re.sub(r"\s(?!xmlns|xml)[A-Za-z_][\w.-]*:([A-Za-z_][\w.-]*=)", r" \1", s)  # atributos
    s = re.sub(r"\sxmlns(:[\w.-]+)?=\"[^\"]*\"", "", s)
    s = ENT_RE.sub(lambda m: html.unescape(m.group(0)) if html.unescape(m.group(0)) != m.group(0) else "", s)
    s = re.sub(r"<\?xml[^>]*\?>", "", s, count=1)
    return ET.fromstring(s.encode("utf-8"))

IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
def imagem(el, bruto):
    for c in el:
        t = local(c.tag)
        u = c.get("url") or ""
        if t in ("thumbnail", "content") and u:
            return u
        if t == "enclosure" and u and (c.get("type", "").startswith("image") or re.search(r"\.(jpe?g|png|webp)", u, re.I)):
            return u
        if t == "link" and c.get("rel") == "enclosure" and c.get("href"):
            return c.get("href")
        if t in ("image", "imagem-destaque"):
            if c.text and c.text.strip().startswith("http"):
                return c.text.strip()
            sub = filho(c, "url")
            if sub is not None and sub.text:
                return sub.text.strip()
    m = IMG_RE.search(html.unescape(bruto or ""))
    if m and not re.search(r"(ebc\.png|ebc\.gif|pixel|1x1|logo)", m.group(1), re.I):
        return m.group(1)
    return ""

def ler_fonte(fonte):
    nome, secao, url = fonte
    try:
        dados = baixar(url)
    except Exception as e:
        return nome, [], f"{type(e).__name__}: {e}"
    try:
        raiz = ET.fromstring(dados)
    except ET.ParseError:
        try:
            raiz = xml_tolerante(dados)
        except Exception as e:
            inicio = dados[:60].decode("utf-8", "replace").replace("\n", " ")
            return nome, [], f"{type(e).__name__} (resposta começa com: {inicio!r})"
    google = "news.google.com" in url
    itens = []
    for el in raiz.iter():
        t = local(el.tag)
        if t not in ("item", "entry"):
            continue
        titulo = limpar(texto(el, "title"), 300)
        if not titulo:
            continue
        link = texto(el, "link")
        if not link:  # Atom
            for c in el:
                if local(c.tag) == "link" and c.get("rel", "alternate") == "alternate":
                    link = c.get("href", ""); break
        if google:  # remove o " - Reuters" do fim do título
            titulo = re.sub(r"\s[-–]\s[^-–]{2,40}$", "", titulo)
        data = parse_data(texto(el, "pubdate", "published", "updated", "date"))
        bruto = texto(el, "description", "summary")
        img = imagem(el, bruto + " " + texto(el, "encoded"))
        resumo = "" if google else limpar(bruto)
        cats = " ".join((c.text or "") for c in el if local(c.tag) == "category").lower()
        alvo = (titulo + " " + cats).lower()
        if any(x in alvo for x in EXCLUIR):
            continue
        itens.append({"f": nome, "s": secao, "t": titulo, "u": link,
                      "r": resumo, "i": img, "c": cats[:300],
                      "d": data.timestamp() if data else None})
    return nome, itens[:MAX_POR_FONTE], None

def main():
    with ThreadPoolExecutor(max_workers=8) as ex:
        resultados = list(ex.map(ler_fonte, FONTES))
    limite = datetime.now(timezone.utc).timestamp() - HORAS_JANELA * 3600
    todos, erros, contagem = [], [], {}
    for nome, itens, erro in resultados:
        if erro:
            erros.append(f"{nome} — {erro}")
        validos = [i for i in itens if i["d"] is None or i["d"] >= limite]
        contagem[nome] = len(validos)
        todos.extend(validos)
    todos.sort(key=lambda i: i["d"] or 0, reverse=True)
    # Remove duplicatas: mesmo link ou mesmo título (ignorando acentos/pontuação).
    # Mantém a primeira ocorrência e registra as outras fontes que publicaram.
    import unicodedata
    def chave(t):
        t = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]+", " ", t).strip()
    vistos, unicos = {}, []
    for i in todos:
        k1, k2 = i["u"].split("?")[0].rstrip("/"), chave(i["t"])
        orig = vistos.get(k1) or vistos.get(k2)
        if orig is not None:
            if i["f"] != orig["f"] and i["f"] not in orig.setdefault("o", []):
                orig["o"].append(i["f"])
            continue
        vistos[k1] = vistos[k2] = i
        unicos.append(i)
    todos = unicos
    contagem = {f[0]: 0 for f in FONTES}
    for i in todos:
        contagem[i["f"]] = contagem.get(i["f"], 0) + 1
    agora = datetime.now(TZ_LOCAL).strftime("%d/%m/%Y %H:%M")
    ts = datetime.now(timezone.utc).timestamp()
    dados = {"itens": todos, "fontes": [f[0] for f in FONTES],
             "secoes": sorted({f[1] for f in FONTES}), "contagem": contagem,
             "erros": erros, "gerado": agora, "ts": ts, "online": ONLINE}
    with open(SAIDA, "w", encoding="utf-8") as fh:
        fh.write(MODELO.replace("__DADOS__", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")))
    print(f"{len(todos)} notícias, {len(erros)} fonte(s) com erro -> {SAIDA}")
    for e in erros:
        print("  ERRO:", e)
    registrar(f"{agora}  {len(todos)} noticias, {len(erros)} erro(s)" + "".join("\n    " + e for e in erros))

LOG = os.path.join(PASTA, "noticias.log")
def registrar(linha):
    """Guarda um histórico curto de execuções (últimas ~200 linhas). Só no PC."""
    if ONLINE:
        return
    try:
        antigas = open(LOG, encoding="utf-8").read().splitlines()[-200:] if os.path.exists(LOG) else []
        with open(LOG, "w", encoding="utf-8") as fh:
            fh.write("\n".join(antigas + [linha]) + "\n")
    except Exception:
        pass

def abrir_pagina():
    """Abre a página numa janela própria do Edge; se não achar o Edge, usa o navegador padrão."""
    import subprocess
    from pathlib import Path
    url = Path(SAIDA).as_uri()
    for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(edge):
            subprocess.Popen([edge, "--app=" + url]); return
    os.startfile(SAIDA)

MODELO = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="900">
<meta name="robots" content="noindex,nofollow">
<title>Notícias</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect x='4' y='8' width='56' height='48' rx='8' fill='%231f5fbf'/%3E%3Crect x='12' y='16' width='40' height='8' rx='2' fill='white'/%3E%3Crect x='12' y='30' width='18' height='18' rx='2' fill='%23cfe0ff'/%3E%3Crect x='34' y='30' width='18' height='4' rx='2' fill='white'/%3E%3Crect x='34' y='37' width='18' height='4' rx='2' fill='white'/%3E%3Crect x='34' y='44' width='12' height='4' rx='2' fill='white'/%3E%3C/svg%3E">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icone-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Notícias">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#f6f6f4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#141414" media="(prefers-color-scheme: dark)">
<style>
:root{--bg:#f6f6f4;--card:#fff;--tx:#1d1d1b;--mut:#6b6b66;--ln:#e3e3de;--ac:#1f5fbf;--chip:#ececE7}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1d1d1d;--tx:#e8e8e3;--mut:#9a9a93;--ln:#2e2e2e;--ac:#7fb0ff;--chip:#2a2a2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,-apple-system,Segoe UI,Arial,sans-serif;-webkit-text-size-adjust:100%}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--ln);padding:12px 16px;z-index:2}
.wrap{max-width:900px;margin:0 auto}
.top{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:18px;margin:0;font-weight:650}.meta{color:var(--mut);font-size:12px}
input{flex:1;min-width:180px;padding:7px 10px;border:1px solid var(--ln);border-radius:8px;background:var(--card);color:var(--tx);font:inherit}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.chip{border:1px solid var(--ln);background:var(--card);color:var(--tx);border-radius:999px;padding:3px 10px;font-size:13px;cursor:pointer}
.chip.on{background:var(--tx);color:var(--bg);border-color:var(--tx)}
.chip.off{opacity:.4;text-decoration:line-through}
main{padding:8px 16px 40px}
.it{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:10px 14px;margin:8px 0;display:flex;gap:14px;align-items:flex-start}
.tx{flex:1;min-width:0}
.th{flex:0 0 150px;height:100px;border-radius:8px;object-fit:cover;background:var(--chip)}
@media (max-width:560px){.th{flex-basis:90px;height:70px}}
#modo{margin-left:auto}
.l1{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--mut);flex-wrap:wrap}
.src{background:var(--chip);color:var(--tx);border-radius:4px;padding:1px 6px;font-weight:600}
a.t{color:var(--tx);text-decoration:none;font-weight:600;display:block;margin:3px 0}
a.t:hover{color:var(--ac)}a.t:visited{color:var(--mut)}
.r{color:var(--mut);font-size:13.5px;margin:0}
.dia{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:18px 0 4px}
.err{font-size:12px;color:#b54;margin-top:24px;overflow-wrap:anywhere}
/* ---- modo grade ---- */
body.grade .wrap{max-width:1500px}
body.grade #lista{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px;align-items:stretch}
body.grade .it{flex-direction:column;margin:0;padding:0;overflow:hidden;gap:0}
body.grade .it>a{order:-1;display:block;width:100%}
body.grade .th{width:100%;height:auto;aspect-ratio:16/9;border-radius:0;flex:none}
body.grade .tx{padding:10px 14px 12px}
body.grade .r{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
body.grade .dia,body.grade .err,body.grade #lista>p{grid-column:1/-1;margin:10px 0 0}
/* ---- temas e preferências ---- */
.tag{background:var(--ac);color:var(--bg);border-radius:4px;padding:1px 6px;font-weight:600}
.chip.tema.on{background:var(--ac);border-color:var(--ac);color:var(--bg)}
#velha{display:none;margin-top:10px;padding:8px 12px;border-radius:8px;background:#b54;color:#fff;font-size:13px}
#prefs{display:none;margin-top:12px;padding:12px;border:1px solid var(--ln);border-radius:10px;background:var(--card)}
#prefs.aberto{display:block}
#prefs .cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:760px){#prefs .cols{grid-template-columns:1fr}}
#prefs label{font-size:13px;font-weight:600;display:block;margin-bottom:4px}
#prefs small{color:var(--mut);font-weight:400;display:block;margin-bottom:6px}
#prefs textarea{width:100%;min-height:150px;padding:8px;border:1px solid var(--ln);border-radius:8px;background:var(--bg);color:var(--tx);font:13px/1.4 ui-monospace,Consolas,monospace}
#prefs .acoes{display:flex;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap}
#prefs .acoes span{color:var(--mut);font-size:12px}
/* ---- celular ---- */
@media (max-width:600px){
 header{position:static;padding:10px 12px}
 .top{gap:8px}
 .meta{flex-basis:100%;order:2}
 input{order:3;flex-basis:100%;font-size:16px;padding:9px 10px}
 .chips{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;margin-top:8px;padding-bottom:2px}
 .chips::-webkit-scrollbar{display:none}
 .chip{flex:0 0 auto;padding:5px 12px}
 main{padding:4px 12px 40px}
 #prefs textarea{font-size:16px}
}
</style></head><body>
<header><div class="wrap">
 <div class="top"><h1>Notícias</h1><span class="meta" id="meta"></span>
 <input id="q" type="search" placeholder="Filtrar por palavra…">
 <button class="chip" id="bt-recarregar" type="button" title="Buscar a versão mais recente da página">↻</button>
 <button class="chip" id="bt-prefs" type="button">⚙ Preferências</button>
 <button class="chip" id="modo" type="button"></button></div>
 <div id="velha"></div>
 <div class="chips" id="temas"></div>
 <div class="chips" id="secoes"></div>
 <div class="chips" id="fontes"></div>
 <div id="prefs">
  <div class="cols">
   <div><label>Meus temas</label>
    <small>Um tema por linha: <b>Nome: termo, termo, termo</b>. Basta o começo da palavra ("eletri" pega elétrica, eletricidade). Acentos e maiúsculas não importam.</small>
    <textarea id="tx-temas" spellcheck="false"></textarea></div>
   <div><label>Bloquear</label>
    <small>Notícias com qualquer um destes termos no título, resumo ou categoria somem da página. Separe por vírgula ou linha.</small>
    <textarea id="tx-bloq" spellcheck="false"></textarea></div>
  </div>
  <div class="acoes"><button class="chip on" id="bt-salvar" type="button">Salvar</button>
   <button class="chip" id="bt-padrao" type="button">Restaurar padrão</button><span id="info-bloq"></span></div>
 </div>
</div></header>
<main class="wrap" id="lista"></main>
<script>
const D=__DADOS__;
const $=id=>document.getElementById(id);
const LS={get(k,d){try{const v=localStorage.getItem(k);return v===null?d:v}catch(e){return d}},
          set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const TEMAS_PADRAO=`Política: política, congresso, senado, câmara dos deputados, deputad, senador, stf, supremo, tse, eleiç, eleitor, governo federal, ministr, planalto
Economia: economia, inflação, juros, selic, pib, dólar, câmbio, banco central, imposto, tribut, fiscal, mercado financeiro, bolsa de valores, ibovespa
Tecnologia: tecnologia, inteligência artificial, ia, software, chip, semicondut, startup, big tech, ciberataque, hacker, apple, google, microsoft, nvidia, openai, anthropic, robô
Energia: energia, elétric, eletricidade, aneel, ons, petróleo, petrobras, combustív, diesel, gasolina, etanol, solar, fotovolt, eólic, hidrelétric, transmissão, distribuidora, apagão, carro elétrico, veículo elétrico, recarga, hidrogênio, bateria`;
const BLOQ_PADRAO=`acidente de trânsito, acidente de moto, acidente com moto, atropel, capot, colisão entre, engavetamento, motociclista, briga de, bbb, reality, novela, fofoca, celebridade, influenciador`;
const norm=s=>(s||"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
const rx=t=>new RegExp("(^|[^a-z0-9])"+norm(t).trim().replace(/[.*+?^${}()|[\]\\]/g,"\\$&"));
let temas=[],bloq=[];
function carregaPrefs(){
 const tt=LS.get("temas",TEMAS_PADRAO), bb=LS.get("bloq",BLOQ_PADRAO);
 $("tx-temas").value=tt; $("tx-bloq").value=bb;
 temas=tt.split("\n").map(l=>{const k=l.indexOf(":");if(k<1)return null;
   const termos=l.slice(k+1).split(",").map(x=>x.trim()).filter(Boolean);
   return termos.length?{nome:l.slice(0,k).trim(),re:termos.map(rx)}:null}).filter(Boolean);
 bloq=bb.split(/[,\n]/).map(x=>x.trim()).filter(Boolean).map(rx);
 for(const i of D.itens){const txt=norm(i.t+" "+i.r+" "+(i.c||""));
  i.bl=bloq.some(r=>r.test(txt));
  i.tm=temas.filter(t=>t.re.some(r=>r.test(txt))).map(t=>t.nome);}
 if(tema!=="Todos"&&!temas.some(t=>t.nome===tema))tema="Todos";
}
let secao="Todas",tema=LS.get("tema","Todos"),ocultas=new Set();
try{ocultas=new Set(JSON.parse(LS.get("ocultas","[]")))}catch(e){}
function chips(){
 const vis=D.itens.filter(i=>!i.bl);
 $("temas").innerHTML=["Todos",...temas.map(t=>t.nome)].map(t=>{
   const n=t==="Todos"?vis.length:vis.filter(i=>i.tm.includes(t)).length;
   return `<button class="chip tema ${t===tema?'on':''}" data-t="${esc(t)}">${t==="Todos"?"Todos os temas":esc(t)} (${n})</button>`}).join("");
 $("secoes").innerHTML=["Todas",...D.secoes].map(s=>`<button class="chip ${s===secao?'on':''}" data-s="${s}">${s}</button>`).join("");
 $("fontes").innerHTML=D.fontes.map(f=>`<button class="chip ${ocultas.has(f)?'off':''}" data-f="${f}" title="clique para ocultar/mostrar">${f} (${D.contagem[f]??0})</button>`).join("");
 const nb=D.itens.length-vis.length;
 $("meta").textContent="Atualizado "+D.gerado+" · "+vis.length+" itens"+(nb?" · "+nb+" bloqueados":"");
 $("info-bloq").textContent=nb+" notícias bloqueadas nesta atualização";
}
document.addEventListener("click",e=>{const b=e.target.closest(".chip");if(!b||b.id)return;
 if(b.dataset.t!==undefined){tema=b.dataset.t;LS.set("tema",tema)}
 else if(b.dataset.s){secao=b.dataset.s}
 else if(b.dataset.f){const f=b.dataset.f;ocultas.has(f)?ocultas.delete(f):ocultas.add(f);LS.set("ocultas",JSON.stringify([...ocultas]))}
 else return;
 chips();render();window.scrollTo(0,0)});
$("q").addEventListener("input",render);
$("bt-prefs").addEventListener("click",()=>$("prefs").classList.toggle("aberto"));
$("bt-salvar").addEventListener("click",()=>{LS.set("temas",$("tx-temas").value);LS.set("bloq",$("tx-bloq").value);
 carregaPrefs();chips();render();$("prefs").classList.remove("aberto")});
$("bt-padrao").addEventListener("click",()=>{if(!confirm("Voltar temas e bloqueios ao padrão?"))return;
 $("tx-temas").value=TEMAS_PADRAO;$("tx-bloq").value=BLOQ_PADRAO});
// No celular a lista ocupa melhor a tela; no PC, a grade
let modo=LS.get("modo",window.innerWidth<600?"lista":"grade");
function aplicaModo(){document.body.classList.toggle("grade",modo==="grade");
 $("modo").textContent=modo==="grade"?"☰ Lista":"▦ Grade"}
$("modo").addEventListener("click",()=>{modo=modo==="grade"?"lista":"grade";LS.set("modo",modo);aplicaModo()});
aplicaModo();
// Recarregar: o parâmetro ?t= evita que o navegador mostre uma cópia guardada
const carregadaEm=Date.now();
function recarregar(){location.replace(location.pathname+"?t="+Date.now())}
$("bt-recarregar").addEventListener("click",recarregar);
// Ao voltar para o app depois de 10 min fora, busca a versão nova sozinho
document.addEventListener("visibilitychange",()=>{
 if(document.visibilityState==="visible"&&Date.now()-carregadaEm>600000)recarregar()});
function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function render(){
 const q=norm($("q").value.trim());let out="",dia="";
 for(const i of D.itens){
  if(i.bl)continue;
  if(secao!=="Todas"&&i.s!==secao)continue; if(ocultas.has(i.f))continue;
  if(tema!=="Todos"&&!i.tm.includes(tema))continue;
  if(q&&!norm(i.t+" "+i.r).includes(q))continue;
  const dt=i.d?new Date(i.d*1000):null;
  const dd=dt?dt.toLocaleDateString("pt-BR",{weekday:"long",day:"2-digit",month:"2-digit"}):"sem data";
  if(dd!==dia){out+=`<div class="dia">${dd}</div>`;dia=dd}
  const hh=dt?dt.toLocaleTimeString("pt-BR",{hour:"2-digit",minute:"2-digit"}):"";
  const tags=i.tm.map(t=>`<span class="tag">${esc(t)}</span>`).join("");
  out+=`<article class="it"><div class="tx"><div class="l1"><span class="src">${esc(i.f)}</span><span>${hh}</span>${tags}</div>
  <a class="t" href="${esc(i.u)}" target="_blank" rel="noopener">${esc(i.t)}</a>${i.o?`<div class="l1">também em: ${i.o.map(esc).join(", ")}</div>`:""}${i.r?`<p class="r">${esc(i.r)}</p>`:""}</div>
  ${i.i?`<a href="${esc(i.u)}" target="_blank" rel="noopener"><img class="th" src="${esc(i.i)}" loading="lazy" referrerpolicy="no-referrer" alt="" onerror="this.parentNode.remove()"></a>`:""}</article>`;
 }
 if(D.erros.length)out+=`<div class="err">Fontes que falharam nesta atualização: ${D.erros.map(esc).join(" · ")}</div>`;
 $("lista").innerHTML=out||"<p>Nada encontrado.</p>";
}
carregaPrefs();chips();render();
(function(){const h=(Date.now()/1000-(D.ts||0))/3600, lim=D.online?3:2;
 if(D.ts&&h>lim){const v=$("velha");v.style.display="block";
  v.textContent="Página desatualizada: última atualização há "+Math.floor(h)+" h. "+(D.online
   ?"A atualização automática no GitHub parece parada — abra o repositório, aba Actions, e veja se há execuções com erro ou se o agendamento foi desativado."
   :"A atualização automática não está rodando — veja o arquivo noticias.log na pasta.")}})();
</script></body></html>"""

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        registrar(f"{datetime.now(TZ_LOCAL):%d/%m/%Y %H:%M}  FALHA GERAL: {type(e).__name__}: {e}")
        raise
    finally:
        if "--abrir" in sys.argv:
            abrir_pagina()
