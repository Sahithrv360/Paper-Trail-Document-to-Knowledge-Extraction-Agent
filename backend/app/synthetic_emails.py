"""Build an explicitly fictional vendor/customer email graph demo.

The fixtures live outside the watched folder and are added to the graph ledger as
synthetic, unreviewed records. They are never presented as source-invoice evidence.
"""

from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, format_datetime
import hashlib
import json
from pathlib import Path
import re

from .config import GRAPH_PATH
from .graph import GraphStore

OUTPUT_DIR = Path("data/synthetic_emails")
THREAD_STEPS = [
    ("vendor", "Invoice shared for reconciliation",
     "Hello {customer},\n\nPlease review synthetic invoice {invoice} and confirm it is routed to the correct accounts-payable queue. This is a fictional training conversation; no real invoice is attached."),
    ("customer", "Re: Invoice received",
     "Hello {vendor},\n\nWe have received the demo thread for invoice {invoice}. Please confirm the purchase-order reference and billing details before we complete our internal match."),
    ("vendor", "Re: Invoice details",
     "Hello {customer},\n\nThe invoice number to use for this fictional reconciliation is {invoice}. Please compare the vendor and customer names with the source invoice; this message does not verify any other invoice fields."),
    ("customer", "Re: Purchase-order match",
     "Hello {vendor},\n\nOur synthetic AP scenario shows the PO reference is not visible in the initial email. Please provide a demo reference so we can exercise the matching workflow. No real PO is being asserted."),
    ("vendor", "Demo PO reference supplied",
     "Hello {customer},\n\nFor this fictional scenario only, use DEMO-PO-{po_suffix} as the purchase-order reference for invoice {invoice}. This reference was generated for the demo and is not present as a verified invoice fact."),
    ("customer", "Re: Delivery or service status",
     "Hello {vendor},\n\nThe demo PO has been matched in our scenario. Please confirm whether the related goods or services are complete so we can describe the next AP step."),
    ("vendor", "Demo completion confirmation",
     "Hello {customer},\n\nFor this synthetic thread, the vendor confirms completion and asks the customer to reconcile the invoice. This is an invented demo event, not independently verified delivery evidence."),
    ("customer", "Invoice queued for AP review",
     "Hello {vendor},\n\nThe fictional invoice {invoice} is now queued for internal AP approval after the PO and completion checks. Approval has not yet been recorded in the source invoice graph."),
    ("vendor", "Request for payment timing",
     "Hello {customer},\n\nCould you share the expected payment timing once the synthetic AP review is complete? No payment has been recorded or confirmed by this message."),
    ("customer", "Re: Payment timing",
     "Hello {vendor},\n\nIn this generated scenario, payment is planned after approval, but it is not confirmed as sent or received. The thread ends with the invoice awaiting that real-world verification."),
]


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "party").casefold()).strip("-") or "party"


def _email_doc_id(invoice_document_id, position):
    return f"synthetic-email-{invoice_document_id.rsplit(':', 1)[-1]}-{position:02d}"


def _email_node_id(email_id):
    return f"SyntheticEmail:{email_id}"


def _email_summary(subject, body):
    """Create a short searchable label for one fictional message."""
    first_sentence = re.split(r"(?<=[.!?])\s+", " ".join(body.split()), maxsplit=1)[0]
    return f"Synthetic demo email — {subject}. {first_sentence}"


def _thread_summary(invoice_number, vendor_name, customer_name, source_status, emails):
    """Describe only the generated thread and preserve its fictional status."""
    return "\n".join([
        "SYNTHETIC DEMO THREAD SUMMARY — fictional correspondence, not invoice evidence.",
        f"Invoice {invoice_number}; vendor {vendor_name}; customer {customer_name}; "
        f"source invoice review status: {source_status}.",
        f"Messages in order: {len(emails)} emails between vendor and customer.",
        "The thread discusses invoice routing and detail confirmation, a fictional demo "
        f"purchase-order reference DEMO-PO-{str(invoice_number)[-4:]}, a fictional "
        "completion check, and queuing the invoice for AP review.",
        "The customer says payment is planned after approval, but payment is not confirmed "
        "as sent or received. The thread does not independently verify delivery, approval, "
        "the purchase order, or any invoice field.",
        "Messages:",
        *[f"{email['position']}. {email['sender']} to {email['recipient']}: "
          f"{email['subject']} — {email['summary']}" for email in emails],
    ])


def _choose_threads(export, limit=5):
    nodes = {node["id"]: node for node in export["nodes"]}
    vendor_for_invoice, customer_for_invoice = {}, {}
    for edge in export["edges"]:
        if edge["type"] == "ISSUED":
            vendor_for_invoice[edge["target"]] = edge["source"]
        elif edge["type"] == "BILLED_TO":
            customer_for_invoice[edge["source"]] = edge["target"]
    candidates = []
    for node in export["nodes"]:
        if node["type"] != "Invoice":
            continue
        invoice_number = (node.get("props") or {}).get("invoice_number")
        vendor_id = vendor_for_invoice.get(node["id"])
        customer_id = customer_for_invoice.get(node["id"])
        if invoice_number and vendor_id in nodes and customer_id in nodes:
            candidates.append((str(invoice_number), node, nodes[vendor_id], nodes[customer_id]))
    candidates.sort(key=lambda item: (item[0], item[1]["id"]))
    if len(candidates) < limit:
        raise RuntimeError(f"Need {limit} invoices with both vendor and customer graph links; found {len(candidates)}")
    return candidates[:limit]


