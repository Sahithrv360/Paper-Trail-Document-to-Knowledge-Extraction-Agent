# Invoice Link Demonstration

This example uses two records already extracted from the downloaded invoice dataset. It demonstrates how invoices connect through one shared item-description hub instead of a dense set of invoice-to-invoice edges.

## Example records

| Invoice | Date | Matching line description | Quantity | Net amount |
|---|---|---|---:|---:|
| `62517865` | `06/02/2015` | Care & Repair of Furniture | 2 | 8.50 |
| `46301857` | `04/08/2017` | Care & Repair of Furniture | 5 | 21.25 |

The matcher folds case and repeated whitespace. It preserves each invoice's own `LineItem` node and connects both line items to a shared `ItemDescription` node with `MATCHES_DESCRIPTION`. This is evidence of matching text, not proof the underlying products are identical. These extractions are still marked `needs_review`; one has an arithmetic mismatch and the other is incomplete.

## Sync and inspect in Neo4j

From the repository root, refresh the projection:

```powershell
python -m backend.app.neo4j_sync
```

Then run this in Neo4j Browser:

```cypher
MATCH (a:PaperTrail:Invoice {invoice_number: '62517865'})
      -[:CONTAINS]->(la:PaperTrail:LineItem)
      -[:MATCHES_DESCRIPTION]->(hub:PaperTrail:ItemDescription)
      <-[:MATCHES_DESCRIPTION]-(lb:PaperTrail:LineItem)
      <-[:CONTAINS]-(b:PaperTrail:Invoice {invoice_number: '46301857'})
RETURN a.invoice_number AS invoice_a,
       la.description AS item_a,
       hub.normalized_description AS shared_description,
       lb.description AS item_b,
       b.invoice_number AS invoice_b;
```

Expected result: one row connecting the two invoices through the normalized shared-description hub. To browse a small sample of all such paths, replace the invoice filters with `MATCH p=(a:PaperTrail:Invoice)-[:CONTAINS]->(:PaperTrail:LineItem)-[:MATCHES_DESCRIPTION]->(:PaperTrail:ItemDescription)<-[:MATCHES_DESCRIPTION]-(:PaperTrail:LineItem)<-[:CONTAINS]-(b:PaperTrail:Invoice) RETURN p LIMIT 25`.

The projection also writes a direct shortcut edge for apps and simple queries. The hub path remains the explanation for that edge:

```cypher
MATCH (a:PaperTrail:Invoice {invoice_number: '62517865'})
      -[r:SAME_ITEM_DESCRIPTION_AS]->
      (b:PaperTrail:Invoice {invoice_number: '46301857'})
RETURN a.invoice_number AS invoice_a,
       r.shared_description AS matching_description,
       r.shared_entity_id AS evidence_hub,
       b.invoice_number AS invoice_b;
```

In the PaperTrail UI, open **Graph**, search for invoice `62517865`, and select it. The default visualization shows shared hubs; enable **Direct links** to display invoice-to-invoice shortcut edges. The detail panel lists connected invoices and the shared fact behind each link. The optional **3D perspective** toggle uses a lightweight SVG projection; drag the background to orbit it.

## Synthetic incremental-file regression

`test_adding_synthetic_invoice_files_updates_only_supported_links` builds three small invoices in memory. The second file repeats the first vendor and item description with different case and whitespace. The third uses a different vendor and unique item. The test expects the second file to create vendor and item links, while the third creates no unsupported connection. This keeps demo data out of `watched/` and the production graph.

Run it with:

```powershell
python -m unittest backend.tests.test_neo4j_graph_rag.Neo4jSyncTests.test_adding_synthetic_invoice_files_updates_only_supported_links -v
```
