"""
MCP tool definitions and their handlers.

READ tools:  list_contacts, list_invoices, get_organisation, list_accounts
WRITE tools: create_contact, create_invoice, create_payment, create_bank_transaction

Manual journals are intentionally NOT writable by this connector;
list_journals is provided read-only for visibility.
"""
import json

from xero_client import api_request

# ---- Tool schema (advertised to the MCP client) ----------------------------

TOOLS = [
    {
        "name": "get_organisation",
        "description": "Get details about the connected Xero organisation (name, base currency, country).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_contacts",
        "description": "List contacts (customers and suppliers). Optionally filter by a search term.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Optional name/email search term."}
            },
        },
    },
    {
        "name": "list_invoices",
        "description": "List invoices. Optionally filter by status (DRAFT, SUBMITTED, AUTHORISED, PAID, VOIDED).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Optional invoice status filter."}
            },
        },
    },
    {
        "name": "list_accounts",
        "description": "List the chart of accounts (account codes and names) for the connected org.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_journals",
        "description": "List manual journals (read-only in this connector).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_contact",
        "description": "Create a new contact (customer or supplier).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contact name (required)."},
                "email": {"type": "string", "description": "Optional email address."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_invoice",
        "description": (
            "Create a sales invoice (ACCREC) or bill (ACCPAY). Provide the contact name, "
            "type, and one or more line items. Created as DRAFT unless status is given."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_name": {"type": "string", "description": "Name of an existing contact."},
                "type": {"type": "string", "description": "ACCREC (sales invoice) or ACCPAY (bill)."},
                "status": {"type": "string", "description": "DRAFT, SUBMITTED, or AUTHORISED. Default DRAFT."},
                "line_items": {
                    "type": "array",
                    "description": "Line items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_amount": {"type": "number"},
                            "account_code": {"type": "string", "description": "GL account code."},
                        },
                        "required": ["description", "quantity", "unit_amount"],
                    },
                },
            },
            "required": ["contact_name", "type", "line_items"],
        },
    },
    {
        "name": "create_payment",
        "description": "Record a payment against an existing invoice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Xero InvoiceID to pay."},
                "account_code": {"type": "string", "description": "Bank/account code the payment is from."},
                "amount": {"type": "number", "description": "Payment amount."},
                "date": {"type": "string", "description": "Payment date YYYY-MM-DD (optional, defaults today)."},
            },
            "required": ["invoice_id", "account_code", "amount"],
        },
    },
    {
        "name": "create_bank_transaction",
        "description": (
            "Create a spend or receive bank transaction. Use type SPEND or RECEIVE. "
            "This is also the recommended way to record journal-like entries, since Xero "
            "does not allow writing manual journals via the API."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "SPEND or RECEIVE."},
                "contact_name": {"type": "string", "description": "Existing contact name."},
                "bank_account_code": {"type": "string", "description": "Bank account code."},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit_amount": {"type": "number"},
                            "account_code": {"type": "string"},
                        },
                        "required": ["description", "unit_amount", "account_code"],
                    },
                },
            },
            "required": ["type", "contact_name", "bank_account_code", "line_items"],
        },
    },
    {
        "name": "list_bank_transactions",
        "description": "List bank transactions. Optionally filter by type (SPEND, RECEIVE) or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Optional SPEND or RECEIVE filter."},
                "status": {"type": "string", "description": "Optional status filter."},
            },
        },
    },
    {
        "name": "list_payments",
        "description": "List payments. Optionally filter by invoice_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Optional InvoiceID to filter by."},
            },
        },
    },
    {
        "name": "get_invoice",
        "description": "Get a single invoice by InvoiceID.",
        "inputSchema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        },
    },
    {
        "name": "update_invoice_status",
        "description": "Update an invoice status (e.g., DRAFT, SUBMITTED, AUTHORISED, PAID, VOIDED).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "status": {"type": "string", "description": "New invoice status."},
            },
            "required": ["invoice_id", "status"],
        },
    },
    {
        "name": "get_contact",
        "description": "Get a single contact by ContactID.",
        "inputSchema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string"}},
            "required": ["contact_id"],
        },
    },
    {
        "name": "update_contact",
        "description": "Update a contact fields by ContactID (name and/or email).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "name": {"type": "string"},
                "email": {"type": "string"},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "list_items",
        "description": "List items.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_item",
        "description": "Create an item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Item Code (required)."},
                "name": {"type": "string", "description": "Optional item name."},
                "description": {"type": "string", "description": "Optional item description."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "update_item",
        "description": "Update an item by code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Item Code (required)."},
                "name": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "list_tracking_categories",
        "description": "List tracking categories.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tax_rates",
        "description": "List tax rates.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "xero_get",
        "description": "Generic GET against the Xero Accounting API (advanced).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "API path starting with / e.g. /Invoices"},
                "params": {"type": "object", "description": "Optional query params", "additionalProperties": True},
            },
            "required": ["path"],
        },
    },
    {
        "name": "xero_post",
        "description": "Generic POST against the Xero Accounting API (advanced).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "API path starting with / e.g. /Invoices"},
                "params": {"type": "object", "description": "Optional query params", "additionalProperties": True},
                "body": {"type": "object", "description": "JSON body", "additionalProperties": True},
            },
            "required": ["path", "body"],
        },
    },
    {
        "name": "xero_put",
        "description": "Generic PUT against the Xero Accounting API (advanced).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "API path starting with / e.g. /Payments"},
                "params": {"type": "object", "description": "Optional query params", "additionalProperties": True},
                "body": {"type": "object", "description": "JSON body", "additionalProperties": True},
            },
            "required": ["path", "body"],
        },
    },
    {
        "name": "xero_delete",
        "description": "Generic DELETE against the Xero Accounting API (advanced).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "API path starting with /"},
                "params": {"type": "object", "description": "Optional query params", "additionalProperties": True},
            },
            "required": ["path"],
        },
    },
]


