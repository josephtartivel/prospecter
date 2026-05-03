"""One-shot smoke test for the Scorer agent.

Hits a real provider (default `mistral/mistral-small-latest`) on a single
synthetic candidate, then prints token-in / token-out / cost / latency
per call. Reads the API key from `.env` via python-dotenv.

Usage:
    uv run python scripts/smoke_scorer.py
    PROSPECTER_MODEL_SCORER=claude-haiku-4-5 uv run python scripts/smoke_scorer.py
"""

from __future__ import annotations

import asyncio
import os
from datetime import date

from dotenv import load_dotenv

from prospecter.agents.scorer import score_candidates
from prospecter.llm import LLM
from prospecter.schemas import ICP, Company


def _candidates() -> list[Company]:
    base = {
        "naf_code": "62.02A",
        "headcount_tranche": "12",
        "headcount_label": "20 to 49",
        "region_code": "11",
        "department_code": "75",
        "postal_code": "75011",
        "commune": "Paris",
        "creation_date": date(2023, 3, 15),
        "is_active": True,
    }
    return [
        Company(
            siren="000000001",
            siret_main="00000000100001",
            name="ExampleCo SAS",
            **base,
        ),
        Company(
            siren="000000002",
            siret_main="00000000200001",
            name="Beta SAS",
            naf_code="56.10C",
            headcount_tranche="21",
            headcount_label="50 to 99",
            region_code="11",
            department_code="92",
            postal_code="92100",
            commune="Boulogne-Billancourt",
            creation_date=date(2019, 8, 1),
            is_active=True,
        ),
    ]


async def main() -> None:
    load_dotenv()
    model = os.environ.get("PROSPECTER_MODEL_SCORER", "mistral/mistral-small-latest")
    icp = ICP(
        naf_codes=["62.02A", "56.10A", "56.10C"],
        headcount_min=10,
        headcount_max=49,
        department_codes=["75"],
    )
    llm = LLM(primary_model=model)
    print(f"smoke: model={model}, candidates={len(_candidates())}")
    scores = await score_candidates(icp, _candidates(), llm=llm, model=model)
    for s in scores:
        print(f"  siren={s.siren} value={s.value} conf={s.confidence:.2f} reason={s.reason!r}")
    print()
    print("per-call usage:")
    for i, rec in enumerate(llm.history, 1):
        print(
            f"  call {i}: in={rec.in_tokens} out={rec.out_tokens} "
            f"cost=${rec.cost_usd:.6f} {rec.duration_ms}ms"
        )
    print(f"total: {llm.total_calls} calls, ${llm.total_cost_usd:.6f}")


if __name__ == "__main__":
    asyncio.run(main())
