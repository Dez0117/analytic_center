import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, type Filters } from "./api";
import type { Category, Item, ParserStatus, PollStatus, Priority, RegulationCase, Source, SourceType, Stats } from "./types";

type Tab = "today" | "regulations" | "sources" | "manual";

const priorityText: Record<Priority, string> = { high: "Высокий", medium: "Средний", low: "Низкий" };
const categoryText: Record<Category, string> = {
  regulation: "Регулирование", reputation: "Репутация", competitors: "Конкуренты", trends: "Тренды",
};
const sourceTypeText: Record<SourceType, string> = {
  rss: "RSS", website: "Сайт", regulator: "Регулятор", telegram: "Telegram", manual: "Ручной", unknown: "Не задан",
};
const pollStatusText: Record<PollStatus, string> = {
  ok: "Материалы собраны", not_modified: "Без изменений", skipped: "Не опрашивается", error: "Ошибка опроса",
};
const fieldText: Record<string, string> = {
  title: "Заголовок", summary: "Саммари", primary_category: "Категория",
  manual_priority: "Итоговый приоритет", tags: "Теги", hidden: "Скрытие",
};

const formatDate = (value: string | null, withTime = true) => value
  ? new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) }).format(new Date(value))
  : "не указано";

type Run = <T,>(work: () => Promise<T>, message?: string | ((result: T) => string)) => Promise<T | undefined>;

function Spinner() { return <span className="spinner" aria-label="Загрузка" />; }

function Sidebar({ tab, onTab, stats }: { tab: Tab; onTab: (tab: Tab) => void; stats: Stats | null }) {
  const nav: Array<[Tab, string, string, number?]> = [
    ["today", "⌁", "Сегодня", stats?.events],
    ["regulations", "§", "НПА на контроле", stats?.regulations],
    ["sources", "◎", "Источники"],
    ["manual", "+", "Добавить материал"],
  ];
  return <aside className="sidebar">
    <div className="brand"><div className="brand-mark"><i /><i /><i /></div><div><strong>GosRadar</strong><span>аналитический центр</span></div></div>
    <nav>
      <span className="nav-label">Рабочее пространство</span>
      {nav.map(([key, icon, label, count]) => <button key={key} className={tab === key ? "active" : ""} onClick={() => onTab(key)}>
        <b>{icon}</b><span>{label}</span>{count !== undefined && <em>{count}</em>}
      </button>)}
    </nav>
    <div className="sidebar-foot">
      <span className="status-dot" /> Система работает
      <small>{stats?.provider || "подключение…"}</small>
    </div>
  </aside>;
}

function Metric({ label, value, tone, hint }: { label: string; value: number; tone: string; hint: string }) {
  return <div className={`metric ${tone}`}><div><span>{label}</span><strong>{value}</strong></div><small>{hint}</small></div>;
}

