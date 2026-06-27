import { useEffect, useState } from "react";
import { HelpCircle, FileText, Shield, ScrollText, Star, Activity } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";

const tabs = [
  { id: "faq", label: "FAQ", icon: HelpCircle },
  { id: "service", label: "Серверы", icon: Activity },
  { id: "rules", label: "Правила", icon: FileText },
  { id: "privacy", label: "Конфиденциальность", icon: Shield },
  { id: "offer", label: "Оферта", icon: ScrollText },
  { id: "statuses", label: "Статусы", icon: Star },
] as const;

// Эмодзи-флаг страны из ISO-кода (две буквы → regional indicators).
function flagEmoji(code: string): string {
  if (!code || code.length !== 2) return "🌐";
  const base = 0x1f1e6;
  return String.fromCodePoint(
    ...[...code.toUpperCase()].map((c) => base + c.charCodeAt(0) - 65),
  );
}

function ServiceStatus() {
  const [nodes, setNodes] = useState<
    { name: string; country_code: string; online: boolean }[]
  >([]);
  const [allOk, setAllOk] = useState(true);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    subscriptionApi
      .serviceStatus()
      .then((s) => {
        setNodes(s.nodes);
        setAllOk(s.all_operational);
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) {
    return <p className="py-6 text-center text-sm text-fg-subtle">Загрузка статуса…</p>;
  }

  return (
    <div className="space-y-5">
      <div
        className={`flex items-center gap-3 rounded-2xl border px-5 py-4 ${
          allOk
            ? "border-success/20 bg-success/10 text-success"
            : "border-warning/20 bg-warning/10 text-warning"
        }`}
      >
        <span className={`h-2.5 w-2.5 rounded-full ${allOk ? "bg-success" : "bg-warning"}`} />
        <p className="text-sm font-semibold">
          {allOk ? "Все серверы работают" : "Часть серверов недоступны"}
        </p>
      </div>

      {nodes.length === 0 ? (
        <p className="py-4 text-center text-sm text-fg-subtle">Нет данных о серверах</p>
      ) : (
        <div className="space-y-2">
          {nodes.map((n, i) => (
            <div
              key={i}
              className="flex items-center gap-3 rounded-2xl border border-border-subtle bg-bg-subtle px-5 py-3.5"
            >
              <span className="text-xl">{flagEmoji(n.country_code)}</span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{n.name}</span>
              <span
                className={`flex items-center gap-1.5 text-xs font-medium ${
                  n.online ? "text-success" : "text-danger"
                }`}
              >
                <span className={`h-2 w-2 rounded-full ${n.online ? "bg-success" : "bg-danger"}`} />
                {n.online ? "Работает" : "Недоступен"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type TabId = (typeof tabs)[number]["id"];

function Faq() {
  const items = [
    {
      q: "Что такое Begemot VPN?",
      a: "Begemot — это сервис защищённого VPN-соединения на базе протокола VLESS/Reality. Мы обеспечиваем высокую скорость, анонимность и стабильную работу на всех ваших устройствах.",
    },
    {
      q: "На скольких устройствах можно использовать?",
      a: "Количество устройств зависит от выбранного тарифа. Вы можете подключить несколько устройств одновременно — ноутбук, телефон, планшет.",
    },
    {
      q: "Как подключиться?",
      a: "После оплаты в разделе «Устройства» появится ваша персональная ссылка-конфигурация. Скопируйте её в приложение (например, v2rayNG, Hiddify, Streisand) — и готово.",
    },
    {
      q: "Что будет после истечения подписки?",
      a: "Соединение будет автоматически приостановлено. Ваши настройки и история сохраняются — достаточно продлить подписку, чтобы всё заработало снова.",
    },
    {
      q: "Есть ли пробный период?",
      a: "Да, новым пользователям доступен бесплатный пробный период. Активировать его можно в разделе «Тарифы».",
    },
    {
      q: "Как работает реферальная программа?",
      a: "Поделитесь своей реферальной ссылкой. Когда приглашённый пользователь оплатит подписку, вы получите бонусные баллы, которые можно потратить на продление.",
    },
    {
      q: "Как пополнить баланс?",
      a: "В разделе «Тарифы» доступны различные способы оплаты: банковская карта, Telegram Stars и криптовалюта. Выберите удобный и следуйте инструкциям.",
    },
    {
      q: "Подключение нестабильно или режется по DPI — что делать?",
      a: "Попробуйте сменить протокол/сервер в приложении (например, Reality обходит большинство блокировок). Если домен кабинета не открывается у вашего оператора — зайдите с включённым VPN. Актуальный статус серверов — на вкладке «Серверы».",
    },
    {
      q: "Скорость низкая — как ускорить?",
      a: "Выберите в приложении сервер поближе или менее загруженный (загрузку видно в кабинете на главной). Перезапустите приложение и подключение. Если проблема сохраняется — напишите в поддержку.",
    },
    {
      q: "Если что-то не работает — куда писать?",
      a: "Напишите в поддержку через Telegram-бота. Мы отвечаем в течение нескольких часов.",
    },
  ];

  const [open, setOpen] = useState<number | null>(null);

  return (
    <div className="space-y-2">
      {items.map((item, i) => (
        <div key={i} className="rounded-2xl border border-border-subtle bg-bg-subtle overflow-hidden">
          <button
            onClick={() => setOpen(open === i ? null : i)}
            className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left"
          >
            <span className="font-medium text-fg text-sm">{item.q}</span>
            <span className={`flex-shrink-0 text-fg-muted transition-transform ${open === i ? "rotate-45" : ""}`}>
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
            </span>
          </button>
          {open === i && (
            <div className="border-t border-border-subtle px-5 pb-4 pt-3">
              <p className="text-sm text-fg-muted leading-relaxed">{item.a}</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Rules() {
  return (
    <div className="prose-sm space-y-6 text-fg-muted leading-relaxed">
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">1. Общие положения</h2>
        <p>Используя сервис Begemot, вы соглашаетесь с настоящими Правилами пользования. Правила могут быть изменены без предварительного уведомления; актуальная версия всегда доступна на этой странице.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">2. Допустимое использование</h2>
        <p>Сервис предназначен исключительно для легального использования. Разрешается:</p>
        <ul className="mt-2 space-y-1 pl-4 list-disc">
          <li>Защита личных данных в публичных сетях</li>
          <li>Обход географических ограничений для доступа к законным ресурсам</li>
          <li>Анонимный сёрфинг в личных целях</li>
        </ul>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">3. Запрещённые действия</h2>
        <p>Категорически запрещено использовать сервис для:</p>
        <ul className="mt-2 space-y-1 pl-4 list-disc">
          <li>Незаконной деятельности, нарушающей законодательство РФ и других стран</li>
          <li>Рассылки спама, фишинга, DDoS-атак</li>
          <li>Распространения вредоносного ПО</li>
          <li>Нарушения авторских прав и интеллектуальной собственности</li>
          <li>Доступа к детскому контенту сексуального характера</li>
        </ul>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">4. Аккаунт</h2>
        <p>Вы несёте полную ответственность за сохранность своих учётных данных. Передача доступа третьим лицам запрещена. При обнаружении нарушений аккаунт может быть заблокирован без возврата средств.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">5. Оплата и возвраты</h2>
        <p>Оплата подписки производится авансом. Возврат средств возможен в течение 24 часов с момента оплаты при условии, что услугой не пользовались. Для запроса возврата обратитесь в поддержку.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">6. Ответственность сервиса</h2>
        <p>Сервис предоставляется «как есть». Мы не гарантируем 100% бесперебойной работы и не несём ответственности за убытки, возникшие в результате недоступности сервиса.</p>
      </section>
    </div>
  );
}

function Privacy() {
  return (
    <div className="space-y-6 text-fg-muted leading-relaxed">
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">Какие данные мы собираем</h2>
        <ul className="space-y-1 pl-4 list-disc text-sm">
          <li>Email-адрес (при регистрации через email)</li>
          <li>Telegram ID и имя пользователя (при авторизации через Telegram)</li>
          <li>Данные о подписке и транзакциях</li>
          <li>Технические данные: IP-адрес при авторизации, тип устройства</li>
        </ul>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">Как мы используем данные</h2>
        <ul className="space-y-1 pl-4 list-disc text-sm">
          <li>Предоставление и поддержка сервиса</li>
          <li>Обработка платежей и управление подпиской</li>
          <li>Отправка важных уведомлений (истечение подписки, технические работы)</li>
          <li>Предотвращение мошенничества</li>
        </ul>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">Логи трафика</h2>
        <p className="text-sm">Мы <strong className="text-fg">не ведём логи</strong> вашего интернет-трафика, посещаемых сайтов или передаваемых данных. Хранятся только технические метаданные, необходимые для работы сервиса (время подключения, объём использованного трафика для тарифного учёта).</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">Передача данных третьим лицам</h2>
        <p className="text-sm">Мы не продаём и не передаём ваши данные третьим лицам, за исключением платёжных систем (для обработки транзакций) и случаев, предусмотренных законодательством.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">Хранение и удаление данных</h2>
        <p className="text-sm">Данные хранятся на защищённых серверах. По запросу вы можете потребовать удаление своего аккаунта и всех связанных данных — обратитесь в поддержку.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">Контакты</h2>
        <p className="text-sm">По вопросам обработки персональных данных обращайтесь через Telegram-бота.</p>
      </section>
    </div>
  );
}

function Offer() {
  return (
    <div className="space-y-6 text-fg-muted leading-relaxed text-sm">
      <p className="text-xs text-fg-subtle">Последнее обновление: июнь 2025 г.</p>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">1. Предмет договора</h2>
        <p>Настоящая публичная оферта (далее — «Оферта») является официальным предложением оказать услуги VPN-сервиса «Begemot» (далее — «Исполнитель») физическому лицу (далее — «Пользователь»), акцептовавшему условия настоящей Оферты.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">2. Акцепт оферты</h2>
        <p>Акцептом настоящей Оферты является регистрация в сервисе и/или оплата любого тарифного плана. С момента акцепта Пользователь считается ознакомленным и согласным со всеми условиями.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">3. Услуги и их стоимость</h2>
        <p>Исполнитель предоставляет доступ к VPN-сервису на условиях выбранного тарифного плана. Стоимость и условия тарифов размещены в разделе «Тарифы» личного кабинета и могут быть изменены Исполнителем в одностороннем порядке с уведомлением Пользователя.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">4. Порядок оплаты</h2>
        <p>Оплата производится в размере 100% стоимости выбранного тарифа до начала оказания услуг. Доступные способы оплаты указаны в разделе «Тарифы».</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">5. Права и обязанности сторон</h2>
        <p className="mb-2"><strong className="text-fg">Исполнитель обязуется:</strong></p>
        <ul className="space-y-1 pl-4 list-disc">
          <li>Обеспечить доступ к сервису в течение оплаченного периода</li>
          <li>Уведомлять об изменении условий оказания услуг</li>
          <li>Обеспечивать защиту персональных данных</li>
        </ul>
        <p className="mb-2 mt-3"><strong className="text-fg">Пользователь обязуется:</strong></p>
        <ul className="space-y-1 pl-4 list-disc">
          <li>Использовать сервис только в законных целях</li>
          <li>Не передавать доступ третьим лицам</li>
          <li>Своевременно вносить оплату</li>
        </ul>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">6. Ответственность</h2>
        <p>Исполнитель не несёт ответственности за невозможность использования сервиса по причинам, не зависящим от Исполнителя (действия провайдеров, блокировки, форс-мажор). Максимальная ответственность Исполнителя ограничена суммой последнего платежа Пользователя.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">7. Расторжение договора</h2>
        <p>Пользователь вправе в любое время прекратить использование сервиса. Исполнитель вправе расторгнуть договор в одностороннем порядке при нарушении Пользователем Правил пользования без возврата оплаченных средств.</p>
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold text-fg">8. Разрешение споров</h2>
        <p>Споры решаются путём переговоров через поддержку сервиса. При невозможности достижения соглашения — в соответствии с законодательством Российской Федерации.</p>
      </section>
    </div>
  );
}

function Statuses() {
  const statuses = [
    {
      label: "Активна",
      color: "bg-success/10 text-success border-success/20",
      dot: "bg-success",
      description: "Подписка активна и работает. Подключение доступно на всех ваших устройствах.",
    },
    {
      label: "Истекает скоро",
      color: "bg-warning/10 text-warning border-warning/20",
      dot: "bg-warning",
      description: "До окончания подписки осталось менее 3 дней. Рекомендуем продлить заранее.",
    },
    {
      label: "Истекла",
      color: "bg-danger/10 text-danger border-danger/20",
      dot: "bg-danger",
      description: "Срок действия подписки завершился. Подключение приостановлено. Продлите подписку для восстановления доступа.",
    },
    {
      label: "Пробная",
      color: "bg-accent/10 text-accent border-accent/20",
      dot: "bg-accent",
      description: "Активен бесплатный пробный период. После его окончания необходимо оформить подписку.",
    },
    {
      label: "Отключена",
      color: "bg-fg-subtle/20 text-fg-muted border-border-subtle",
      dot: "bg-fg-muted",
      description: "Подписка отключена администратором. Обратитесь в поддержку для уточнения причины.",
    },
    {
      label: "Нет подписки",
      color: "bg-fg-subtle/10 text-fg-subtle border-border-subtle",
      dot: "bg-fg-subtle",
      description: "Подписка ещё не оформлена. Перейдите в раздел «Тарифы», чтобы выбрать план.",
    },
  ];

  const trafficStatuses = [
    {
      label: "В норме",
      color: "text-success",
      bar: "bg-success",
      pct: 35,
      description: "Использовано менее 80% лимита трафика.",
    },
    {
      label: "Заканчивается",
      color: "text-warning",
      bar: "bg-warning",
      pct: 85,
      description: "Использовано более 80% лимита. Скорость будет снижена при достижении 100%.",
    },
    {
      label: "Исчерпан",
      color: "text-danger",
      bar: "bg-danger",
      pct: 100,
      description: "Лимит трафика исчерпан. Подключение ограничено до следующего сброса или продления.",
    },
  ];

  return (
    <div className="space-y-8">
      <div>
        <h2 className="mb-3 text-base font-semibold text-fg">Статусы подписки</h2>
        <div className="space-y-3">
          {statuses.map(s => (
            <div key={s.label} className={`flex items-start gap-4 rounded-2xl border px-5 py-4 ${s.color}`}>
              <div className={`mt-1 h-2.5 w-2.5 flex-shrink-0 rounded-full ${s.dot}`} />
              <div>
                <p className="font-semibold text-sm">{s.label}</p>
                <p className="mt-0.5 text-xs opacity-80 leading-relaxed">{s.description}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-3 text-base font-semibold text-fg">Статусы трафика</h2>
        <div className="space-y-3">
          {trafficStatuses.map(s => (
            <div key={s.label} className="rounded-2xl border border-border-subtle bg-bg-subtle px-5 py-4">
              <div className="mb-2 flex items-center justify-between">
                <span className={`text-sm font-semibold ${s.color}`}>{s.label}</span>
                <span className="text-xs text-fg-muted">{s.pct}%</span>
              </div>
              <div className="mb-3 h-2 w-full overflow-hidden rounded-full bg-bg-raised">
                <div className={`h-full rounded-full ${s.bar}`} style={{ width: `${s.pct}%` }} />
              </div>
              <p className="text-xs text-fg-muted leading-relaxed">{s.description}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const CONTENT: Record<TabId, React.ReactNode> = {
  faq: <Faq />,
  service: <ServiceStatus />,
  rules: <Rules />,
  privacy: <Privacy />,
  offer: <Offer />,
  statuses: <Statuses />,
};

export default function InfoPage() {
  const [active, setActive] = useState<TabId>("faq");

  return (
    <div className="space-y-6">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-fg">
          <svg className="h-6 w-6 text-fg-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <circle cx="12" cy="12" r="10" />
            <path strokeLinecap="round" d="M12 16v-4m0-4h.01" />
          </svg>
          Информация
        </h1>

        {/* Tab bar */}
        <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActive(id)}
              className={`flex flex-shrink-0 items-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium transition-colors ${
                active === id
                  ? "border-accent bg-accent text-accent-fg"
                  : "border-border-subtle bg-bg-subtle text-fg-muted hover:text-fg hover:bg-bg-raised"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="animate-fade-in">{CONTENT[active]}</div>
      </div>
  );
}
