"""
pipeline.py — AgentPipeline orchestrates the 3-agent Research → Risk → Decision flow.

Research Agent: uses the full agentic tool loop (Claude/OpenAI) or enriched single-shot (Ollama/Gemini)
Risk Agent:     single-shot LLM call with research report + pre-computed risk context
Decision Agent: single-shot LLM call, returns final Decision
"""
import asyncio
import json
import logging
from typing import Optional

from app.analysis.service import TechnicalAnalysisService
from app.config import Settings
from app.engine.portfolio import PortfolioManager
from app.llm.agent_prompts import (
    DECISION_AGENT_SYSTEM,
    RESEARCH_AGENT_SYSTEM,
    RISK_AGENT_SYSTEM,
    build_decision_message,
    build_research_message,
    build_risk_message,
)
from app.llm.base import Decision, LLMProvider, TradeContext
from app.llm.risk_context import build_risk_context
from app.llm.schemas import ResearchReportSchema, RiskAssessmentSchema
from app.llm.tool_executor import ToolExecutor
from app.llm.tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

# Tools available to the Research Agent (all except submit_decision)
_RESEARCH_TOOLS = [t for t in AGENT_TOOLS if t["name"] != "submit_decision"]
_MAX_RESEARCH_ITERATIONS = 20  # prevent infinite loop if model never submits report


def _report_to_text(report: ResearchReportSchema) -> str:
    return (
        f"PRICE: {report.price_summary}\n"
        f"TECHNICALS: {report.technical_summary}\n"
        f"SENTIMENT: {report.sentiment_summary}\n"
        f"NEWS: {report.news_summary}\n"
        f"WEB SEARCH: {report.web_search_summary}\n"
        f"OVERALL: {report.overall_assessment}"
    )


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from LLM output."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            inner = parts[1]
            # Drop optional language tag line (e.g. "json\n")
            if "\n" in inner:
                inner = inner[inner.index("\n") + 1:]
            raw = inner
    return raw.strip()


