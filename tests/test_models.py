import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reput_ai.db.models.user import User
from reput_ai.db.models.branch import CompanyBranch, PlatformType, ToneOfVoice
from reput_ai.db.models.review import Review, ReviewStatus
from reput_ai.db.models.subscription import Subscription, SubscriptionStatus


@pytest.mark.asyncio
async def test_create_user(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(
            email="test_user@example.com",
            hashed_password="hashed_secret_password",
            telegram_id=987654321,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        assert isinstance(user.id, uuid.UUID)
        assert user.email == "test_user@example.com"
        assert user.telegram_id == 987654321
        assert user.created_at is not None


@pytest.mark.asyncio
async def test_user_email_unique_constraint(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user1 = User(
            email="duplicate@example.com",
            hashed_password="pwd1",
        )
        session.add(user1)
        await session.commit()

    async with session_factory() as session:
        user2 = User(
            email="duplicate@example.com",
            hashed_password="pwd2",
        )
        session.add(user2)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_user_telegram_id_unique_constraint(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user1 = User(
            email="user_tg1@example.com",
            hashed_password="pwd1",
            telegram_id=111222333,
        )
        session.add(user1)
        await session.commit()

    async with session_factory() as session:
        user2 = User(
            email="user_tg2@example.com",
            hashed_password="pwd2",
            telegram_id=111222333,
        )
        session.add(user2)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_company_branch_creation_and_relationship(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(
            email="branch_owner@example.com",
            hashed_password="pwd",
        )
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Центральный филиал",
            platform_type=PlatformType.YANDEX,
            platform_url="https://yandex.ru/maps/org/123",
            tone_of_voice=ToneOfVoice.FRIENDLY,
            is_active=True,
        )
        session.add(branch)
        await session.commit()
        user_id = user.id

    async with session_factory() as session:
        stmt = select(User).options(selectinload(User.branches)).where(User.id == user_id)
        res = await session.execute(stmt)
        loaded_user = res.scalar_one()
        assert len(loaded_user.branches) == 1
        assert loaded_user.branches[0].name == "Центральный филиал"
        assert loaded_user.branches[0].platform_type == PlatformType.YANDEX
        assert loaded_user.branches[0].tone_of_voice == ToneOfVoice.FRIENDLY
        assert loaded_user.branches[0].is_active is True


@pytest.mark.asyncio
async def test_company_branch_tone_of_voice_enum_defaults(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(
            email="branch_default@example.com",
            hashed_password="pwd",
        )
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Филиал по умолчанию",
            platform_type=PlatformType.GIS2,
            platform_url="https://2gis.ru/firm/123",
        )
        session.add(branch)
        await session.commit()
        await session.refresh(branch)

        assert branch.tone_of_voice == ToneOfVoice.OFFICIAL
        assert branch.is_active is True


@pytest.mark.asyncio
async def test_review_creation_and_defaults(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(email="rev_owner@example.com", hashed_password="pwd")
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Филиал для отзывов",
            platform_type=PlatformType.AVITO,
            platform_url="https://avito.ru/brand/123",
        )
        session.add(branch)
        await session.commit()

        review = Review(
            branch_id=branch.id,
            external_id="ext-rev-100",
            author_name="Алексей Иванов",
            rating=5,
            text="Отличный сервис, рекомендую!",
        )
        session.add(review)
        await session.commit()
        await session.refresh(review)

        assert review.status == ReviewStatus.NEW
        assert review.rating == 5
        assert review.author_name == "Алексей Иванов"
        assert review.created_at is not None
        assert review.generated_reply is None
        assert review.final_reply is None


@pytest.mark.asyncio
async def test_review_unique_branch_and_external_id(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(email="rev_unique@example.com", hashed_password="pwd")
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Филиал с дубликатами",
            platform_type=PlatformType.GOOGLE,
            platform_url="https://google.com/maps/place/123",
        )
        session.add(branch)
        await session.commit()

        review1 = Review(
            branch_id=branch.id,
            external_id="duplicate-ext-id-1",
            author_name="Клиент 1",
            rating=4,
            text="Хорошо",
        )
        session.add(review1)
        await session.commit()
        branch_id = branch.id

    async with session_factory() as session:
        # Same branch_id and external_id must fail
        review2 = Review(
            branch_id=branch_id,
            external_id="duplicate-ext-id-1",
            author_name="Клиент 2",
            rating=2,
            text="Плохо",
        )
        session.add(review2)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_review_rating_range_check_constraint(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(email="rev_rating@example.com", hashed_password="pwd")
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Филиал для проверки рейтинга",
            platform_type=PlatformType.YANDEX,
            platform_url="https://yandex.ru/maps/123",
        )
        session.add(branch)
        await session.commit()
        branch_id = branch.id

    # Rating 0 is less than 1 -> must fail
    async with session_factory() as session:
        review_invalid_low = Review(
            branch_id=branch_id,
            external_id="ext-rating-0",
            author_name="Клиент 0",
            rating=0,
            text="Недопустимый рейтинг 0",
        )
        session.add(review_invalid_low)
        with pytest.raises(IntegrityError):
            await session.commit()

    # Rating 6 is greater than 5 -> must fail
    async with session_factory() as session:
        review_invalid_high = Review(
            branch_id=branch_id,
            external_id="ext-rating-6",
            author_name="Клиент 6",
            rating=6,
            text="Недопустимый рейтинг 6",
        )
        session.add(review_invalid_high)
        with pytest.raises(IntegrityError):
            await session.commit()



@pytest.mark.asyncio
async def test_review_status_lifecycle_and_reply(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(email="rev_status@example.com", hashed_password="pwd")
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Филиал статус тест",
            platform_type=PlatformType.YANDEX,
            platform_url="https://yandex.ru/maps/321",
        )
        session.add(branch)
        await session.commit()

        review = Review(
            branch_id=branch.id,
            external_id="ext-status-flow",
            author_name="Иван",
            rating=3,
            text="Нормально, но могло быть лучше",
            generated_reply="Спасибо за отзыв! Мы улучшим наш сервис.",
        )
        session.add(review)
        await session.commit()
        await session.refresh(review)
        review_id = review.id

        assert review.status == ReviewStatus.NEW

    async with session_factory() as session:
        # Update to PENDING_APPROVAL
        review = await session.get(Review, review_id)
        review.status = ReviewStatus.PENDING_APPROVAL
        await session.commit()

    async with session_factory() as session:
        # Approve and add final reply
        review = await session.get(Review, review_id)
        assert review.status == ReviewStatus.PENDING_APPROVAL
        review.status = ReviewStatus.APPROVED
        review.final_reply = "Спасибо за ваш отзыв! Мы приняли во внимание ваши замечания."
        await session.commit()

    async with session_factory() as session:
        review = await session.get(Review, review_id)
        assert review.status == ReviewStatus.APPROVED
        assert review.final_reply == "Спасибо за ваш отзыв! Мы приняли во внимание ваши замечания."


@pytest.mark.asyncio
async def test_subscription_creation_and_defaults(session_factory: async_sessionmaker[AsyncSession]):
    trial_end = datetime(2026, 10, 1, 12, 0, 0)
    async with session_factory() as session:
        user = User(email="sub_user@example.com", hashed_password="pwd")
        session.add(user)
        await session.commit()

        subscription = Subscription(
            user_id=user.id,
            trial_ends_at=trial_end,
        )
        session.add(subscription)
        await session.commit()
        await session.refresh(subscription)

        assert isinstance(subscription.id, uuid.UUID)
        assert subscription.status == SubscriptionStatus.TRIAL
        assert subscription.user_id == user.id
        assert subscription.trial_ends_at.replace(tzinfo=None) == trial_end.replace(tzinfo=None)
        assert subscription.paid_until is None
        assert subscription.payment_provider_id is None
        user_id = user.id

    async with session_factory() as session:
        # Check User -> Subscriptions relationship
        stmt = select(User).options(selectinload(User.subscriptions)).where(User.id == user_id)
        res = await session.execute(stmt)
        loaded_user = res.scalar_one()
        assert len(loaded_user.subscriptions) == 1
        assert loaded_user.subscriptions[0].status == SubscriptionStatus.TRIAL


@pytest.mark.asyncio
async def test_cascade_delete_user_deletes_branches_and_subscriptions(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        user = User(email="cascade@example.com", hashed_password="pwd")
        session.add(user)
        await session.commit()

        branch = CompanyBranch(
            user_id=user.id,
            name="Удаляемый филиал",
            platform_type=PlatformType.YANDEX,
            platform_url="https://yandex.ru/1",
        )
        subscription = Subscription(user_id=user.id)
        session.add_all([branch, subscription])
        await session.commit()

        review = Review(
            branch_id=branch.id,
            external_id="ext-cascade-1",
            author_name="Клиент",
            rating=5,
            text="Супер!",
        )
        session.add(review)
        await session.commit()

        user_id = user.id
        branch_id = branch.id
        sub_id = subscription.id
        review_id = review.id

    # Delete User
    async with session_factory() as session:
        user = await session.get(User, user_id)
        await session.delete(user)
        await session.commit()

    async with session_factory() as session:
        # Verify CompanyBranch is removed
        branch_res = await session.execute(select(CompanyBranch).where(CompanyBranch.id == branch_id))
        assert branch_res.scalar_one_or_none() is None

        # Verify Subscription is removed
        sub_res = await session.execute(select(Subscription).where(Subscription.id == sub_id))
        assert sub_res.scalar_one_or_none() is None

        # Verify Review is removed
        review_res = await session.execute(select(Review).where(Review.id == review_id))
        assert review_res.scalar_one_or_none() is None

