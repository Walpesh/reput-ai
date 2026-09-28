"""ReputationAI Web Dashboard — Streamlit UI on top of the existing backend API.

The dashboard is a thin, headless consumer of the FastAPI REST API: every piece of
data (users, branches, reviews, subscriptions) is fetched through the centralized
API layer ``reput_ai.dashboard.client`` — the dashboard never touches the database.

Pages (navigation in the sidebar):
    Обзор    — KPI, filters, rating chart, funnel short link, review table
    Филиалы  — CompanyBranch list / creation / configuration
    Отзывы   — Review list, details and the existing backend state transitions
    Подписка — Subscription information (status, trial_ends_at, paid_until,
                payment_provider_id)

Run:
    streamlit run src/reput_ai/dashboard/app.py --server.port 8501
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from reput_ai.config import settings
from reput_ai.dashboard import client as api
from reput_ai.dashboard.client import DashboardAPIError
from reput_ai.dashboard.metrics import compute_kpis, filter_reviews, rating_distribution
from reput_ai.dashboard.options import (
    PLATFORM_TYPES,
    REVIEW_STATUSES,
    TONE_OF_VOICE_OPTIONS,
)

st.set_page_config(
    page_title="ReputationAI Dashboard",
    page_icon="⭐",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = settings.DASHBOARD_API_BASE_URL
NAV_PAGES = ["Обзор", "Филиалы", "Отзывы", "Подписка"]
ANY_OPTION = "(любой)"


# ---------------------------------------------------------------------------
# Data loaders (cached; each builds its own short-lived client)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def load_health(base_url: str) -> dict[str, str]:
    with api.ReputAPIClient(base_url=base_url) as client:
        return client.health()


@st.cache_data(ttl=30, show_spinner=False)
def load_me(base_url: str, token: str) -> dict[str, Any]:
    with api.ReputAPIClient(base_url=base_url) as client:
        return client.me(token)


@st.cache_data(ttl=30, show_spinner=False)
def load_branches(base_url: str, token: str) -> list[dict[str, Any]]:
    with api.ReputAPIClient(base_url=base_url) as client:
        return client.list_branches(token)


@st.cache_data(ttl=30, show_spinner=False)
def load_reviews(base_url: str, token: str) -> list[dict[str, Any]]:
    with api.ReputAPIClient(base_url=base_url) as client:
        return client.list_reviews(token)


@st.cache_data(ttl=30, show_spinner=False)
def load_subscription(base_url: str, token: str) -> dict[str, Any]:
    with api.ReputAPIClient(base_url=base_url) as client:
        return client.get_subscription(token)


@st.cache_data(ttl=120, show_spinner=False)
def load_short_link(base_url: str, token: str, branch_id: str) -> dict[str, Any]:
    with api.ReputAPIClient(base_url=base_url) as client:
        return client.get_short_link(token, branch_id)


# ---------------------------------------------------------------------------
# Session / error handling helpers
# ---------------------------------------------------------------------------
def _logout() -> None:
    """Drop the session: the JWT lives in ``st.session_state`` only."""
    st.session_state.pop("access_token", None)
    st.session_state.pop("user_email", None)
    st.cache_data.clear()
    st.rerun()


def _flash(message: str) -> None:
    """Queue a success message that survives the post-mutation rerun."""
    st.session_state["flash_message"] = message


def _render_flash() -> None:
    message = st.session_state.pop("flash_message", None)
    if message:
        st.success(message)


def render_api_error(exc: DashboardAPIError) -> None:
    """Explicit handling of auth / validation / server / network API errors."""
    if exc.status_code == 401:
        st.error("Сессия недействительна или истекла. Войдите снова.")
        if st.button("Выйти", key="logout_after_error", width="stretch"):
            _logout()
    elif exc.status_code == 0:
        st.error(f"Backend API недоступен: {exc.detail}")
        if st.button("Повторить", key="retry_after_error", width="stretch"):
            st.cache_data.clear()
            st.rerun()
    elif exc.status_code == 422:
        st.error(f"Ошибка валидации данных: {exc.detail}")
    elif exc.status_code >= 500:
        st.error(f"Ошибка сервера ({exc.status_code}): {exc.detail}")
    else:
        st.error(f"Ошибка API ({exc.status_code}): {exc.detail}")


# ---------------------------------------------------------------------------
# Authentication UI (existing backend auth endpoints only)
# ---------------------------------------------------------------------------
def _handle_register(email: str, password: str, telegram_raw: str) -> None:
    """Create an account via POST /auth/register and sign in right away."""
    if not email.strip() or not password:
        st.error("Укажите e-mail и пароль.")
        return
    telegram_id: int | None = None
    if telegram_raw.strip():
        try:
            telegram_id = int(telegram_raw.strip())
        except ValueError:
            st.error("Telegram ID должен быть числом.")
            return
    try:
        with api.ReputAPIClient(base_url=API_BASE) as client:
            client.register(email.strip(), password, telegram_id)
            access = client.login(email.strip(), password)
            user = client.me(access)
    except DashboardAPIError as exc:
        st.error(f"Ошибка регистрации: {exc.detail}")
    else:
        st.session_state["access_token"] = access
        st.session_state["user_email"] = user.get("email", email)
        st.cache_data.clear()
        st.rerun()


def render_sidebar() -> str | None:
    with st.sidebar:
        st.title("⭐ ReputationAI")
        st.caption(f"API: `{API_BASE}`")

        token: str | None = st.session_state.get("access_token")
        if token:
            st.success("Вы вошли в систему")
            if st.button("Выйти", key="logout_button", width="stretch"):
                _logout()
            return token

        st.subheader("Вход в API")
        with st.form("login_form"):
            email = st.text_input("E-mail", autocomplete="email", key="login_email")
            password = st.text_input("Пароль", type="password", key="login_password")
            if st.form_submit_button("Войти", key="login_submit", width="stretch"):
                try:
                    with api.ReputAPIClient(base_url=API_BASE) as client:
                        access = client.login(email, password)
                        user = client.me(access)
                except DashboardAPIError as exc:
                    st.error(f"Ошибка входа: {exc.detail}")
                else:
                    st.session_state["access_token"] = access
                    st.session_state["user_email"] = user.get("email", email)
                    st.cache_data.clear()
                    st.rerun()

        with st.expander("Регистрация"), st.form("register_form"):
            reg_email = st.text_input("E-mail", key="reg_email")
            reg_password = st.text_input("Пароль", type="password", key="reg_password")
            reg_telegram = st.text_input("Telegram ID (необязательно)", key="reg_telegram")
            if st.form_submit_button(
                "Создать аккаунт", key="reg_submit", width="stretch"
            ):
                _handle_register(reg_email, reg_password, reg_telegram)
        return None


def render_nav() -> str:
    with st.sidebar:
        st.subheader("Разделы")
        return st.radio(
            "Разделы", NAV_PAGES, key="nav_page", label_visibility="collapsed"
        )


def render_public_page() -> None:
    st.title("⭐ ReputationAI")
    st.info(
        "Войдите или зарегистрируйтесь в боковой панели, "
        "чтобы подключиться к backend API."
    )
    try:
        health = load_health(API_BASE)
    except DashboardAPIError as exc:
        render_api_error(exc)
    else:
        st.success(f"Backend API доступен: {health.get('app', 'ReputationAI')}")


# ---------------------------------------------------------------------------
# Page: Обзор (KPI, filters, rating chart, funnel, review table)
# ---------------------------------------------------------------------------
def render_overview(token: str) -> None:
    try:
        health = load_health(API_BASE)
        me = load_me(API_BASE, token)
        branches = load_branches(API_BASE, token)
        reviews = load_reviews(API_BASE, token)
        subscription = load_subscription(API_BASE, token)
    except DashboardAPIError as exc:
        render_api_error(exc)
        return

    st.title("Панель управления репутацией")
    st.caption(
        f"Пользователь: {me.get('email', '—')} · "
        f"API: {health.get('status', 'unknown')}"
    )

    # -- KPI row ---------------------------------------------------------
    kpi = compute_kpis(reviews)
    with st.container(horizontal=True):
        st.metric("Отзывов всего", kpi["total"], border=True)
        st.metric("Средний рейтинг", kpi["avg_rating"], border=True)
        st.metric("Негатив (1–3★)", kpi["negative"], border=True)
        st.metric("Позитив (4–5★)", kpi["positive"], border=True)
        st.metric("Ожидают обработки", kpi["pending"], border=True)

    # -- Filters (sidebar) ----------------------------------------------
    branch_by_id = {str(b["id"]): b for b in branches}
    with st.sidebar:
        st.subheader("Фильтры")
        selected_branches = st.multiselect(
            "Филиалы",
            options=list(branch_by_id.keys()),
            format_func=lambda i: branch_by_id[i].get("name", i),
            key="ov_branches",
        )
        rating_range = st.slider("Рейтинг", 1, 5, (1, 5), key="ov_rating")
        statuses = sorted({str(r.get("status", "")) for r in reviews})
        selected_status = st.selectbox(
            "Статус", [ANY_OPTION] + statuses, key="ov_status"
        )

    filtered = filter_reviews(
        reviews,
        branch_ids=set(selected_branches) or None,
        min_rating=rating_range[0],
        max_rating=rating_range[1],
        status=None if selected_status == ANY_OPTION else selected_status,
    )

    # -- Charts + subscription summary -----------------------------------
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
            if st.button("Получить короткую ссылку", key="funnel_get_link"):
                try:
                    link = load_short_link(API_BASE, token, funnel_branch)
                except DashboardAPIError as exc:
                    st.error(exc.detail)
                else:
                    st.code(link.get("short_url") or link.get("url") or str(link))
                    st.caption(
                        "4–5★ публикуются на площадках, "
                        "1–3★ уходят менеджменту в Telegram."
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
                    "Филиал": branch_by_id.get(str(r.get("branch_id")), {}).get(
                        "name", "—"
                    ),
                    "Создан": r.get("created_at"),
                }
                for r in filtered
            ]
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.info("Нет отзывов по выбранным фильтрам.")


# ---------------------------------------------------------------------------
# Page: Филиалы (CompanyBranch list / creation / configuration)
# ---------------------------------------------------------------------------
def render_branches(token: str) -> None:
    st.title("Филиалы")
    try:
        branches = load_branches(API_BASE, token)
    except DashboardAPIError as exc:
        render_api_error(exc)
        return

    branch_by_id = {str(b["id"]): b for b in branches}
    if branches:
        rows = [
            {
                "Название": b.get("name"),
                "Платформа": b.get("platform_type"),
                "Ссылка": b.get("platform_url"),
                "Tone of voice": b.get("tone_of_voice"),
                "Активен": b.get("is_active"),
            }
            for b in branches
        ]
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Филиалов пока нет — создайте первый.")

    _render_branch_create(token)
    if branches:
        _render_branch_edit(token, branch_by_id)


def _render_branch_create(token: str) -> None:
    st.subheader("Новый филиал")
    with st.form("branch_create_form"):
        name = st.text_input("Название", key="branch_name")
        platform_type = st.selectbox(
            "Платформа", PLATFORM_TYPES, key="branch_platform_type"
        )
        platform_url = st.text_input(
            "Ссылка на карточку (platform_url)", key="branch_platform_url"
        )
        tone_of_voice = st.selectbox(
            "Tone of voice", TONE_OF_VOICE_OPTIONS, key="branch_tone"
        )
        is_active = st.checkbox("Активен", value=True, key="branch_is_active")
        submitted = st.form_submit_button(
            "Создать филиал", key="branch_create_submit", width="stretch"
        )
    if not submitted:
        return
    if not name.strip() or not platform_url.strip():
        st.error("Заполните название и ссылку на карточку.")
        return
    try:
        with api.ReputAPIClient(base_url=API_BASE) as client:
            created = client.create_branch(
                token,
                name=name.strip(),
                platform_type=platform_type,
                platform_url=platform_url.strip(),
                tone_of_voice=tone_of_voice,
                is_active=is_active,
            )
    except DashboardAPIError as exc:
        render_api_error(exc)
    else:
        st.cache_data.clear()
        _flash(f"Филиал «{created.get('name', name.strip())}» создан.")
        st.rerun()


def _render_branch_edit(
    token: str, branch_by_id: dict[str, dict[str, Any]]
) -> None:
    st.subheader("Настройка филиала")
    selected = st.selectbox(
        "Филиал",
        options=list(branch_by_id.keys()),
        format_func=lambda i: branch_by_id[i].get("name", i),
        key="branch_edit_select",
    )
    branch = branch_by_id[selected]
    st.caption(
        "PATCH /api/v1/branches/{id} принимает только name, tone_of_voice и "
        "is_active: платформа и URL филиала меняются только при создании."
    )
    with st.form(key=f"branch_edit_form_{selected}"):
        name = st.text_input(
            "Название",
            value=str(branch.get("name", "")),
            key=f"branch_edit_name_{selected}",
        )
        st.text_input(
            "Платформа (только чтение)",
            value=str(branch.get("platform_type", "")),
            key=f"branch_edit_platform_{selected}",
            disabled=True,
        )
        st.text_input(
            "Ссылка (только чтение)",
            value=str(branch.get("platform_url", "")),
            key=f"branch_edit_url_{selected}",
            disabled=True,
        )
        current_tone = str(branch.get("tone_of_voice", TONE_OF_VOICE_OPTIONS[0]))
        tone_index = (
            TONE_OF_VOICE_OPTIONS.index(current_tone)
            if current_tone in TONE_OF_VOICE_OPTIONS
            else 0
        )
        tone = st.selectbox(
            "Tone of voice",
            TONE_OF_VOICE_OPTIONS,
            index=tone_index,
            key=f"branch_edit_tone_{selected}",
        )
        is_active = st.checkbox(
            "Активен",
            value=bool(branch.get("is_active", True)),
            key=f"branch_edit_active_{selected}",
        )
        submitted = st.form_submit_button(
            "Сохранить", key=f"branch_edit_submit_{selected}", width="stretch"
        )
    if not submitted:
        return
    if not name.strip():
        st.error("Название не может быть пустым.")
        return
    try:
        with api.ReputAPIClient(base_url=API_BASE) as client:
            updated = client.update_branch(
                token,
                selected,
                name=name.strip(),
                tone_of_voice=tone,
                is_active=is_active,
            )
    except DashboardAPIError as exc:
        render_api_error(exc)
    else:
        st.cache_data.clear()
        _flash(f"Филиал «{updated.get('name', name.strip())}» обновлён.")
        st.rerun()


# ---------------------------------------------------------------------------
# Page: Отзывы (list / details / existing backend state transitions)
# ---------------------------------------------------------------------------
def _review_label(
    review_id: str,
    reviews: list[dict[str, Any]],
    branch_by_id: dict[str, dict[str, Any]],
) -> str:
    review = next(r for r in reviews if str(r["id"]) == review_id)
    branch = branch_by_id.get(str(review.get("branch_id")), {}).get("name", "—")
    return (
        f"{review.get('author_name')} · {review.get('rating')}★ · "
        f"{review.get('status')} · {branch}"
    )


def render_reviews(token: str) -> None:
    st.title("Отзывы")
    try:
        branches = load_branches(API_BASE, token)
        reviews = load_reviews(API_BASE, token)
    except DashboardAPIError as exc:
        render_api_error(exc)
        return

    branch_by_id = {str(b["id"]): b for b in branches}

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_branches = st.multiselect(
            "Филиалы",
            options=list(branch_by_id.keys()),
            format_func=lambda i: branch_by_id[i].get("name", i),
            key="rev_branches",
        )
    with col2:
        rating_range = st.slider("Рейтинг", 1, 5, (1, 5), key="rev_rating")
    with col3:
        selected_status = st.selectbox(
            "Статус", [ANY_OPTION] + REVIEW_STATUSES, key="rev_status_filter"
        )

    filtered = filter_reviews(
        reviews,
        branch_ids=set(selected_branches) or None,
        min_rating=rating_range[0],
        max_rating=rating_range[1],
        status=None if selected_status == ANY_OPTION else selected_status,
    )

    st.subheader(f"Список отзывов ({len(filtered)})")
    if not filtered:
        st.info("Нет отзывов по выбранным фильтрам.")
        return

    rows = [
        {
            "Автор": r.get("author_name"),
            "Рейтинг": r.get("rating"),
            "Текст": r.get("text"),
            "Статус": r.get("status"),
            "Филиал": branch_by_id.get(str(r.get("branch_id")), {}).get("name", "—"),
            "Создан": r.get("created_at"),
        }
        for r in filtered
    ]
    st.dataframe(rows, hide_index=True, width="stretch")

    st.subheader("Детали отзыва")
    selected_review = st.selectbox(
        "Отзыв",
        options=[str(r["id"]) for r in filtered],
        format_func=lambda i: _review_label(i, filtered, branch_by_id),
        key="rev_select",
    )
    review = next(r for r in filtered if str(r["id"]) == selected_review)
    _render_review_details(review, branch_by_id)
    _render_review_transition(token, review)


def _render_review_details(
    review: dict[str, Any], branch_by_id: dict[str, dict[str, Any]]
) -> None:
    branch = branch_by_id.get(str(review.get("branch_id")), {})
    st.markdown(f"**Филиал:** {branch.get('name', '—')}")
    st.markdown(f"**Автор (author_name):** {review.get('author_name', '—')}")
    st.markdown(f"**Рейтинг (rating):** {review.get('rating')} / 5")
    st.markdown(f"**Статус (status):** `{review.get('status')}`")
    st.markdown(f"**Создан (created_at):** {review.get('created_at')}")
    st.markdown("**Текст отзыва (text):**")
    st.write(review.get("text") or "—")
    st.markdown("**Сгенерированный ответ (generated_reply):**")
    st.write(review.get("generated_reply") or "—")
    st.markdown("**Финальный ответ (final_reply):**")
    st.write(review.get("final_reply") or "—")


def _render_review_transition(token: str, review: dict[str, Any]) -> None:
    review_id = str(review["id"])
    current_status = str(review.get("status", REVIEW_STATUSES[0]))
    st.subheader("Переход статуса отзыва")
    with st.form(key=f"review_status_form_{review_id}"):
        status_index = (
            REVIEW_STATUSES.index(current_status)
            if current_status in REVIEW_STATUSES
            else 0
        )
        new_status = st.selectbox(
            "Статус",
            REVIEW_STATUSES,
            index=status_index,
            key=f"review_status_{review_id}",
        )
        final_reply = st.text_area(
            "Итоговый ответ (final_reply)",
            value=str(review.get("final_reply") or ""),
            key=f"review_reply_{review_id}",
            help="Оставьте пустым, чтобы не менять текущий ответ.",
        )
        submitted = st.form_submit_button(
            "Сохранить изменения",
            key=f"review_submit_{review_id}",
            width="stretch",
        )
    if not submitted:
        return
    payload_reply = final_reply.strip() or None
    try:
        with api.ReputAPIClient(base_url=API_BASE) as client:
            updated = client.update_review_status(
                token, review_id, new_status, payload_reply
            )
    except DashboardAPIError as exc:
        render_api_error(exc)
    else:
        st.cache_data.clear()
        _flash(f"Отзыв переведён в статус {updated.get('status', new_status)}.")
        st.rerun()


# ---------------------------------------------------------------------------
# Page: Подписка (existing Subscription information)
# ---------------------------------------------------------------------------
def _fmt(value: Any) -> str:
    return str(value) if value is not None and value != "" else "—"


def render_subscription(token: str) -> None:
    st.title("Подписка")
    try:
        subscription = load_subscription(API_BASE, token)
    except DashboardAPIError as exc:
        render_api_error(exc)
        return

    st.subheader("Текущая подписка")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Статус", _fmt(subscription.get("status")), border=True)
    col2.metric(
        "Триал до (trial_ends_at)",
        _fmt(subscription.get("trial_ends_at")),
        border=True,
    )
    col3.metric(
        "Оплачено до (paid_until)",
        _fmt(subscription.get("paid_until")),
        border=True,
    )
    col4.metric(
        "ID платежа (payment_provider_id)",
        _fmt(subscription.get("payment_provider_id")),
        border=True,
    )
    st.caption(f"ID подписки: {_fmt(subscription.get('id'))}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    token = render_sidebar()
    if token is None:
        render_public_page()
        return
    _render_flash()
    page = render_nav()
    if page == "Филиалы":
        render_branches(token)
    elif page == "Отзывы":
        render_reviews(token)
    elif page == "Подписка":
        render_subscription(token)
    else:
        render_overview(token)


if __name__ == "__main__":
    main()





