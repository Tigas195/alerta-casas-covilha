# -*- coding: utf-8 -*-
"""
Alerta de casas à venda na Covilhã
==================================
Pesquisa apartamentos e moradias no Imovirtual e no Casa Sapo, filtra pelos
critérios definidos em config.json e envia um e-mail com os anúncios novos.

Critérios (config.json):
  - Preço: 130.000 € a 210.000 €
  - Tipologia: T3 ou superior
  - Casas de banho: pelo menos 2
  - Construção: no máximo 20 anos (obras novas / em construção contam como recentes)
  - Apartamentos: têm de ter elevador
  - Zonas: Covilhã e Canhoso, Tortosendo, Boidobra, Refúgio

Utilização:
  python alerta_casas.py              # pesquisa e envia e-mail com anúncios novos
  python alerta_casas.py --dry-run    # pesquisa e mostra resultados, sem enviar e-mail
  python alerta_casas.py --test-email # envia apenas um e-mail de teste
"""

import argparse
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
ESTADO_PATH = BASE_DIR / "estado.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

# ritmo mínimo entre pedidos ao mesmo site (o Casa Sapo bloqueia pedidos rápidos)
RITMO_POR_DOMINIO = {"casa.sapo.pt": 4.0, "www.imovirtual.com": 1.2}
_ultimo_pedido = {}

# Imovirtual: roomsNumber é o nº de divisões (T3 => FOUR)
ROOMS_WORD = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
    "SIX_OR_MORE": 6, "TEN_OR_MORE": 10,
}


# ---------------------------------------------------------------- utilidades

