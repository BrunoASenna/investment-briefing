"""
CIO Daily Briefing v2.0 — Multi-stage Quant Pipeline
=====================================================
Stage 1: Data Layer     — preços reais, macro, notícias, memória
Stage 2: Quant Engine   — momentum, novelty, regime, rotação setorial
Stage 3: LLM Interpreter— narrativa apenas, não decisões
Stage 4: Validation     — valida JSON, corrige números, verifica diversidade
Stage 5: Output         — salva briefing.json, atualiza histórico, WhatsApp
"""
import os, json, re, time, math, urllib.request, urllib.parse
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

# ─── Config ──────────────────────────────────────────────────────────────────
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
WHATSAPP_NUMBER  = os.environ["WHATSAPP_NUMBER"]
CALLMEBOT_APIKEY = os.environ["CALLMEBOT_APIKEY"]
SITE_URL         = os.environ["SITE_URL"]

TZ_BR     = ZoneInfo("America/Sao_Paulo")
today_str = datetime.now(TZ_BR).strftime("%d/%m/%Y")
today_iso = datetime.now(TZ_BR).strftime("%Y-%m-%d")
weekday   = datetime.now(TZ_BR).weekday()  # 0=seg, 4=sex

HISTORY_FILE = "data/history.json"

# ─── Universo de ativos ──────────────────────────────────────────────────────
UNIVERSE = {
    "PETR4.SA":  {"name": "Petrobras PN",          "sector": "energia"},
    "VALE3.SA":  {"name": "Vale ON",               "sector": "mineracao"},
    "ITUB4.SA":  {"name": "Itaú Unibanco PN",      "sector": "financeiro"},
    "BBDC4.SA":  {"name": "Bradesco PN",           "sector": "financeiro"},
    "BBAS3.SA":  {"name": "Banco do Brasil ON",    "sector": "financeiro"},
    "WEGE3.SA":  {"name": "WEG ON",                "sector": "industrial"},
    "RENT3.SA":  {"name": "Localiza ON",           "sector": "consumo"},
    "RADL3.SA":  {"name": "Raia Drogasil ON",      "sector": "saude"},
    "LREN3.SA":  {"name": "Lojas Renner ON",       "sector": "varejo"},
    "SUZB3.SA":  {"name": "Suzano ON",             "sector": "materiais"},
    "EMBR3.SA":  {"name": "Embraer ON",            "sector": "industrial"},
    "VIVT3.SA":  {"name": "Telefônica Vivo ON",    "sector": "telecom"},
    "CMIG4.SA":  {"name": "Cemig PN",              "sector": "utilidades"},
    "EGIE3.SA":  {"name": "Engie Brasil ON",       "sector": "utilidades"},
    "TAEE11.SA": {"name": "Transmissão Paulista",  "sector": "utilidades"},
    "FLRY3.SA":  {"name": "Fleury ON",             "sector": "saude"},
    "HAPV3.SA":  {"name": "Hapvida ON",            "sector": "saude"},
    "SBSP3.SA":  {"name": "Sabesp ON",             "sector": "utilidades"},
    "CSAN3.SA":  {"name": "Cosan ON",              "sector": "energia"},
    "TOTS3.SA":  {"name": "TOTVS ON",              "sector": "tecnologia"},
    "KNCR11.SA": {"name": "Kinea Rendimentos CRI", "sector": "fii_papel"},
    "XPML11.SA": {"name": "XP Malls",             "sector": "fii_tijolo"},
    "BTLG11.SA": {"name": "BTG Logística",         "sector": "fii_tijolo"},
    "HGRU11.SA": {"name": "CSHG Renda Urbana",     "sector": "fii_tijolo"},
    "PVBI11.SA": {"name": "VBI Prime Properties",  "sector": "fii_tijolo"},
}

# Regime defensivo favorece estes setores; agressivo favorece os outros
DEFENSIVE_SECTORS = {"utilidades", "saude", "fii_papel", "telecom"}
OFFENSIVE_SECTORS  = {"energia", "mineracao", "industrial", "tecnologia", "materiais"}

# ─── Helpers ─────────────────────────────────────────────────────────────────
def fetch(url: str, timeout: int = 10) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  WARN fetch({url[:60]}...): {e}")
        return None

