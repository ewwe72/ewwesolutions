import type { Source } from '../types';

const statusPillClass: Record<string, string> = {
  ok: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  sync: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  lag: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  'błąd': 'bg-rose-500/15 text-rose-300 border-rose-500/30',
  live: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
};

function formatCount(n: number | null, status: string): string {
  if (n === null) return status === 'live' ? '∞' : '—';
  return n.toLocaleString('pl-PL').replace(/,/g, ' ');
}

interface Props {
  source: Source;
  active: boolean;
  onClick: () => void;
}

export function SourceCard({ source, active, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl border transition text-left ${
        active
          ? 'bg-[#1f242d] border-[#3a4150]'
          : 'bg-[#1a1d24] border-[#262a33] hover:bg-[#1f242d]'
      }`}
    >
      <span className={`w-2 h-2 rounded-full ${source.dot} shrink-0`} />
      <div className="flex-1 min-w-0">
        <div className="text-[15px] text-white truncate">{source.name}</div>
        <div className="text-xs text-gray-500 truncate">{source.protocol}</div>
      </div>
      <div className="text-[15px] text-gray-200 tabular-nums whitespace-nowrap">
        {formatCount(source.count, source.status)}
      </div>
      <span
        className={`px-2.5 py-0.5 rounded-full text-xs border ${statusPillClass[source.status]}`}
      >
        {source.status}
      </span>
    </button>
  );
}
