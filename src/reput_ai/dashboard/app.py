"""ReputationAI Web Dashboard — Streamlit UI on top of the existing backend API.

Run:
    streamlit run src/reput_ai/dashboard/app.py --server.port 8501

Headless First: every piece of data is fetched through the REST API
(``reput_ai.dashboard.client``); the dashboard never touches the database.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from reput_ai.config import settings
from reput_ai.dashboard.client import DashboardAPIError, ReputAPIClient
from reput_ai.dashboard.metrics import compute_kpis, filter_reviews, rating_distribution

st.set_page_config(
    page_title="ReputationAI Dashboard",
    page_icon="⭐",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = settings.DASHBOARD_API_BASE_URL


# ---------------------------------------------------------------------------
# Data loaders (cached; each builds its own short-lived client)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def load_health(base_url: str) -> dict[str, str]:
    with ReputAPIClient(base_url=base_url) as client:
        return client.health()


@st.cache_data(ttl=30, show_spinner=False)
def load_me(base_url: str, token: str) -> dict[str, Any]:
    with ReputAPIClient(base_url=base_url) as client:
        return client.me(token)


@st.cache_data(ttl=30, show_spinner=False)
def load_branches(base_url: str, token: str) -> list[dict[str, Any]]:
    with ReputAPIClient(base_url=base_url) as client:
        return client.list_branches(token)


@st.cache_data(ttl=30, show_spinner=False)
def load_reviews(base_url: str, token: str) -> list[dict[str, Any]]:
    with ReputAPIClient(base_url=base_url) as client:
        return client.list_reviews(token)


@st.cache_data(ttl=30, show_spinner=False)
def load_subscription(base_url: str, token: str) -> dict[str, Any]:
    with ReputAPIClient(base_url=base_url) as client:
        return client.get_subscription(token)


@st.cache_data(ttl=120, show_spinner=False)
def load_short_link(base_url: str, token: str, branch_id: str) -> dict[str, Any]:
    with ReputAPIClient(base_url=base_url) as client:
        return client.get_short_link(token, branch_id)


# ---------------------------------------------------------------------------
# Sidebar: connection + login
# ---------------------------------------------------------------------------
def render_sidebar() -> str | None:
    with st.sidebar:
        st.title("⭐ ReputationAI")
        st.caption(f"API: `{API_BASE}`")

        token: str | None = st.session_state.get("access_token")

        if token:
            st.success("Вы вошли в систему")
            if st.button("Выйти", use_container_width=True):
                st.session_state.pop("access_token", None)
                st.session_state.pop("user_email", None)
                st.cache_data.clear()
                st.rerun()
            return token

        st.subheader("Вход в API")
        with st.form("login_form"):
            email = st.text_input("E-mail", autocomplete="email")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти", use_container_width=True)
            if submitted:
                try:
                    with ReputAPIClient() as client:
                        access = client.login(email, password)
                        user = client.me(access)
                except DashboardAPIError as exc:
                    st.error(f"Ошибка входа: {exc.detail}")
                else:
                    st.session_state["access_token"] = access
                    st.session_state["user_email"] = user.get("email", email)
                    st.rerun()
        return None


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------
def render_dashboard(token: str) -> None:
    try:
        health = load_health(API_BASE)
        me = load_me(API_BASE, token)
        branches = load_branches(API_BASE, token)
        reviews = load_reviews(API_BASE, token)
        subscription = load_subscription(API_BASE, token)
    except DashboardAPIError as exc:
        st.error(f"Backend API недоступен: {exc.detail}")
        if st.button("Повторить"):
            st.cache_data.clear()
            st.rerun()
        return

    st.title("Панель управления репутацией")
    st.caption(f"Пользователь: {me.get('email', '—')} · API: {health.get('status', 'unknown')}")

    # -- KPI row --------------------------------------------------------
    kpi = compute_kpis(reviews)
    with st.container(horizontal=True):
        st.metric("Отзывов всего", kpi["total"], border=True)
        st.metric("Средний рейтинг", kpi["avg_rating"], border=True)
        st.metric("Негатив (1–3★)", kpi["negative"], border=True)
        st.metric("Позитив (4–5★)", kpi["positive"], border=True)
        st.metric("Ожидают обработки", kpi["pending"], border=True)

    # -- Filters --------------------------------------------------------
    branch_by_id = {str(b["id"]): b for b in branches}
    with st.sidebar:
        st.subheader("Фильтры")
        selected_branches = st.multiselect(
            "Филиалы",
            options=list(branch_by_id.keys()),
            format_func=lambda i: branch_by_id[i].get("name", i),
        )
        rating_range = st.slider("Рейтинг", 1, 5, (1, 5))
        statuses = sorted({str(r.get("status", "")) for r in reviews})
        selected_status = st.selectbox("Статус", ["(любой)"] + statuses)

    filtered = filter_reviews(
        reviews,
        branch_ids=set(selected_branches) or None,
        min_rating=rating_range[0],
        max_rating=rating_range[1],
        status=None if selected_status == "(любой)" else selected_status,
    )


    # -- Charts + subscription ------------------------------------------
    col_charts, col_sub = st.columns([3, 2])
    with col_charts, st.container(border=True):
        st.subheader("Распределение оценок")
        dist = rating_distribution(filtered)
        st.bar_chart(pd.Series(dist, name="Отзывов"), height=260)
    with col_sub, st.container(border=True):
        st.subheader("Подписка")
        st.metric("Статус", subscription.get("status", "—"), border=True)
        if subscription.get("trial_ends_at"):
            st.caption(f"Триал до: {subscription['trial_ends_at']}")
        if subscription.get("paid_until"):
            st.caption(f"Оплачено до: {subscription['paid_until']}")

    # -- Funnel quick access --------------------------------------------
    if branches:
        with st.container(border=True):
            st.subheader("Воронка перехвата (QR / короткая ссылка)")
            funnel_branch = st.selectbox(
                "Филиал",
                options=[str(b["id"]) for b in branches],
                format_func=lambda i: branch_by_id[i].get("name", i),
                key="funnel_branch",
            )
            if st.button("Получить короткую ссылку"):
                try:
                    link = load_short_link(API_BASE, token, funnel_branch)
                except DashboardAPIError as exc:
                    st.error(exc.detail)
                else:
                    st.code(link.get("short_url") or link.get("url") or str(link))
                    st.caption(
                        "4–5★ публикуются на площадках, 1–3★ уходят менеджменту в Telegram."
                    )

    # -- Reviews table ---------------------------------------------------
    with st.container(border=True):
        st.subheader(f"Отзывы ({len(filtered)})")
        if filtered:
            rows = [
                {
                    "Рейтинг": r.get("rating"),
                    "Автор": r.get("author_name"),
                    "Текст": r.get("text"),
                    "Статус": r.get("status"),
                    "Филиал": branch_by_id.get(str(r.get("branch_id")), {}).get("name", "—"),
                    "Создан": r.get("created_at"),
                }
                for r in filtered
            ]
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.info("Нет отзывов по выбранным фильтрам.")


def main() -> None:
    token = render_sidebar()
    if token is None:
        st.title("⭐ ReputationAI")
        st.info("Введите логин и пароль в боковой панели, чтобы подключиться к backend API.")
        try:
            health = load_health(API_BASE)
            st.success(f"Backend API доступен: {health.get('app', 'ReputationAI')}")
        except DashboardAPIError as exc:
            st.error(f"Backend API недоступен: {exc.detail}")
        return
    render_dashboard(token)


if __name__ == "__main__":
    main()