def safe_div(a, b):
    return (a - b) / b if b and b != 0 else None

def percentile_rank(values: list[float | None], target: float | None) -> float:
    """Retorna 0-1 indicando posição percentual do target na lista."""
    if target is None:
        return 0.5
    valid = [v for v in values if v is not None]
    if not valid:
        return 0.5
    below = sum(1 for v in valid if v < target)
    return below / len(valid)

# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — DATA LAYER
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_macro() -> dict:
    """Busca Selic, IPCA, Dólar e IBOV de APIs oficiais."""
    result = {}

    # Selic Over (série 1178)
    d = fetch("https://api.bcb.gov.br/dados/serie/bcdata.sgs.1178/dados/ultimos/1?formato=json")
    result["selic"] = f"{float(d[0]['valor']):.2f}%".replace(".", ",") if d else "N/D"

    # IPCA 12m (série 13522)
    d = fetch("https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/1?formato=json")
    result["ipca"] = f"{float(d[0]['valor']):.2f}%".replace(".", ",") if d else "N/D"

    # Selic Meta (série 432) para contexto de ciclo
    d = fetch("https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/5?formato=json")
    if d and len(d) >= 2:
        vals = [float(x["valor"]) for x in d]
        trend = "alta" if vals[-1] > vals[0] else ("queda" if vals[-1] < vals[0] else "estável")
        result["selic_trend"] = trend
    else:
        result["selic_trend"] = "estável"

    # Dólar PTAX via BCB
    result["dolar"] = "N/D"
    for delta in range(5):
        d_str = (date.today() - timedelta(days=delta)).strftime("%m-%d-%Y")
        d = fetch(f"https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
                  f"CotacaoDolarDia(dataCotacao=@d)?@d='{d_str}'&$format=json&$top=1")
        if d and d.get("value"):
            val = float(d["value"][0]["cotacaoVenda"])
            result["dolar"] = f"R$ {val:.2f}".replace(".", ",")
            break

    # IBOV (histórico 3 meses para regime detector)
    d = fetch("https://query1.finance.yahoo.com/v8/finance/chart/%5EBVSP?interval=1d&range=3mo")
    if d:
        try:
            closes = [c for c in d["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
            result["ibov_closes"] = closes
            result["ibov"]        = f"{closes[-1]:,.0f}".replace(",", ".")
            r1d = safe_div(closes[-1], closes[-2])
            result["ibov_var"]    = f"{'+' if r1d >= 0 else ''}{r1d*100:.2f}%" if r1d else "N/D"
            result["ibov_r21d"]   = safe_div(closes[-1], closes[-22]) if len(closes) >= 22 else None
            result["ibov_r63d"]   = safe_div(closes[-1], closes[-63]) if len(closes) >= 63 else None
        except Exception as e:
            print(f"  WARN IBOV parse: {e}")
            result.setdefault("ibov", "N/D"); result.setdefault("ibov_var", "N/D")
    else:
        result["ibov"] = "N/D"; result["ibov_var"] = "N/D"

    return result


def fetch_prices() -> dict:
    """
    Busca histórico de preços (3 meses) para todos os ativos do universo.
    Retorna dict ticker → lista de closes.
    """
    prices = {}
    tickers = list(UNIVERSE.keys())
    print(f"  Buscando preços de {len(tickers)} ativos...")
    for ticker in tickers:
        d = fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=3mo")
        if d:
            try:
                closes = [c for c in d["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
                if len(closes) >= 5:
                    prices[ticker] = closes
            except Exception:
                pass
        time.sleep(0.3)  # evita rate limit
    print(f"  Preços obtidos: {len(prices)}/{len(tickers)} ativos")
    return prices


def fetch_news() -> list[str]:
    """Busca headlines de RSS financeiros brasileiros."""
    feeds = [
        "https://www.infomoney.com.br/feed/",
        "https://feeds.valor.globo.com/financas",
    ]
    headlines = []
    for url in feeds:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                xml_data = r.read()
            root = ET.fromstring(xml_data)
            items = root.findall(".//item")[:5]
            for item in items:
                title = item.find("title")
                if title is not None and title.text:
                    headlines.append(title.text.strip())
        except Exception as e:
            print(f"  WARN RSS {url[:40]}: {e}")
    return headlines[:8]


def load_history() -> list[dict]:
    """Carrega histórico dos últimos 7 briefings."""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_history(history: list[dict], new_entry: dict):
    """Salva novo briefing no histórico (mantém últimos 30 dias)."""
    history.append(new_entry)
    history = history[-30:]
    os.makedirs("data", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — QUANT ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def detect_regime(macro: dict) -> dict:
    """
    Detecta regime de mercado com base em IBOV e Selic.
    Retorna: regime (BULL/BEAR/NEUTRAL), perfil (RISK_ON/RISK_OFF/BALANCED)
    """
    r21d = macro.get("ibov_r21d")
    r63d = macro.get("ibov_r63d")

    # Regime de médio prazo
    if r21d and r21d > 0.04:
        regime = "BULL"
    elif r21d and r21d < -0.04:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"

    # Perfil do dia
    ibov_var_str = macro.get("ibov_var", "0%").replace("+", "").replace("%", "").replace(",", ".")
    try:
        ibov_today = float(ibov_var_str)
    except Exception:
        ibov_today = 0

    if ibov_today > 0.8:
        perfil = "RISK_ON"
    elif ibov_today < -0.8:
        perfil = "RISK_OFF"
    else:
        perfil = "BALANCED"

    # Selic alta → penaliza growth, favorece dividendos e FIIs de papel
    selic_str = macro.get("selic", "0").replace("%", "").replace(",", ".")
    try:
        selic_val = float(selic_str)
    except Exception:
        selic_val = 13.0
    high_rates = selic_val >= 13.0

    return {
        "regime":     regime,
        "perfil":     perfil,
        "high_rates": high_rates,
        "ibov_today": ibov_today,
        "r21d":       r21d,
        "r63d":       r63d,
    }


def calculate_momentum(prices: dict) -> dict:
    """
    Calcula scores de momentum para cada ativo.
    Retorna dict ticker → {r1d, r5d, r21d, composite}
    """
    scores = {}
    # Coleta todos os retornos para calcular percentis
    r1d_all, r5d_all, r21d_all = [], [], []

    raw = {}
    for ticker, closes in prices.items():
        r1d  = safe_div(closes[-1], closes[-2])  if len(closes) >= 2  else None
        r5d  = safe_div(closes[-1], closes[-6])  if len(closes) >= 6  else None
        r21d = safe_div(closes[-1], closes[-22]) if len(closes) >= 22 else None
        raw[ticker] = {"r1d": r1d, "r5d": r5d, "r21d": r21d, "price": closes[-1]}
        if r1d  is not None: r1d_all.append(r1d)
        if r5d  is not None: r5d_all.append(r5d)
        if r21d is not None: r21d_all.append(r21d)

    # Score composto = percentil ponderado
    for ticker, m in raw.items():
        p1d  = percentile_rank(r1d_all,  m["r1d"])
        p5d  = percentile_rank(r5d_all,  m["r5d"])
        p21d = percentile_rank(r21d_all, m["r21d"])
        composite = 0.20 * p1d + 0.35 * p5d + 0.45 * p21d
        scores[ticker] = {
            "r1d":      m["r1d"],
            "r5d":      m["r5d"],
            "r21d":     m["r21d"],
            "price":    m["price"],
            "momentum": composite,
        }
    return scores


def calculate_sector_rotation(momentum_scores: dict) -> dict:
    """
    Calcula performance média por setor e detecta rotação.
    Retorna dict setor → score médio, ordenado.
    """
    sector_scores: dict[str, list[float]] = {}
    for ticker, data in momentum_scores.items():
        sector = UNIVERSE.get(ticker, {}).get("sector", "outro")
        sector_scores.setdefault(sector, []).append(data["momentum"])

    return {
        sector: sum(vals) / len(vals)
        for sector, vals in sector_scores.items()
        if vals
    }


def calculate_novelty(ticker: str, history: list[dict], window_days: int = 3) -> float:
    """
    Penaliza ativos que apareceram recentemente.
    Score 1.0 = nunca apareceu | 0.0 = apareceu todos os últimos dias.
    """
    base_ticker = ticker.replace(".SA", "")
    appearances = 0
    for entry in history[-window_days:]:
        tickers_in_entry = [
            opp.get("ticker", "").replace(".SA", "")
            for opp in entry.get("opportunities", [])
        ]
        if base_ticker in tickers_in_entry:
            appearances += 1
    return max(0.0, 1.0 - appearances * (1.0 / window_days))


def regime_fit_score(ticker: str, regime: dict) -> float:
    """
    Score de adequação do ativo ao regime atual.
    """
    sector = UNIVERSE.get(ticker, {}).get("sector", "outro")
    score = 0.5  # neutro

    if regime["perfil"] == "RISK_OFF" or regime["high_rates"]:
        if sector in DEFENSIVE_SECTORS:
            score = 0.9
        elif sector in OFFENSIVE_SECTORS:
            score = 0.2

    elif regime["perfil"] == "RISK_ON":
        if sector in OFFENSIVE_SECTORS:
            score = 0.9
        elif sector in DEFENSIVE_SECTORS:
            score = 0.4

    # Selic alta sempre beneficia FII de papel
    if regime["high_rates"] and sector == "fii_papel":
        score = min(1.0, score + 0.2)

    return score


def build_ranking(
    momentum_scores: dict,
    sector_rotation: dict,
    regime: dict,
    history: list[dict],
    n_top: int = 5,
) -> list[dict]:
    """
    Compõe score final e retorna top-N ativos rankeados.
    Score = 40% momentum + 25% novelty + 20% regime_fit + 15% sector_rotation
    """
    results = []
    sector_scores_norm = {}
    if sector_rotation:
        max_s = max(sector_rotation.values())
        min_s = min(sector_rotation.values())
        rng   = max_s - min_s if max_s != min_s else 1
        sector_scores_norm = {k: (v - min_s) / rng for k, v in sector_rotation.items()}

    for ticker, m in momentum_scores.items():
        sector   = UNIVERSE.get(ticker, {}).get("sector", "outro")
        novelty  = calculate_novelty(ticker, history)
        reg_fit  = regime_fit_score(ticker, regime)
        sect_scr = sector_scores_norm.get(sector, 0.5)

        final_score = (
            0.40 * m["momentum"] +
            0.25 * novelty       +
            0.20 * reg_fit       +
            0.15 * sect_scr
        )

        r1d_pct  = f"{m['r1d']*100:+.2f}%"  if m["r1d"]  else "N/D"
        r5d_pct  = f"{m['r5d']*100:+.2f}%"  if m["r5d"]  else "N/D"
        r21d_pct = f"{m['r21d']*100:+.2f}%"  if m["r21d"] else "N/D"

        results.append({
            "ticker":       ticker,
            "ticker_clean": ticker.replace(".SA", ""),
            "name":         UNIVERSE[ticker]["name"],
            "sector":       UNIVERSE[ticker]["sector"],
            "price":        f"R$ {m['price']:.2f}".replace(".", ","),
            "r1d":          r1d_pct,
            "r5d":          r5d_pct,
            "r21d":         r21d_pct,
            "momentum":     round(m["momentum"], 3),
            "novelty":      round(novelty, 2),
            "regime_fit":   round(reg_fit, 2),
            "sector_score": round(sect_scr, 2),
            "final_score":  round(final_score, 3),
        })

    results.sort(key=lambda x: x["final_score"], reverse=True)

    # Garantir diversidade setorial no top-N: máx 2 do mesmo setor
    top, seen_sectors = [], {}
    for asset in results:
        s = asset["sector"]
        if seen_sectors.get(s, 0) < 2:
            top.append(asset)
            seen_sectors[s] = seen_sectors.get(s, 0) + 1
        if len(top) == n_top:
            break

    return top


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — LLM INTERPRETER
# ═══════════════════════════════════════════════════════════════════════════════

def build_prompt(macro: dict, regime: dict, ranking: list[dict],
                 sector_rotation: dict, news: list[str], history: list[dict]) -> str:

    # Formata ranking para o prompt
    ranking_text = ""
    total_budget = 100
    values = _allocate_budget(ranking[:3], regime)
    for i, asset in enumerate(ranking[:3]):
        val = values[i]
        ranking_text += (
            f"  #{i+1} {asset['ticker_clean']} — {asset['name']} ({asset['sector']})\n"
            f"      Preço: {asset['price']} | 1d: {asset['r1d']} | 5d: {asset['r5d']} | 21d: {asset['r21d']}\n"
            f"      Score: {asset['final_score']} | Novelty: {asset['novelty']} | RegimeFit: {asset['regime_fit']}\n"
            f"      Alocar: R$ {val}\n\n"
        )

    # Top setores
    top_sectors = sorted(sector_rotation.items(), key=lambda x: x[1], reverse=True)[:3]
    sector_text = " | ".join(f"{s}: {v:.2f}" for s, v in top_sectors)

    # Ativos recentes para contexto
    recent_tickers = []
    for entry in history[-5:]:
        for opp in entry.get("opportunities", []):
            t = opp.get("ticker", "")
            if t and t not in recent_tickers:
                recent_tickers.append(t)

    # Headlines
    news_text = "\n".join(f"  • {h}" for h in news) if news else "  • Sem notícias disponíveis"

    # Delta vs ontem
    delta_text = ""
    if history:
        last = history[-1]
        last_ibov = last.get("macro", {}).get("ibov", "N/D")
        delta_text = f"IBOV ontem: {last_ibov} | Hoje: {macro.get('ibov', 'N/D')}"

    return f"""Você é o analista-narrador de um sistema quantitativo de investimentos.

## DADOS CALCULADOS PELO SISTEMA (NÃO ALTERE)

Estes dados foram calculados deterministicamente. Sua função é NARRAR e EXPLICAR, não DECIDIR.

### Macro Real ({today_str})
SELIC: {macro['selic']} (tendência: {macro.get('selic_trend','N/D')})
IPCA 12m: {macro['ipca']}
Dólar PTAX: {macro['dolar']}
IBOV: {macro['ibov']} ({macro.get('ibov_var','N/D')})
{delta_text}

### Regime Detectado
Regime 21d: {regime['regime']} | Perfil do dia: {regime['perfil']}
Juros elevados: {'Sim' if regime['high_rates'] else 'Não'}
Interpretação: {'Favorece defensivos e FIIs de papel' if regime['high_rates'] and regime['perfil'] != 'RISK_ON' else 'Favorece ativos de risco' if regime['perfil'] == 'RISK_ON' else 'Cenário balanceado'}

### Rotação Setorial (5d)
{sector_text}

### Rankings Quantitativos — TOP 3 SELECIONADOS PELO SISTEMA
{ranking_text}
### Headlines do Dia
{news_text}

### Ativos Recomendados Recentemente (evitar repetir narrativa)
{', '.join(recent_tickers[-6:]) if recent_tickers else 'Nenhum'}

---

## SUA MISSÃO

Gere o JSON abaixo. Os campos MARCADOS como [FIXO] devem ser copiados literalmente.
Para campos de texto, escreva análise DENSA, ESPECÍFICA e baseada nos dados acima.
NÃO invente preços, yields ou valuations não fornecidos.
Se não souber um número exato, use intervalo ou omita.

Retorne APENAS o JSON, sem markdown:

{{
  "date": "{today_str}",
  "regime": {{
    "label": "{regime['regime']} / {regime['perfil']}",
    "summary": "1 frase descrevendo o regime atual e o que ele significa para investidores hoje"
  }},
  "opportunities": [
    {{
      "ticker": "{ranking[0]['ticker_clean'] if ranking else 'N/D'}",
      "name": "{ranking[0]['name'] if ranking else 'N/D'}",
      "value": "{values[0] if values else 40}",
      "price": "{ranking[0]['price'] if ranking else 'N/D'}",
      "score": "{ranking[0]['final_score'] if ranking else 0}",
      "reason": "Tese específica em 2 frases: por que este ativo hoje? Use momentum ({ranking[0]['r5d'] if ranking else 'N/D'} em 5d), setor ({ranking[0]['sector'] if ranking else 'N/D'}) e regime ({regime['perfil']})",
      "potential": "Estimativa com base no score e regime — sem inventar números absolutos",
      "risk": "Baixo|Médio|Alto",
      "horizon": "prazo adequado ao regime"
    }},
    {{
      "ticker": "{ranking[1]['ticker_clean'] if len(ranking)>1 else 'N/D'}",
      "name": "{ranking[1]['name'] if len(ranking)>1 else 'N/D'}",
      "value": "{values[1] if len(values)>1 else 35}",
      "price": "{ranking[1]['price'] if len(ranking)>1 else 'N/D'}",
      "score": "{ranking[1]['final_score'] if len(ranking)>1 else 0}",
      "reason": "Tese específica baseada nos dados reais fornecidos",
      "potential": "Estimativa baseada no contexto",
      "risk": "Baixo|Médio|Alto",
      "horizon": "prazo adequado"
    }},
    {{
      "ticker": "{ranking[2]['ticker_clean'] if len(ranking)>2 else 'N/D'}",
      "name": "{ranking[2]['name'] if len(ranking)>2 else 'N/D'}",
      "value": "{values[2] if len(values)>2 else 25}",
      "price": "{ranking[2]['price'] if len(ranking)>2 else 'N/D'}",
      "score": "{ranking[2]['final_score'] if len(ranking)>2 else 0}",
      "reason": "Tese específica baseada nos dados reais fornecidos",
      "potential": "Estimativa baseada no contexto",
      "risk": "Baixo|Médio|Alto",
      "horizon": "prazo adequado"
    }}
  ],
  "macro_indicators": [
    {{"label": "SELIC", "value": "{macro['selic']}", "sub": "tendência: {macro.get('selic_trend','N/D')}", "trend": "neutral"}},
    {{"label": "IPCA 12m", "value": "{macro['ipca']}", "sub": "acumulado 12 meses", "trend": "down"}},
    {{"label": "Dólar", "value": "{macro['dolar']}", "sub": "PTAX venda", "trend": "neutral"}},
    {{"label": "IBOV", "value": "{macro['ibov']}", "sub": "{macro.get('ibov_var','N/D')} hoje", "trend": "{'up' if macro.get('ibov_var','0%').startswith('+') else 'down'}"}}
  ],
  "macro_summary": "3 linhas densas conectando SELIC {macro['selic']}, IPCA {macro['ipca']}, IBOV {macro.get('ibov_var','N/D')} e regime {regime['perfil']}. Cite números reais. Explique impacto prático para quem vai aportar R$100 hoje.",
  "analysts": {{
    "enter": ["Ativo específico do ranking — motivo quantitativo"],
    "reinforce": ["Ativo com bom momentum 21d — motivo"],
    "avoid": ["Setor ou ativo com momentum negativo — dado específico"],
    "realize": ["Ativo que teve alta recente e pode estar sobrecomprado"]
  }},
  "bbas3_metrics": [
    {{"label": "Cotação", "value": "{macro.get('bbas3_price', 'N/D')}", "trend": "neutral"}},
    {{"label": "Var. dia", "value": "{macro.get('bbas3_var', 'N/D')}", "trend": "neutral"}},
    {{"label": "Alvo consenso", "value": "R$ 26,02", "trend": "up"}},
    {{"label": "Upside", "value": "CALCULE: (26.02 / preco_atual - 1) * 100", "trend": "up"}},
    {{"label": "Div. Yield", "value": "~4,1% (base lucro revisado 2026)", "trend": "neutral"}},
    {{"label": "Consenso", "value": "Neutro — 9 de 13 analistas", "trend": "neutral"}}
  ],
  "bbas3_strategy": "Aguardar|Comprar|Reforçar|Realizar",
  "bbas3_summary": "Status BBAS3 hoje: cite cotação real {macro.get('bbas3_price','N/D')}, variação {macro.get('bbas3_var','N/D')}, contexto pós-1T26 (lucro -53%, inadimplência agro 6.22%), próximo gatilho (resultado 2T26 em agosto). 3-4 frases.",
  "executive_summary": {{
    "smartest_move": "Ação executável hoje — cite ativo específico do ranking e valor R$X",
    "biggest_risk": "Risco concreto baseado no regime {regime['regime']} e dados do dia",
    "best_opportunity": "Assimetria risco-retorno com dados numéricos reais",
    "common_mistake": "Erro comportamental específico para o cenário de hoje"
  }}
}}"""


def _allocate_budget(top3: list[dict], regime: dict) -> list[int]:
    """Aloca R$100 proporcionalmente ao score dos top 3."""
    if not top3:
        return [40, 35, 25]
    scores = [a["final_score"] for a in top3]
    total  = sum(scores)
    raw    = [round(s / total * 100) for s in scores]
    # Ajusta para somar exatamente 100
    diff = 100 - sum(raw)
    raw[0] += diff
    return raw


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3B — OpenAI Call
# ═══════════════════════════════════════════════════════════════════════════════

def call_openai(prompt: str) -> str:
    # Temperatura dinâmica: mais alta no início da semana (mais variação de mercado)
    temperature = 0.5 if weekday == 0 else 0.35

    payload = json.dumps({
        "model": "gpt-4o",
        "max_tokens": 2500,
        "temperature": temperature,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Você é o analista-narrador de um sistema financeiro quantitativo. "
                    "Sua função é EXCLUSIVAMENTE escrever textos explicativos para dados já calculados. "
                    "NUNCA substitua dados numéricos fornecidos por estimativas próprias. "
                    "NUNCA invente preços, yields ou valuations. "
                    "Se um campo pedir cálculo, faça o cálculo com os números fornecidos. "
                    "Retorne apenas JSON válido, sem markdown."
                )
            },
            {"role": "user", "content": prompt}
        ]
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_and_fix(data: dict, macro: dict, ranking: list[dict]) -> dict:
    """Sobrescreve campos numéricos críticos com valores reais."""

    data["date"] = today_str

    # Garante macro_indicators com valores reais
    macro_map = {
        "SELIC":    macro.get("selic", "N/D"),
        "IPCA 12m": macro.get("ipca",  "N/D"),
        "Dólar":    macro.get("dolar", "N/D"),
        "IBOV":     macro.get("ibov",  "N/D"),
    }
    for m in data.get("macro_indicators", []):
        label = m.get("label", "")
        if label in macro_map:
            m["value"] = macro_map[label]

    # Garante BBAS3 com valores reais
    bbas3_map = {
        "Cotação":  macro.get("bbas3_price", "N/D"),
        "Var. dia": macro.get("bbas3_var",   "N/D"),
    }
    for m in data.get("bbas3_metrics", []):
        label = m.get("label", "")
        if label in bbas3_map and bbas3_map[label] != "N/D":
            m["value"] = bbas3_map[label]
        # Calcula upside real
        if label == "Upside" and macro.get("bbas3_price_raw"):
            try:
                upside = (26.02 / macro["bbas3_price_raw"] - 1) * 100
                m["value"] = f"+{upside:.1f}%"
            except Exception:
                pass

    # Garante que tickers nas oportunidades batem com o ranking
    if ranking:
        for i, opp in enumerate(data.get("opportunities", [])):
            if i < len(ranking):
                opp["ticker"] = ranking[i]["ticker_clean"]
                opp["price"]  = ranking[i]["price"]
                opp["score"]  = str(ranking[i]["final_score"])

    # Verifica diversidade de setores
    sectors_in_opps = set()
    for opp in data.get("opportunities", []):
        ticker_sa = opp.get("ticker", "") + ".SA"
        s = UNIVERSE.get(ticker_sa, {}).get("sector", "")
        sectors_in_opps.add(s)

    data["_meta"] = {
        "generated_at": datetime.now(TZ_BR).isoformat(),
        "regime":       data.get("regime", {}).get("label", "N/D"),
        "sectors":      list(sectors_in_opps),
        "diversity_ok": len(sectors_in_opps) >= 2,
        "pipeline":     "v2.0-quant"
    }

    return data


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def send_whatsapp(message: str):
    encoded = urllib.parse.quote(message)
    url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_NUMBER}&text={encoded}&apikey={CALLMEBOT_APIKEY}"
    print(f"  Enviando WhatsApp para {WHATSAPP_NUMBER}...")
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                body = r.read().decode()
                print(f"  CallMeBot: {body[:120]}")
                if "queued" in body.lower():
                    print("  WhatsApp enviado com sucesso.")
                    return
        except Exception as e:
            print(f"  Tentativa {attempt} falhou: {e}")
        if attempt < 3:
            time.sleep(3)
    print("  WARN: WhatsApp pode não ter sido entregue.")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'='*60}")
    print(f"CIO Daily v2.0 — {today_str}")
    print(f"{'='*60}\n")

    # ── Stage 1: Data ─────────────────────────────────────────
    print("[1/5] Coletando dados...")
    macro   = fetch_macro()
    prices  = fetch_prices()
    news    = fetch_news()
    history = load_history()

    # Busca BBAS3 separadamente para seção dedicada
    bbas3_data = prices.get("BBAS3.SA")
    if bbas3_data and len(bbas3_data) >= 2:
        p     = bbas3_data[-1]
        r     = safe_div(p, bbas3_data[-2]) or 0
        macro["bbas3_price"]     = f"R$ {p:.2f}".replace(".", ",")
        macro["bbas3_price_raw"] = p
        macro["bbas3_var"]       = f"{'+' if r >= 0 else ''}{r*100:.2f}%"

    print(f"  Macro: SELIC={macro['selic']} IPCA={macro['ipca']} "
          f"Dólar={macro['dolar']} IBOV={macro['ibov']} ({macro.get('ibov_var','N/D')})")
    print(f"  Notícias: {len(news)} headlines | Histórico: {len(history)} dias")

    # ── Stage 2: Quant Engine ─────────────────────────────────
    print("\n[2/5] Rodando quant engine...")
    regime          = detect_regime(macro)
    momentum_scores = calculate_momentum(prices)
    sector_rotation = calculate_sector_rotation(momentum_scores)
    ranking         = build_ranking(momentum_scores, sector_rotation, regime, history)

    print(f"  Regime: {regime['regime']} / {regime['perfil']}")
    print(f"  Setores em destaque: {sorted(sector_rotation, key=sector_rotation.get, reverse=True)[:3]}")
    print(f"  Top 3: {[a['ticker_clean'] for a in ranking[:3]]}")

    # ── Stage 3: LLM ──────────────────────────────────────────
    print("\n[3/5] Chamando GPT-4o (narrador)...")
    prompt = build_prompt(macro, regime, ranking, sector_rotation, news, history)
    raw    = call_openai(prompt)

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"JSON não encontrado: {raw[:300]}")
    data = json.loads(match.group())

    # ── Stage 4: Validation ───────────────────────────────────
    print("\n[4/5] Validando e corrigindo...")
    data = validate_and_fix(data, macro, ranking)
    meta = data.get("_meta", {})
    print(f"  Diversidade setorial: {meta.get('sectors')} — OK: {meta.get('diversity_ok')}")

    # ── Stage 5: Output ───────────────────────────────────────
    print("\n[5/5] Salvando e notificando...")

    with open("briefing.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("  briefing.json salvo.")

    history_entry = {
        "date":          today_iso,
        "regime":        regime,
        "opportunities": data.get("opportunities", []),
        "macro":         {"selic": macro["selic"], "ipca": macro["ipca"],
                          "dolar": macro["dolar"], "ibov": macro["ibov"]},
    }
    save_history(history, history_entry)
    print("  Histórico atualizado.")

    # WhatsApp com resumo executivo real
    top1  = data.get("opportunities", [{}])[0]
    move  = data.get("executive_summary", {}).get("smartest_move", "")
    msg = (
        f"Bom dia! Briefing {today_str}\n\n"
        f"Regime: {regime['regime']} / {regime['perfil']}\n"
        f"IBOV: {macro['ibov']} ({macro.get('ibov_var','N/D')}) | "
        f"SELIC: {macro['selic']} | Dolar: {macro['dolar']}\n\n"
        f"Top pick: {top1.get('ticker','?')} R${top1.get('value','?')}\n"
        f"{move[:80]}...\n\n"
        f"Ver completo: {SITE_URL}"
    )
    send_whatsapp(msg)

    print(f"\n{'='*60}")
    print(f"Briefing gerado com sucesso — pipeline v2.0")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