class AgentPipeline:
    def __init__(
        self,
        llm: LLMProvider,
        settings: Settings,
        portfolio: PortfolioManager,
        ta_service: TechnicalAnalysisService,
    ):
        self.llm = llm
        self.settings = settings
        self.portfolio = portfolio
        self.ta_service = ta_service

    async def run(
        self,
        context: TradeContext,
        tool_executor: ToolExecutor,
        session,
    ) -> tuple[Decision, list[dict]]:
        """
        Run the full 3-agent pipeline. Returns (Decision, trace).
        The trace list is stored in AgentReasoningTrace.tool_calls.
        """
        trace: list[dict] = []

        # ── Agent 1: Research ────────────────────────────────────────────────
        logger.info(f"[pipeline] Research Agent starting for {context.ticker}")
        research_text, research_calls = await self._run_research(context, tool_executor)
        trace.append({
            "agent": "research",
            "report": research_text,
            "tool_calls": research_calls,
        })
        logger.info(f"[pipeline] Research Agent done ({len(research_calls)} tool calls)")

        # ── Pre-compute Risk Context ─────────────────────────────────────────
        try:
            risk_ctx = await build_risk_context(
                ticker=context.ticker,
                portfolio=self.portfolio,
                session=session,
                settings=self.settings,
            )
        except Exception as e:
            logger.warning(f"[pipeline] Risk context build failed: {e}")
            risk_ctx = (
                f"Portfolio value: ${context.portfolio_value:,.2f} | "
                f"Cash: ${context.cash_balance:,.2f} | "
                f"Wallet remaining: ${context.wallet_remaining:,.2f}"
            )

        # ── Agent 2: Risk ────────────────────────────────────────────────────
        logger.info(f"[pipeline] Risk Agent starting for {context.ticker}")
        risk_text, risk_parsed = await self._run_risk(context, research_text, risk_ctx)
        trace.append({
            "agent": "risk",
            "assessment": risk_text,
            "risk_level": risk_parsed.risk_level if risk_parsed else "unknown",
            "suggested_position_pct": risk_parsed.suggested_position_pct if risk_parsed else context.max_position_pct,
        })
        logger.info(f"[pipeline] Risk Agent done: {risk_parsed.risk_level if risk_parsed else 'parse failed'}")

        # ── Agent 3: Decision ────────────────────────────────────────────────
        logger.info(f"[pipeline] Decision Agent starting for {context.ticker}")
        decision = await self._run_decision(context, research_text, risk_text)
        trace.append({
            "agent": "decision",
            "action": decision.action,
            "quantity": int(decision.quantity),
            "reasoning": decision.reasoning,
            "confidence": decision.confidence,
        })
        logger.info(
            f"[pipeline] Decision Agent done: {decision.action} x{decision.quantity} "
            f"(conf:{decision.confidence:.2f})"
        )

        return decision, trace

    # ── Research Agent ────────────────────────────────────────────────────────

    async def _run_research(
        self,
        context: TradeContext,
        tool_executor: ToolExecutor,
    ) -> tuple[str, list[dict]]:
        provider = self.llm.provider_name
        if provider == "claude":
            return await self._research_claude(context, tool_executor)
        elif provider == "openai":
            return await self._research_openai(context, tool_executor)
        else:
            return await self._research_enriched(context, tool_executor)

    async def _research_claude(
        self,
        context: TradeContext,
        tool_executor: ToolExecutor,
    ) -> tuple[str, list[dict]]:
        client = self.llm.client
        messages = [{"role": "user", "content": build_research_message(context)}]
        trace: list[dict] = []

        for _iteration in range(_MAX_RESEARCH_ITERATIONS):
            response = await client.messages.create(
                model=self.llm.model,
                max_tokens=2048,
                system=RESEARCH_AGENT_SYSTEM,
                tools=_RESEARCH_TOOLS,
                messages=messages,
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                logger.warning("[pipeline] Claude research: no tool calls, falling back to enriched")
                return await self._research_enriched(context, tool_executor)

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in tool_use_blocks:
                if block.name == "submit_research_report":
                    trace.append({"tool": "submit_research_report", "arguments": block.input, "result": "submitted"})
                    try:
                        parsed = ResearchReportSchema.model_validate(block.input)
                        return _report_to_text(parsed), trace
                    except Exception:
                        return str(block.input), trace

                result_str = await tool_executor.execute(block.name, block.input)
                result_data = json.loads(result_str)
                trace.append({"tool": block.name, "arguments": block.input, "result": result_data})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        logger.warning("[pipeline] Claude research: hit max iterations, falling back to enriched")
        return await self._research_enriched(context, tool_executor)

    async def _research_openai(
        self,
        context: TradeContext,
        tool_executor: ToolExecutor,
    ) -> tuple[str, list[dict]]:
        from app.llm.openai_provider import _to_openai_tools

        client = self.llm.client
        openai_tools = _to_openai_tools(_RESEARCH_TOOLS)
        messages = [
            {"role": "system", "content": RESEARCH_AGENT_SYSTEM},
            {"role": "user", "content": build_research_message(context)},
        ]
        trace: list[dict] = []

        for _iteration in range(_MAX_RESEARCH_ITERATIONS):
            response = await client.chat.completions.create(
                model=self.llm.model,
                max_tokens=2048,
                tools=openai_tools,
                tool_choice="auto",
                messages=messages,
            )
            msg = response.choices[0].message

            if not msg.tool_calls:
                logger.warning("[pipeline] OpenAI research: no tool calls, falling back to enriched")
                return await self._research_enriched(context, tool_executor)

            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]})

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                arguments = json.loads(tc.function.arguments)

                if tool_name == "submit_research_report":
                    trace.append({"tool": "submit_research_report", "arguments": arguments, "result": "submitted"})
                    try:
                        parsed = ResearchReportSchema.model_validate(arguments)
                        return _report_to_text(parsed), trace
                    except Exception:
                        return str(arguments), trace

                result_str = await tool_executor.execute(tool_name, arguments)
                result_data = json.loads(result_str)
                trace.append({"tool": tool_name, "arguments": arguments, "result": result_data})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        logger.warning("[pipeline] OpenAI research: hit max iterations, falling back to enriched")
        return await self._research_enriched(context, tool_executor)

    async def _research_enriched(
        self,
        context: TradeContext,
        tool_executor: ToolExecutor,
    ) -> tuple[str, list[dict]]:
        """
        Enriched single-shot research for Ollama/Gemini (and as fallback for Claude/OpenAI).
        Pre-fetches all data, runs web search, then asks LLM for a structured report.
        """
        ticker = context.ticker
        trace: list[dict] = []

        fetch_specs = [
            ("get_price_history", {"ticker": ticker, "hours": 24}),
            ("get_technical_indicators", {"ticker": ticker}),
            ("get_sentiment", {"ticker": ticker, "limit": 10}),
            ("get_recent_news", {"ticker": ticker, "limit": 5}),
            ("check_portfolio", {}),
        ]
        raw_results = await asyncio.gather(
            *[tool_executor.execute(name, args) for name, args in fetch_specs]
        )
        data_blocks = []
        for (tool_name, args), result_str in zip(fetch_specs, raw_results):
            result_data = json.loads(result_str)
            trace.append({"tool": tool_name, "arguments": args, "result": result_data})
            data_blocks.append(f"{tool_name.upper()}: {json.dumps(result_data, indent=2)}")

        # Web search
        query = f"{ticker} stock {context.trigger_detail[:80]}"
        ws_result_str = await tool_executor.execute("web_search", {"query": query})
        ws_data = json.loads(ws_result_str)
        trace.append({"tool": "web_search", "arguments": {"query": query}, "result": ws_data})
        data_blocks.append(f"WEB_SEARCH: {json.dumps(ws_data, indent=2)}")

        enriched_prompt = (
            f"Ticker: {ticker} | Price: ${context.current_price:.2f}\n"
            f"Trigger: {context.trigger_type} — {context.trigger_detail}\n\n"
            "Based on the following pre-fetched data, write a research report as JSON.\n\n"
            + "\n\n".join(data_blocks)
            + "\n\nReturn ONLY a JSON object with these keys: "
            "price_summary, technical_summary, sentiment_summary, news_summary, "
            "web_search_summary, overall_assessment"
        )

        raw = await self.llm.single_shot(RESEARCH_AGENT_SYSTEM, enriched_prompt)
        try:
            parsed = ResearchReportSchema.model_validate(json.loads(_strip_fences(raw)))
            report_text = _report_to_text(parsed)
        except Exception as e:
            logger.warning(f"[pipeline] Research report parse failed: {e}")
            report_text = raw

        trace.append({"tool": "submit_research_report", "arguments": {}, "result": "submitted"})
        return report_text, trace

    # ── Risk Agent ────────────────────────────────────────────────────────────

    async def _run_risk(
        self,
        context: TradeContext,
        research_report: str,
        risk_ctx: str,
    ) -> tuple[str, Optional[RiskAssessmentSchema]]:
        user_msg = build_risk_message(research_report, risk_ctx)
        raw = await self.llm.single_shot(RISK_AGENT_SYSTEM, user_msg)
        raw = _strip_fences(raw)
        try:
            parsed = RiskAssessmentSchema.model_validate(json.loads(raw))
            return raw, parsed
        except Exception as e:
            logger.warning(f"[pipeline] Risk assessment parse failed: {e}")
            return raw, None

    # ── Decision Agent ────────────────────────────────────────────────────────

    async def _run_decision(
        self,
        context: TradeContext,
        research_report: str,
        risk_assessment: str,
    ) -> Decision:
        user_msg = build_decision_message(context, research_report, risk_assessment)
        raw = await self.llm.single_shot(DECISION_AGENT_SYSTEM, user_msg)
        raw = _strip_fences(raw)
        try:
            return self.llm._parse_decision(json.loads(raw))
        except Exception as e:
            logger.warning(f"[pipeline] Decision parse failed: {e} — defaulting to hold")
            return Decision(action="hold", quantity=0, reasoning=f"Parse error: {e}", confidence=0.1)
