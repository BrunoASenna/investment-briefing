"""
Gera o briefing diário de investimentos via OpenAI API e salva em briefing.json.
Busca dados reais de APIs públicas antes de chamar o GPT para evitar alucinações.
Roda via GitHub Actions todo dia às 8h (Brasília).
"""
import os
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
WHATSAPP_NUMBER  = os.environ["WHATSAPP_NUMBER"]
CALLMEBOT_APIKEY = os.environ["CALLMEBOT_APIKEY"]
SITE_URL         = os.environ["SITE_URL"]

today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")


# ─── Busca de dados reais ────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 8) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"WARN fetch({url}): {e}")
        return None


def get_selic() -> str:
    """Taxa Selic Over anualizada — Banco Central série 1178."""
    data = fetch("https://api.bcb.gov.br/dados/serie/bcdata.sgs.1178/dados/ultimos/1?formato=json")
    if data:
        return f"{float(data[0]['valor']):.2f}%".replace(".", ",")
    return "N/D"


def get_ipca_12m() -> str:
    """IPCA acumulado 12 meses — Banco Central série 13522."""
    data = fetch("https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/1?formato=json")
    if data:
        return f"{float(data[0]['valor']):.2f}%".replace(".", ",")
    return "N/D"


def get_dolar() -> str:
    """Dólar PTAX venda — Banco Central (fonte primária) com fallback AwesomeAPI."""
    from datetime import date, timedelta
    # BCB PTAX — tenta últimos 5 dias úteis
    for delta in range(5):
        d = (date.today() - timedelta(days=delta)).strftime("%m-%d-%Y")
        data = fetch(f"https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarDia(dataCotacao=@dataCotacao)?@dataCotacao='{d}'&$format=json&$top=1")
        if data and data.get("value"):
            val = float(data["value"][0]["cotacaoVenda"])
            return f"R$ {val:.2f}".replace(".", ",")
    # Fallback: AwesomeAPI
    data = fetch("https://economia.awesomeapi.com.br/json/last/USD-BRL")
    if data and "USDBRL" in data:
        val = float(data["USDBRL"]["bid"])
        return f"R$ {val:.2f}".replace(".", ",")
    return "N/D"


def get_ibov() -> tuple[str, str]:
    """IBOV via Yahoo Finance — retorna (valor, variacao_pct)."""
    data = fetch("https://query1.finance.yahoo.com/v8/finance/chart/%5EBVSP?interval=1d&range=2d")
    try:
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            prev, last = closes[-2], closes[-1]
            var = ((last - prev) / prev) * 100
            sinal = "+" if var >= 0 else ""
            return f"{last:,.0f}".replace(",", "."), f"{sinal}{var:.2f}%"
        elif closes:
            return f"{closes[-1]:,.0f}".replace(",", "."), "N/D"
    except Exception as e:
        print(f"WARN IBOV: {e}")
    return "N/D", "N/D"


def get_bbas3() -> tuple[str, str]:
    """BBAS3 cotação e variação via Yahoo Finance."""
    data = fetch("https://query1.finance.yahoo.com/v8/finance/chart/BBAS3.SA?interval=1d&range=2d")
    try:
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            prev, last = closes[-2], closes[-1]
            var = ((last - prev) / prev) * 100
            sinal = "+" if var >= 0 else ""
            return f"R$ {last:.2f}".replace(".", ","), f"{sinal}{var:.2f}%"
        elif closes:
            return f"R$ {closes[-1]:.2f}".replace(".", ","), "N/D"
    except Exception as e:
        print(f"WARN BBAS3: {e}")
    return "N/D", "N/D"


# ─── Prompt ─────────────────────────────────────────────────────────────────

