"""
MCP tool definitions and their handlers.

READ tools:  list_contacts, list_invoices, get_organisation, list_accounts
WRITE tools: create_contact, create_invoice, create_payment, create_bank_transaction

Manual journals are intentionally NOT writable by this connector;
list_journals is provided read-only for visibility.
"""
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
]


# ---- Helpers ----------------------------------------------------------------

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

    raise ValueError(f"Unknown tool: {name}")
