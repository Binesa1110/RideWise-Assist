# RideWise Assist

RideWise Assist is a lightweight Streamlit application for ride-sharing operations support. It can answer booking, cancellation, damage, and policy questions while clearly reporting missing policy or LLM configuration.

## Features

- SQLite-backed local database for customer, booking, payment, and damage information
- Development user roles for customer, support, and admin access
- User-scoped TXT and PDF export of the current chat
- Administrator dashboard with aggregate booking, payment, and damage metrics
- Compact, customer-scoped memory of prior issues with an admin-only review table
- NeoStats tool-calling agent for authorized booking/damage lookups, policy retrieval, deterministic calculations, and admin analytics
- Policy PDF loading using PyMuPDF with graceful missing-file handling
- Optional NeoStats LLM integration via environment configuration
- Pytest coverage for database, access control, and policy logic

## Setup

1. Create a virtual environment and install dependencies:
   python -m pip install -r requirements.txt
2. Copy the environment template:
   copy .env.example .env
3. Update the NeoStats values if you have access to the official endpoint.
4. Run the app:
   streamlit run app.py

## Local execution

python -m pip install -r requirements.txt
streamlit run app.py

## Notes

- The app is intentionally development-oriented when the original database or policy PDFs are not present.
- The sidebar role selector is a development convenience, not authentication: anyone with app access can select any role, including admin. The dashboard's server-side permission check enforces the application's role rules, but the selector must be replaced by trusted identity-provider authentication before deployment.
- Admin analytics use the existing SQLite database and display aggregates only. The current booking schema has no cancellation timestamp, so cancellation trends are reported as unavailable rather than inferred from unrelated dates. Payment amounts are grouped by their recorded status; only `paid` records appear in the paid-amount total, which is not net revenue because refunds and chargebacks are not modeled. Monthly payment metrics use valid `paid_at` values only.
- Status columns are free-form text in SQLite. Existing development records use booking statuses `active` and `completed`, payment statuses `paid` and `pending`, and damage-report status `open`; analytics retain additional status values in their distributions instead of silently classifying them.
- Analytics currently use `bookings.status`, `payments.status/amount/currency/paid_at`, and `damage_reports.status`. The database also has booking start/end times and damage-report creation times, but these are not used as cancellation dates or payment dates.
- No secrets or credentials should be committed to source control.
- If policy files or database records are missing, the app reports the gap instead of inventing data.
- Agent tool selection requires a configured NeoStats deployment that supports OpenAI-compatible chat-completion tools. Tool calls are capped, arguments are validated, customer queries are scoped to the development user's own database ID, and arithmetic inputs must be stated by the user or sourced from an authorized booking.
- Customer memory stores only validated issue categories and compact keywords in the existing SQLite database, never full chat transcripts. Memory extraction uses the configured NeoStats deployment after meaningful customer issues; extraction or persistence errors are logged and do not block the answer. Previous-issue queries search only the verified customer's rows, ask for clarification when top matches tie, and state when no match exists. Admin memory search exposes customer IDs and issue summaries, not contact details.
- The current `policies/` directory contains no rental policy PDFs. Until applicable policy documents are added, the agent cannot determine refund eligibility or policy-based penalties. Refund and cancellation-penalty tools only perform calculations from explicit inputs and do not establish eligibility.
