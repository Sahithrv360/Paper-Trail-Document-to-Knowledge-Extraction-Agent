# Synthetic vendor/customer email demo

This folder contains exactly 50 fictional `.eml` messages: 10 messages for each of five invoice threads. They are local graph/demo fixtures only. No messages were sent, and the generated PO, completion, AP, and payment events are not source-invoice evidence.

The threads link the existing invoice, vendor, and customer nodes. Each email node has a short searchable summary, and each invoice has its own `ConversationSummary` chunk linked to that invoice and its 10 messages. A combined summary node covers all five threads. Graph RAG searches these summary fields with invoice numbers, subjects, email bodies, and graph relationships; the answer prompt labels every synthetic event as fictional, not verified business evidence. Each source invoice retains its original review status.

Selected invoice numbers: `10060256`, `10299181`, `10372826`, `10541959`, and `10817861`.

To regenerate the fixtures and idempotently sync them into the graph ledger, run this from the repository root:

```powershell
python -m backend.app.synthetic_emails
```

These files are outside `watched/` and are not ingested as ordinary invoice documents.