def build_prompt(dados: dict) -> str:
    return f"""Você é um CIO (Chief Investment Officer) gerando um briefing diário de investimentos para {today}.

## DADOS REAIS COLETADOS AGORA — USE EXATAMENTE ESTES VALORES

Os dados abaixo foram buscados em tempo real de APIs oficiais (Banco Central, Yahoo Finance).
Você DEVE usar esses valores no JSON. Nunca os substitua por estimativas ou memória de treinamento.

| Indicador     | Valor real         |
|---------------|--------------------|
| SELIC (Over)  | {dados['selic']}   |
| IPCA 12m      | {dados['ipca']}    |
| Dólar (PTAX)  | {dados['dolar']}   |
| IBOVESPA      | {dados['ibov']} ({dados['ibov_var']}) |
| BBAS3         | {dados['bbas3']} ({dados['bbas3_var']}) |

## REGRAS ABSOLUTAS

1. Os valores macro acima são FIXOS — copie-os literalmente no JSON
2. Para ativos e análises qualitativas: use seu conhecimento mais recente
3. Se não tiver certeza de um número de ativo → use intervalo ("R$ 20-22") ou omita
4. NUNCA invente cotações, preços-alvo ou yields sem base
5. O total das oportunidades deve somar exatamente R$ 100
6. Seja direto, acionável, sem texto genérico

## ESTRUTURA DE RESPOSTA

Retorne APENAS o JSON abaixo, sem markdown, sem texto antes ou depois:

{{
  "date": "{today}",
  "opportunities": [
    {{
      "ticker": "TICKER",
      "name": "Nome completo do ativo",
      "value": "40",
      "reason": "Motivo objetivo com base em dados reais — sem invenções",
      "potential": "X% a.a. ou +X% em Y meses",
      "risk": "Baixo|Médio|Alto",
      "horizon": "prazo sugerido"
    }}
  ],
  "macro_indicators": [
    {{"label": "SELIC", "value": "{dados['selic']}", "sub": "taxa Over anualizada", "trend": "neutral"}},
    {{"label": "IPCA 12m", "value": "{dados['ipca']}", "sub": "acumulado 12 meses", "trend": "down"}},
    {{"label": "Dólar", "value": "{dados['dolar']}", "sub": "PTAX venda", "trend": "neutral"}},
    {{"label": "IBOV", "value": "{dados['ibov']}", "sub": "{dados['ibov_var']} hoje", "trend": "PREENCHA: up|down|neutral"}}
  ],
  "macro_summary": "3 linhas densas sobre o cenário macro de hoje e impacto em investimentos. Cite os números reais acima.",
  "analysts": {{
    "enter": ["Ativo — motivo específico com contexto"],
    "reinforce": ["Ativo — motivo específico"],
    "avoid": ["Ativo — motivo de risco concreto"],
    "realize": ["Ativo — motivo de realização"]
  }},
  "bbas3_metrics": [
    {{"label": "Cotação", "value": "{dados['bbas3']}", "trend": "PREENCHA"}},
    {{"label": "Var. dia", "value": "{dados['bbas3_var']}", "trend": "PREENCHA"}},
    {{"label": "Alvo consenso", "value": "R$ 26,02", "trend": "up"}},
    {{"label": "Upside", "value": "CALCULE com base na cotação real acima", "trend": "up"}},
    {{"label": "Div. Yield", "value": "~4,1% (base lucro revisado)", "trend": "neutral"}},
    {{"label": "Consenso", "value": "Neutro (9 analistas)", "trend": "neutral"}}
  ],
  "bbas3_strategy": "Aguardar|Comprar|Reforçar|Realizar",
  "bbas3_summary": "Análise de BBAS3 com: situação atual (lucro 1T26 -53%, inadimplência agro 6,22%), gatilho próximo (resultado 2T26 em agosto), estratégia recomendada. 3-4 frases densas.",
  "executive_summary": {{
    "smartest_move": "Ação específica e executável hoje — com ativo e valor",
    "biggest_risk": "Risco concreto com dados — não genérico",
    "best_opportunity": "Oportunidade com números reais de risco-retorno",
    "common_mistake": "Erro comportamental ou técnico comum hoje — direto"
  }}
}}"""


# ─── OpenAI ─────────────────────────────────────────────────────────────────

def call_openai(prompt: str) -> str:
    payload = json.dumps({
        "model": "gpt-4o",
        "max_tokens": 2048,
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Você é um analista financeiro sênior especializado no mercado brasileiro. "
                    "Sua única função é retornar JSON estruturado com análises precisas. "
                    "Nunca invente dados numéricos. Quando dados reais são fornecidos no prompt, "
                    "use-os literalmente. Para análises qualitativas, use conhecimento atualizado."
                )
            },
            {"role": "user", "content": prompt}
        ]
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


# ─── WhatsApp ────────────────────────────────────────────────────────────────

def send_whatsapp(message: str):
    encoded = urllib.parse.quote(message)
    url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_NUMBER}&text={encoded}&apikey={CALLMEBOT_APIKEY}"
    print(f"Enviando WhatsApp para {WHATSAPP_NUMBER}...")
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                body = r.read().decode()
                print(f"CallMeBot resposta: {body[:200]}")
                if "queued" in body.lower() or "message" in body.lower():
                    print("WhatsApp enviado com sucesso.")
                    return
        except Exception as e:
            print(f"Tentativa {attempt} falhou: {e}")
        if attempt < 3:
            import time; time.sleep(3)
    print("WARN: WhatsApp pode não ter sido entregue.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"Gerando briefing para {today}...")

    # 1. Coleta dados reais
    print("Buscando dados de mercado...")
    ibov, ibov_var   = get_ibov()
    bbas3, bbas3_var = get_bbas3()
    dados = {
        "selic":     get_selic(),
        "ipca":      get_ipca_12m(),
        "dolar":     get_dolar(),
        "ibov":      ibov,
        "ibov_var":  ibov_var,
        "bbas3":     bbas3,
        "bbas3_var": bbas3_var,
    }
    print(f"Dados coletados: {dados}")

    # 2. Gera briefing com GPT injetando dados reais
    print("Chamando OpenAI...")
    prompt = build_prompt(dados)
    raw = call_openai(prompt)

    # 3. Extrai e valida JSON
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"JSON não encontrado: {raw[:400]}")
    data = json.loads(match.group())

    # 4. Garante que os dados reais não foram sobrescritos
    data["date"] = today
    for m in data.get("macro_indicators", []):
        if m["label"] == "SELIC":
            m["value"] = dados["selic"]
        elif m["label"] in ("IPCA 12m", "IPCA"):
            m["value"] = dados["ipca"]
        elif m["label"] == "Dólar":
            m["value"] = dados["dolar"]
        elif m["label"] == "IBOV":
            m["value"] = dados["ibov"]
    for m in data.get("bbas3_metrics", []):
        if m["label"] == "Cotação":
            m["value"] = dados["bbas3"]
        elif m["label"] in ("Var. dia", "Var. 12m"):
            m["value"] = dados["bbas3_var"]

    # 5. Salva
    with open("briefing.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("briefing.json salvo.")

    # 6. Envia WhatsApp
    msg = (
        f"Bom dia! Briefing de {today} pronto.\n\n"
        f"SELIC {dados['selic']} | IBOV {dados['ibov']} ({dados['ibov_var']}) | "
        f"Dolar {dados['dolar']}\n\n"
        f"Acesse: {SITE_URL}"
    )
    send_whatsapp(msg)


if __name__ == "__main__":
    main()
