import { useHubStore } from '../store/useHubStore';
import { t } from '../i18n/pl';

const sourceTagColor: Record<string, string> = {
  Stripe: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  SAP: 'bg-amber-600/15 text-amber-200 border-amber-600/30',
  Kafka: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  Salesforce: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  GA4: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  PostgreSQL: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  SOAP: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
};

function matchesFilter(sourceName: string, filterId: string | null, sources: { id: string; shortLabel: string; name: string }[]) {
  if (!filterId) return true;
  const src = sources.find((s) => s.id === filterId);
  if (!src) return true;
  return sourceName === src.shortLabel || src.name.startsWith(sourceName) || sourceName === 'PostgreSQL' && src.id === 'postgres';
}

export function EventLog() {
  const { events, filterSourceId, sources, setFilter } = useHubStore();
  const filtered = events.filter((e) => matchesFilter(e.source, filterSourceId, sources));
  const activeSource = sources.find((s) => s.id === filterSourceId);

  return (
    <div className="bg-[#15181f] rounded-2xl border border-[#262a33] p-4 flex flex-col min-h-[280px]">
      <div className="flex items-baseline justify-between mb-3">
        <h4 className="text-sm text-gray-200 font-medium">{t.panels.log}</h4>
        {activeSource ? (
          <button
            onClick={() => setFilter(null)}
            className="text-xs text-emerald-400 hover:text-emerald-300"
          >
            {activeSource.shortLabel} · {t.clearFilter}
          </button>
        ) : (
          <span className="text-xs text-gray-500">{t.panels.logSubtitle}</span>
        )}
      </div>
      <ul className="space-y-3 overflow-y-auto pr-1 flex-1">
        {filtered.map((e) => (
          <li key={e.id} className="flex gap-3 items-start text-sm">
            <span className="text-xs text-gray-500 tabular-nums w-10 shrink-0 mt-0.5">
              {e.time}
            </span>
            <span
              className={`px-2 py-0.5 rounded-md text-[10px] border shrink-0 mt-0.5 ${sourceTagColor[e.source] ?? 'bg-gray-500/15 text-gray-300 border-gray-500/30'}`}
            >
              {e.source}
            </span>
            <span className="text-gray-300 leading-snug">{e.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