# ---- Helpers ----------------------------------------------------------------

def _format_json(data: dict) -> str:
    text = json.dumps(data, indent=2, sort_keys=True)
    if len(text) > 9000:
        return text[:9000] + "\n... (truncated)"
    return text


def _validate_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError('path must start with "/" (e.g. /Invoices)')
    if "://" in path or ".." in path:
        raise ValueError("invalid path")
    # Prevent accidental attempts to call auth/identity endpoints via the generic tools.
    blocked = ("/connect", "/identity", "/oauth", "/token")
    if path.startswith(blocked):
        raise ValueError("path not allowed")
    return path


async def _find_contact_id(name: str) -> str:
    """Resolve a contact name to its Xero ContactID."""
    data = await api_request("GET", "/Contacts", params={"where": f'Name=="{name}"'})
    contacts = data.get("Contacts", [])
    if not contacts:
        raise ValueError(f'No contact found named "{name}". Create it first with create_contact.')
    return contacts[0]["ContactID"]


# ---- Handlers ---------------------------------------------------------------

async def call_tool(name: str, args: dict) -> str:
    if name == "get_organisation":
        data = await api_request("GET", "/Organisation")
        org = data.get("Organisations", [{}])[0]
        return (
            f"Name: {org.get('Name')}\n"
            f"Country: {org.get('CountryCode')}\n"
            f"Base currency: {org.get('BaseCurrency')}\n"
            f"Status: {org.get('OrganisationStatus')}"
        )

    if name == "list_contacts":
        params = {}
        if args.get("search"):
            params["where"] = f'Name.Contains("{args["search"]}")'
        data = await api_request("GET", "/Contacts", params=params or None)
        contacts = data.get("Contacts", [])
        if not contacts:
            return "No contacts found."
        lines = [f'- {c.get("Name")} ({c.get("EmailAddress") or "no email"}) [{c.get("ContactID")}]'
                 for c in contacts[:50]]
        return "\n".join(lines)

    if name == "list_invoices":
        params = {}
        if args.get("status"):
            params["where"] = f'Status=="{args["status"].upper()}"'
        data = await api_request("GET", "/Invoices", params=params or None)
        invoices = data.get("Invoices", [])
        if not invoices:
            return "No invoices found."
        lines = [
            f'- {inv.get("Type")} {inv.get("InvoiceNumber") or "(no number)"} | '
            f'{inv.get("Contact", {}).get("Name")} | {inv.get("Total")} {inv.get("CurrencyCode")} | '
            f'{inv.get("Status")} [{inv.get("InvoiceID")}]'
            for inv in invoices[:50]
        ]
        return "\n".join(lines)

    if name == "list_accounts":
        data = await api_request("GET", "/Accounts")
        accounts = data.get("Accounts", [])
        lines = [f'- {a.get("Code")}: {a.get("Name")} ({a.get("Type")})' for a in accounts]
        return "\n".join(lines) if lines else "No accounts found."

    if name == "list_journals":
        data = await api_request("GET", "/ManualJournals")
        journals = data.get("ManualJournals", [])
        if not journals:
            return "No manual journals found."
        lines = [
            f'- {j.get("Narration") or "(no narration)"} | {j.get("Total")} {j.get("CurrencyCode")} | '
            f'{j.get("Status")} [{j.get("ManualJournalID")}]'
            for j in journals[:50]
        ]
        return "\n".join(lines)

    if name == "create_contact":
        body = {"Contacts": [{"Name": args["name"]}]}
        if args.get("email"):
            body["Contacts"][0]["EmailAddress"] = args["email"]
        data = await api_request("POST", "/Contacts", json_body=body)
        c = data.get("Contacts", [{}])[0]
        return f'Created contact "{c.get("Name")}" [{c.get("ContactID")}]'

    if name == "create_invoice":
        contact_id = await _find_contact_id(args["contact_name"])
        line_items = [
            {
                "Description": li["description"],
                "Quantity": li["quantity"],
                "UnitAmount": li["unit_amount"],
                **({"AccountCode": li["account_code"]} if li.get("account_code") else {}),
            }
            for li in args["line_items"]
        ]
        body = {
            "Invoices": [
                {
                    "Type": args["type"].upper(),
                    "Contact": {"ContactID": contact_id},
                    "LineItems": line_items,
                    "Status": args.get("status", "DRAFT").upper(),
                }
            ]
        }
        data = await api_request("POST", "/Invoices", json_body=body)
        inv = data.get("Invoices", [{}])[0]
        return (
            f'Created {inv.get("Type")} invoice {inv.get("InvoiceNumber") or "(draft)"} | '
            f'Total {inv.get("Total")} {inv.get("CurrencyCode")} | Status {inv.get("Status")} '
            f'[{inv.get("InvoiceID")}]'
        )

    if name == "create_payment":
        body = {
            "Payments": [
                {
                    "Invoice": {"InvoiceID": args["invoice_id"]},
                    "Account": {"Code": args["account_code"]},
                    "Amount": args["amount"],
                    **({"Date": args["date"]} if args.get("date") else {}),
                }
            ]
        }
        data = await api_request("PUT", "/Payments", json_body=body)
        p = data.get("Payments", [{}])[0]
        return f'Recorded payment of {p.get("Amount")} [{p.get("PaymentID")}] status {p.get("Status")}'

    if name == "create_bank_transaction":
        contact_id = await _find_contact_id(args["contact_name"])
        line_items = [
            {
                "Description": li["description"],
                "Quantity": li.get("quantity", 1),
                "UnitAmount": li["unit_amount"],
                "AccountCode": li["account_code"],
            }
            for li in args["line_items"]
        ]
        body = {
            "BankTransactions": [
                {
                    "Type": args["type"].upper(),
                    "Contact": {"ContactID": contact_id},
                    "BankAccount": {"Code": args["bank_account_code"]},
                    "LineItems": line_items,
                }
            ]
        }
        data = await api_request("POST", "/BankTransactions", json_body=body)
        t = data.get("BankTransactions", [{}])[0]
        return f'Created {t.get("Type")} bank transaction {t.get("Total")} [{t.get("BankTransactionID")}]'

    if name == "list_bank_transactions":
        params = {}
        where = []
        if args.get("type"):
            where.append(f'Type=="{args["type"].upper()}"')
        if args.get("status"):
            where.append(f'Status=="{args["status"].upper()}"')
        if where:
            params["where"] = " && ".join(where)
        data = await api_request("GET", "/BankTransactions", params=params or None)
        txns = data.get("BankTransactions", [])
        if not txns:
            return "No bank transactions found."
        lines = [
            f'- {t.get("Type")} | {t.get("Date")} | {t.get("Total")} {t.get("CurrencyCode")} | '
            f'{t.get("Status")} [{t.get("BankTransactionID")}]'
            for t in txns[:50]
        ]
        return "\n".join(lines)

    if name == "list_payments":
        params = {}
        if args.get("invoice_id"):
            params["where"] = f'Invoice.InvoiceID==Guid("{args["invoice_id"]}")'
        data = await api_request("GET", "/Payments", params=params or None)
        payments = data.get("Payments", [])
        if not payments:
            return "No payments found."
        lines = [
            f'- {p.get("Date")} | {p.get("Amount")} | {p.get("Status")} [{p.get("PaymentID")}]'
            for p in payments[:50]
        ]
        return "\n".join(lines)

    if name == "get_invoice":
        invoice_id = args["invoice_id"]
        data = await api_request("GET", f"/Invoices/{invoice_id}")
        return _format_json(data)

    if name == "update_invoice_status":
        body = {"Invoices": [{"InvoiceID": args["invoice_id"], "Status": args["status"].upper()}]}
        data = await api_request("POST", "/Invoices", json_body=body)
        inv = data.get("Invoices", [{}])[0]
        return f'Updated invoice status to {inv.get("Status")} [{inv.get("InvoiceID")}]'

    if name == "get_contact":
        contact_id = args["contact_id"]
        data = await api_request("GET", f"/Contacts/{contact_id}")
        return _format_json(data)

    if name == "update_contact":
        contact = {"ContactID": args["contact_id"]}
        if args.get("name"):
            contact["Name"] = args["name"]
        if args.get("email"):
            contact["EmailAddress"] = args["email"]
        body = {"Contacts": [contact]}
        data = await api_request("POST", "/Contacts", json_body=body)
        c = data.get("Contacts", [{}])[0]
        return f'Updated contact "{c.get("Name")}" [{c.get("ContactID")}]'

    if name == "list_items":
        data = await api_request("GET", "/Items")
        items = data.get("Items", [])
        if not items:
            return "No items found."
        lines = [f'- {i.get("Code")}: {i.get("Name") or ""}'.rstrip() for i in items[:100]]
        return "\n".join(lines)

    if name == "create_item":
        item = {"Code": args["code"]}
        if args.get("name"):
            item["Name"] = args["name"]
        if args.get("description"):
            item["Description"] = args["description"]
        body = {"Items": [item]}
        data = await api_request("POST", "/Items", json_body=body)
        it = data.get("Items", [{}])[0]
        return f'Created item {it.get("Code")} [{it.get("ItemID")}]'

    if name == "update_item":
        item = {"Code": args["code"]}
        if args.get("name"):
            item["Name"] = args["name"]
        if args.get("description"):
            item["Description"] = args["description"]
        body = {"Items": [item]}
        data = await api_request("POST", "/Items", json_body=body)
        it = data.get("Items", [{}])[0]
        return f'Updated item {it.get("Code")} [{it.get("ItemID")}]'

    if name == "list_tracking_categories":
        data = await api_request("GET", "/TrackingCategories")
        cats = data.get("TrackingCategories", [])
        if not cats:
            return "No tracking categories found."
        lines = [f'- {c.get("Name")} [{c.get("TrackingCategoryID")}]' for c in cats[:50]]
        return "\n".join(lines)

    if name == "list_tax_rates":
        data = await api_request("GET", "/TaxRates")
        rates = data.get("TaxRates", [])
        if not rates:
            return "No tax rates found."
        lines = [f'- {r.get("Name")} ({r.get("TaxType")})' for r in rates[:100]]
        return "\n".join(lines)

    if name == "xero_get":
        path = _validate_path(args["path"])
        data = await api_request("GET", path, params=args.get("params"))
        return _format_json(data)

    if name == "xero_post":
        path = _validate_path(args["path"])
        data = await api_request("POST", path, params=args.get("params"), json_body=args["body"])
        return _format_json(data)

    if name == "xero_put":
        path = _validate_path(args["path"])
        data = await api_request("PUT", path, params=args.get("params"), json_body=args["body"])
        return _format_json(data)

    if name == "xero_delete":
        path = _validate_path(args["path"])
        data = await api_request("DELETE", path, params=args.get("params"))
        return _format_json(data)

    raise ValueError(f"Unknown tool: {name}")
