import { t } from '../i18n/pl';
import { useHubStore } from '../store/useHubStore';

const transformations = [
  { id: 'normalization', label: t.sidebar.items.normalization },
  { id: 'deduplication', label: t.sidebar.items.deduplication },
  { id: 'fieldMapping', label: t.sidebar.items.fieldMapping, active: true },
  { id: 'validation', label: t.sidebar.items.validation },
];

export function Sidebar() {
  const { sources, filterSourceId, toggleFilter } = useHubStore();

  return (
    <aside className="w-[200px] shrink-0 border-r border-[#262a33] bg-[#0f1115] py-4 px-3">
      <h3 className="text-[10px] tracking-widest text-gray-500 font-semibold px-2 mb-2">
        {t.sidebar.sources}
      </h3>
      <ul className="space-y-0.5 mb-6">
        {sources.map((s) => (
          <li key={s.id}>
            <button
              onClick={() => toggleFilter(s.id)}
              className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-sm text-left transition ${
                filterSourceId === s.id
                  ? 'bg-[#1f242d] text-white'
                  : 'text-gray-300 hover:bg-[#1a1d24]'
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${s.dot} shrink-0`} />
              <span className="truncate">{s.name.replace(' prod-db', '').replace(' Stream', '')}</span>
            </button>
          </li>
        ))}
      </ul>

      <h3 className="text-[10px] tracking-widest text-gray-500 font-semibold px-2 mb-2">
        {t.sidebar.transformations}
      </h3>
      <ul className="space-y-0.5">
        {transformations.map((tr) => (
          <li key={tr.id}>
            <button
              className={`w-full text-left px-2 py-1.5 rounded-md text-sm transition ${
                tr.active
                  ? 'bg-[#1f242d] text-white font-medium'
                  : 'text-gray-300 hover:bg-[#1a1d24]'
              }`}
            >
              {tr.label}
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