def _write_email(path, sender_name, sender_email, recipient_name, recipient_email,
                 sent_at, subject, body, message_id, previous_message_id, invoice_number):
    message = EmailMessage()
    message["From"] = formataddr((sender_name, sender_email))
    message["To"] = formataddr((recipient_name, recipient_email))
    message["Date"] = format_datetime(sent_at)
    message["Subject"] = f"[SYNTHETIC DEMO] Invoice {invoice_number}: {subject}"
    message["Message-ID"] = f"<{message_id}@papertrail-synthetic.invalid>"
    message["X-Synthetic-Demo"] = "true"
    message["X-Source-Classification"] = "fictional-not-evidence"
    message["X-Related-Invoice"] = str(invoice_number)
    if previous_message_id:
        previous_header = f"<{previous_message_id}@papertrail-synthetic.invalid>"
        message["In-Reply-To"] = previous_header
        message["References"] = previous_header
    notice = ("SYNTHETIC DEMO EMAIL — fictional correspondence generated for graph and agent testing. "
              "It is not a real communication, not payment or delivery evidence, and must not be treated "
              "as a verified business event.\n\n")
    message.set_content(notice + body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(message.as_bytes())


def build_synthetic_emails(graph_path=GRAPH_PATH, output_dir=OUTPUT_DIR, limit=5):
    store = GraphStore(graph_path)
    export = store.export(reviewed_only=False)
    threads = _choose_threads(export, limit)
    graph_nodes = {node["id"]: node for node in export["nodes"]}
    document_ids, email_node_ids = [], []
    emails_by_thread = []
    summary_lines = [
        "SYNTHETIC DEMO SUMMARY — all email events below are fictional and are not invoice evidence.",
        "The 50 generated messages exercise vendor/customer communication, PO matching, completion checks, AP review, and payment-status questions.",
        "Across every thread, payment remains planned or pending; no payment is represented as sent, received, or verified.",
        "The actual source invoice records are separate graph data and remain subject to their displayed review status.",
        "",
    ]
    total_emails = 0

    for thread_index, (invoice_number, invoice, vendor, customer) in enumerate(threads, start=1):
        invoice_props = invoice.get("props") or {}
        vendor_props = vendor.get("props") or {}
        customer_props = customer.get("props") or {}
        vendor_name = vendor_props.get("name") or "Demo Vendor"
        customer_name = customer_props.get("name") or "Demo Customer"
        source_status = invoice_props.get("review_status", "unknown")
        invoice_doc_id = invoice["id"].split(":", 1)[1]
        vendor_email = f"billing+{thread_index}@{_slug(vendor_name)}.invalid"
        customer_email = f"ap+{thread_index}@{_slug(customer_name)}.invalid"
        thread_emails = []
        previous_email_id = None
        previous_message_id = None
        base_date = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=thread_index * 12)

        for position, (sender_role, subject, template) in enumerate(THREAD_STEPS, start=1):
            sender_is_vendor = sender_role == "vendor"
            sender_name, sender_address = ((vendor_name, vendor_email) if sender_is_vendor
                                           else (customer_name, customer_email))
            recipient_name, recipient_address = ((customer_name, customer_email) if sender_is_vendor
                                                  else (vendor_name, vendor_email))
            recipient_role = "customer" if sender_is_vendor else "vendor"
            email_id = f"{invoice_doc_id}-{position:02d}"
            doc_id = _email_doc_id(invoice_doc_id, position)
            relative_path = Path(output_dir) / str(invoice_number) / f"email-{position:02d}.eml"
            path = Path(relative_path)
            sent_at = base_date + timedelta(days=position - 1, hours=9)
            body = template.format(invoice=invoice_number, vendor=vendor_name,
                                   customer=customer_name,
                                   po_suffix=str(invoice_number)[-4:])
            _write_email(path, sender_name, sender_address, recipient_name, recipient_address,
                         sent_at, subject, body, email_id, previous_message_id, invoice_number)
            content = path.read_bytes()
            record = {
                "doc_type": "synthetic_email", "synthetic_record_type": "email",
                "email_id": email_id, "invoice_number": str(invoice_number),
                "invoice_node_id": invoice["id"], "subject": subject,
                "sent_at": sent_at.isoformat(), "sender_name": sender_name,
                "sender_email": sender_address, "sender_role": sender_role,
                "sender_node_id": vendor["id"] if sender_is_vendor else customer["id"],
                "recipient_name": recipient_name, "recipient_email": recipient_address,
                "recipient_role": recipient_role,
                "recipient_node_id": customer["id"] if sender_is_vendor else vendor["id"],
                "body": ("SYNTHETIC DEMO EMAIL — fictional correspondence, not evidence. " + body),
                "summary_text": _email_summary(subject, body),
                "thread_position": position,
                "previous_email_id": _email_node_id(previous_email_id) if previous_email_id else None,
                "provenance": {"provider": "synthetic_demo", "model_revision": "fixture-v1"},
            }
            store.sync(doc_id, hashlib.sha256(content).hexdigest(), record,
                       filename=str(path.as_posix()), evidence={"synthetic": True})
            previous_email_id = email_id
            previous_message_id = email_id
            email_node_ids.append(_email_node_id(email_id))
            document_ids.append(doc_id)
            thread_emails.append({"email_id": email_id, "node_id": _email_node_id(email_id),
                                  "position": position, "subject": subject,
                                  "sender": sender_name, "recipient": recipient_name,
                                  "sent_at": sent_at.isoformat(),
                                  "summary": _email_summary(subject, body)})
            total_emails += 1

        # Keep one compact, directly searchable chunk next to each invoice thread.
        thread_summary = _thread_summary(invoice_number, vendor_name, customer_name,
                                         source_status, thread_emails)
        thread_summary_id = f"business-invoice-thread-{invoice_doc_id}-v1"
        thread_summary_doc_id = f"synthetic-summary-{invoice_doc_id}"
        thread_summary_path = Path(output_dir) / str(invoice_number) / "conversation-summary.md"
        thread_summary_path.parent.mkdir(parents=True, exist_ok=True)
        thread_summary_path.write_text(thread_summary + "\n", encoding="utf-8")
        thread_summary_record = {
            "doc_type": "synthetic_summary", "synthetic_record_type": "summary",
            "summary_id": thread_summary_id,
            "title": f"Synthetic conversation summary for invoice {invoice_number}",
            "summary_scope": "invoice_thread", "invoice_number": str(invoice_number),
            "vendor_name": vendor_name, "customer_name": customer_name,
            "summary_text": thread_summary,
            "invoice_numbers": [str(invoice_number)],
            "invoice_node_ids": [invoice["id"]],
            "email_node_ids": [email["node_id"] for email in thread_emails],
            "provenance": {"provider": "synthetic_demo", "model_revision": "fixture-v1"},
        }
        store.sync(thread_summary_doc_id,
                   hashlib.sha256(thread_summary_path.read_bytes()).hexdigest(),
                   thread_summary_record, filename=str(thread_summary_path.as_posix()),
                   evidence={"synthetic": True, "chunk_type": "invoice_conversation_summary"})
        document_ids.append(thread_summary_doc_id)

        emails_by_thread.append({"invoice": invoice, "invoice_number": str(invoice_number),
                                 "vendor": vendor, "customer": customer,
                                 "emails": thread_emails,
                                 "summary": thread_summary,
                                 "summary_node_id": f"ConversationSummary:{thread_summary_id}"})
        summary_lines.extend([
            f"Invoice {invoice_number} | vendor: {vendor_name} | customer: {customer_name} | source review status: {source_status}.",
            f"Synthetic thread outcome: the parties exchange invoice routing and a fictional demo PO reference, discuss a fictional completion check, and queue the invoice for AP approval. The customer says payment is planned after approval, but no payment is confirmed.",
            "",
        ])

    summary_text = "\n".join(summary_lines)
    summary_id = "business-invoice-conversations-demo-v1"
    summary_doc_id = "synthetic-summary-business-invoices-v1"
    summary_path = Path(output_dir) / "combined-summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary_text + "\n", encoding="utf-8")
    summary_record = {
        "doc_type": "synthetic_summary", "synthetic_record_type": "summary",
        "summary_id": summary_id, "title": "Synthetic vendor/customer conversations across five invoices",
        "summary_text": summary_text,
        "invoice_numbers": [thread["invoice_number"] for thread in emails_by_thread],
        "invoice_node_ids": [thread["invoice"]["id"] for thread in emails_by_thread],
        "email_node_ids": email_node_ids,
        "provenance": {"provider": "synthetic_demo", "model_revision": "fixture-v1"},
    }
    store.sync(summary_doc_id, hashlib.sha256(summary_path.read_bytes()).hexdigest(),
               summary_record, filename=str(summary_path.as_posix()),
               evidence={"synthetic": True, "chunk_type": "business_conversation_summary"})

    return {"email_count": total_emails, "invoice_numbers": [thread["invoice_number"]
            for thread in emails_by_thread], "summary_node_id": f"ConversationSummary:{summary_id}",
            "summary_path": str(summary_path), "output_dir": str(output_dir),
            "document_ids": document_ids + [summary_doc_id],
            "thread_summary_node_ids": [thread["summary_node_id"] for thread in emails_by_thread]}


if __name__ == "__main__":
    print(json.dumps(build_synthetic_emails(), indent=2))
