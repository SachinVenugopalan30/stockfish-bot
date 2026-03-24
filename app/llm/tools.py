"""
Agent tool definitions used by all LLM providers that support tool calling.
The LLM autonomously decides which tools to call before submitting a decision.
"""

AGENT_TOOLS = [
    {
        "name": "get_price_history",
        "description": (
            "Get recent price data for a ticker. "
            "Use this to see price trends and momentum before deciding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol (e.g. NVDA)"},
                "hours": {"type": "integer", "description": "How many hours of history to fetch (default 24)", "default": 24},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_technical_indicators",
        "description": (
            "Get computed technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA) for a ticker. "
            "RSI > 70 means overbought (sell signal if you hold). RSI < 30 means oversold (buy signal). "
            "MACD histogram > 0 is bullish, < 0 is bearish. "
            "Price above upper Bollinger Band suggests sell; below lower band suggests buy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_sentiment",
        "description": (
            "Get recent FinBERT sentiment scores for a ticker from news and Reddit. "
            "Returns scores from -1 (very negative) to +1 (very positive)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"},
                "limit": {"type": "integer", "description": "Number of recent scores to return (default 5)", "default": 5},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_recent_news",
        "description": "Get recent news headlines mentioning this ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"},
                "limit": {"type": "integer", "description": "Number of headlines to return (default 5)", "default": 5},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "check_portfolio",
        "description": "Get current portfolio state: holdings, cash balance, invested capital, and per-position P&L.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web for recent information about a stock, company, or market event. "
            "Use this to find news, earnings reports, analyst opinions, or macro events "
            "that may not be in the local database yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'NVDA earnings Q1 2026 results', 'TSLA recall news')",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "submit_research_report",
        "description": (
            "Submit your completed research report after using the available tools. "
            "Call this LAST, after you have investigated price history, indicators, sentiment, news, and web search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "price_summary": {
                    "type": "string",
                    "description": "Summary of recent price action and momentum",
                },
                "technical_summary": {
                    "type": "string",
                    "description": "Summary of technical indicators (RSI, MACD, Bollinger)",
                },
                "sentiment_summary": {
                    "type": "string",
                    "description": "Summary of FinBERT sentiment scores",
                },
                "news_summary": {
                    "type": "string",
                    "description": "Summary of recent news headlines",
                },
                "web_search_summary": {
                    "type": "string",
                    "description": "Summary of web search findings (use 'no web search performed' if skipped)",
                },
                "overall_assessment": {
                    "type": "string",
                    "description": "Overall bull/bear/neutral assessment with key reasoning",
                },
            },
            "required": [
                "price_summary",
                "technical_summary",
                "sentiment_summary",
                "news_summary",
                "web_search_summary",
                "overall_assessment",
            ],
        },
    },
    {
        "name": "submit_decision",
        "description": (
            "Submit your final trading decision. "
            "Call this AFTER you have gathered enough information using the other tools. "
            "Always call at least one research tool before submitting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["buy", "sell", "hold"],
                    "description": "Trading action",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of shares (0 for hold)",
                    "minimum": 0,
                },
                "reasoning": {
                    "type": "string",
                    "description": "One sentence explaining your decision based on what you found",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence from 0.0 (uncertain) to 1.0 (very confident)",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            "required": ["action", "quantity", "reasoning", "confidence"],
        },
    },
]

# Lookup map for quick access
AGENT_TOOLS_BY_NAME = {t["name"]: t for t in AGENT_TOOLS}
