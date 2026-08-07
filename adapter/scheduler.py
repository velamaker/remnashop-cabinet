"""Фоновые задачи адаптера: регистрация, цикл и память о запусках.

Адаптер до сих пор жил только на чужих запросах — даже оплаченные покупки он
дожимает лениво, когда человек откроет экран (compose.settle_intents). Для
дайджеста, порога трафика, утренней сводки и «возвращалки» этого мало: их
некому «открыть», они должны случаться сами.

Здесь общий цикл. Задача объявляется декоратором рядом со своим кодом:

    from scheduler import task

    @task("traffic-warning", minutes=15)
    async def traffic_warning(ctx):
        ...

Время последнего запуска лежит в своей SQLite (state.State), а не в памяти
процесса: контейнер перезапускают при каждом обновлении, и расписание в
переменной означало бы либо потерянную ночную рассылку, либо вторую такую же
через минуту после старта. Без доступной базы цикл вообще не поднимается —
задачи, которые пишут людям и тратят деньги, без памяти о запусках опаснее, чем
их отсутствие (правило проекта: честный отказ лучше тихой самодеятельности).

Чего здесь СОЗНАТЕЛЬНО нет:
  • очереди, воркеров и приоритетов — задачи редкие и короткие;
  • догона пропущенного: простояли час при интервале 15 минут — задача отработает
    ОДИН раз, а не четыре подряд; четыре одинаковых письма человеку не нужны;
  • расписания по часам («каждый день в 9:00»): цикл знает только интервалы, а
    подходящее время суток задача проверяет сама — так она сама и решает, что
    для неё «утро» с учётом часового пояса пользователя.
"""

from __future__ import annotations

import asyncio
import inspect
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

Job = Callable[..., Awaitable[Any]]

# Цикл просыпается не реже этого, даже когда до ближайшей задачи часы: часы
# машины могли съехать, а база — оказаться изменённой другой копией процесса.
_MAX_SLEEP = 30.0
# Нижняя граница сна: без неё задача с секундным интервалом крутила бы цикл
# вхолостую на полной скорости.
_MIN_SLEEP = 0.2
# Сколько ждём уже работающие задачи при выключении. Дольше держать нельзя:
# docker даёт контейнеру около десяти секунд до SIGKILL, и лучше отменить задачу
# самим (она увидит CancelledError), чем быть убитым посреди неё.
_STOP_GRACE = 5.0
# Задача без предела времени, зависшая на сетевом чтении, останавливает СВОЁ
# расписание навсегда — молча и до перезапуска. Поэтому предел есть всегда, а
# задача может назначить свой.
_DEFAULT_TIMEOUT = 300.0


class _Overtime(Exception):
    """Наш предел времени сработал. Отдельный тип нужен потому, что задача может
    поймать свой собственный TimeoutError изнутри, и путать их в логе нельзя:
    «не уложилась» и «внутри отвалилось по таймауту» чинятся по-разному."""


def _log(message: str) -> None:
    # Тот же префикс, что у остальных сообщений адаптера: лог контейнера общий.
    print(f"[adapter] расписание: {message}", flush=True)


@dataclass(frozen=True)
class Task:
    name: str
    interval: float          # секунды между НАЧАЛАМИ запусков
    func: Job
    run_at_start: bool
    timeout: float | None


# Реестр общий на процесс: задачи регистрируются при импорте своих модулей,
# ровно как обработчики через compose.handler.
_TASKS: dict[str, Task] = {}


def task(
    name: str,
    *,
    seconds: float = 0,
    minutes: float = 0,
    hours: float = 0,
    run_at_start: bool = True,
    timeout: float | None = _DEFAULT_TIMEOUT,
) -> Callable[[Job], Job]:
    """Объявляет фоновую задачу.

    `run_at_start=False` — для задач, которым нельзя срабатывать в момент старта
    (рассылки): такая задача заводится «как будто только что отработала» и уйдёт
    в дело через интервал. Считается это по записи в базе, поэтому один раз за
    всю жизнь установки, а не после каждого перезапуска.

    Имя — ключ в базе, а не подпись: переименование задачи начинает её историю
    заново. Повтор имени — ошибка на месте, а не «кто последний импортировался,
    тот и в расписании»: молча пропавшая задача ищется потом неделями.
    """
    interval = float(seconds) + float(minutes) * 60 + float(hours) * 3600
    if interval <= 0:
        raise ValueError(f"задача {name}: интервал должен быть больше нуля")

    def wrap(func: Job) -> Job:
        if not inspect.iscoroutinefunction(func):
            # Обычная функция выполнилась бы прямо в цикле и заблокировала бы
            # весь адаптер, включая ответы кабинету. Ловим на месте объявления,
            # а не раз в час в логе.
            raise TypeError(f"задача {name} должна быть async def")
        if name in _TASKS and _TASKS[name].func is not func:
            raise ValueError(f"задача {name} уже зарегистрирована другим модулем")
        _TASKS[name] = Task(name, interval, func, run_at_start, timeout)
        return func

    return wrap


