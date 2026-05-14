import { useHubStore } from '../store/useHubStore';
import { t } from '../i18n/pl';

export function VolumeBySource() {
  const { sources } = useHubStore();
  const ranked = [...sources]
    .filter((s) => s.volume > 0)
    .sort((a, b) => b.volume - a.volume);
  const max = Math.max(...ranked.map((s) => s.volume));

  return (
    <div className="bg-[#15181f] rounded-2xl border border-[#262a33] p-4">
      <h4 className="text-sm text-gray-200 font-medium mb-3">{t.panels.volume}</h4>
      <ul className="space-y-2">
        {ranked.map((s) => (
          <li key={s.id} className="flex items-center gap-3 text-xs">
            <span className="w-16 text-gray-400 shrink-0">{s.shortLabel}</span>
            <div className="flex-1 h-1.5 bg-[#262a33] rounded-full overflow-hidden">
              <div
                className={`h-full ${s.barColor} rounded-full`}
                style={{ width: `${(s.volume / max) * 100}%` }}
              />
            </div>
            <span className="w-12 text-right text-gray-300 tabular-nums">
              {s.volume.toFixed(1)}k
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