def normalizar(texto):
    """minúsculas, sem acentos, hífens viram espaços."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[-_/]+", " ", texto.lower()).strip()


def carregar_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"ERRO: falta o ficheiro de configuração {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    # no GitHub Actions as credenciais vêm de secrets, não do config.json
    em = cfg["email"]
    em["remetente"] = os.environ.get("GMAIL_REMETENTE", em["remetente"])
    em["app_password"] = os.environ.get("GMAIL_APP_PASSWORD", em["app_password"])
    em["destinatario"] = os.environ.get("GMAIL_DESTINATARIO", em["destinatario"])
    return cfg


def carregar_estado():
    if ESTADO_PATH.exists():
        with open(ESTADO_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"alertados": {}, "cache_detalhes": {}}


def guardar_estado(estado):
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)


def obter(url, tentativas=4):
    dominio = re.sub(r"^https?://([^/]+).*", r"\1", url)
    ritmo = RITMO_POR_DOMINIO.get(dominio, 2.0)
    for i in range(tentativas):
        espera = _ultimo_pedido.get(dominio, 0) + ritmo - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        _ultimo_pedido[dominio] = time.monotonic()
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 429:
                pausa = 20 * (i + 1)
                print(f"    aviso: HTTP 429 (limite de pedidos) — a esperar {pausa}s")
                time.sleep(pausa)
                continue
            print(f"    aviso: HTTP {r.status_code} em {url}")
        except requests.RequestException as e:
            print(f"    aviso: {e}")
        time.sleep(3 * (i + 1))
    return None


def extrair_tipologia(texto):
    m = re.search(r"\bT(\d+)\b", texto or "", re.I)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- Imovirtual

def imv_next_data(html):
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
        html, re.S)
    return json.loads(m.group(1)) if m else None


def imv_pesquisar(cfg):
    """Devolve a lista de anúncios (dados do cartão) do Imovirtual."""
    f = cfg["filtros"]
    anuncios = []
    for tipo, slug_tipo in (("Apartamento", "apartamento"), ("Moradia", "moradia")):
        pagina, total_paginas = 1, 1
        while pagina <= total_paginas and pagina <= 10:
            url = (f"https://www.imovirtual.com/pt/resultados/comprar/{slug_tipo}"
                   f"/castelo-branco/covilha?priceMin={f['preco_min']}"
                   f"&priceMax={f['preco_max']}&limit=72&page={pagina}")
            r = obter(url)
            if not r:
                break
            data = imv_next_data(r.text)
            try:
                ads = data["props"]["pageProps"]["data"]["searchAds"]
            except (KeyError, TypeError):
                print("    aviso: estrutura inesperada no Imovirtual (o site pode ter mudado)")
                break
            total_paginas = (ads.get("pagination") or {}).get("totalPages", 1)
            for it in ads.get("items") or []:
                if not it.get("slug"):
                    continue
                preco = (it.get("totalPrice") or {}).get("value")
                rooms = ROOMS_WORD.get(it.get("roomsNumber") or "", None)
                tipologia = extrair_tipologia(it.get("title"))
                if tipologia is None and rooms:
                    tipologia = rooms - 1  # no Imovirtual T3 aparece como 4 divisões
                # freguesia (nível "parish" do reverse geocoding)
                freguesia = None
                loc = (it.get("location") or {}).get("reverseGeocoding") or {}
                for l in loc.get("locations") or []:
                    if l.get("locationLevel") == "parish":
                        freguesia = l.get("name")
                imagem = ""
                if it.get("images"):
                    imagem = (it["images"][0] or {}).get("medium", "")
                anuncios.append({
                    "portal": "imovirtual",
                    "id": f"imovirtual:{it['id']}",
                    "titulo": it.get("title", "").strip(),
                    "tipo": "Apartamento" if it.get("estate") == "FLAT" else tipo,
                    "preco": preco,
                    "tipologia": tipologia,
                    "area": it.get("areaInSquareMeters"),
                    "freguesia": freguesia,
                    "url": f"https://www.imovirtual.com/pt/anuncio/{it['slug']}",
                    "imagem": imagem,
                })
            pagina += 1
    return anuncios


def imv_detalhe(url):
    """Extrai casas de banho, ano, elevador e estado de construção do detalhe."""
    r = obter(url)
    if not r:
        return None
    data = imv_next_data(r.text)
    try:
        ad = data["props"]["pageProps"]["ad"]
    except (KeyError, TypeError):
        return None
    tgt = ad.get("target") or {}

    def primeiro_int(v):
        if isinstance(v, list) and v:
            v = v[0]
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return None

    casas_banho = primeiro_int(tgt.get("Bathrooms_num"))
    ano = primeiro_int(tgt.get("Build_year") or tgt.get("Construction_year"))
    estado_constr = ""
    ec = tgt.get("Construction_status")
    if isinstance(ec, list) and ec:
        estado_constr = str(ec[0])
    mercado = str(tgt.get("MarketType") or "")

    elevador = None  # None = não indicado
    extras = tgt.get("Extras_types") or []
    if "lift" in extras:
        elevador = True
    for info in ad.get("additionalInformation") or []:
        if info.get("label") == "lift":
            vals = info.get("values") or []
            if "::y" in vals:
                elevador = True
            elif "::n" in vals:
                elevador = False
    return {
        "casas_banho": casas_banho,
        "ano": ano,
        "elevador": elevador,
        "obra_nova": estado_constr in ("to_completion", "under_construction")
                     or mercado == "primary",
    }


# ----------------------------------------------------------------- Casa Sapo

def sapo_pesquisar(cfg):
    """Devolve a lista de anúncios (dados do cartão) do Casa Sapo."""
    anuncios, vistos = [], set()
    for tipo_url in ("comprar-apartamentos", "comprar-moradias"):
        pagina = 1
        while pagina <= 10:
            url = f"https://casa.sapo.pt/{tipo_url}/covilha/?pn={pagina}"
            r = obter(url)
            if not r:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("div.property")
            novos = 0
            for card in cards:
                uid = (card.get("id") or "").replace("property_", "")
                a = card.select_one("a[href$='.html']")
                if not uid or not a or uid in vistos:
                    continue
                vistos.add(uid)
                novos += 1
                tipo_el = card.select_one("div.property-type")
                preco_el = card.select_one("div.property-price")
                loc_el = card.select_one("div.property-location")
                feat_el = card.select_one("div.property-features")
                tipo_txt = tipo_el.get_text(" ", strip=True) if tipo_el else ""
                preco = None
                if preco_el:
                    digs = re.sub(r"[^\d]", "", preco_el.get_text())
                    preco = int(digs) if digs else None
                area = None
                estado_txt = ""
                if feat_el:
                    ftxt = feat_el.get_text(" ", strip=True)
                    estado_txt = ftxt
                    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", ftxt)
                    if m:
                        area = float(m.group(1).replace(",", "."))
                anuncios.append({
                    "portal": "casasapo",
                    "id": f"casasapo:{uid}",
                    "titulo": tipo_txt or "Imóvel",
                    "tipo": "Moradia" if "moradia" in normalizar(tipo_txt) else "Apartamento",
                    "preco": preco,
                    "tipologia": extrair_tipologia(tipo_txt),
                    "area": area,
                    "freguesia": loc_el.get_text(" ", strip=True) if loc_el else "",
                    "url": "https://casa.sapo.pt" + a["href"],
                    "imagem": (card.select_one("img[src]") or {}).get("src", ""),
                    "estado_txt": estado_txt,
                })
            if novos == 0:
                break
            pagina += 1
    return anuncios


def sapo_detalhe(url):
    """Extrai o que for possível do detalhe do Casa Sapo (nem sempre há dados)."""
    r = obter(url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")

    dados = {}
    for item in soup.select("div.detail-main-features-item"):
        t = item.select_one("div.detail-main-features-item-title")
        v = item.select_one("div.detail-main-features-item-value")
        if t and v:
            dados[normalizar(t.get_text(strip=True))] = v.get_text(" ", strip=True)

    texto = normalizar(soup.get_text(" ", strip=True))

    casas_banho = None
    for chave, valor in dados.items():
        if "banho" in chave or chave == "wc":
            m = re.search(r"\d+", valor)
            if m:
                casas_banho = int(m.group(0))
    if casas_banho is None:
        m = re.search(r"(\d+)\s+casas?\s+de\s+banho", texto)
        if m:
            casas_banho = int(m.group(1))

    ano = None
    for chave, valor in dados.items():
        if "ano" in chave and "constru" in chave:
            m = re.search(r"(19|20)\d{2}", valor)
            if m:
                ano = int(m.group(0))
    if ano is None:
        m = re.search(r"ano de construcao\D{0,10}((19|20)\d{2})", texto)
        if m:
            ano = int(m.group(1))

    elevador = None
    if re.search(r"\b(sem|nao tem|nao possui)\s+elevador", texto):
        elevador = False
    elif "elevador" in texto:
        elevador = True

    estado = dados.get("estado", "")
    obra_nova = any(p in normalizar(estado) for p in ("em construcao", "novo", "em projeto"))

    return {"casas_banho": casas_banho, "ano": ano,
            "elevador": elevador, "obra_nova": obra_nova}


# ------------------------------------------------------------------- filtros

def zona_aceite(anuncio, zonas_norm):
    campos = " ".join(normalizar(anuncio.get(c)) for c in ("freguesia", "titulo", "url"))
    return any(z in campos for z in zonas_norm)


def passa_filtros_cartao(anuncio, cfg):
    """Filtros que se aplicam só com os dados do cartão de pesquisa."""
    f = cfg["filtros"]
    if anuncio.get("preco") is None:
        return False
    if not (f["preco_min"] <= anuncio["preco"] <= f["preco_max"]):
        return False
    if anuncio.get("tipologia") is None or anuncio["tipologia"] < f["tipologia_min"]:
        return False
    zonas_norm = [normalizar(z) for z in f["zonas"]]
    if not zona_aceite(anuncio, zonas_norm):
        return False
    return True


def avaliar_detalhe(anuncio, det, cfg):
    """
    Aplica os critérios que dependem do detalhe.
    Devolve (aceite, avisos): anúncios que violam explicitamente um critério
    são rejeitados; campos em falta geram aviso mas não excluem.
    """
    f = cfg["filtros"]
    avisos = []
    ano_min = datetime.now().year - f["idade_max_anos"]

    cb = det.get("casas_banho")
    if cb is not None:
        if cb < f["casas_banho_min"]:
            return False, [], f"só {cb} casa(s) de banho"
    else:
        avisos.append("nº de casas de banho não indicado")

    ano = det.get("ano")
    if ano is not None:
        if ano < ano_min:
            return False, [], f"construção de {ano} (mínimo {ano_min})"
    elif det.get("obra_nova"):
        pass  # obra nova/em construção conta como recente
    else:
        avisos.append("ano de construção não indicado")

    if anuncio["tipo"] == "Apartamento" and f["elevador_obrigatorio_apartamento"]:
        elev = det.get("elevador")
        if elev is False:
            return False, [], "apartamento sem elevador"
        if elev is None:
            avisos.append("elevador não indicado")

    if avisos and not f.get("incluir_dados_em_falta", True):
        return False, [], "dados em falta: " + "; ".join(avisos)
    return True, avisos, ""


# -------------------------------------------------------------------- e-mail

def formatar_preco(v):
    return f"{v:,.0f} €".replace(",", ".") if v is not None else "—"


def construir_email_html(novos):
    linhas = []
    for a in novos:
        det = a["detalhe"]
        cb = det.get("casas_banho")
        ano = det.get("ano")
        elev = det.get("elevador")
        elev_txt = "Sim" if elev else ("Não indicado" if elev is None else "Não")
        ano_txt = str(ano) if ano else ("Obra nova" if det.get("obra_nova") else "Não indicado")
        avisos = ""
        if a["avisos"]:
            avisos = ("<div style='color:#b45309;font-size:12px;margin-top:4px'>⚠ "
                      + "; ".join(a["avisos"]) + "</div>")
        img = (f"<img src='{a['imagem']}' width='220' style='border-radius:8px;display:block'>"
               if a.get("imagem") else "")
        linhas.append(f"""
        <table style="width:100%;border:1px solid #e5e7eb;border-radius:10px;
                      margin-bottom:14px;border-collapse:separate;padding:10px;
                      font-family:Segoe UI,Arial,sans-serif">
          <tr>
            <td style="width:230px;vertical-align:top">{img}</td>
            <td style="vertical-align:top;padding-left:12px">
              <a href="{a['url']}" style="font-size:16px;font-weight:600;color:#1d4ed8;
                 text-decoration:none">{a['titulo']}</a>
              <div style="font-size:20px;font-weight:700;margin:6px 0">{formatar_preco(a['preco'])}</div>
              <div style="font-size:13px;color:#374151;line-height:1.7">
                📍 {a.get('freguesia') or '—'}<br>
                🏠 {a['tipo']} T{a['tipologia']} · {a.get('area') or '—'} m²<br>
                🛁 Casas de banho: {cb if cb is not None else 'não indicado'} ·
                🏗 Ano: {ano_txt} ·
                🛗 Elevador: {elev_txt}<br>
                🔎 Fonte: {'Imovirtual' if a['portal'] == 'imovirtual' else 'Casa Sapo'}
              </div>
              {avisos}
            </td>
          </tr>
        </table>""")
    data_txt = datetime.now().strftime("%d/%m/%Y %H:%M")
    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:720px">
      <h2 style="color:#111827">🏠 {len(novos)} novo(s) imóvel(is) que cumprem os teus critérios</h2>
      <p style="color:#6b7280;font-size:13px">
        Covilhã e Canhoso · Tortosendo · Boidobra · Refúgio — T3+, 130.000–210.000 €,
        ≥2 WC, construção recente, elevador (apartamentos). Pesquisa de {data_txt}.
      </p>
      {''.join(linhas)}
      <p style="color:#9ca3af;font-size:11px">E-mail automático do script alerta_casas.py.
      Confirma sempre os dados no anúncio original.</p>
    </div>"""


