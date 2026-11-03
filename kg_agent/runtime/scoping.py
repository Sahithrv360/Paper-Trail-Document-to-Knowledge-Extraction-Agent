"""Keep named customer or invoice questions within their graph neighborhood."""

import re


def _normalized(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _contains_name(question: str, name: str) -> bool:
    folded_question, folded_name = _normalized(question), _normalized(name)
    return bool(folded_name and re.search(
        rf"(?:^| ){re.escape(folded_name)}(?: |$)", folded_question))


def scope_for_question(question: str, store) -> tuple[set[str], dict]:
    """Resolve an exact customer/invoice mention and collect its invoices and thread."""
    nodes = {node.id: node for node in store.all_nodes()}
    edges = list(getattr(store, "edges", []))
    customers = [node for node in nodes.values() if node.type == "Customer"
                 and _contains_name(question, node.name)]
    invoices = [node for node in nodes.values() if node.type == "Invoice"
                and node.properties.get("invoice_number")
                and _contains_name(question, str(node.properties["invoice_number"]))]

    if len(customers) == 1:
        customer = customers[0]
        invoice_ids = set()
        for edge in edges:
            if edge.type != "BILLED_TO":
                continue
            if edge.source == customer.id and nodes.get(edge.target, None) and nodes[edge.target].type == "Invoice":
                invoice_ids.add(edge.target)
            elif edge.target == customer.id and nodes.get(edge.source, None) and nodes[edge.source].type == "Invoice":
                invoice_ids.add(edge.source)
        scope = {customer.id, *invoice_ids}
        reason = {"type": "customer", "id": customer.id, "name": customer.name}
    elif len(invoices) == 1:
        invoice_ids = {invoices[0].id}
        scope = set(invoice_ids)
        reason = {"type": "invoice", "id": invoices[0].id,
                  "name": str(invoices[0].properties["invoice_number"])}
    else:
        return set(), {}

    invoice_numbers = {str(nodes[node_id].properties.get("invoice_number"))
                        for node_id in invoice_ids
                        if nodes[node_id].properties.get("invoice_number")}
    customer_names = {customer.name.casefold() for customer in customers}
    for node in nodes.values():
        properties = node.properties
        if node.type in {"SyntheticEmail", "ConversationSummary"}:
            numbers = {str(properties.get("invoice_number") or "")}
            numbers.update(str(value) for value in properties.get("invoice_numbers", []) or [])
            linked_customer = str(properties.get("customer_name") or "").casefold()
            if numbers.intersection(invoice_numbers) or linked_customer in customer_names:
                scope.add(node.id)
        elif node.type.casefold() in {"apreview", "ap_review", "review", "approval"}:
            if any(edge.source == node.id and edge.target in invoice_ids
                   or edge.target == node.id and edge.source in invoice_ids for edge in edges):
                scope.add(node.id)
    for edge in edges:
        if ((edge.type in {"SUMMARY_INCLUDES_EMAIL", "EMAIL_REFERENCES_INVOICE",
                           "SUMMARY_REFERENCES_INVOICE"})
                and (edge.source in scope or edge.target in scope)):
            scope.update((edge.source, edge.target))
    return scope, reason


def scope_evidence_to_question(question: str, store, evidence: list):
    """Filter retrieved facts to an exact customer or invoice neighborhood."""
    allowed, reason = scope_for_question(question, store)
    if not allowed:
        return evidence, None
    nodes = {node.id: node for node in store.all_nodes()}
    edges = {edge.id: edge for edge in getattr(store, "edges", [])}
    scoped = []
    for item in evidence:
        related_ids = set(item.node_ids)
        if item.doc_id:
            related_ids.add(item.doc_id)
        for edge_id in item.edge_ids:
            edge = edges.get(edge_id)
            if edge:
                related_ids.update((edge.source, edge.target))
        invoice_ids = {node_id for node_id in related_ids
                       if nodes.get(node_id) and nodes[node_id].type == "Invoice"}
        if invoice_ids and not invoice_ids.issubset(allowed):
            continue
        if related_ids.intersection(allowed):
            scoped.append(item)
            continue
        if item.doc_id and item.doc_id not in allowed:
            continue
        text = _normalized(item.text)
        if any(_normalized(name) and _normalized(name) in text
               for name in (reason.get("name", ""), *[
                   node.name for node_id, node in nodes.items()
                   if node_id in allowed and node.type in {"Customer", "Invoice"}])):
            scoped.append(item)
    return scoped, reason
