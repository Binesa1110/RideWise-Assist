# Design decisions for RideWise Assist

## Architecture

- The project uses a simple Python package layout to keep the app easy to run and test within a short hackathon window.
- SQLite is used through Python's built-in sqlite3 module so there is no extra database service dependency.
- Streamlit is the main interface to keep fast prototyping and local demos straightforward.

## Development assumptions

- If the original `ridewise.db` is not present, the app creates a clearly labelled development database in `data/ridewise_dev.db` instead of fabricating production data.
- Policy PDFs are loaded from `policies/` only when present. Missing files do not block the app but are clearly reported.
- LLM integration remains optional and guarded by environment variables. If the NeoStats endpoint is missing, the application continues running and clearly explains the limitation.

## Security and privacy

- Authorization checks are enforced in server-side logic, not only in the UI.
- Customer users are prevented from reading another customer's private records.
- Secrets stay in environment variables and `.env`, never in code or UI output.
