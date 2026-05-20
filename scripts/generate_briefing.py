"""
Gera o briefing diário de investimentos via Claude API e salva em briefing.json.
Roda via GitHub Actions todo dia às 8h (Brasília).
"""
import os
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
WHATSAPP_NUMBER   = os.environ["WHATSAPP_NUMBER"]    # ex: +5511999999999
CALLMEBOT_APIKEY  = os.environ["CALLMEBOT_APIKEY"]   # obtido no CallMeBot
GITHUB_PAGES_URL  = os.environ["GITHUB_PAGES_URL"]   # ex: https://seugithub.github.io/investment-briefing

today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")

PROMPT = f"""Você é um CIO (Chief Investment Officer) gerando um briefing diário de investimentos para {today}.

Retorne APENAS um JSON válido (sem markdown, sem explicações) com esta estrutura exata:

{{
  "date": "{today}",
  "opportunities": [
    {{
      "ticker": "TICKER",
      "name": "Nome do ativo",
      "value": "40",
      "reason": "Motivo objetivo em 1-2 frases com dados reais",
      "potential": "X% a.a.",
      "risk": "Baixo|Médio|Alto",
      "horizon": "prazo"
    }}
  ],
  "macro_indicators": [
    {{"label": "SELIC", "value": "XX%", "sub": "contexto", "trend": "up|down|neutral"}},
    {{"label": "IPCA", "value": "X,XX%", "sub": "contexto", "trend": "up|down|neutral"}},
    {{"label": "Dólar", "value": "R$ X,XX", "sub": "contexto", "trend": "up|down|neutral"}},
    {{"label": "IBOV", "value": "XXX.XXX", "sub": "variação", "trend": "up|down|neutral"}}
  ],
  "macro_summary": "3 linhas sobre cenário macro focado em investimentos hoje",
  "analysts": {{
    "enter": ["Ativo — motivo"],
    "reinforce": ["Ativo — motivo"],
    "avoid": ["Ativo — motivo"],
    "realize": ["Ativo — motivo"]
  }},
  "bbas3_metrics": [
    {{"label": "Cotação", "value": "R$ XX,XX", "trend": "up|down|neutral"}},
    {{"label": "Var. 12m", "value": "X%", "trend": "up|down|neutral"}},
    {{"label": "Alvo consenso", "value": "R$ XX,XX", "trend": "up"}},
    {{"label": "Upside", "value": "+X%", "trend": "up"}},
    {{"label": "Div. Yield", "value": "X,X%", "trend": "neutral"}},
    {{"label": "Consenso", "value": "Neutro|Compra|Venda", "trend": "neutral"}}
  ],
  "bbas3_strategy": "Aguardar|Comprar|Reforçar|Realizar",
  "bbas3_summary": "Resumo executivo de BBAS3 com situação atual, riscos e gatilhos. 3-4 frases.",
  "executive_summary": {{
    "smartest_move": "Movimento mais inteligente hoje — específico e acionável",
    "biggest_risk": "Maior risco do momento — concreto",
    "best_opportunity": "Melhor oportunidade risco-retorno — com números",
    "common_mistake": "Erro mais comum do investidor hoje — direto"
  }}
}}

REGRAS:
- Use dados reais e recentes do mercado brasileiro
- Nunca invente dados — se não souber, use a última informação disponível
- O total das oportunidades deve somar R$100
- Seja direto e acionável
- Considere: valuation, juros, inflação, fluxo institucional, dividendos, risco-retorno, momentum, macro"""


def call_claude(prompt: str) -> str:
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    return body["content"][0]["text"]


def send_whatsapp(message: str):
    encoded = urllib.parse.quote(message)
    url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_NUMBER}&text={encoded}&apikey={CALLMEBOT_APIKEY}"
    try:
        urllib.request.urlopen(url)
        print("WhatsApp enviado.")
    except Exception as e:
        print(f"Erro WhatsApp: {e}")


def main():
    print(f"Gerando briefing para {today}...")
    raw = call_claude(PROMPT)

    # Extrai JSON mesmo se vier com texto ao redor
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"JSON não encontrado na resposta: {raw[:300]}")

    data = json.loads(match.group())

    with open("briefing.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("briefing.json salvo.")

    msg = (
        f"📊 *CIO Daily — {today}*\n\n"
        f"Bom dia! Seu briefing de investimentos está pronto.\n\n"
        f"🔗 {GITHUB_PAGES_URL}\n\n"
        f"_Acesse para ver oportunidades, cenário macro e status BBAS3._"
    )
    send_whatsapp(msg)


if __name__ == "__main__":
    main()
