"""Create the small reproducible procurement graph used by local demos and tests."""

import json
from pathlib import Path


def build_sample(directory: Path | str = "data/sample") -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    nodes, edges, chunks = [], [], []
    for index in range(1, 9):
        suffix = f"{index:02d}"
        supplier = f"supplier-{suffix}"
        contract = f"contract-2026-{suffix}"
        invoice = f"invoice-PT-{2026000 + index}"
        customer = f"customer-{suffix}"
        item = f"item-{suffix}"
        contract_chunk = f"chunk-contract-{suffix}"
        invoice_chunk = f"chunk-invoice-{suffix}"
        nodes.extend([
            {"id": supplier, "type": "Supplier", "name": f"Northstar Supply {suffix}",
             "aliases": [f"Northstar {suffix}"], "description": "Procurement supplier",
             "properties": {"country": "US", "supplier_code": f"SUP-{suffix}"},
             "chunk_ids": [f"chunk-party-{suffix}"]},
            {"id": contract, "type": "Contract", "name": f"MSA-2026-{suffix}",
             "aliases": [], "description": "Master supply agreement",
             "properties": {"start_date": "2026-01-01", "end_date": f"2026-{3 + index:02d}-30",
                            "contract_value": index * 12500, "currency": "USD"},
             "chunk_ids": [contract_chunk]},
            {"id": invoice, "type": "Invoice", "name": invoice,
             "aliases": [f"invoice {invoice}"], "description": "Supplier invoice",
             "properties": {"invoice_number": invoice, "invoice_date": f"2026-02-{index:02d}",
                            "total": index * 1250, "currency": "USD"},
             "chunk_ids": [invoice_chunk]},
            {"id": customer, "type": "Customer", "name": f"Cedar Ridge Manufacturing {suffix}",
             "aliases": [f"Cedar Ridge {suffix}"], "description": "Procurement customer",
             "properties": {"customer_code": f"CUST-{suffix}"},
             "chunk_ids": [f"chunk-party-{suffix}"]},
            {"id": item, "type": "Item", "name": f"Industrial valve type {suffix}",
             "aliases": [], "description": "Catalog item purchased under supplier agreements",
             "properties": {"sku": f"VALVE-{suffix}", "unit": "each"},
             "chunk_ids": [f"chunk-item-{suffix}"]},
        ])
        edges.extend([
            {"id": f"edge-has-contract-{suffix}", "source": supplier, "target": contract,
             "type": "HAS_CONTRACT", "properties": {}, "chunk_ids": [contract_chunk]},
            {"id": f"edge-issued-invoice-{suffix}", "source": supplier, "target": invoice,
             "type": "ISSUED_INVOICE", "properties": {}, "chunk_ids": [invoice_chunk]},
            {"id": f"edge-billed-to-{suffix}", "source": invoice, "target": customer,
             "type": "BILLED_TO", "properties": {}, "chunk_ids": [invoice_chunk]},
            {"id": f"edge-contract-customer-{suffix}", "source": contract, "target": customer,
             "type": "CONTRACT_WITH_CUSTOMER", "properties": {}, "chunk_ids": [contract_chunk]},
            {"id": f"edge-contract-item-{suffix}", "source": contract, "target": item,
             "type": "COVERS_ITEM", "properties": {}, "chunk_ids": [contract_chunk]},
            {"id": f"edge-invoice-item-{suffix}", "source": invoice, "target": item,
             "type": "CONTAINS_ITEM", "properties": {"quantity": index},
             "chunk_ids": [invoice_chunk]},
            {"id": f"edge-serves-customer-{suffix}", "source": supplier, "target": customer,
             "type": "SERVES_CUSTOMER", "properties": {}, "chunk_ids": [contract_chunk]},
        ])
        chunks.extend([
            {"id": contract_chunk, "doc_id": contract, "page": 1,
             "text": f"MSA-2026-{suffix} is an agreement between Northstar Supply {suffix} "
                     f"and Cedar Ridge Manufacturing {suffix}. It covers industrial valve type "
                     f"{suffix} with a contract value of ${index * 12500:,} USD."},
            {"id": invoice_chunk, "doc_id": invoice, "page": 1,
             "text": f"Invoice {invoice}, dated 2026-02-{index:02d}, was issued by Northstar "
                     f"Supply {suffix} to Cedar Ridge Manufacturing {suffix}. Total: "
                     f"${index * 1250:,} USD for {index} industrial valves."},
        ])
        if index <= 7:
            chunks.append({"id": f"chunk-party-{suffix}", "doc_id": f"supplier-profile-{suffix}",
                           "page": 1, "text": f"Northstar Supply {suffix} supplies Cedar Ridge "
                           f"Manufacturing {suffix} under code SUP-{suffix}."})
        if index <= 7:
            chunks.append({"id": f"chunk-item-{suffix}", "doc_id": f"catalog-{suffix}",
                           "page": 1, "text": f"Catalog SKU VALVE-{suffix}: industrial valve "
                           f"type {suffix}, sold by the each."})
    edges.extend({"id": f"edge-invoice-contract-{i:02d}",
                  "source": f"invoice-PT-{2026000 + i}",
                  "target": f"contract-2026-{i:02d}",
                  "type": "REFERENCES_CONTRACT", "properties": {},
                  "chunk_ids": [f"chunk-invoice-{i:02d}"]}
                 for i in range(1, 9, 2))
    for filename, records in (("nodes.jsonl", nodes), ("edges.jsonl", edges),
                              ("chunks.jsonl", chunks)):
        (directory / filename).write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8")


if __name__ == "__main__":
    build_sample()
