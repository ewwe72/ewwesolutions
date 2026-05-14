import { MoreHorizontal } from 'lucide-react';
import { t } from '../i18n/pl';

const tabs = [
  { id: 'overview', label: t.tabs.overview, active: true },
  { id: 'mapping', label: t.tabs.mapping },
  { id: 'flows', label: t.tabs.flows },
  { id: 'alerts', label: t.tabs.alerts },
  { id: 'settings', label: t.tabs.settings },
];

export function Topbar() {
  return (
    <header className="flex items-center gap-6 px-5 py-3 border-b border-[#262a33] bg-[#0f1115]">
      <div className="flex items-center gap-2 min-w-[180px]">
        <span className="w-2 h-2 rounded-full bg-emerald-500" />
        <span className="font-semibold text-[15px] leading-tight text-white">
          DataFabric<br />Hub
        </span>
      </div>

      <nav className="flex items-center gap-6 flex-1">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`relative text-sm pb-1 transition ${
              tab.active
                ? 'text-white font-medium'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {tab.label}
            {tab.active && (
              <span className="absolute -bottom-1 left-0 right-0 h-[2px] bg-emerald-500 rounded-full" />
            )}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#1a1d24] border border-[#262a33] text-xs text-gray-200">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          {t.status.live}
        </span>
        <span className="px-3 py-1 rounded-full bg-amber-950/60 border border-amber-700/40 text-xs text-amber-300">
          {t.status.warnings}
        </span>
        <button
          aria-label="menu"
          className="p-1.5 rounded-full hover:bg-[#1a1d24] text-gray-400"
        >
          <MoreHorizontal size={18} />
        </button>
      </div>
    </header>
  );
}
