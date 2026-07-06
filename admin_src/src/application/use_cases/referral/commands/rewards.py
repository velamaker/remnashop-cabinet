# OVERLAY (RемнаShop): реф-награда типа POINTS начисляется в БАЛЛАХ по курсу
# 1 балл = 7 ₽. reward.amount при PERCENT = сумма_платежа × % (в ₽), поэтому в
# баллы переводим делением на 7: points = round(reward.amount / 7).
# Пример: платёж 700 ₽, L1=15% → 105 ₽ / 7 = 15 баллов (=105 ₽ при конвертации).
# Дальше юзер сам конвертирует баллы в ₽-баланс в кабинете. Меняется ТОЛЬКО
# коэффициент в ветке POINTS GiveReferrerReward. Остальное — как в базе.
# ВНИМАНИЕ: файл перекрывает базовый — при обновлении базы сверять с оригиналом.
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import EventPublisher, Interactor
from src.application.common.dao import ReferralDao, SettingsDao, SubscriptionDao, UserDao
from src.application.common.uow import UnitOfWork
from src.application.dto import ReferralRewardDto, TransactionDto, UserDto
from src.application.events import ReferralRewardFailedEvent, ReferralRewardReceivedEvent
from src.application.use_cases.referral.queries.calculations import (
    CalculateReferralReward,
    CalculateReferralRewardDto,
)
from src.application.use_cases.subscription.commands.management import (
    AddSubscriptionDuration,
    AddSubscriptionDurationDto,
)
from src.application.use_cases.user.commands.profile_edit import (
    ChangeUserPoints,
    ChangeUserPointsDto,
)
from src.infrastructure.services.overlay_cashback import award_purchase_cashback
from src.infrastructure.services.overlay_push import notify_user_push
from src.core.enums import PurchaseType, ReferralAccrualStrategy, ReferralLevel, ReferralRewardType


@dataclass(frozen=True)
class GiveReferrerRewardDto:
    user_id: int
    reward: ReferralRewardDto
    referred_name: str