function ItemCard({ item, onOpen }: { item: Item; onOpen: (item: Item) => void }) {
  const analysis = item.analysis;
  const priority = item.display_priority;
  return <article className={`item-card priority-${priority || "none"}`} onClick={() => onOpen(item)}>
    <div className="card-top">
      <div className="pills">
        {priority && <span className={`pill priority ${priority}`}><i />{priorityText[priority]}</span>}
        {analysis && <span className="pill neutral">{categoryText[analysis.primary_category]}</span>}
        {analysis?.review_required && <span className="pill review">! Нужна проверка</span>}
        {!analysis && <span className="pill pending">Без анализа</span>}
      </div>
      <button className="icon-button" aria-label="Открыть карточку">→</button>
    </div>
    <h3>{item.title}</h3>
    <p>{analysis?.summary || item.text.slice(0, 220) + (item.text.length > 220 ? "…" : "")}</p>
    {analysis?.impact_for_gs_labs && <div className="impact"><span>Почему важно GS Labs</span>{analysis.impact_for_gs_labs}</div>}
    <div className="card-meta">
      <span className="source-avatar">{item.source.name.slice(0, 1)}</span>
      <span>{item.source.name}</span>
      <span className="type-badge small">{sourceTypeText[item.source.type as SourceType] || item.source.type}</span><i />
      <span>{formatDate(item.published_at || item.fetched_at)}</span>
      {item.cluster_sources.length > 1 && <><i /><span className="cluster-count">⧉ {item.cluster_sources.length} источника</span></>}
    </div>
    {analysis && <div className="tag-row">{analysis.affected_products.slice(0, 3).map(tag => <span key={tag}>{tag}</span>)}{analysis.tags.slice(0, 2).map(tag => <span key={tag}>#{tag}</span>)}</div>}
  </article>;
}

function FiltersBar({ filters, setFilters, sources }: { filters: Filters; setFilters: (filters: Filters) => void; sources: Source[] }) {
  const set = (key: keyof Filters, value: string) => setFilters({ ...filters, [key]: value });
  const selectCount = Object.values(filters).filter(Boolean).length - (filters.q ? 1 : 0);
  return <div className="filters-panel">
    <label className="search"><span>⌕</span><input value={filters.q || ""} onChange={e => set("q", e.target.value)} placeholder="Поиск по материалам, тегам, компаниям…" /></label>
    <div className="filters">
      <input type="date" aria-label="Дата" value={filters.published_date || ""} onChange={e => set("published_date", e.target.value)} />
      <select aria-label="Источник" value={filters.source || ""} onChange={e => set("source", e.target.value)}><option value="">Все источники</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select>
      <select aria-label="Категория" value={filters.category || ""} onChange={e => set("category", e.target.value)}><option value="">Все категории</option>{Object.entries(categoryText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select aria-label="Приоритет" value={filters.priority || ""} onChange={e => set("priority", e.target.value)}><option value="">Любой приоритет</option>{Object.entries(priorityText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select aria-label="Тип" value={filters.entity_type || ""} onChange={e => set("entity_type", e.target.value)}><option value="">Новости и НПА</option><option value="news">Новости</option><option value="regulation">НПА</option></select>
      <select aria-label="Проверка" value={filters.review_required || ""} onChange={e => set("review_required", e.target.value)}><option value="">Любой статус</option><option value="true">Нужна проверка</option><option value="false">Проверено автоматически</option></select>
      {selectCount > 0 && <button className="clear" onClick={() => setFilters({ q: filters.q })}>Сбросить · {selectCount}</button>}
    </div>
  </div>;
}

function DetailDrawer({ item, onClose, onChanged, run }: { item: Item; onClose: () => void; onChanged: (item?: Item) => void; run: Run }) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState({ title: "", summary: "", primary_category: "trends" as Category, manual_priority: "" as Priority | "", tags: "", reason: "" });
  useEffect(() => setDraft({
    title: item.title,
    summary: item.analysis?.summary || "",
    primary_category: item.analysis?.primary_category || "trends",
    manual_priority: item.manual_priority || "",
    tags: item.analysis?.tags.join(", ") || "",
    reason: "",
  }), [item]);
  const analysis = item.analysis;

  const save = async () => {
    setSaving(true);
    const updated = await run(() => api.patchItem(item.id, {
      title: draft.title,
      summary: draft.summary,
      primary_category: draft.primary_category,
      ...(draft.manual_priority ? { manual_priority: draft.manual_priority } : {}),
      tags: draft.tags.split(",").map(tag => tag.trim()).filter(Boolean),
      reason: draft.reason || undefined,
    }), "Правки сохранены");
    setSaving(false);
    if (updated) { setEditing(false); onChanged(updated); }
  };
  const analyze = async (again = false) => {
    const updated = await run(() => api.analyze(item.id, again), again ? "Создана новая версия анализа" : "Материал проанализирован");
    if (updated) onChanged(updated);
  };

  return <div className="drawer-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <aside className="drawer" role="dialog" aria-modal="true" aria-label="Карточка материала">
      <div className="drawer-head"><div><span className="eyebrow">Карточка события · {item.cluster_id}</span><div className="pills">
        {item.display_priority && <span className={`pill priority ${item.display_priority}`}><i />{priorityText[item.display_priority]}</span>}
        {analysis?.review_required && <span className="pill review">! Нужна проверка</span>}
      </div></div><button className="close" onClick={onClose}>×</button></div>
      <div className="drawer-scroll">
        {editing ? <div className="edit-form">
          <label>Заголовок<input value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
          <label>Саммари<textarea rows={5} value={draft.summary} onChange={e => setDraft({ ...draft, summary: e.target.value })} /></label>
          <div className="form-row"><label>Категория<select value={draft.primary_category} onChange={e => setDraft({ ...draft, primary_category: e.target.value as Category })}>{Object.entries(categoryText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Итоговый приоритет<select value={draft.manual_priority} onChange={e => setDraft({ ...draft, manual_priority: e.target.value as Priority | "" })}><option value="">Не переопределять</option>{Object.entries(priorityText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
          <label>Теги<input value={draft.tags} onChange={e => setDraft({ ...draft, tags: e.target.value })} placeholder="через запятую" /></label>
          <label>Причина правки<input value={draft.reason} onChange={e => setDraft({ ...draft, reason: e.target.value })} placeholder="Необязательно" /></label>
          <div className="edit-actions"><button className="button ghost" onClick={() => setEditing(false)}>Отмена</button><button className="button primary" disabled={saving} onClick={save}>{saving ? <Spinner /> : "Сохранить правки"}</button></div>
        </div> : <>
          <div className="detail-title"><h2>{item.title}</h2><div className="detail-meta"><span>{item.source.name}</span><i />{formatDate(item.published_at || item.fetched_at)}{item.url && <a href={item.url} target="_blank" rel="noreferrer">Открыть оригинал ↗</a>}</div></div>
          {!analysis ? <div className="no-analysis"><div>◇</div><h3>Материал ещё не проанализирован</h3><p>Запустите безопасный mock-анализ или подключённую модель.</p><button className="button primary" onClick={() => analyze()}>Проанализировать</button></div> : <>
            {analysis.review_required && <div className="review-banner"><b>Требуется проверка специалиста</b><span>Evidence, чувствительная тема или формат ответа потребовали ручного контроля.</span></div>}
            <section><span className="section-label">Коротко</span><p className="summary">{analysis.summary}</p></section>
            <section className="facts"><span className="section-label">Что произошло</span><dl><div><dt>Кто</dt><dd>{analysis.who.join(", ") || "не указано"}</dd></div><div><dt>Что</dt><dd>{analysis.what}</dd></div><div><dt>Когда</dt><dd>{analysis.when ? formatDate(analysis.when) : "не указано"}</dd></div></dl></section>
            <section className="gs-impact"><span className="section-label">Почему важно GS Labs</span><p>{analysis.impact_for_gs_labs}</p>{analysis.affected_products.length > 0 && <div className="tag-row">{analysis.affected_products.map(product => <span key={product}>{product}</span>)}</div>}</section>
            <section><span className="section-label">Приоритет и основание</span><div className="priority-grid"><div><small>AI предложил</small><b className={analysis.suggested_priority}>{priorityText[analysis.suggested_priority]}</b></div><div><small>После правил</small><b className={analysis.effective_priority}>{priorityText[analysis.effective_priority]}</b></div><div><small>Решение аналитика</small><b className={item.manual_priority || "unset"}>{item.manual_priority ? priorityText[item.manual_priority] : "Не задано"}</b></div></div><p className="muted">{analysis.priority_reason}</p></section>
            <section><span className="section-label">Доказательства</span><div className="evidence-list">{analysis.evidence.map((evidence, index) => <blockquote key={index}><p>«{evidence.quote}»</p><cite>{evidence.claim}</cite></blockquote>)}</div></section>
            {analysis.uncertainties.length > 0 && <section><span className="section-label">Что нужно уточнить</span><ul className="uncertainties">{analysis.uncertainties.map(value => <li key={value}>{value}</li>)}</ul></section>}
            <section><span className="section-label">Теги</span><div className="tag-row">{analysis.tags.map(tag => <span key={tag}>#{tag}</span>)}</div></section>
          </>}
          <section><span className="section-label">Источники события · {item.cluster_sources.length}</span><div className="sources-list">{item.cluster_sources.map(source => <div key={source.item_id}><span className="source-avatar">{source.source.slice(0, 1)}</span><p><b>{source.source}</b><small>{formatDate(source.published_at)}</small></p>{source.url && <a href={source.url} target="_blank" rel="noreferrer">↗</a>}</div>)}</div></section>
          {item.audit.length > 0 && <section><span className="section-label">История правок</span><div className="audit-list">{item.audit.map(entry => <div key={entry.id}><i /><p><b>{fieldText[entry.field] || entry.field}</b><span>{entry.user} · {formatDate(entry.created_at)}{entry.reason ? ` · ${entry.reason}` : ""}</span></p></div>)}</div></section>}
        </>}
      </div>
      {!editing && <div className="drawer-actions"><button className="button ghost" onClick={() => analyze(true)}>Обновить AI-анализ</button><button className="button primary" onClick={() => setEditing(true)}>Редактировать</button></div>}
    </aside>
  </div>;
}

function Regulations({ cases, onOpen }: { cases: RegulationCase[]; onOpen: (id: string) => void }) {
  return <div className="page-section"><div className="section-heading"><div><span className="eyebrow">Долгий жизненный цикл</span><h2>НПА на контроле</h2><p>Стадии, контрольные даты и все обновления одного дела.</p></div></div>
    {cases.length === 0 ? <Empty title="Пока нет НПА" text="Загрузите и проанализируйте demo-данные — здесь появятся таймлайны." /> : <div className="reg-grid">{cases.map(item => <article className="reg-card" key={item.id}>
      <div className="reg-head"><span>§</span><div><small>{item.identifier || "Без номера"}</small><h3>{item.title}</h3></div><b>{item.stage || "стадия не указана"}</b></div>
      <div className="deadlines"><div><span>Следующая контрольная дата</span><strong>{formatDate(item.next_checkpoint, false)}</strong></div><div><span>Вступление в силу</span><strong>{formatDate(item.effective_date, false)}</strong></div></div>
      <div className="timeline">{item.timeline.map((event, index) => <button key={event.item_id} onClick={() => onOpen(event.item_id)}><i className={index === item.timeline.length - 1 ? "current" : ""} /><time>{formatDate(event.date, false)}</time><div><b>{event.stage}</b><span>{event.title}</span></div></button>)}</div>
    </article>)}</div>}
  </div>;
}

function SourceRow({ source, onChanged, onShowItems, run }: { source: Source; onChanged: () => void; onShowItems: (id: string) => void; run: Run }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const blank = () => ({
    name: source.name,
    url: source.url || "",
    type: source.type,
    poll_interval_minutes: String(source.poll_interval_minutes),
    config: JSON.stringify(source.config || {}, null, 2),
  });
  const [draft, setDraft] = useState(blank);
  useEffect(() => { setDraft(blank()); setProblem(""); }, [source]);

  const save = async () => {
    let config: Record<string, unknown>;
    try { config = draft.config.trim() ? JSON.parse(draft.config) : {}; }
    catch { setProblem("Настройки сбора должны быть корректным JSON"); return; }
    const interval = Number(draft.poll_interval_minutes);
    if (!Number.isInteger(interval) || interval < 1 || interval > 1440) { setProblem("Интервал опроса — целое число от 1 до 1440 минут"); return; }
    setProblem(""); setBusy(true);
    const saved = await run(() => api.patchSource(source.id, {
      name: draft.name, url: draft.url, type: draft.type, poll_interval_minutes: interval, config,
    }), "Источник обновлён");
    setBusy(false);
    if (saved) { setEditing(false); onChanged(); }
  };

  const poll = async () => {
    setBusy(true);
    const report = await run(() => api.pollSource(source.id), result => result.status === "error"
      ? `${result.source_name}: опрос не удался`
      : result.status === "skipped" ? `${result.source_name}: источник не опрашивается`
      : `${result.source_name}: получено ${result.fetched}, новых ${result.stored}, повторов ${result.duplicates}`);
    setBusy(false);
    if (report) onChanged();
  };

  const remove = async () => {
    if (!window.confirm(`Удалить источник «${source.name}»? Собранные материалы останутся в ленте.`)) return;
    await run(() => api.deleteSource(source.id), "Источник удалён");
    onChanged();
  };

  return <>
    <div className={`table-row${source.enabled ? "" : " off"}`}>
      <div><span className="source-avatar">{source.name.slice(0, 1)}</span><p><b>{source.name}</b><small>{source.url || "URL не указан"}</small></p></div>
      <span className="type-badge">{sourceTypeText[source.type] || source.type}</span>
      <span>{source.pollable ? `каждые ${source.poll_interval_minutes} мин` : "вручную"}</span>
      <button className="link-count" disabled={!source.items_count} onClick={() => onShowItems(source.id)} title="Показать материалы этого источника">
        {source.items_count}{source.items_count > 0 && <i>→</i>}
      </button>
      <span className={`poll-state ${source.last_status || "none"}`} title={source.last_error || ""}>
        <i />
        <p><b>{source.last_status ? pollStatusText[source.last_status] : "Ещё не опрашивался"}</b>
        <small>{source.last_polled_at ? formatDate(source.last_polled_at) : "нет данных"}</small></p>
      </span>
      <label className="switch" title={source.enabled ? "Выключить сбор" : "Включить сбор"}>
        <input type="checkbox" checked={source.enabled} onChange={async () => { await run(() => api.patchSource(source.id, { enabled: !source.enabled }), "Настройка сохранена"); onChanged(); }} /><i />
      </label>
      <div className="row-actions">
        <button className="button tiny" disabled={busy || !source.pollable || !source.enabled} onClick={poll}>{busy ? <Spinner /> : "Опросить"}</button>
        <button className="button tiny ghost" onClick={() => setEditing(!editing)}>{editing ? "Свернуть" : "Изменить"}</button>
        <button className="delete" aria-label="Удалить источник" onClick={remove}>×</button>
      </div>
    </div>
    {editing && <div className="source-edit">
      <div className="form-row">
        <label>Название<input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
        <label>Адрес<input value={draft.url} onChange={e => setDraft({ ...draft, url: e.target.value })} placeholder="https://… или @channel" /></label>
      </div>
      <div className="form-row">
        <label>Тип<select value={draft.type} onChange={e => setDraft({ ...draft, type: e.target.value as SourceType })}>
          {Object.entries(sourceTypeText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label>Интервал опроса, мин<input type="number" min={1} max={1440} value={draft.poll_interval_minutes} onChange={e => setDraft({ ...draft, poll_interval_minutes: e.target.value })} /></label>
      </div>
      <label>Настройки сбора (JSON)<textarea rows={5} spellCheck={false} value={draft.config} onChange={e => setDraft({ ...draft, config: e.target.value })} /></label>
      <p className="hint">fetcher, max_items, fetch_full_text, link_pattern, min_text_chars — разбор источника настраивается без правки кода.</p>
      {source.last_error && <p className="row-error">Последняя ошибка: {source.last_error}</p>}
      {problem && <p className="row-error">{problem}</p>}
      <div className="edit-actions">
        <button className="button ghost" onClick={() => { setDraft(blank()); setEditing(false); setProblem(""); }}>Отмена</button>
        <button className="button primary" disabled={busy} onClick={save}>{busy ? <Spinner /> : "Сохранить источник"}</button>
      </div>
    </div>}
  </>;
}

function SourcesPage({ sources, parser, onChanged, onShowItems, run }: { sources: Source[]; parser: ParserStatus | null; onChanged: () => void; onShowItems: (id: string) => void; run: Run }) {
  const [form, setForm] = useState({ name: "", type: "rss", url: "", poll_interval_minutes: "30" });
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const saved = await run(() => api.createSource({ ...form, poll_interval_minutes: Number(form.poll_interval_minutes) || 30 }), "Источник добавлен, сбор начнётся по расписанию");
    if (saved) { setForm({ name: "", type: "rss", url: "", poll_interval_minutes: "30" }); onChanged(); }
  };
  const collected = sources.reduce((total, source) => total + source.items_count, 0);
  const failing = sources.filter(source => source.last_status === "error");

  const pollAll = async (force: boolean) => {
    setBusy(true);
    const result = await run(() => api.runParser(force), value => value.polled === 0
      ? "По расписанию сейчас опрашивать нечего"
      : `Опрошено источников: ${value.polled}, новых материалов: ${value.stored}, повторов отсечено: ${value.duplicates}`);
    setBusy(false);
    if (result) onChanged();
  };
  const importDefaults = async () => {
    const result = await run(api.importDefaultSources, value => `Добавлено источников: ${value.created.length}`);
    if (result) onChanged();
  };

  return <div className="page-section"><div className="section-heading"><div><span className="eyebrow">Контур сбора</span><h2>Источники</h2>
    <p>СМИ, сайты регуляторов и Telegram-каналы. Каждый источник опрашивается по своему интервалу, повторы отсекаются по ссылке и тексту.</p></div></div>

    <div className="parser-panel">
      <div className={`parser-state ${parser?.running ? "live" : "idle"}`}><i />
        <p><b>{parser?.running ? "Планировщик работает" : parser?.enabled === false ? "Планировщик выключен" : "Планировщик не запущен"}</b>
        <small>{parser ? `проверка расписания каждые ${parser.tick_seconds} с · ждут опроса: ${parser.due_now} · последний запуск: ${formatDate(parser.last_run_at)}` : "нет данных"}</small></p>
      </div>
      <div className="parser-actions">
        <button className="button ghost" disabled={busy} onClick={() => pollAll(false)}>{busy ? <Spinner /> : "Опросить по расписанию"}</button>
        <button className="button primary" disabled={busy} onClick={() => pollAll(true)}>{busy ? <Spinner /> : "Опросить все источники"}</button>
      </div>
    </div>

    <div className="source-stats">
      <span><b>{sources.length}</b> источников, включено {sources.filter(source => source.enabled).length}</span>
      <span><b>{collected}</b> материалов собрано</span>
      {failing.length > 0 && <span className="bad"><b>{failing.length}</b> с ошибкой опроса</span>}
      {sources.length === 0 && <button className="button tiny ghost" onClick={importDefaults}>Добавить источники по умолчанию</button>}
    </div>

    <form className="source-form" onSubmit={submit}>
      <input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Название источника" />
      <input value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} placeholder="https://…/rss.xml или @channel" />
      <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })}>
        {Object.entries(sourceTypeText).filter(([value]) => value !== "unknown").map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <input type="number" min={1} max={1440} aria-label="Интервал опроса в минутах" value={form.poll_interval_minutes} onChange={e => setForm({ ...form, poll_interval_minutes: e.target.value })} />
      <button className="button primary">Добавить</button>
    </form>

    {sources.length === 0
      ? <Empty title="Источников пока нет" text="Добавьте RSS-ленту, сайт регулятора или Telegram-канал — сбор начнётся автоматически." />
      : <div className="source-table">
        <div className="table-head"><span>Источник</span><span>Тип</span><span>Расписание</span><span>Собрано</span><span>Последний опрос</span><span>Сбор</span><span /></div>
        {sources.map(source => <SourceRow key={source.id} source={source} onChanged={onChanged} onShowItems={onShowItems} run={run} />)}
      </div>}
  </div>;
}

function ManualPage({ onCreated, run }: { onCreated: (item: Item) => void; run: Run }) {
  const [form, setForm] = useState({ title: "", text: "", url: "", source_name: "Ручное добавление", published_at: "" });
  const submit = async (event: FormEvent) => { event.preventDefault(); const item = await run(() => api.createManual({ ...form, published_at: form.published_at || null }), "Материал добавлен"); if (item) { setForm({ title: "", text: "", url: "", source_name: "Ручное добавление", published_at: "" }); onCreated(item); } };
  return <div className="page-section manual-page"><div className="section-heading"><div><span className="eyebrow">Ручной импорт</span><h2>Добавить материал</h2><p>Для материалов вне настроенных источников. Анализ можно запустить сразу из карточки.</p></div></div>
    <form className="manual-form" onSubmit={submit}><label>Заголовок<input required value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} placeholder="Что произошло?" /></label><label>Полный текст<textarea required rows={10} value={form.text} onChange={e => setForm({ ...form, text: e.target.value })} placeholder="Вставьте текст публикации или НПА…" /></label><div className="form-row"><label>Ссылка на оригинал<input type="url" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} placeholder="https://…" /></label><label>Название источника<input value={form.source_name} onChange={e => setForm({ ...form, source_name: e.target.value })} /></label></div><label>Дата публикации<input type="datetime-local" value={form.published_at} onChange={e => setForm({ ...form, published_at: e.target.value })} /></label><button className="button primary">Добавить материал</button></form>
  </div>;
}

function Empty({ title, text }: { title: string; text: string }) { return <div className="empty"><div className="radar-empty"><i /><i /><i /><b /></div><h3>{title}</h3><p>{text}</p></div>; }

export default function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [items, setItems] = useState<Item[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [regulations, setRegulations] = useState<RegulationCase[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [parser, setParser] = useState<ParserStatus | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [selected, setSelected] = useState<Item | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [newItems, newSources, newRegulations, newStats, newParser] = await Promise.all([
        api.items(filters), api.sources(), api.regulations(), api.stats(), api.parserStatus(),
      ]);
      setItems(newItems); setSources(newSources); setRegulations(newRegulations); setStats(newStats); setParser(newParser);
      setUpdatedAt(new Date());
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось загрузить данные"); }
    finally { setLoading(false); }
  }, [filters]);
  useEffect(() => { const timer = setTimeout(refresh, filters.q ? 250 : 0); return () => clearTimeout(timer); }, [refresh, filters.q]);
  useEffect(() => {
    if (tab !== "today" || selected) return;
    const timer = setInterval(refresh, 60000);
    return () => clearInterval(timer);
  }, [tab, selected, refresh]);

  const run: Run = async (work, message) => {
    setError("");
    try {
      const result = await work();
      const text = typeof message === "function" ? message(result) : message;
      if (text) { setToast(text); setTimeout(() => setToast(""), 3600); }
      return result;
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Неизвестная ошибка"); return undefined; }
  };
  const demo = async () => { setBusy(true); const result = await run(api.loadDemo, "Demo-набор загружен"); if (result) await refresh(); setBusy(false); };
  const analyze = async () => { setBusy(true); const result = await run(api.analyzeBatch, "Анализ завершён"); if (result) await refresh(); setBusy(false); };
  const openById = async (id: string) => { const item = await run(() => api.item(id)); if (item) setSelected(item); };
  const collect = async () => {
    setBusy(true);
    const result = await run(() => api.runParser(false), value => value.polled === 0
      ? "По расписанию сейчас опрашивать нечего"
      : `Опрошено источников: ${value.polled}, новых материалов: ${value.stored}`);
    if (result) await refresh();
    setBusy(false);
  };
  const showSourceItems = (id: string) => { setFilters({ source: id }); setTab("today"); };
  const heading = useMemo(() => new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long" }).format(new Date()), []);

  return <div className="app-shell">
    <Sidebar tab={tab} onTab={setTab} stats={stats} />
    <main>
      <header className="topbar"><div><span className={`live-dot${parser?.running ? "" : " off"}`} />{updatedAt ? `Обновлено в ${new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(updatedAt)}` : "Загрузка…"}{parser?.running && " · сбор идёт по расписанию"}</div><div className="demo-badge">{stats?.demo_mode ? "Демо-режим" : "OpenRouter"}</div><div className="user"><span>ДА</span><p><b>Демо Аналитик</b><small>PR / GR</small></p></div></header>
      {tab === "today" && <div className="page-section">
        <div className="hero"><div><span className="eyebrow">Оперативная повестка</span><h1>Сегодня</h1><p>{heading.charAt(0).toUpperCase() + heading.slice(1)} · одна карточка на событие</p></div><div className="hero-actions">{stats?.demo_mode && <button className="button ghost" disabled={busy} onClick={demo}>{busy ? <Spinner /> : "↓ Загрузить demo-данные"}</button>}<button className="button ghost" disabled={busy} onClick={collect}>{busy ? <Spinner /> : "⟳ Собрать из источников"}</button><button className="button primary" disabled={busy || !stats?.raw_items} onClick={analyze}>{busy ? <Spinner /> : "✦ Проанализировать последние"}</button></div></div>
        <div className="metrics"><Metric label="Событий в повестке" value={stats?.events || 0} tone="green" hint={`${stats?.raw_items || 0} исходных материалов`} /><Metric label="Высокий приоритет" value={stats?.high || 0} tone="red" hint="требуют внимания сегодня" /><Metric label="Нужна проверка" value={stats?.review || 0} tone="amber" hint="очередь аналитика" /><Metric label="Обработано AI" value={stats?.analyzed || 0} tone="blue" hint={`среднее ${stats?.average_latency_ms || 0} мс`} /></div>
        {items.length > 0 && filters.source && <div className="filter-note">Показаны материалы источника <b>{sources.find(source => source.id === filters.source)?.name || filters.source}</b><button className="clear" onClick={() => setFilters({})}>Показать все</button></div>}
        <FiltersBar filters={filters} setFilters={setFilters} sources={sources} />
        <div className="feed-head"><h2>События <span>{items.length}</span></h2><span>Сначала важные</span></div>
        {loading ? <div className="loading"><Spinner /> Загружаем повестку…</div> : items.length ? <div className="feed">{items.map(item => <ItemCard key={item.cluster_id} item={item} onOpen={setSelected} />)}</div> : <Empty title="Повестка пока пуста" text={stats?.demo_mode ? "Загрузите честно маркированный demo-набор или добавьте материал вручную." : "Соберите материалы из подключённых источников или добавьте публикацию вручную."} />}
      </div>}
      {tab === "regulations" && <Regulations cases={regulations} onOpen={openById} />}
      {tab === "sources" && <SourcesPage sources={sources} parser={parser} onChanged={refresh} onShowItems={showSourceItems} run={run} />}
      {tab === "manual" && <ManualPage run={run} onCreated={item => { setSelected(item); refresh(); }} />}
    </main>
    {selected && <DetailDrawer item={selected} onClose={() => setSelected(null)} run={run} onChanged={async item => { if (item) setSelected(item); else setSelected(null); await refresh(); }} />}
    {error && <div className="alert"><b>Ошибка</b><span>{error}</span><button onClick={() => setError("")}>×</button></div>}
    {toast && <div className="toast">✓ {toast}</div>}
  </div>;
}
