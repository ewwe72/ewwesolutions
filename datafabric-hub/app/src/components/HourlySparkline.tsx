import { t } from '../i18n/pl';

const bars = (() => {
  const arr: number[] = [];
  for (let i = 0; i < 16; i++) {
    const base = 18 + Math.sin(i * 0.4) * 8;
    const growth = i > 10 ? (i - 10) * 4 : 0;
    arr.push(Math.max(8, base + growth + Math.random() * 6));
  }
  return arr;
})();

export function HourlySparkline() {
  const max = Math.max(...bars);
  return (
    <div className="bg-[#15181f] rounded-2xl border border-[#262a33] p-4">
      <div className="flex items-end gap-1 h-[72px]">
        {bars.map((v, i) => {
          const isLatest = i === bars.length - 1;
          return (
            <div
              key={i}
              className={`flex-1 rounded-sm ${isLatest ? 'bg-emerald-400' : 'bg-emerald-500/70'}`}
              style={{ height: `${(v / max) * 100}%` }}
            />
          );
        })}
      </div>
      <div className="flex justify-between text-[11px] text-gray-500 mt-2">
        <span>{t.chart.morning}</span>
        <span>{t.chart.noon}</span>
        <span>{t.chart.now}</span>
      </div>
    </div>
  );
}
