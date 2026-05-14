import { ArrowDown } from 'lucide-react';
import { schema, schemaVersion } from '../mocks/schema';
import { t } from '../i18n/pl';

const entityColor: Record<string, string> = {
  USR: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  ORD: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  EVT: 'bg-sky-500/20 text-sky-300 border-sky-500/30',
};

const tagColor: Record<string, string> = {
  CRM: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  PG: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  Stripe: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  SAP: 'bg-amber-600/15 text-amber-200 border-amber-600/30',
  GA4: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  Kafka: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
};

export function UnifiedSchema() {
  return (
    <div className="bg-[#15181f] rounded-2xl border border-[#262a33] p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h4 className="text-sm text-gray-200 font-medium">{t.panels.schema}</h4>
        <span className="text-xs text-gray-500">
          {t.panels.version} {schemaVersion}
        </span>
      </div>
      <div className="space-y-2">
        {schema.map((e, idx) => (
          <div key={e.id}>
            <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-[#1a1d24] border border-[#262a33]">
              <span
                className={`px-2 py-0.5 rounded-md text-[11px] font-semibold border ${entityColor[e.id]}`}
              >
                {e.id}
              </span>
              <span className="text-sm text-white">{e.name}</span>
              <span className="text-xs text-gray-400 flex-1 truncate">{e.fields}</span>
              <div className="flex gap-1 shrink-0">
                {e.sources.map((src) => (
                  <span
                    key={src}
                    className={`px-2 py-0.5 rounded-md text-[10px] border ${tagColor[src] ?? 'bg-gray-500/15 text-gray-300 border-gray-500/30'}`}
                  >
                    {src}
                  </span>
                ))}
              </div>
            </div>
            {idx < schema.length - 1 && (
              <div className="flex justify-center py-0.5">
                <ArrowDown size={14} className="text-gray-600" />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
