import { useHubStore } from '../store/useHubStore';
import { SourceCard } from './SourceCard';

export function SourceList() {
  const { sources, filterSourceId, toggleFilter } = useHubStore();

  return (
    <div className="bg-[#15181f] rounded-2xl border border-[#262a33] p-3 space-y-2">
      {sources.map((s) => (
        <SourceCard
          key={s.id}
          source={s}
          active={filterSourceId === s.id}
          onClick={() => toggleFilter(s.id)}
        />
      ))}
    </div>
  );
}