class GiveReferrerReward(Interactor[GiveReferrerRewardDto, None]):
    required_permission = None

    def __init__(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        subscription_dao: SubscriptionDao,
        referral_dao: ReferralDao,
        event_publisher: EventPublisher,
        change_user_points: ChangeUserPoints,
        add_subscription_duration: AddSubscriptionDuration,
        session: AsyncSession,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.referral_dao = referral_dao
        self.event_publisher = event_publisher
        self.change_user_points = change_user_points
        self.add_subscription_duration = add_subscription_duration
        # OVERLAY: сессия для best-effort web-push рефереру (см. конец _execute).
        self.session = session

    async def _execute(self, actor: UserDto, data: GiveReferrerRewardDto) -> None:
        reward = data.reward

        user = await self.user_dao.get_by_id(data.user_id)
        if not user:
            logger.warning(f"{actor.log} User '{data.user_id}' not found, unable to apply reward")
            return

        logger.info(
            f"{actor.log} Start applying reward of '{reward.amount}' "
            f"'{reward.type}' to user '{user.remna_name}'"
        )
        if reward.type == ReferralRewardType.POINTS:
            # OVERLAY: reward.amount при PERCENT = сумма_платежа × % (в ₽).
            # Начисляем БАЛЛЫ по курсу 1 балл = 7 ₽ → делим на 7.
            points = max(1, round(reward.amount / 7))
            await self.change_user_points.system(
                ChangeUserPointsDto(
                    user_id=user.id,
                    amount=points,
                )
            )

        elif reward.type == ReferralRewardType.EXTRA_DAYS:
            subscription = await self.subscription_dao.get_current(user.id)  # only active

            if not subscription or subscription.is_trial:
                logger.warning(
                    f"{actor.log} Current subscription not found "
                    f"for user '{user.remna_name}', unable to add days"
                )

                event_failed = ReferralRewardFailedEvent(
                    user=user,
                    name=data.referred_name,
                    value=reward.amount,
                    reward_type=reward.type,
                )

                await self.event_publisher.publish(event_failed)
                return

            logger.info(
                f"{actor.log} Current subscription found for user '{user.remna_name}', "
                f"expire date '{subscription.expire_at}'"
            )

            await self.add_subscription_duration.system(
                AddSubscriptionDurationDto(
                    user_id=user.id,
                    days=reward.amount,
                ),
            )

        else:
            raise ValueError(
                f"Failed to apply reward: unknown type '{reward.type}' for user '{user.remna_name}'"
            )

        # Mark issued immediately after a successful grant, before any notification.
        # Full atomicity is impossible: the grant (points/days, incl. a Remnawave call
        # for EXTRA_DAYS) commits in its own transaction, so a crash between that commit
        # and this one can still leave is_issued=False. Tightening the order — grant →
        # mark issued → notify — minimizes that window and avoids a failed notification
        # leaving the reward looking pending.
        async with self.uow:
            await self.referral_dao.mark_reward_as_issued(reward.id)
            await self.uow.commit()

        event_reward = ReferralRewardReceivedEvent(
            user=user,
            name=data.referred_name,
            value=reward.amount,
            reward_type=reward.type,
        )
        await self.event_publisher.publish(event_reward)
        logger.info(f"{actor.log} Finished applying reward to user '{user.id}'")

        # OVERLAY: web-push рефереру (PWA/iOS) вдобавок к TG/email базового образа.
        # best-effort — notify_user_push никогда не бросает наружу.
        if reward.type == ReferralRewardType.POINTS:
            reward_n: int = max(1, round(reward.amount / 7))
            msgs = {
                "ru": ("🎁 Реферальная награда", "Начислено {n} баллов за друга {name}."),
                "en": ("🎁 Referral reward", "You earned {n} points for inviting {name}."),
            }
        else:
            reward_n = reward.amount
            msgs = {
                "ru": ("🎁 Реферальная награда", "Начислено {n} дн. подписки за друга {name}."),
                "en": ("🎁 Referral reward", "You got {n} bonus day(s) for inviting {name}."),
            }
        await notify_user_push(
            self.session, user, msgs, url="/referral", tag="referral",
            n=reward_n, name=data.referred_name,
        )


@dataclass(frozen=True)
class AssignReferralRewardsDto:
    user: UserDto
    transaction: TransactionDto


class AssignReferralRewards(Interactor[AssignReferralRewardsDto, None]):
    required_permission = None

    def __init__(
        self,
        uow: UnitOfWork,
        settings_dao: SettingsDao,
        user_dao: UserDao,
        referral_dao: ReferralDao,
        calculate_referral_reward: CalculateReferralReward,
        give_referrer_reward: GiveReferrerReward,
        change_user_points: ChangeUserPoints,
    ) -> None:
        self.uow = uow
        self.settings_dao = settings_dao
        self.user_dao = user_dao
        self.referral_dao = referral_dao
        self.calculate_referral_reward = calculate_referral_reward
        self.give_referrer_reward = give_referrer_reward
        self.change_user_points = change_user_points

    async def _execute(self, actor: UserDto, data: AssignReferralRewardsDto) -> None:  # noqa: C901
        # OVERLAY: кэшбэк ПОКУПАТЕЛЮ — независим от рефералки, поэтому начисляется
        # в самом начале, ДО всех реферальных early-return (выключенная рефералка,
        # не-NEW платёж, отсутствие реферера). best-effort, наружу не бросает.
        await award_purchase_cashback(data.user, data.transaction, self.change_user_points)

        settings = await self.settings_dao.get()

        if not settings.referral.enable:
            logger.info("Referral system is disabled; reward assignment skipped")
            return

        if data.transaction.plan_snapshot and data.transaction.plan_snapshot.is_trial:
            logger.info(
                f"Skip rewards: transaction '{data.transaction.id}' is a trial plan purchase"
            )
            return

        if (
            settings.referral.accrual_strategy == ReferralAccrualStrategy.ON_FIRST_PAYMENT
            and data.transaction.purchase_type != PurchaseType.NEW
        ):
            logger.info(
                f"Skip rewards: transaction '{data.transaction.id}' purchase type "
                f"'{data.transaction.purchase_type}' is not NEW"
            )
            return

        referral, parent = await self.referral_dao.get_referral_chain(data.user.id)

        if not referral:
            logger.info(f"{data.user.log} not referred; reward assignment skipped")
            return

        reward_type = settings.referral.reward.type
        reward_chain = {ReferralLevel.FIRST: referral.referrer}
        referral_ids = {ReferralLevel.FIRST: referral.id}

        if parent:
            reward_chain[ReferralLevel.SECOND] = parent.referrer
            referral_ids[ReferralLevel.SECOND] = parent.id

        for level, referrer in reward_chain.items():
            if level > settings.referral.level:
                continue

            try:
                config_value = settings.referral.reward.config.get(level)
                if config_value is None:
                    logger.info(f"No reward config for level '{level.name}'")
                    continue

                reward_amount = await self.calculate_referral_reward.system(
                    CalculateReferralRewardDto(
                        settings=settings.referral,
                        transaction=data.transaction,
                        config_value=config_value,
                    )
                )

                if not reward_amount or reward_amount <= 0:
                    logger.warning(
                        f"Reward amount <= 0 for referrer '{referrer.remna_name}', "
                        f"level '{level.name}'"
                    )
                    continue

                async with self.uow:
                    reward = await self.referral_dao.create_reward(
                        reward=ReferralRewardDto(
                            user_id=referrer.id,
                            type=reward_type,
                            amount=reward_amount,
                            is_issued=False,
                        ),
                        referral_id=referral_ids[level],
                    )
                    await self.uow.commit()

                await self.give_referrer_reward.system(
                    GiveReferrerRewardDto(
                        user_id=referrer.id,
                        reward=reward,
                        referred_name=data.user.name,
                    )
                )

                logger.info(
                    f"Issued '{reward_type}' reward '{reward_amount}' for referrer "
                    f"'{referrer.remna_name}' (level '{level.name}')"
                )
            except Exception:
                logger.exception(
                    f"Failed to assign referral reward for level '{level.name}' "
                    f"to referrer '{referrer.remna_name}' — skipping this level"
                )
