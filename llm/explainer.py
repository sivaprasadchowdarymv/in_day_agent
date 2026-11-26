"""Recommendation explanations.

The deterministic template is always produced. An optional LLM may rephrase it,
but the rephrased text is accepted only if every number it contains already
appears in the evidence (a 'number guard'), so the LLM cannot introduce prices
or statistics."""
from __future__ import annotations

import json
import re

from config.logging_setup import get_logger
from llm.providers import LLMError, LLMProvider, extract_json

log = get_logger("explainer")

SECTIONS = ["WHY THIS STOCK?", "WHY THIS ENTRY?", "WHY THIS STOP-LOSS?", "WHY THESE TARGETS?",
            "WHAT CAN INVALIDATE THE TRADE?"]


def template_explanation(cand) -> dict[str, str]:
    p, sig, sc = cand.plan, cand.signal, cand.score
    comp_lines = [f"{c.name}: {c.points:.1f}/{c.weight:.0f} – {'; '.join(c.evidence)}" for c in sc.components]
    why_stock = (f"{cand.symbol} ({cand.sector}) produced a {sig.strategy_name} {p.direction} signal on the "
                 f"{sig.bar_ts:%H:%M} candle. Setup score {sc.total:.0f}/100 (quality ranking, not a probability). "
                 + " | ".join(sig.reasons + sig.confirmations) + ". Score detail → " + " ; ".join(comp_lines))
    if cand.screenshot_evidence:
        why_stock += f" Also identified from {len(cand.screenshot_evidence)} uploaded screenshot(s) and re-verified on fresh data."
    why_entry = " ".join(p.entry_notes) + f" Current verified price {p.current_price} ({p.price_label})."
    why_stop = (" ".join(p.stop_notes) + f" Risk per share ₹{p.risk_per_share:.2f}; quantity {p.quantity} keeps the "
                f"planned loss at ₹{p.max_planned_loss:.2f} (limited by {p.limiting_factor}).")
    tgt = f"T1 {p.target1}" + (f", T2 {p.target2}" if p.target2 else "") + (f", T3 {p.target3}" if p.target3 else "")
    why_targets = (f"{tgt}. " + " ".join(p.target_notes) + f" Gross R:R to T1 = {p.risk_reward}; after estimated "
                   f"costs ₹{p.est_costs_at_target:.2f} the net R:R ≈ {p.net_risk_reward}.")
    invalid = "; ".join(p.invalidation)
    return dict(zip(SECTIONS, [why_stock, why_entry, why_stop, why_targets, invalid]))


_NUM = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.findall(text):
        v = m.replace(",", "").rstrip(".")
        if not v or v in "+-":
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        out.add(f"{f:.2f}")
    return out


def number_guard(candidate_text: str, evidence_text: str) -> tuple[bool, set[str]]:
    allowed = _numbers(evidence_text)
    # trivial small integers (section numbering, '1', '2', '3') are allowed
    allowed |= {f"{float(i):.2f}" for i in range(0, 11)}
    extra = _numbers(candidate_text) - allowed
    return not extra, extra


def llm_explanation(llm: LLMProvider, template: dict[str, str]) -> tuple[dict[str, str], str]:
    """Returns (explanation, source) where source is 'llm' or 'template'."""
    if not llm.available():
        return template, "template"
    evidence = json.dumps(template, ensure_ascii=False)
    prompt = ("Rewrite the following trading-setup evidence into clear, plain English for a beginner. Keep the same "
              "five keys. Use ONLY facts and numbers present in the evidence. Do not add predictions, probabilities, "
              "news or new numbers. Return JSON with the same keys.\n\nEVIDENCE:\n" + evidence)
    try:
        resp = llm.generate(prompt, system="You explain deterministic trading analysis. Never invent data.",
                            json_mode=True, max_tokens=900)
        data = extract_json(resp.text)
        out = {k: str(data.get(k, template[k])) for k in SECTIONS}
        ok, extra = number_guard(" ".join(out.values()), evidence)
        if not ok:
            log.warning(f"LLM explanation rejected by number guard: {sorted(extra)[:5]}")
            return template, "template (LLM text rejected: introduced numbers)"
        return out, f"llm ({resp.provider}:{resp.model})"
    except (LLMError, ValueError) as exc:
        log.warning(f"LLM explanation failed: {exc}")
        return template, "template (LLM unavailable)"