def _default_state() -> Any:
    """Своя память адаптера. Импорт локальный: на уровне модуля вышел бы круг
    (main → compose → модули с задачами → scheduler)."""
    import main  # noqa: PLC0415

    return main.state


def _default_context() -> Any:
    """Контекст для задачи — тот же Ctx, что у обработчиков, только без сессии
    пользователя: за фоновой задачей никто не стоит.

    Значит, задаче доступны админское API бота (`ctx.admin`), панель
    (`ctx.panel_json`) и своя память (`ctx.state`), а `ctx.json` к личным ручкам
    бота ответит 401 — и это правда, а не поломка: личные данные фоновой задаче
    неоткуда взять, кроме как через админский ключ.
    """
    import main  # noqa: PLC0415
    from compose import Ctx  # noqa: PLC0415

    if main.client is None:
        # Такое бывает только между стартом процесса и подъёмом клиента.
        raise RuntimeError("клиент к боту ещё не поднят")
    return Ctx(main.client, None, {}, main.panel, main.admin, main.state)


class Scheduler:
    def __init__(self) -> None:
        self._loop: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._state: Any = None
        self._context_factory: Callable[[], Any] = _default_context

    @property
    def running(self) -> bool:
        return self._loop is not None and not self._loop.done()

    async def start(
        self, state: Any = None, context_factory: Callable[[], Any] | None = None
    ) -> bool:
        """Поднимает цикл. False — не поднялся, причина уже в логе."""
        if self.running:
            return True  # повторный вызов не должен плодить второй цикл
        self._state = state if state is not None else _default_state()
        if self._state is None or not getattr(self._state, "available", False):
            _log(
                "не запущено: своя база недоступна, а без памяти о запусках "
                "задачи повторялись бы после каждого перезапуска"
            )
            return False
        if not _TASKS:
            _log("задач не зарегистрировано — цикл не нужен")
            return False
        self._context_factory = context_factory or _default_context
        self._stop.clear()
        self._loop = asyncio.create_task(self._cycle(), name="adapter-scheduler")
        _log("запущено, задач: " + ", ".join(sorted(_TASKS)))
        return True

    async def stop(self) -> None:
        """Останавливает цикл и по-хорошему дожидается начатых задач."""
        loop, self._loop = self._loop, None
        self._stop.set()
        if loop is None or loop.done():
            return
        try:
            # Ждём ровно столько, сколько отпущено циклу на прощание с задачами
            # (_STOP_GRACE) плюс запас на саму отмену.
            await asyncio.wait_for(asyncio.shield(loop), _STOP_GRACE + 2)
        except (TimeoutError, asyncio.CancelledError):
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        _log("остановлено")

    def snapshot(self) -> list[dict[str, Any]]:
        """Состояние расписания для диагностики: когда бегала, чем кончилось."""
        rows = {r["name"]: r for r in self._state.task_runs()} if self._state else {}
        out = []
        for item in sorted(_TASKS.values(), key=lambda t: t.name):
            row = rows.get(item.name) or {}
            job = self._jobs.get(item.name)
            out.append(
                {
                    "name": item.name,
                    "interval_seconds": item.interval,
                    "last_run": row.get("last_run"),
                    "finished_at": row.get("finished_at"),
                    "last_status": row.get("last_status"),
                    "last_error": row.get("last_error"),
                    "runs": row.get("runs") or 0,
                    "failures": row.get("failures") or 0,
                    "in_progress": job is not None and not job.done(),
                }
            )
        return out

    # --- внутреннее ----------------------------------------------------------

    async def _cycle(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    delay = self._tick()
                except Exception as exc:  # noqa: BLE001 — цикл не имеет права умирать
                    # Сюда попадает беда самого расписания (например, база вдруг
                    # заблокирована). Умерший цикл — это тихо пропавшие рассылки:
                    # никто не заметит, пока не спохватится через неделю.
                    _log(f"сбой обхода задач: {exc!r}")
                    traceback.print_exc()
                    delay = _MAX_SLEEP
                try:
                    # Ждём не sleep'ом, а событием: выключение не должно ждать
                    # окончания паузы (docker всё равно столько не даст).
                    await asyncio.wait_for(self._stop.wait(), delay)
                except TimeoutError:
                    continue
                break
        finally:
            await self._drain()

    def _tick(self) -> float:
        """Запускает всё, чему пришёл срок, и возвращает время до ближайшего."""
        now = datetime.now(timezone.utc)
        rows = {r["name"]: r for r in self._state.task_runs()}
        sleep = _MAX_SLEEP
        # По копии реестра: модуль с задачами может доехать позже (importlib в
        # compose), а менять словарь во время обхода нельзя.
        for item in list(_TASKS.values()):
            job = self._jobs.get(item.name)
            if job is not None:
                if not job.done():
                    # Предыдущий запуск ещё идёт: задача оказалась длиннее своего
                    # интервала. Накладывать её на себя нельзя — второй проход
                    # разошлёт то же самое повторно.
                    sleep = min(sleep, item.interval)
                    continue
                del self._jobs[item.name]
            left = self._seconds_left(item, rows.get(item.name), now)
            if left <= 0 and self._state.claim_task_run(
                item.name, item.interval, item.run_at_start, now
            ):
                self._jobs[item.name] = asyncio.create_task(
                    self._execute(item), name=f"adapter-task:{item.name}"
                )
                left = item.interval
            elif left <= 0:
                # Срок пришёл, а запуск не достался (первое знакомство с
                # run_at_start=False или чужой захват) — ждём целый интервал.
                left = item.interval
            sleep = min(sleep, left)
        return max(_MIN_SLEEP, sleep)

    @staticmethod
    def _seconds_left(item: Task, row: dict[str, Any] | None, now: datetime) -> float:
        last = (row or {}).get("last_run")
        if not last:
            return 0.0
        try:
            previous = datetime.fromisoformat(str(last))
        except ValueError:
            return 0.0  # запись испорчена — считаем, что пора
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        left = item.interval - (now - previous).total_seconds()
        # Отрицательное «осталось» бывает при съехавших назад часах: тогда пора.
        return left if left > 0 else 0.0

    async def _execute(self, item: Task) -> None:
        try:
            ctx = self._context_factory()
            if inspect.isawaitable(ctx):
                ctx = await ctx
            work = item.func(ctx)
            if item.timeout:
                # Не wait_for: он отдаёт один и тот же TimeoutError и когда предел
                # вышел у нас, и когда задача поймала свой собственный внутри, —
                # а `limit.expired()` различает эти два случая точно.
                try:
                    async with asyncio.timeout(item.timeout) as limit:
                        await work
                except TimeoutError:
                    if limit.expired():
                        raise _Overtime from None
                    raise  # её собственный таймаут — обычная ошибка задачи
            else:
                await work
        except asyncio.CancelledError:
            # Выключение адаптера. Отмечаем честно: по журналу должно быть видно,
            # что задача не отработала, а была прервана обновлением.
            self._finish(item, False, "прервана остановкой адаптера")
            raise
        except _Overtime:
            self._finish(item, False, f"не уложилась в {item.timeout:.0f} с")
            _log(f"{item.name}: превышен предел времени, запуск брошен")
        except Exception as exc:  # noqa: BLE001 — упавшая задача не роняет процесс
            # Трейс в лог обязателен: у фоновой задачи нет пользователя, которому
            # видно ошибку, и кроме лога причина не всплывёт нигде.
            self._finish(item, False, f"{type(exc).__name__}: {exc}")
            _log(f"{item.name}: упала — {exc!r}")
            traceback.print_exc()
        else:
            self._finish(item, True)

    def _finish(self, item: Task, ok: bool, error: str | None = None) -> None:
        # Запись итога не имеет права уронить обработку ошибки, ради которой её и
        # зовут: если база отвалилась, останется хотя бы строка в логе.
        try:
            self._state.finish_task_run(item.name, ok, error)
        except Exception as exc:  # noqa: BLE001
            _log(f"{item.name}: не записан итог запуска: {exc!r}")

    async def _drain(self) -> None:
        """Прощание с начатыми задачами при выключении."""
        jobs = [j for j in self._jobs.values() if not j.done()]
        self._jobs.clear()
        if not jobs:
            return
        _, pending = await asyncio.wait(jobs, timeout=_STOP_GRACE)
        for job in pending:
            # Дотянуть до конца не вышло: обрываем сами, иначе нас оборвёт
            # SIGKILL — и в базе не останется даже пометки о неудаче.
            job.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            _log(f"прерваны незаконченные задачи: {len(pending)}")


# Один экземпляр на процесс — как и всё остальное состояние адаптера.
scheduler = Scheduler()
