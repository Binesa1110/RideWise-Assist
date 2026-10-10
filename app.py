from datetime import datetime, timezone

import streamlit as st

from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from analytics import AdminAuthorizationError, get_admin_analytics
from assistant import RideWiseAssistant
from chat_export import get_user_conversation, render_chat_pdf, render_chat_txt
from config import is_llm_configured
from customer_memory import (
    ISSUE_CATEGORIES,
    ISSUE_STATUSES,
    format_admin_memory_rows,
    list_admin_customer_memories,
)
from database import ensure_database

ensure_database()

st.set_page_config(page_title="RideWise Assist", page_icon="🚗", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --rw-navy: #0b1f33;
        --rw-navy-soft: #142f48;
        --rw-teal: #13b8a6;
        --rw-teal-deep: #087f78;
        --rw-ink: #16283a;
        --rw-muted: #607286;
        --rw-canvas: #f3f7fa;
    }
    .stApp {
        background:
            radial-gradient(ellipse at 88% 0%, rgba(19, 184, 166, .08), transparent 34%),
            var(--rw-canvas);
        color: var(--rw-ink);
    }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] {
        max-width: 1180px;
        padding-top: 1.35rem;
        padding-bottom: 2.2rem;
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, var(--rw-navy), #102a40 100%);
        border-right: 1px solid rgba(255,255,255,.08);
    }
    [data-testid="stSidebar"] * { color: #eaf3f8; }
    [data-testid="stSidebar"] [data-testid="stSelectbox"] > label,
    [data-testid="stSidebar"] [data-testid="stTextInput"] > label {
        color: #b8cad7;
        font-size: .82rem;
        font-weight: 600;
    }
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background: rgba(255,255,255,.09);
        border-color: rgba(255,255,255,.16);
        border-radius: 10px;
    }
    [data-testid="stSidebar"] [data-testid="stAlert"] {
        background: rgba(255,255,255,.08);
        border: 1px solid rgba(255,255,255,.1);
        border-radius: 12px;
    }
    [data-testid="stSidebar"] [data-testid="stButton"] button {
        color: white;
        background: var(--rw-teal-deep);
        border: 0;
        border-radius: 10px;
        font-weight: 700;
    }
    .rw-sidebar-brand {
        display: flex;
        align-items: center;
        gap: .7rem;
        margin: .1rem 0 1.1rem;
        color: white;
        font-size: 1.15rem;
        font-weight: 750;
        letter-spacing: -.02em;
    }
    .rw-brand-icon {
        display: grid;
        width: 2.35rem;
        height: 2.35rem;
        place-items: center;
        border-radius: 12px;
        background: linear-gradient(145deg, #20c9b4, #087f78);
        font-size: 1.2rem;
    }
    .rw-sidebar-label {
        margin: 1.1rem 0 .45rem;
        color: #91a9ba;
        font-size: .69rem;
        font-weight: 750;
        letter-spacing: .12em;
        text-transform: uppercase;
    }
    .rw-hero {
        position: relative;
        overflow: hidden;
        margin: 0 0 1.1rem;
        padding: 1.45rem 1.7rem;
        border: 1px solid rgba(255,255,255,.1);
        border-radius: 20px;
        background: linear-gradient(112deg, #0b1f33 0%, #123852 70%, #087f78 145%);
        box-shadow: 0 14px 36px rgba(11,31,51,.13);
        color: white;
    }
    .rw-hero:after {
        position: absolute;
        top: -95px;
        right: -45px;
        width: 260px;
        height: 260px;
        border: 1px solid rgba(255,255,255,.1);
        border-radius: 50%;
        box-shadow: 0 0 0 32px rgba(255,255,255,.025), 0 0 0 66px rgba(255,255,255,.02);
        content: "";
        pointer-events: none;
    }
    .rw-hero-top {
        display: flex;
        align-items: center;
        gap: .85rem;
    }
    .rw-hero-icon {
        display: grid;
        width: 3.2rem;
        height: 3.2rem;
        place-items: center;
        border: 1px solid rgba(255,255,255,.18);
        border-radius: 15px;
        background: rgba(255,255,255,.1);
        font-size: 1.65rem;
    }
    .rw-hero h1 {
        margin: 0;
        color: white;
        font-size: clamp(1.55rem, 3vw, 2.05rem);
        font-weight: 760;
        letter-spacing: -.04em;
    }
    .rw-hero p {
        margin: .28rem 0 0;
        color: #c6d8e2;
        font-size: .96rem;
    }
    .rw-hero-status {
        display: inline-flex;
        align-items: center;
        gap: .45rem;
        margin-top: 1rem;
        padding: .34rem .62rem;
        border: 1px solid rgba(255,255,255,.14);
        border-radius: 999px;
        background: rgba(255,255,255,.08);
        color: #e7f4f2;
        font-size: .76rem;
        font-weight: 650;
    }
    .rw-status-dot {
        width: .48rem;
        height: .48rem;
        border-radius: 50%;
        background: #36d6ad;
        box-shadow: 0 0 0 3px rgba(54,214,173,.16);
    }
    .rw-status-dot.offline { background: #f0ae5c; box-shadow: 0 0 0 3px rgba(240,174,92,.16); }
    [data-testid="stRadio"] > label,
    [data-testid="stSubheader"] {
        color: var(--rw-navy);
        font-weight: 700;
    }
    [data-testid="stChatMessage"] {
        gap: .8rem;
        margin-bottom: .7rem;
        padding: 1rem 1.1rem;
        border: 1px solid #e2eaf0;
        border-radius: 16px;
        background: white;
        box-shadow: 0 5px 16px rgba(20,47,72,.045);
    }
    [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
        border-color: #cdece8;
        background: linear-gradient(110deg, #e9f8f6, #f8fcfb);
    }
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
        color: var(--rw-ink);
        font-size: .97rem;
        line-height: 1.62;
    }
    [data-testid="stChatInput"] {
        border: 1px solid #d8e4eb;
        border-radius: 16px;
        background: white;
        box-shadow: 0 8px 22px rgba(20,47,72,.07);
    }
    [data-testid="stChatInput"]:focus-within {
        border-color: var(--rw-teal);
        box-shadow: 0 0 0 3px rgba(19,184,166,.13);
    }
    [data-testid="stButton"] button,
    [data-testid="stDownloadButton"] button {
        min-height: 2.65rem;
        border: 1px solid #dce6ec;
        border-radius: 11px;
        background: white;
        color: var(--rw-navy);
        font-weight: 600;
        transition: border-color .15s ease, background .15s ease, transform .15s ease;
    }
    [data-testid="stButton"] button:hover,
    [data-testid="stDownloadButton"] button:hover {
        border-color: var(--rw-teal);
        background: #eefaf8;
        color: var(--rw-teal-deep);
        transform: translateY(-1px);
    }
    [data-testid="stMetric"] {
        padding: .85rem 1rem;
        border: 1px solid #e1eaf0;
        border-radius: 14px;
        background: white;
        box-shadow: 0 4px 14px rgba(20,47,72,.04);
    }
    [data-testid="stDataFrame"] {
        overflow: hidden;
        border: 1px solid #e1eaf0;
        border-radius: 14px;
        background: white;
    }
    h2, h3 { letter-spacing: -.025em; }
    @media (max-width: 700px) {
        [data-testid="stMainBlockContainer"] {
            padding: .8rem .85rem 1.5rem;
        }
        .rw-hero { padding: 1.1rem 1rem; border-radius: 16px; }
        .rw-hero:after { right: -180px; }
        .rw-hero-icon { width: 2.7rem; height: 2.7rem; }
        [data-testid="stChatMessage"] { padding: .8rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "user" not in st.session_state:
    st.session_state.user = DEFAULT_ACCESS_CONTROL.login("customer", "customer")

# Bind the existing Streamlit conversation state to the user before a role switch.
get_user_conversation(st.session_state, st.session_state.user)

with st.sidebar:
    st.markdown(
        '<div class="rw-sidebar-brand"><span class="rw-brand-icon">🚘</span>'
        "<span>RideWise Assist</span></div>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="rw-sidebar-label">Connection</div>', unsafe_allow_html=True)
    if is_llm_configured():
        st.success("AI connected · NeoStats")
    else:
        st.warning("AI unavailable · configuration missing")

    st.markdown('<div class="rw-sidebar-label">Workspace</div>', unsafe_allow_html=True)
    st.info("RideWise workspace ready")
    st.markdown('<div class="rw-sidebar-label">Development access</div>', unsafe_allow_html=True)
    st.caption(
        "Development-only sign-in. This role selector is not production authentication."
    )

    role = st.selectbox("Development login role", ["customer", "support", "admin"])
    username = st.text_input("Username", value=role)
    if st.button("Log in"):
        user = DEFAULT_ACCESS_CONTROL.login(username, role)
        if user:
            st.session_state["user"] = user
            st.success(f"Logged in as {user.username} ({user.role})")
        else:
            st.error("Login failed in development mode.")

user: DevelopmentUser = st.session_state.user
messages = get_user_conversation(st.session_state, user)
pages = ["Chat", "Admin analytics"] if user.role == "admin" else ["Chat"]
page = st.radio("Page", pages, horizontal=True, key=f"page_{user.role}")

if page == "Admin analytics":
    try:
        analytics = get_admin_analytics(user)
    except AdminAuthorizationError as error:
        st.error(str(error))
        st.stop()

    st.markdown(
        '<div class="rw-hero"><div class="rw-hero-top">'
        '<div class="rw-hero-icon">📊</div><div><h1>Admin analytics</h1>'
        '<p>Operational overview and customer issue insights</p></div></div></div>',
        unsafe_allow_html=True,
    )
    st.caption("Aggregate metrics from the configured SQLite database; no customer details are shown.")

    bookings = analytics["bookings"]
    if not analytics["coverage"]["bookings_table_available"]:
        st.warning("Bookings table is unavailable in the configured database.")
    booking_columns = st.columns(4)
    for column, label, value in zip(
        booking_columns,
        ("Total bookings", "Active", "Completed", "Cancelled"),
        (bookings["total"], bookings["active"], bookings["completed"], bookings["cancelled"]),
    ):
        column.metric(label, value if value is not None else "Unavailable")
    st.caption(
        "Active/completed/cancelled counts match those exact status values "
        "(both cancelled spellings are recognized); other values remain in the distribution."
    )

    st.subheader("Booking status distribution")
    if bookings["status_distribution"]:
        st.dataframe(bookings["status_distribution"], hide_index=True, use_container_width=True)
    elif analytics["coverage"]["bookings_table_available"]:
        st.info("No booking status records are available.")

    st.subheader("Cancellation trend")
    if bookings["cancellation_trend"] is None:
        st.info(bookings["cancellation_trend_note"])
    elif bookings["cancellation_trend"]:
        st.dataframe(bookings["cancellation_trend"], hide_index=True, use_container_width=True)
        st.caption(bookings["cancellation_trend_note"])
    else:
        st.info("No dated cancellation records are available.")

    payments = analytics["payments"]
    st.subheader("Payment totals by recorded status and currency")
    if not analytics["coverage"]["payments_table_available"]:
        st.warning("Payments table is unavailable in the configured database.")
    elif payments["by_status"]:
        st.dataframe(payments["by_status"], hide_index=True, use_container_width=True)
        st.caption(payments["revenue_note"])
    else:
        st.info("No payment records with status data are available.")

    if payments["paid_totals"]:
        st.subheader("Recorded paid amounts (not net revenue)")
        st.dataframe(payments["paid_totals"], hide_index=True, use_container_width=True)

    st.subheader("Payment trend")
    if payments["monthly"] is None:
        st.info(payments["monthly_note"])
    elif payments["monthly"]:
        st.dataframe(payments["monthly"], hide_index=True, use_container_width=True)
        st.caption(payments["monthly_note"])
    else:
        st.info("No payments with valid payment dates are available.")

    damages = analytics["damage_reports"]
    st.subheader("Damage reports by status")
    if not analytics["coverage"]["damage_reports_table_available"]:
        st.warning("Damage reports table is unavailable in the configured database.")
    elif damages["by_status"]:
        st.metric("Total damage reports", damages["total"])
        st.dataframe(damages["by_status"], hide_index=True, use_container_width=True)
    else:
        st.metric("Total damage reports", damages["total"])
        st.info("No damage report status records are available.")

    st.subheader("Customer Memory & Previous Issues")
    memory_search = st.text_input(
        "Search customer ID or issue keywords", key="memory_search"
    )
    memory_filter_col1, memory_filter_col2 = st.columns(2)
    memory_category = memory_filter_col1.selectbox(
        "Issue category",
        ["All"] + sorted(ISSUE_CATEGORIES),
        key="memory_category",
    )
    memory_status = memory_filter_col2.selectbox(
        "Issue status",
        ["All"] + sorted(ISSUE_STATUSES),
        key="memory_status",
    )
    try:
        memory_records = list_admin_customer_memories(
            user,
            search=memory_search,
            issue_category="" if memory_category == "All" else memory_category,
            issue_status="" if memory_status == "All" else memory_status,
        )
    except (AdminAuthorizationError, PermissionError) as error:
        st.error(str(error))
        st.stop()
    if memory_records:
        st.dataframe(
            format_admin_memory_rows(memory_records),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No customer memory records match these filters.")
    st.stop()

connected = is_llm_configured()
status_class = "" if connected else "offline"
status_label = "AI connected · NeoStats" if connected else "AI unavailable"
st.markdown(
    '<section class="rw-hero"><div class="rw-hero-top">'
    '<div class="rw-hero-icon">🚘</div><div>'
    '<h1>RideWise Assist</h1>'
    '<p>Your intelligent ride-support assistant</p></div></div>'
    f'<div class="rw-hero-status"><span class="rw-status-dot {status_class}"></span>'
    f'{status_label}</div></section>',
    unsafe_allow_html=True,
)
st.caption(f"Signed in as **{user.username}** · {user.role.title()} workspace")
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Message RideWise Assist…")
if prompt:
    messages.append(
        {
            "role": "user",
            "content": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    with st.chat_message("user"):
        st.markdown(prompt)

    assistant = RideWiseAssistant(user=user)
    response = assistant.answer_question(prompt)
    response_text = response.get("answer", "No answer available.")
    with st.chat_message("assistant"):
        st.markdown(response_text)
    messages.append(
        {
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )

st.subheader("Suggested questions")
questions = [
    "What is the refund policy for a cancellation?",
    "What records can I access as a customer?",
    "How do late return fees work?",
    "Can you explain EV battery charges?",
]
for row_start in range(0, len(questions), 2):
    question_columns = st.columns(2)
    for column, q in zip(question_columns, questions[row_start : row_start + 2]):
        if not column.button(q, key=q, use_container_width=True):
            continue
        messages.append(
            {
                "role": "user",
                "content": q,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        with st.chat_message("user"):
            st.markdown(q)
        assistant = RideWiseAssistant(user=user)
        out = assistant.answer_question(q)
        answer = out.get("answer", "No answer available.")
        with st.chat_message("assistant"):
            st.markdown(answer)
        messages.append(
            {
                "role": "assistant",
                "content": answer,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

st.subheader("Export this conversation")
st.caption("Exports include only this user's visible chat messages and do not call the LLM.")
export_column_txt, export_column_pdf = st.columns(2)
export_time = datetime.now(timezone.utc)
export_column_txt.download_button(
    "Download TXT",
    data=render_chat_txt(messages, export_time),
    file_name="ridewise_conversation.txt",
    mime="text/plain",
    disabled=not messages,
)
export_column_pdf.download_button(
    "Download PDF",
    data=render_chat_pdf(messages, export_time),
    file_name="ridewise_conversation.pdf",
    mime="application/pdf",
    disabled=not messages,
)
if not messages:
    st.info("There are no messages in this conversation to export yet.")