def enviar_email(cfg, assunto, html):
    em = cfg["email"]
    if "COLOCA_AQUI" in em["app_password"]:
        print("\nERRO: falta configurar a App Password do Gmail em config.json.")
        print("Vê as instruções no README.md. O e-mail NÃO foi enviado.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = em["remetente"]
    msg["To"] = em["destinatario"]
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as srv:
        srv.login(em["remetente"], em["app_password"])
        srv.sendmail(em["remetente"], [em["destinatario"]], msg.as_string())
    return True


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Alertas de casas à venda na Covilhã")
    ap.add_argument("--dry-run", action="store_true",
                    help="pesquisa e mostra resultados sem enviar e-mail nem gravar estado")
    ap.add_argument("--test-email", action="store_true",
                    help="envia apenas um e-mail de teste")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    cfg = carregar_config()

    if args.test_email:
        ok = enviar_email(cfg, "✅ Teste — alertas de casas Covilhã",
                          "<p>O script de alertas está configurado corretamente! 🏠</p>")
        print("E-mail de teste enviado." if ok else "Falhou o envio do e-mail de teste.")
        return

    estado = carregar_estado()
    cache = estado["cache_detalhes"]

    print("A pesquisar Imovirtual…")
    anuncios = imv_pesquisar(cfg)
    print(f"  {len(anuncios)} anúncios encontrados")
    print("A pesquisar Casa Sapo…")
    sapo = sapo_pesquisar(cfg)
    print(f"  {len(sapo)} anúncios encontrados")
    anuncios += sapo

    candidatos = [a for a in anuncios if passa_filtros_cartao(a, cfg)]
    print(f"\n{len(candidatos)} anúncios passam preço/tipologia/zona; a verificar detalhes…")

    aprovados = []
    for a in candidatos:
        if a["id"] in cache:
            det = cache[a["id"]]
        else:
            det = (imv_detalhe(a["url"]) if a["portal"] == "imovirtual"
                   else sapo_detalhe(a["url"]))
            if det is None:
                print(f"  não consegui ler o detalhe de {a['url']}")
                continue
            cache[a["id"]] = det
        aceite, avisos, motivo = avaliar_detalhe(a, det, cfg)
        if aceite:
            a["detalhe"] = det
            a["avisos"] = avisos
            aprovados.append(a)
        elif args.dry_run:
            print(f"  rejeitado ({motivo}): {a['titulo'][:50]} — {a['url']}")

    novos = [a for a in aprovados if a["id"] not in estado["alertados"]]
    print(f"{len(aprovados)} cumprem todos os critérios; {len(novos)} são novos.\n")

    for a in aprovados:
        marca = "NOVO " if a["id"] in [n["id"] for n in novos] else "      "
        print(f"  {marca}{formatar_preco(a['preco']):>12}  T{a['tipologia']} "
              f"{a['tipo']:<12} {a.get('freguesia') or '':<30.30} {a['url']}")

    if args.dry_run:
        print("\n(dry-run: nenhum e-mail enviado, nenhum estado gravado)")
        return

    if not novos:
        print("Sem anúncios novos — nenhum e-mail enviado.")
        guardar_estado(estado)  # guarda cache de detalhes na mesma
        return

    assunto = f"🏠 {len(novos)} novo(s) imóvel(is) na Covilhã ({formatar_preco(cfg['filtros']['preco_min'])}–{formatar_preco(cfg['filtros']['preco_max'])}, T3+)"
    if enviar_email(cfg, assunto, construir_email_html(novos)):
        agora = datetime.now().isoformat(timespec="seconds")
        for a in novos:
            estado["alertados"][a["id"]] = {
                "quando": agora, "titulo": a["titulo"], "preco": a["preco"],
            }
        print(f"E-mail enviado para {cfg['email']['destinatario']} com {len(novos)} anúncio(s).")
    guardar_estado(estado)


if __name__ == "__main__":
    main()
