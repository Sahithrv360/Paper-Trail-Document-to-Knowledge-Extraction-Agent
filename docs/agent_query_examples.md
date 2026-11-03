# Agent query examples

Use names and invoice numbers that currently exist in the graph. The Ask Knowledge page also builds a set of clickable examples from the loaded graph so the sample entities stay current.

| Query type | Example question | Check in the response |
|---|---|---|
| Entity lookup | `Summarize invoice {{invoice_number}}: customer, vendor, amount, line items, and review status.` | The answer names only facts in the cited invoice and linked records. |
| Customer account summary | `Prepare an account briefing for {{customer_name}}, covering invoice amounts, review status, products, and linked communications.` | Customer resolution, relevant invoice neighbors, and an explicit note when communication data is absent. |
| Multi-hop relationship | `Show how {{vendor_name}} is connected to {{customer_name}} through invoices and products.` | A connected vendor → invoice → customer/product path and the relationship labels in the graph view. |
| Email analysis | `Summarize the email conversation linked to {{customer_name}}, including commitments and open actions.` | Only linked conversation summaries or emails; the answer says when none are available. |
| Comparison | `Compare {{customer_a}} and {{customer_b}} using their invoice activity and linked customers.` | Evidence from both named entities, with missing fields called out instead of inferred. |
| Aggregation | `How many invoice records are in the knowledge graph?` | A graph-backed count, not a count inferred from retrieved snippets. |
| Review risks | `Which invoices need review, and what evidence explains why they need attention?` | Review-state facts and evidence attached to the relevant records. |
| Follow-up | Ask an account summary first, then ask `And what actions remain open?` | The follow-up stays with the prior customer and uses the recent chat context. |
| Unknown entity guard | `Prepare an account briefing for Nonexistent Demo Customer 000.` | A clear no-match response; no facts borrowed from a similarly named customer. |

## Manual check procedure

1. Select **Agent · plan, retrieve, verify** on Ask Knowledge.
2. Use a current graph-backed entity from a sample chip or the Knowledge Graph search.
3. Check the answer citations, the node and edge counts, the cited-node outline, and the selected-node relationship list.
4. Repeat with **Standard graph retrieval** and confirm it does not claim model-generated prose.
5. For local generation, set `AGENT_LLM_PROVIDER=ollama` and `GRAPH_RAG_MODEL` to an installed chat model, restart the backend, and confirm the model name on the answer card.

This is a manual query set, not a labeled accuracy benchmark. Do not score factual accuracy until the selected entities and expected facts have been checked against the source documents.
