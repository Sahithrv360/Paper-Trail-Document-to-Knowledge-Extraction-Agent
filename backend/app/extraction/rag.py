"""Auditable retrieval over TRAINING examples only.

Retrieval returns record IDs plus *format-shape* guidance (e.g. dates as 99/99/9999),
never field values, so retrieved invoices cannot leak values into the current result.
Validation/test annotations are refused at construction time.
"""

import math
import re
from collections import Counter

from .providers import RetrievalContext
from .schema import normalize_donut_result

_TOKEN = re.compile(r"[a-z0-9]+")


def shape(value) -> str:
    """Mask a value to its format: digits->9, letters->A, other characters kept."""
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", str(value)))


def _tokens(text: str) -> list:
    return _TOKEN.findall(text.casefold())


class TrainingRetriever:
    def __init__(self, examples: list, split: str = "train"):
        if split != "train":
            raise ValueError("RAG corpus must be the training split only")
        self.docs = []
        for example in examples:
            record = normalize_donut_result(example["ground_truth"]).to_dict()
            descriptor = self._descriptor(record)
            self.docs.append({
                "id": f"{split}:{example['row_index']}",
                "shapes": descriptor,
                "tokens": Counter(_tokens(" ".join(descriptor.values()))),
            })
        df = Counter(token for doc in self.docs for token in doc["tokens"])
        n = max(len(self.docs), 1)
        self.idf = {token: math.log((1 + n) / (1 + count)) + 1 for token, count in df.items()}

    @staticmethod
    def _descriptor(record: dict) -> dict:
        out = {}
        for key in ("invoice_number", "invoice_date", "total_amount", "subtotal", "tax_amount"):
            if record.get(key) is not None:
                out[key] = shape(record[key])
        if record["customer"].get("tax_id"):
            out["customer_tax_id"] = shape(record["customer"]["tax_id"])
        if record.get("bank_account_iban"):
            out["bank_account_iban"] = shape(record["bank_account_iban"])
        out["item_count"] = str(len(record["items"]))
        return out

    def _score(self, query: Counter, doc: dict) -> float:
        dot = sum(query[t] * doc["tokens"][t] * self.idf.get(t, 1) ** 2 for t in query)
        norm = lambda c: math.sqrt(sum((v * self.idf.get(t, 1)) ** 2 for t, v in c.items())) or 1.0
        return dot / (norm(query) * norm(doc["tokens"]))

    def retrieve(self, query_text: str, k: int = 3) -> RetrievalContext:
        """`query_text` is any text derived from the CURRENT document (e.g. OCR/first pass)."""
        query = Counter(_tokens(shape(query_text)))
        ranked = sorted(self.docs, key=lambda d: (-self._score(query, d), d["id"]))[:k]
        hits = [{"id": d["id"], "score": round(self._score(query, d), 4)} for d in ranked]
        lines = [f"- {d['id']}: " + ", ".join(f"{k_}={v}" for k_, v in d["shapes"].items())
                 for d in ranked]
        return RetrievalContext(hits=hits, guidance="\n".join(lines))
